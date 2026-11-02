"""Respaldo estadístico de cada pronóstico (Salva, 1 y 2-nov-2026): solo para mostrar, no mueve probabilidades.

- Forma de los últimos 5 partidos de cada equipo (G/E/P, puntos, goles a favor y en contra).
- Historial directo: últimos 5 duelos de los últimos 4 años.
- Últimos 10 de cada equipo por selección: cuántas veces se cumplió esa línea (p. ej. "más de 2.5 goles"
  pasó en 7 de los últimos 10 partidos del local y en 6 de los del visitante).

La prueba del 29-oct-2026 (17,447 partidos, 6 ligas, Pinnacle de cierre) mostró que la forma y el historial
directo no mejoran el precio de Pinnacle: por eso se muestran como contexto, sin ajustar nada.
Salen del historial de la liga (datos/historial/<liga>.csv): partidos de otras competencias no cuentan.
"""
from __future__ import annotations

import pandas as pd

from .equipos import resolver
from .modelos.conteos import filas_con_estadisticas

N_FORMA = 5
N_ULTIMOS = 10
N_H2H = 5
ANIOS_H2H = 4


def _terminados(hist: pd.DataFrame) -> pd.DataFrame:
    d = hist.dropna(subset=["goles_local", "goles_visitante"])
    if "terminado" in d.columns:
        d = d[d["terminado"].astype(str).str.lower().isin(("true", "1", "1.0"))]
    return d.sort_values("fecha")


def _de_equipo(d: pd.DataFrame, equipo: str) -> pd.DataFrame:
    return d[(d["local"] == equipo) | (d["visitante"] == equipo)]


def _gf_gc(fila, equipo: str) -> tuple[float, float]:
    if fila["local"] == equipo:
        return float(fila["goles_local"]), float(fila["goles_visitante"])
    return float(fila["goles_visitante"]), float(fila["goles_local"])


def forma(d: pd.DataFrame, equipo: str, n: int = N_FORMA) -> dict | None:
    filas = _de_equipo(d, equipo).tail(n)
    if filas.empty:
        return None
    letras, puntos, gf_t, gc_t = [], 0, 0, 0
    for _, f in filas.iterrows():
        gf, gc = _gf_gc(f, equipo)
        gf_t, gc_t = gf_t + gf, gc_t + gc
        if gf > gc:
            letras.append("G")
            puntos += 3
        elif gf == gc:
            letras.append("E")
            puntos += 1
        else:
            letras.append("P")
    # la más reciente al final
    return {"racha": "".join(letras), "puntos": puntos, "de": 3 * len(letras),
            "goles_favor": int(gf_t), "goles_contra": int(gc_t), "partidos": len(letras)}


def historial_directo(d: pd.DataFrame, local: str, visitante: str, n: int = N_H2H, anios: int = ANIOS_H2H) -> list[dict]:
    fechas = pd.to_datetime(d["fecha"])
    desde = fechas.max() - pd.DateOffset(years=anios) if len(d) else None
    duelos = d[(((d["local"] == local) & (d["visitante"] == visitante)) |
                ((d["local"] == visitante) & (d["visitante"] == local))) & (fechas >= desde)]
    return [{"fecha": str(f["fecha"])[:10], "local": f["local"], "visitante": f["visitante"],
             "marcador": f"{int(f['goles_local'])}-{int(f['goles_visitante'])}"} for _, f in duelos.tail(n).iterrows()]


def _cumple(mercado: str, seleccion: str, i: float, j: float, f, equipo: str) -> bool | None:
    """¿Se cumplió la selección en ese partido? i, j = goles del "local" y del "visitante" de la selección
    vistos desde `equipo` (si el equipo es el visitante del pronóstico, sus goles cuentan como j)."""
    if mercado == "ganador":
        return {"1": i > j, "X": i == j, "2": i < j}.get(seleccion)
    if mercado == "doble_oportunidad":
        return {"1X": i >= j, "X2": i <= j, "12": i != j}.get(seleccion)
    if mercado == "ambos_anotan":
        return {"si": i > 0 and j > 0, "no": not (i > 0 and j > 0)}.get(seleccion)
    lado, _, linea = seleccion.rpartition("_")
    try:
        x = float(linea)
    except ValueError:
        return None
    if mercado == "goles":
        total = i + j
    elif mercado == "goles_equipo":
        eq, _, lado = lado.partition("_")
        total = i if eq == "local" else j
    elif mercado == "handicap":
        dif = (i - j) if lado == "local" else (j - i)
        return dif + x > 0 if dif + x != 0 else None  # línea entera empatada: devolución, no cuenta
    elif mercado in ("corners", "tarjetas"):
        a, b = f.get(f"{mercado}_local"), f.get(f"{mercado}_visitante")
        if pd.isna(a) or pd.isna(b):
            return None
        total = float(a) + float(b)
    else:
        return None
    if lado == "mas":
        return total > x
    if lado == "menos":
        return total < x
    return None


MERCADOS = ("ganador", "doble_oportunidad", "ambos_anotan", "goles", "goles_equipo", "handicap", "corners", "tarjetas")


def ultimos(d: pd.DataFrame, d_stats: pd.DataFrame, equipo: str, como: str, mercado: str, seleccion: str,
            n: int = N_ULTIMOS) -> str | None:
    """'7/10': veces que la selección se cumplió en los últimos n partidos de `equipo` (como local o
    visitante del pronóstico, `como`). Córners y tarjetas solo con partidos que traen estadísticas."""
    if mercado not in MERCADOS:
        return None
    base = d_stats if mercado in ("corners", "tarjetas") else d
    si = total = 0
    for _, f in _de_equipo(base, equipo).tail(n).iterrows():
        gf, gc = _gf_gc(f, equipo)
        i, j = (gf, gc) if como == "local" else (gc, gf)
        r = _cumple(mercado, seleccion, i, j, f, equipo)
        if r is None:
            continue
        total += 1
        si += bool(r)
    return f"{si}/{total}" if total else None


def respaldo(hist: pd.DataFrame, local: str, visitante: str, selecciones: list[dict]) -> dict:
    """Agrega `ultimos10` a cada selección (en el lugar) y devuelve la forma y el historial directo."""
    d = _terminados(hist)
    conocidos = sorted(set(d["local"]) | set(d["visitante"]))
    loc = resolver(local, conocidos) if conocidos else None
    vis = resolver(visitante, conocidos) if conocidos else None
    if not loc or not vis:
        return {"nota": "sin historial de uno de los equipos en esta liga"}
    d_stats = filas_con_estadisticas(d)
    for s in selecciones:
        par = {"local": ultimos(d, d_stats, loc, "local", s["mercado"], s["seleccion"]),
               "visitante": ultimos(d, d_stats, vis, "visitante", s["mercado"], s["seleccion"])}
        if par["local"] or par["visitante"]:
            s["ultimos10"] = par
    return {"forma_local": forma(d, loc), "forma_visitante": forma(d, vis),
            "historial_directo": historial_directo(d, loc, vis)}
