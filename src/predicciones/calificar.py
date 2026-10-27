"""Califica selecciones con el resultado final del partido."""
from __future__ import annotations

from .datos import tarjetas_winpot
from .equipos import normalizar

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


AMBIGUO = object()


def _jugador(jugadores: dict, nombre: str, jugador_id: str | None):
    """Primero por id de ESPN; si no hay id (o no está), por nombre. Dos con el mismo nombre: AMBIGUO."""
    if jugador_id and str(jugador_id) in jugadores:
        return jugadores[str(jugador_id)]
    n = normalizar(nombre)
    hallados = [v for v in jugadores.values() if normalizar(v.get("nombre") or "") == n]
    if len(hallados) > 1:
        return AMBIGUO
    return hallados[0] if hallados else None


def tiros_jugador(seleccion: str, r: dict, jugador_id: str | None = None) -> str:
    """'Nombre Jugador:mas_0.5' con r["tiros_jugadores"] = {id: {nombre, participo, tiros_puerta}}.

    Reglas de Winpot: jugador que no participa (o que no está en el acta) = devuelta.
    ESPN da los tiros del partido completo: con prórroga queda sin datos (se liquida a mano).
    Nombre repetido sin id que lo distinga: sin datos (a mano).
    """
    nombre, linea = seleccion.rsplit(":", 1)
    jugadores = r.get("tiros_jugadores")
    if not jugadores or r.get("prorroga"):
        return SIN_DATOS
    j = _jugador(jugadores, nombre, jugador_id)
    if j is AMBIGUO:
        return SIN_DATOS
    if j is None or not j.get("participo"):
        return DEVUELVE
    return _comparar_linea(j.get("tiros_puerta"), linea)


def _pago_anticipado(seleccion: str, gl: int, gv: int, r: dict) -> str:
    """Ganador 1/2 que perdería: gana si su equipo fue 2 goles arriba en algún momento
    (tiempo reglamentario). Sin la secuencia de goles: SIN_DATOS (revisión a mano)."""
    lado = "local" if seleccion == "1" else "visitante"
    if (gl if lado == "local" else gv) < 2:
        return PIERDE  # con menos de 2 goles nunca fue 2 arriba
    ventaja = r.get(f"ventaja_max_{lado}")
    if ventaja is None:
        return SIN_DATOS
    return GANA if ventaja >= 2 else PIERDE


def calificar(mercado: str, seleccion: str, r: dict, pago_anticipado: bool = False,
              jugador_id: str | None = None) -> str:
    """r: goles_local, goles_visitante, goles_*_pt, corners_*, tarjetas_* (o amarillas_*, rojas_*,
    segundas_amarillas_*); para el pago anticipado, ventaja_max_local / ventaja_max_visitante; y para
    tiros de jugador, tiros_jugadores (jugador_id: id de ESPN del jugador).

    pago_anticipado: Winpot paga el ganador "1" o "2" si ese equipo fue 2 goles arriba
    en algún momento, aunque no gane. No aplica al empate ni a otros mercados."""
    if mercado == "tiros_jugador":
        return tiros_jugador(seleccion, r, jugador_id)
    gl, gv = r.get("goles_local"), r.get("goles_visitante")
    if gl is None or gv is None:
        return SIN_DATOS
    if mercado == "ganador":
        real = "1" if gl > gv else "2" if gv > gl else "X"
        if seleccion == real:
            return GANA
        if pago_anticipado and seleccion in ("1", "2"):
            return _pago_anticipado(seleccion, gl, gv, r)
        return PIERDE
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
    if mercado == "tarjetas_1x2":
        if r.get("prorroga"):
            return SIN_DATOS  # ESPN mezcla las de la prórroga: se liquida a mano
        a, b = (_tarjetas(r, l) for l in ("local", "visitante"))
        if a is None or b is None:
            return SIN_DATOS
        real = "1" if a > b else "2" if b > a else "X"
        return GANA if seleccion == real else PIERDE
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
