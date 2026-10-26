"""API pública (no oficial) de ESPN: calendario, estadio, resultados y estadísticas.

No necesita clave. Formato de URL:
https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/scoreboard?dates=AAAAMMDD
https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/summary?event=<id>
Nota: los nombres de campos se validan al conectar la red; si ESPN cambia algo,
estas funciones devuelven None en lugar de romper.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .http import obtener

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

LIGAS = {
    "premier": "eng.1",
    "laliga": "esp.1",
    "serie_a": "ita.1",
    "bundesliga": "ger.1",
    "ligue_1": "fra.1",
    "liga_mx": "mex.1",
    "mls": "usa.1",
    "brasil": "bra.1",
    "argentina": "arg.1",
    "champions": "uefa.champions",
    "europa_league": "uefa.europa",
    "libertadores": "conmebol.libertadores",
    "concacaf": "concacaf.champions",
    "mundial": "fifa.world",
    "eliminatorias_conmebol": "fifa.worldq.conmebol",
    "eliminatorias_concacaf": "fifa.worldq.concacaf",
    "nations_league": "uefa.nations",
    "copa_america": "conmebol.america",
    "eurocopa": "uefa.euro",
    "amistosos": "fifa.friendly",
}

SONORA = timezone(timedelta(hours=-7))  # Sonora no usa horario de verano


def _partido(ev: dict, liga: str) -> dict:
    comp = ev["competitions"][0]
    equipos = {c["homeAway"]: c for c in comp["competitors"]}
    venue = comp.get("venue") or {}
    direccion = venue.get("address") or {}
    inicio = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
    estado = ev.get("status", {}).get("type", {})

    def goles(lado):
        try:
            return int(equipos[lado].get("score"))
        except (TypeError, ValueError):
            return None

    def goles_pt(lado):
        try:
            return int(float(equipos[lado]["linescores"][0]["value"]))
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    return {
        "espn_id": ev["id"],
        "liga": liga,
        "inicio_utc": inicio.isoformat(),
        "inicio_sonora": inicio.astimezone(SONORA).strftime("%Y-%m-%d %H:%M"),
        "local": equipos["home"]["team"]["displayName"],
        "visitante": equipos["away"]["team"]["displayName"],
        "estadio": venue.get("fullName"),
        "ciudad": direccion.get("city"),
        "pais": direccion.get("country"),
        "terminado": bool(estado.get("completed")),
        "goles_local": goles("home"),
        "goles_visitante": goles("away"),
        "goles_local_pt": goles_pt("home"),
        "goles_visitante_pt": goles_pt("away"),
    }


def calendario(liga: str, dia: date | None = None, hasta: date | None = None) -> list[dict]:
    slug = LIGAS[liga]
    dia = dia or datetime.now(SONORA).date()
    fechas = dia.strftime("%Y%m%d") + (f"-{hasta.strftime('%Y%m%d')}" if hasta else "")
    datos = obtener(f"{BASE}/{slug}/scoreboard", {"dates": fechas, "limit": 500}, horas_cache=1)
    return [_partido(ev, liga) for ev in datos.get("events", [])]


def estadisticas(liga: str, espn_id: str) -> dict | None:
    """Córners, tarjetas y tiros de un partido terminado."""
    slug = LIGAS[liga]
    datos = obtener(f"{BASE}/{slug}/summary", {"event": espn_id}, horas_cache=2)
    equipos = (datos.get("boxscore") or {}).get("teams") or []
    if len(equipos) != 2:
        return None
    out = {}
    for t in equipos:
        lado = "local" if t.get("homeAway") == "home" else "visitante"
        stats = {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}

        def num(nombre):
            try:
                return float(stats.get(nombre))
            except (TypeError, ValueError):
                return None

        out[f"corners_{lado}"] = num("wonCorners")
        out[f"amarillas_{lado}"] = num("yellowCards")
        out[f"rojas_{lado}"] = num("redCards")
        out[f"tiros_{lado}"] = num("totalShots")
        out[f"tiros_puerta_{lado}"] = num("shotsOnTarget")
    for lado in ("local", "visitante"):
        a, r = out.get(f"amarillas_{lado}"), out.get(f"rojas_{lado}")
        out[f"tarjetas_{lado}"] = None if a is None else a + (r or 0)
    return out


def historial(liga: str, desde: date, hasta: date, con_estadisticas: bool = True) -> pd.DataFrame:
    """Historial de partidos terminados (para ligas que football-data no detalla)."""
    filas = []
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + timedelta(days=30), hasta)
        for p in calendario(liga, inicio, fin):
            if not p["terminado"]:
                continue
            if con_estadisticas:
                p.update(estadisticas(liga, p["espn_id"]) or {})
            filas.append(p)
        inicio = fin + timedelta(days=1)
    df = pd.DataFrame(filas)
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["inicio_utc"]).dt.tz_localize(None)
    return df
