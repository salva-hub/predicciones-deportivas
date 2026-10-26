"""Califica selecciones con el resultado final del partido."""
from __future__ import annotations

from .datos import tarjetas_winpot

GANA, PIERDE, DEVUELVE, SIN_DATOS = "ganada", "perdida", "devuelta", "sin_datos"


def _comparar_linea(valor: float | None, seleccion: str) -> str:
    """seleccion tipo 'mas_2.5' o 'menos_9.5'."""
    if valor is None:
        return SIN_DATOS
    lado, linea = seleccion.rsplit("_", 1)
    linea = float(linea)
    if valor == linea:
        return DEVUELVE
    gana = valor > linea if lado.endswith("mas") else valor < linea
    return GANA if gana else PIERDE


def _tarjetas(r: dict, lado: str) -> float | None:
    """Tarjetas de un lado como Winpot (amarilla 1, roja 2, doble amarilla 3).

    Con amarillas y rojas se recalcula; si solo viene tarjetas_<lado> se usa tal cual.
    """
    if r.get(f"amarillas_{lado}") is not None:
        return tarjetas_winpot(r[f"amarillas_{lado}"], r.get(f"rojas_{lado}"),
                               r.get(f"segundas_amarillas_{lado}"))
    return r.get(f"tarjetas_{lado}")


def calificar(mercado: str, seleccion: str, r: dict) -> str:
    """r: goles_local, goles_visitante, goles_*_pt, corners_*, tarjetas_* (o amarillas_*, rojas_*,
    segundas_amarillas_*)."""
    gl, gv = r.get("goles_local"), r.get("goles_visitante")
    if gl is None or gv is None:
        return SIN_DATOS
    if mercado == "ganador":
        real = "1" if gl > gv else "2" if gv > gl else "X"
        return GANA if seleccion == real else PIERDE
    if mercado == "doble_oportunidad":
        real = "1" if gl > gv else "2" if gv > gl else "X"
        return GANA if real in seleccion else PIERDE
    if mercado == "goles":
        return _comparar_linea(gl + gv, seleccion)
    if mercado == "ambos_anotan":
        ambos = gl > 0 and gv > 0
        return GANA if ambos == (seleccion == "si") else PIERDE
    if mercado == "goles_equipo":
        lado, resto = seleccion.split("_", 1)
        return _comparar_linea(gl if lado == "local" else gv, resto)
    if mercado == "primer_tiempo":
        pl, pv = r.get("goles_local_pt"), r.get("goles_visitante_pt")
        if pl is None or pv is None:
            return SIN_DATOS
        sel = seleccion.removeprefix("pt_")
        if sel in ("1", "X", "2"):
            real = "1" if pl > pv else "2" if pv > pl else "X"
            return GANA if sel == real else PIERDE
        return _comparar_linea(pl + pv, sel)
    if mercado == "handicap":
        lado, linea = seleccion.split("_", 1)
        dif = (gl - gv) if lado == "local" else (gv - gl)
        ajustado = dif + float(linea)
        if ajustado == 0:
            return DEVUELVE
        return GANA if ajustado > 0 else PIERDE
    if mercado in ("corners", "tarjetas"):
        a, b = (_tarjetas(r, l) for l in ("local", "visitante")) if mercado == "tarjetas" else (
            r.get("corners_local"), r.get("corners_visitante"))
        if a is None or b is None:
            return SIN_DATOS
        return _comparar_linea(a + b, seleccion)
    return SIN_DATOS


def ganancia(estado: str, monto: float, cuota_decimal: float) -> float:
    if estado == GANA:
        return round(monto * (cuota_decimal - 1), 2)
    if estado == PIERDE:
        return -monto
    return 0.0


def estado_parlay(estados: list[str]) -> str:
    """Una pata perdida pierde el parlay aunque las demás estén pendientes o sin datos."""
    if PIERDE in estados:
        return PIERDE
    if SIN_DATOS in estados or "pendiente" in estados:
        return SIN_DATOS
    if all(e == DEVUELVE for e in estados):
        return DEVUELVE
    return GANA  # las patas devueltas cuentan como cuota 1.0
