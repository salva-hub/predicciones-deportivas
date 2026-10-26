"""Tiros a puerta por jugador, leídos de `rosters` del resumen de ESPN (gratis, sin clave).

https://site.api.espn.com/apis/site/v2/sports/soccer/<liga>/summary?event=<id>
Cada equipo trae `roster`: un renglón por jugador con `starter`, `subbedIn`,
`subbedOut`, `position` y `stats` (`shotsOnTarget`, `totalShots`, `appearances`...).
Los minutos no vienen: se calculan con el minuto de los cambios (`plays` con
`substitution: true`) y de la roja; sin ese minuto se estiman (titular sustituido
70', suplente que entra 20').

Antes del partido, en cuanto ESPN publica la alineación oficial (~1 hora antes),
`rosters` ya trae `starter` y la posición (G, D, M, F), pero sin estadísticas.

Limitaciones:
- Las estadísticas por jugador de ESPN cubren el partido completo: en un partido
  con prórroga incluyen la prórroga (Winpot liquida en 90 min): esas patas se
  liquidan a mano.
- ESPN no trae `rosters` (o no trae estadísticas por jugador) en algunas
  competiciones o partidos; entonces no hay tiros de jugador.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from .espn import BASE, LIGAS
from .http import obtener

HORAS_CACHE_TERMINADO = 24 * 365  # un partido terminado ya no cambia
MINUTOS_PARTIDO = 90
MINUTOS_SIN_RELOJ = {"sale": 70, "entra": 20}
MAX_PARTIDOS_EQUIPO = 50
HILOS = 6


def resumen(liga: str, espn_id: str, horas_cache: float = HORAS_CACHE_TERMINADO) -> dict:
    return obtener(f"{BASE}/{LIGAS[liga]}/summary", {"event": str(espn_id)}, horas_cache=horas_cache)


def posicion(p: dict) -> str | None:
    """G, D, M o F a partir de la posición de ESPN (None si es suplente o no se sabe).

    Antes del partido ESPN usa "Goalkeeper/Defender/Midfielder/Forward" (G, D, M, F);
    después, nombres detallados ("Center Left Defender", "Attacking Midfielder Right"...).
    """
    pos = p.get("position") or {}
    nombre = f"{pos.get('name') or ''} {pos.get('displayName') or ''}".lower()
    abbr = str(pos.get("abbreviation") or "").upper()
    if "substitute" in nombre or abbr == "SUB":
        return None
    if "goalkeeper" in nombre or abbr in ("G", "GK"):
        return "G"
    if "midfield" in nombre:
        return "M"
    if "defender" in nombre or "back" in nombre or abbr in ("D", "CB", "LB", "RB"):
        return "D"
    if "forward" in nombre or "striker" in nombre or "wing" in nombre or abbr in ("F", "FW", "ST", "CF"):
        return "F"
    if abbr in ("M", "CM", "DM", "AM"):
        return "M"
    return None


def _minuto(texto: str | None) -> int | None:
    """"74'" -> 74; "45'+2'" -> 45; "90'+4'" -> 90 (la reposición cuenta como el minuto base)."""
    m = re.match(r"\s*(\d+)", str(texto or ""))
    return min(int(m.group(1)), MINUTOS_PARTIDO) if m else None


def _stats(p: dict) -> dict[str, float]:
    out = {}
    for s in p.get("stats") or []:
        try:
            out[s.get("name")] = float(s.get("value", s.get("displayValue")))
        except (TypeError, ValueError):
            continue
    return out


def minutos(p: dict) -> int:
    """Minutos jugados (0 si no jugó), con el minuto de los cambios y de la roja."""
    titular, entra, sale = bool(p.get("starter")), bool(p.get("subbedIn")), bool(p.get("subbedOut"))
    if not titular and not entra:
        return 0
    cambios = [_minuto((x.get("clock") or {}).get("displayValue")) for x in p.get("plays") or [] if x.get("substitution")]
    cambios = [c for c in cambios if c is not None]
    rojas = [_minuto((x.get("clock") or {}).get("displayValue")) for x in p.get("plays") or [] if x.get("redCard")]
    rojas = [r for r in rojas if r is not None]
    if titular:
        inicio = 0
    elif cambios:
        inicio = cambios[0]
    else:
        inicio = MINUTOS_PARTIDO - MINUTOS_SIN_RELOJ["entra"]
    fin = MINUTOS_PARTIDO
    if sale:
        salidas = [c for c in cambios if c >= inicio]
        fin = salidas[-1] if salidas and (titular or len(cambios) > 1) else (
            MINUTOS_SIN_RELOJ["sale"] if titular else MINUTOS_PARTIDO)
    if rojas:
        fin = min(fin, rojas[0])
    return max(1, fin - inicio)


def actuaciones(datos: dict) -> list[dict]:
    """Una fila por jugador de cada equipo del resumen (vacío si ESPN no trae rosters)."""
    out = []
    for t in datos.get("rosters") or []:
        eq = t.get("team") or {}
        for p in t.get("roster") or []:
            a = p.get("athlete") or {}
            st = _stats(p)
            mins = minutos(p)
            participo = mins > 0 or st.get("appearances", 0) > 0
            out.append({
                "equipo": eq.get("displayName"), "equipo_id": str(eq.get("id") or ""),
                "lado": t.get("homeAway"), "jugador_id": str(a.get("id") or ""),
                "nombre": a.get("displayName") or a.get("fullName") or "",
                "titular": bool(p.get("starter")), "participo": bool(participo),
                "minutos": mins if participo else 0, "posicion": posicion(p),
                "tiros_puerta": st.get("shotsOnTarget"), "tiros": st.get("totalShots"),
            })
    return out


def tiros_por_jugador(datos: dict) -> dict[str, dict] | None:
    """Para calificar: {nombre: {id, equipo, participo, tiros_puerta}} (None sin rosters con estadísticas)."""
    filas = actuaciones(datos)
    if not filas or all(x["tiros_puerta"] is None for x in filas):
        return None
    return {x["nombre"]: {"id": x["jugador_id"], "equipo": x["equipo"], "participo": x["participo"],
                          "tiros_puerta": x["tiros_puerta"]} for x in filas if x["nombre"]}


def alineacion(datos: dict) -> dict[str, list[dict]] | None:
    """Titulares oficiales por lado ("home"/"away"): nombre, id y posición.

    None si ESPN todavía no publica la alineación (sin titulares en algún equipo).
    """
    out = {}
    for t in datos.get("rosters") or []:
        tit = [p for p in t.get("roster") or [] if p.get("starter")]
        if len(tit) < 11:
            return None
        out[t.get("homeAway")] = [{
            "jugador_id": str((p.get("athlete") or {}).get("id") or ""),
            "nombre": (p.get("athlete") or {}).get("displayName") or "",
            "posicion": posicion(p),
            "equipo": (t.get("team") or {}).get("displayName"),
        } for p in tit]
    return out if {"home", "away"} <= set(out) else None


def alineacion_partido(liga: str, espn_id: str) -> dict[str, list[dict]] | None:
    """Alineación oficial de un partido por jugarse (caché corta: cambia hasta el inicio)."""
    return alineacion(resumen(liga, espn_id, horas_cache=0.1))


def inicio_temporada_anterior(dia: date, liga: str) -> date:
    """Inicio de la temporada anterior a la de `dia`.

    Ligas de año calendario (MLS, Brasil, Argentina, Libertadores): 1 de enero;
    las demás, 1 de julio (Europa y el Apertura de Liga MX empiezan en julio/agosto).
    """
    if liga in ("mls", "brasil", "argentina", "libertadores"):
        return date(dia.year - 1, 1, 1)
    anio = dia.year if dia.month >= 7 else dia.year - 1
    return date(anio - 1, 7, 1)


def partidos_equipo(hist: pd.DataFrame, equipo: str, antes: pd.Timestamp, desde: date,
                    maximo: int = MAX_PARTIDOS_EQUIPO) -> pd.DataFrame:
    """Partidos del equipo en el historial (con espn_id), entre `desde` y `antes`, los más recientes."""
    if hist.empty or "espn_id" not in hist.columns:
        return hist.iloc[0:0]
    f = pd.to_datetime(hist["fecha"])
    m = ((hist["local"] == equipo) | (hist["visitante"] == equipo)) & (f < antes) & (f >= pd.Timestamp(desde))
    m &= hist["espn_id"].notna()
    return hist[m].sort_values("fecha").tail(maximo)


def actuaciones_partidos(partidos: pd.DataFrame, liga_defecto: str) -> pd.DataFrame:
    """Actuaciones de todos los jugadores en esos partidos (descarga en paralelo, con caché)."""
    def uno(fila):
        liga = fila.get("liga") if isinstance(fila.get("liga"), str) and fila.get("liga") in LIGAS else liga_defecto
        try:
            filas = actuaciones(resumen(liga, str(fila["espn_id"]).split(".")[0]))
        except Exception:  # noqa: BLE001 - un partido sin datos no tumba el resto
            return []
        for x in filas:
            x.update(espn_id=str(fila["espn_id"]), fecha=pd.Timestamp(fila["fecha"]))
        return filas

    filas = [r for _, r in partidos.iterrows()]
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        todas = [x for lote in pool.map(uno, filas) for x in lote]
    return pd.DataFrame(todas)


def actuaciones_equipo(liga: str, hist: pd.DataFrame, equipo: str, antes: pd.Timestamp) -> pd.DataFrame:
    """Actuaciones de los jugadores de `equipo` en sus partidos de esta temporada y la anterior."""
    desde = inicio_temporada_anterior(pd.Timestamp(antes).date(), liga)
    act = actuaciones_partidos(partidos_equipo(hist, equipo, antes, desde), liga)
    if act.empty:
        return act
    return act[act["equipo"] == equipo].reset_index(drop=True)
