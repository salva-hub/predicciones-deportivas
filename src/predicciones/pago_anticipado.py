"""Pago Anticipado de fútbol de Winpot (reglas Altenar v1.29).

Una apuesta prepartido al ganador (1X2) local o visitante se paga completa como
ganada si ese equipo llega a ir 2 goles arriba en cualquier momento del partido,
aunque al final empate o pierda. Aplica a sencillas, múltiples (parlays), apuestas
de sistema y bet builder. No aplica al empate, a apuestas en vivo ni con Cash Out.

Probabilidad: P(gana) + P(va 2 arriba en algún momento y no gana).

El término extra sale de la matriz de marcadores. Con los goles como procesos de
Poisson, dado el marcador final (x, y) el orden de los goles es un arreglo al azar
de x goles del local y y del visitante, todos igual de probables. Por el principio
de reflexión, de los C(x+y, x) órdenes posibles con x ≤ y, los que tocan "local +2"
son C(x+y, x−2). Así el término es exacto y determinista (sin simulación).

Calificación: con la secuencia de goles de ESPN (solo tiempo reglamentario) se
calcula la mayor ventaja que tuvo cada equipo (ventaja_max_local / _visitante).
"""
from __future__ import annotations

from math import comb

import numpy as np

# Multiplicador de calibración del término extra. Validación del 26-oct-2026 con ESPN y
# Pinnacle de cierre (football-data), 5 ligas grandes, 2023/24 y 2024/25, 3,295 partidos:
# pasó 138 veces (un equipo fue +2 y no ganó) contra 101 ± 10 que daba el modelo
# (razón 1.37; por liga de 1.07 a 1.66). El orden al azar de los goles sí cuadra
# (147 contra 132 dado el marcador final): lo que falta es que el modelo de Poisson da
# pocos marcadores altos parejos (2-2, 3-3, 2-3). Se corrige con un multiplicador.
CALIBRACION = 1.35

NOTA = ("Pago anticipado de Winpot: el ganador local/visitante incluye la probabilidad de ir "
        "2 goles arriba y no ganar (local +{l:.1%}, visitante +{v:.1%})")
NOTA_SIN = "Sin pago anticipado en este partido (--sin-pago-anticipado): ganador sin el extra"
SELECCIONES = ("1", "2")


def prob_toca_dos(goles_equipo: int, goles_rival: int) -> float:
    """Fracción de órdenes de los goles en que el equipo va 2 arriba en algún momento,
    para un marcador final en que el equipo NO gana (goles_equipo ≤ goles_rival)."""
    x, y = int(goles_equipo), int(goles_rival)
    if x < 2:
        return 0.0
    if x > y:
        raise ValueError("solo para marcadores en que el equipo no gana")
    return comb(x + y, x - 2) / comb(x + y, x)


def extra(m: np.ndarray, calibracion: float = CALIBRACION) -> tuple[float, float]:
    """(extra_local, extra_visitante): P(ir 2 arriba en algún momento y no ganar)."""
    n = m.shape[0]
    el = ev = 0.0
    for x in range(2, n):
        for y in range(x, n):
            q = prob_toca_dos(x, y)
            el += m[x, y] * q   # local x, visitante y ≥ x
            ev += m[y, x] * q   # visitante x, local y ≥ x
    return float(el * calibracion), float(ev * calibracion)


def aplicar(ganador: dict[str, float], m: np.ndarray, calibracion: float = CALIBRACION) -> dict[str, float]:
    """Ganador con pago anticipado. "1" y "2" ya no suman 1 con "X": un mismo partido puede
    pagar a los dos (uno va 2 arriba y el otro gana al final)."""
    el, ev = extra(m, calibracion)
    out = dict(ganador)
    out["1"] = min(ganador["1"] + el, 0.999)
    out["2"] = min(ganador["2"] + ev, 0.999)
    return out


def ventajas_maximas(secuencia: list[str]) -> tuple[int, int]:
    """Mayor ventaja del local y del visitante a lo largo del partido.

    secuencia: goles en orden, "L" (local) o "V" (visitante)."""
    dif = max_l = max_v = 0
    for g in secuencia:
        dif += 1 if g == "L" else -1
        max_l, max_v = max(max_l, dif), max(max_v, -dif)
    return max_l, max_v
