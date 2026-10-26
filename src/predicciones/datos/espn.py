"""API pública (no oficial) de ESPN: calendario, estadio, resultados y estadísticas.

No necesita clave. Formato de URL:
https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/scoreboard?dates=AAAAMMDD  (un día)
https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/scoreboard?dates=AAAAMM&limit=500  (un mes)
https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/summary?event=<id>
Los rangos AAAAMMDD-AAAAMMDD ya no funcionan (HTTP 400). ESPN agrupa por fecha
de la hora del Este de EE. UU., no por fecha UTC.
Nota: los nombres de campos se validan al conectar la red; si ESPN cambia algo,
estas funciones devuelven None en lugar de romper.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
ESTE = ZoneInfo("America/New_York")  # ESPN agrupa los partidos por esta fecha
TOPE = 500  # máximo de eventos por consulta


ESTADOS_PRORROGA = {"STATUS_FINAL_AET", "STATUS_FINAL_PEN"}


def _linea(c: dict, i: int) -> int | None:
    try:
        x = c["linescores"][i]
        return int(float(x.get("value", x.get("displayValue"))))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _partido(ev: dict, liga: str) -> dict:
    comp = ev["competitions"][0]
    equipos = {c["homeAway"]: c for c in comp["competitors"]}
    venue = comp.get("venue") or {}
    direccion = venue.get("address") or {}
    inicio = datetime.fromisoformat((ev.get("date") or comp["date"]).replace("Z", "+00:00"))
    estado = (ev.get("status") or comp.get("status") or {}).get("type", {})

    def goles(lado):
        try:
            return int(equipos[lado].get("score"))
        except (TypeError, ValueError):
            return None

    # el marcador de ESPN incluye la prórroga: para los mercados vale el de 90 minutos
    prorroga = estado.get("name") in ESTADOS_PRORROGA or any(
        len(equipos[l].get("linescores") or []) > 2 for l in ("home", "away"))
    if prorroga:
        g90 = {}
        for lado in ("home", "away"):
            a, b = _linea(equipos[lado], 0), _linea(equipos[lado], 1)
            g90[lado] = None if a is None or b is None else a + b
        gl, gv = g90["home"], g90["away"]
        if gl is None or gv is None:
            gl = gv = None
    else:
        gl, gv = goles("home"), goles("away")

    out = {
        "espn_id": str(ev["id"]),
        "liga": liga,
        "inicio_utc": inicio.isoformat(),
        "inicio_sonora": inicio.astimezone(SONORA).strftime("%Y-%m-%d %H:%M"),
        "local": equipos["home"]["team"]["displayName"],
        "visitante": equipos["away"]["team"]["displayName"],
        "estadio": venue.get("fullName"),
        "ciudad": direccion.get("city"),
        "pais": direccion.get("country"),
        "terminado": bool(estado.get("completed")),
        "goles_local": gl,
        "goles_visitante": gv,
        "goles_local_pt": _linea(equipos["home"], 0),
        "goles_visitante_pt": _linea(equipos["away"], 0),
    }
    if prorroga:
        out["prorroga"] = True
    return out


def _meses(a: date, b: date) -> list[tuple[int, int]]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _scoreboard(slug: str, fechas: str) -> list[dict]:
    datos = obtener(f"{BASE}/{slug}/scoreboard", {"dates": fechas, "limit": TOPE}, horas_cache=1)
    return datos.get("events", [])


def _fecha_este(p: dict) -> date:
    return datetime.fromisoformat(p["inicio_utc"]).astimezone(ESTE).date()


def calendario(liga: str, dia: date | None = None, hasta: date | None = None) -> list[dict]:
    """Partidos de un día o, con `hasta`, de un rango (por fecha del Este de EE. UU.)."""
    slug = LIGAS[liga]
    dia = dia or datetime.now(SONORA).date()
    if not hasta:
        return [_partido(ev, liga) for ev in _scoreboard(slug, dia.strftime("%Y%m%d"))]
    # se pide cada mes completo (con un día de margen) y se filtra por fecha del Este
    a, b = dia - timedelta(days=1), hasta + timedelta(days=1)
    vistos: dict[str, dict] = {}
    for y, m in _meses(a, b):
        eventos = _scoreboard(slug, f"{y}{m:02d}")
        if len(eventos) >= TOPE:
            # mes lleno: puede faltar algo, se pide día por día
            eventos = []
            d = max(a, date(y, m, 1))
            while d <= b and d.month == m:
                eventos += _scoreboard(slug, d.strftime("%Y%m%d"))
                d += timedelta(days=1)
        for ev in eventos:
            p = _partido(ev, liga)
            if dia <= _fecha_este(p) <= hasta:
                vistos[p["espn_id"]] = p
    return sorted(vistos.values(), key=lambda p: p["inicio_utc"])


def estadisticas(liga: str, espn_id: str) -> dict | None:
    """Córners, tarjetas y tiros de un partido terminado."""
    slug = LIGAS[liga]
    datos = obtener(f"{BASE}/{slug}/summary", {"event": espn_id}, horas_cache=2)
    return _estadisticas(datos)


def _estadisticas(datos: dict) -> dict | None:
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
    arbitro = _arbitro(datos)
    if arbitro:
        out["arbitro"] = arbitro
    return out


def _arbitro(datos: dict) -> str | None:
    """Nombre del árbitro central, si ESPN lo trae."""
    oficiales = [o for o in ((datos.get("gameInfo") or {}).get("officials") or [])
                 if isinstance(o, dict) and o.get("displayName")]
    if not oficiales:
        return None

    def puesto(o):
        x = o.get("position")
        if isinstance(x, dict):
            x = x.get("name") or x.get("displayName")
        return str(x or "").lower()

    for o in oficiales:
        if "referee" in puesto(o) and "assistant" not in puesto(o) and "video" not in puesto(o):
            return o["displayName"]
    return oficiales[0]["displayName"]


def resultado(liga: str, espn_id: str, inicio_utc: str | None = None) -> dict | None:
    """Estado, marcador y estadísticas de un partido, leídos de summary?event=<id>.

    Si summary no trae el encabezado, busca el partido en el calendario del día
    anterior al siguiente (ESPN agrupa por fecha del Este de EE. UU.).
    """
    slug = LIGAS[liga]
    try:
        datos = obtener(f"{BASE}/{slug}/summary", {"event": espn_id}, horas_cache=0.25)
        comp = ((datos.get("header") or {}).get("competitions") or [None])[0]
        if comp and len(comp.get("competitors") or []) == 2:
            ev = {"id": espn_id, "date": comp.get("date"), "status": comp.get("status"), "competitions": [comp]}
            p = _partido(ev, liga)
            p.update(_estadisticas(datos) or {})
            return p
    except Exception:  # noqa: BLE001 - se intenta con el calendario
        pass
    if not inicio_utc:
        return None
    dia = datetime.fromisoformat(inicio_utc).date()
    for p in calendario(liga, dia - timedelta(days=1), dia + timedelta(days=1)):
        if p["espn_id"] == str(espn_id):
            if p["terminado"]:
                try:
                    p.update(estadisticas(liga, espn_id) or {})
                except Exception:  # noqa: BLE001 - sin córners/tarjetas
                    pass
            return p
    return None


def historial(liga: str, desde: date, hasta: date, con_estadisticas: bool = True) -> pd.DataFrame:
    """Historial de partidos terminados (para ligas que football-data no detalla)."""
    filas = []
    inicio = desde
    while inicio <= hasta:
        # mes calendario por mes calendario
        fin_mes = (date(inicio.year + inicio.month // 12, inicio.month % 12 + 1, 1) - timedelta(days=1))
        fin = min(fin_mes, hasta)
        filas += [p for p in calendario(liga, inicio, fin) if p["terminado"]]
        inicio = fin + timedelta(days=1)
    if con_estadisticas and filas:
        def una(p):
            try:
                return estadisticas(liga, p["espn_id"]) or {}
            except Exception:  # noqa: BLE001 - un partido sin estadísticas no tumba la liga
                return {}

        with ThreadPoolExecutor(max_workers=4) as pool:
            for p, extra in zip(filas, pool.map(una, filas)):
                p.update(extra)
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.drop_duplicates(subset=["espn_id"], keep="last")
        df["fecha"] = pd.to_datetime(df["inicio_utc"]).dt.tz_localize(None)
    return df
