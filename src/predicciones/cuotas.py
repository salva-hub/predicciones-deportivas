"""Conversión de cuotas (americana <-> decimal), margen de la casa y valor esperado.

Salva lee las cuotas en formato americano (-110, +200), así que todo lo que se
muestra sale en ese formato. Internamente se trabaja en decimal.
"""
from __future__ import annotations

import math
from typing import Iterable


def americana_a_decimal(americana: float) -> float:
    """+200 -> 3.00 ; -150 -> 1.667"""
    if americana == 0 or -100 < americana < 100:
        raise ValueError(f"Cuota americana inválida: {americana}")
    if americana > 0:
        return 1 + americana / 100
    return 1 + 100 / abs(americana)


def decimal_a_americana(decimal: float) -> int:
    """3.00 -> +200 ; 1.667 -> -150 (redondeado al entero)."""
    if decimal <= 1:
        raise ValueError(f"Cuota decimal inválida: {decimal}")
    if decimal >= 2:
        return int(round((decimal - 1) * 100))
    return int(round(-100 / (decimal - 1)))


def formato_americano(decimal: float, minima: bool = False) -> str:
    """minima=True redondea hacia la cuota que paga más (nunca por debajo del mínimo)."""
    if minima:
        if decimal >= 2:
            a = math.ceil((decimal - 1) * 100 - 1e-9)
        else:
            a = math.ceil(-100 / (decimal - 1) - 1e-9)
            if a > -100:
                a = 100
    else:
        a = decimal_a_americana(decimal)
    return f"+{a}" if a > 0 else f"{a}"


def prob_a_decimal(p: float) -> float:
    """Cuota justa (sin margen) para una probabilidad."""
    if not 0 < p < 1:
        raise ValueError(f"Probabilidad fuera de rango: {p}")
    return 1 / p


def quitar_margen(cuotas_decimales: Iterable[float]) -> list[float]:
    """Probabilidades sin margen de un mercado completo (p. ej. 1X2 o más/menos).

    Usa el método "power" (Clarke et al.), que corrige mejor que el proporcional
    el sesgo hacia los no favoritos.
    """
    cuotas = list(cuotas_decimales)
    implicitas = [1 / c for c in cuotas]
    if abs(sum(implicitas) - 1) < 1e-9:
        return implicitas
    lo, hi = 0.5, 2.5
    for _ in range(100):
        k = (lo + hi) / 2
        s = sum(p ** k for p in implicitas)
        if s > 1:
            lo = k
        else:
            hi = k
    return [p ** k for p in implicitas]


def margen(cuotas_decimales: Iterable[float]) -> float:
    """Margen de la casa: 0.05 = 5%."""
    return sum(1 / c for c in cuotas_decimales) - 1


def valor_esperado(p: float, decimal: float) -> float:
    """Ganancia esperada por peso apostado: 0.10 = +10%."""
    return p * decimal - 1


def cuota_minima(p: float, ve_minimo: float) -> float:
    """Cuota decimal mínima para que la apuesta alcance el valor esperado pedido."""
    return (1 + ve_minimo) / p


def cuota_parlay(decimales: Iterable[float]) -> float:
    return math.prod(decimales)
