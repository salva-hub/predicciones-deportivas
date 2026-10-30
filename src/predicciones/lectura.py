"""Lectura del partido (aprobada por Salva el 30-oct-2026; solo en papel).

Cuarta línea de la prueba en papel, aparte del sistema, de la elección de Salva y del pick del modelo.
Imita la forma de elegir de Salva con reglas fijas para poder medirla:

1. Antes de ver las cuotas de Winpot, Claude escribe la lectura del partido (forma de los últimos 5,
   historial directo, estilo, bajas, motivación) y elige las patas (`cli lectura`).
2. Pata principal: ganador del favorito (con pago anticipado) o la doble oportunidad del favorito
   (X + favorito). La doble oportunidad solo entra si Winpot la paga dentro de 4% de su precio justo.
3. Patas extra (goles, ambos anotan, goles por equipo, hándicap, córners, tarjetas): solo con
   probabilidad de 60% o más.
4. Una pata sola va como sencilla si Winpot paga −200 o mejor. Si no, combinada de 2 o 3 patas con
   cuota total de +200 a +350. Varias patas del mismo partido ("crea tu apuesta") solo si el momio
   total de Winpot paga al menos 90% del precio justo de la probabilidad conjunta.
5. Montos del sistema sobre su propio bank en papel (`registro.bank_papel("lectura")`).

Por qué así (historial de 6,166 partidos, Pinnacle de cierre, ver el documento de la propuesta):
el ganador del favorito con pago anticipado sale entre 0% y +3%; la doble oportunidad pierde ~6%
porque Winpot le cobra ~9% de margen; las combinadas suman el margen de cada pata.
"""
from __future__ import annotations

import math

import numpy as np

from . import bank as B
from . import cuotas as C
from . import valor as V
from .modelos.goles import matriz_marcadores

LECTURA = "lectura"
PROB_MIN_EXTRA = 0.60
PROB_MIN_FAVORITO = 0.40       # sin pago anticipado: con menos no hay favorito claro
CUOTA_MIN_SENCILLA = 1.5       # −200
TOLERANCIA_DO = V.TOLERANCIA_ATRACTIVA   # doble oportunidad: Winpot ≥ justa × 0.96
MIN_PAGO_MISMO_PARTIDO = 0.90  # "crea tu apuesta": Winpot ≥ 90% del precio justo conjunto
MERCADOS_PRINCIPALES = ("ganador", "doble_oportunidad")
MERCADOS_EXTRA = ("goles", "ambos_anotan", "goles_equipo", "handicap", "corners", "tarjetas")
MERCADOS_GOLES = ("ganador", "doble_oportunidad", "goles", "ambos_anotan", "goles_equipo", "handicap")
PORCENTAJE_BAJA = 0.02         # confianza baja: 2% (el sistema no apostaría; aquí se mide igual)


class ReglaLectura(ValueError):
    """La selección no cumple las reglas de la lectura del partido."""


def _sel(p: dict, mercado: str, seleccion: str) -> dict | None:
    return next((s for s in p.get("selecciones", []) if s["mercado"] == mercado and s["seleccion"] == seleccion), None)


def probs_1x2(p: dict) -> tuple[float, float, float]:
    """1X2 sin pago anticipado (suma 1)."""
    out = []
    for k in ("1", "X", "2"):
        s = _sel(p, "ganador", k)
        if not s:
            raise ReglaLectura(f"{p.get('partido')}: el pronóstico no tiene el ganador {k}")
        out.append(s.get("prob_sin_pago_anticipado", s["prob"]))
    return tuple(out)


def favorito(p: dict) -> str:
    p1, _, p2 = probs_1x2(p)
    return "1" if p1 >= p2 else "2"


def prob_pata(p: dict, mercado: str, seleccion: str) -> tuple[float, str]:
    """(probabilidad, confianza) de una pata. La doble oportunidad se arma con el 1X2 si el pronóstico no la trae."""
    s = _sel(p, mercado, seleccion)
    if s:
        if s.get("devolucion"):
            raise ReglaLectura(f"{mercado} {seleccion}: línea con devolución, no se usa en la lectura")
        return s["prob"], s["confianza"]
    if mercado == "doble_oportunidad" and seleccion in ("1X", "X2", "12"):
        p1, px, p2 = probs_1x2(p)
        prob = {"1X": p1 + px, "X2": px + p2, "12": p1 + p2}[seleccion]
        conf = _sel(p, "ganador", "1")["confianza"]
        return prob, conf
    raise ReglaLectura(f"{p.get('partido')}: {mercado} {seleccion} no está en el pronóstico")


def validar_eleccion(p: dict, patas: list[tuple[str, str]]) -> None:
    """Reglas de la elección (antes de ver Winpot): 1 pata principal como mucho, del favorito;
    extras con 60% o más; nada de mercados fuera de la lista."""
    if not patas:
        raise ReglaLectura("hace falta al menos una pata")
    principales = [x for x in patas if x[0] in MERCADOS_PRINCIPALES]
    if len(principales) > 1:
        raise ReglaLectura("solo una pata principal (ganador o doble oportunidad) por partido")
    if len(set(patas)) != len(patas):
        raise ReglaLectura("hay patas repetidas")
    fav = favorito(p)
    for mercado, sel in patas:
        if mercado not in MERCADOS_PRINCIPALES + MERCADOS_EXTRA:
            raise ReglaLectura(f"{mercado}: mercado fuera de la lectura del partido")
        prob, _ = prob_pata(p, mercado, sel)
        if mercado == "ganador":
            p1, _, p2 = probs_1x2(p)
            if sel != fav or max(p1, p2) < PROB_MIN_FAVORITO:
                raise ReglaLectura(f"ganador {sel}: solo el ganador del favorito (Pinnacle {PROB_MIN_FAVORITO:.0%} o más)")
        elif mercado == "doble_oportunidad":
            if sel != ("1X" if fav == "1" else "X2"):
                raise ReglaLectura(f"doble oportunidad {sel}: solo empate o gana el favorito")
        elif prob < PROB_MIN_EXTRA:
            raise ReglaLectura(f"{mercado} {sel}: probabilidad {prob:.0%}, menos de {PROB_MIN_EXTRA:.0%}")


def _condicion(m: np.ndarray, mercado: str, sel: str) -> np.ndarray:
    i, j = np.indices(m.shape)
    if mercado == "ganador":
        return {"1": i > j, "X": i == j, "2": i < j}[sel]
    if mercado == "doble_oportunidad":
        return {"1X": i >= j, "X2": i <= j, "12": i != j}[sel]
    if mercado == "goles":
        lado, linea = sel.split("_")
        return (i + j > float(linea)) if lado == "mas" else (i + j < float(linea))
    if mercado == "ambos_anotan":
        ambos = (i > 0) & (j > 0)
        return ambos if sel == "si" else ~ambos
    if mercado == "goles_equipo":
        equipo, lado, linea = sel.split("_")
        g = i if equipo == "local" else j
        return (g > float(linea)) if lado == "mas" else (g < float(linea))
    if mercado == "handicap":
        equipo, linea = sel.split("_")
        dif = (i - j) if equipo == "local" else (j - i)
        return dif + float(linea) > 0
    raise KeyError(mercado)


def prob_conjunta(p: dict, patas: list[tuple[str, str]]) -> float:
    """Probabilidad de que salgan todas las patas de un mismo partido.

    - Una sola pata: la del pronóstico (el ganador, con pago anticipado).
    - Varias patas de goles: se combinan en la matriz de marcadores de los goles esperados del
      pronóstico, y el resultado se reescala con el promedio de (probabilidad del pronóstico /
      probabilidad en la matriz) de cada pata, sin pasar de la pata menos probable. Es una
      aproximación: la matriz no lleva la corrección de marcadores bajos y el ganador va sin pago
      anticipado (queda del lado conservador).
    - Córners y tarjetas se toman como independientes de los goles y entre sí.
    """
    if len(patas) == 1:
        return prob_pata(p, *patas[0])[0]
    goles = [x for x in patas if x[0] in MERCADOS_GOLES]
    otros = [x for x in patas if x[0] not in MERCADOS_GOLES]
    prob = 1.0
    if len(goles) == 1:
        prob = prob_pata(p, *goles[0])[0]
    elif goles:
        mu_l, mu_v = p["goles_esperados"]
        m = matriz_marcadores(float(mu_l), float(mu_v))
        m = m / m.sum()
        mask = np.ones(m.shape, dtype=bool)
        razones, probs = [], []
        for mercado, sel in goles:
            c = _condicion(m, mercado, sel)
            mask &= c
            p_sel = probs_1x2(p)[("1", "X", "2").index(sel)] if mercado == "ganador" else prob_pata(p, mercado, sel)[0]
            marginal = float(m[c].sum())
            razones.append(p_sel / marginal if marginal > 0 else 1.0)
            probs.append(p_sel)
        prob = min(float(m[mask].sum()) * sum(razones) / len(razones), min(probs))
    for mercado, sel in otros:
        prob *= prob_pata(p, mercado, sel)[0]
    return prob


def confianza_minima(confianzas: list[str]) -> str:
    return min(confianzas, key=V.ORDEN_CONFIANZA.index)


def monto(bank: float, tipo: str, cuota: float, prob: float, confianza: str, mercado: str | None = None) -> float:
    """Montos del sistema (Salva, 30-oct-2026) sobre el bank en papel de la lectura.

    - Sencilla con momio negativo (−200 a −101): lo necesario para ganar 5% (alta) o 2.5% (media).
    - Sencilla con momio positivo: 5% con alta y valor de al menos el doble del mínimo; 3% alta o media.
    - Combinada: 5% si todas las patas son de confianza alta, 3% si alguna es media.
    - Confianza baja: 2% (el sistema no apostaría; aquí se mide igual).
    """
    if confianza == "baja":
        return B.monto(bank, PORCENTAJE_BAJA)
    if tipo == "sencilla":
        if cuota < 2.0:
            return B.monto_sencilla_fuerte(bank, confianza, cuota)
        ve = prob * cuota - 1
        ve_min = V.VE_MIN[V.familia(mercado or "goles")]
        pct = B.REGLAS.sencilla_max if confianza == "alta" and ve >= 2 * ve_min else B.REGLAS.sencilla_media
        return B.monto(bank, pct)
    return B.monto(bank, B.porcentaje_combinada(confianza))


def revisar_precio(tipo: str, patas: list[dict], cuota_total: float, prob_total: float,
                   mismo_partido: bool) -> None:
    """Reglas de precio (con las cuotas de Winpot). `patas` trae mercado, seleccion, prob y cuota."""
    for q in patas:
        if q["mercado"] == "doble_oportunidad" and q.get("cuota"):
            justa = 1 / q["prob"]
            if q["cuota"] < justa * (1 - TOLERANCIA_DO):
                raise ReglaLectura(
                    f"doble oportunidad a {C.formato_americano(q['cuota'])}: Winpot paga más de 4% por debajo "
                    f"del precio justo ({C.formato_americano(justa)})")
    if tipo == "sencilla":
        if cuota_total < CUOTA_MIN_SENCILLA - 1e-9:
            raise ReglaLectura(f"sencilla a {C.formato_americano(cuota_total)}: peor que −200, va en combinada")
        return
    if not (V.COMBINADA_CUOTA_MIN - 1e-9 <= cuota_total <= V.COMBINADA_CUOTA_MAX + 1e-9):
        raise ReglaLectura(f"combinada a {C.formato_americano(cuota_total)}: fuera de +200 a +350")
    if mismo_partido:
        justa = 1 / prob_total
        if cuota_total < justa * MIN_PAGO_MISMO_PARTIDO:
            raise ReglaLectura(
                f"crea tu apuesta a {C.formato_americano(cuota_total)}: Winpot paga menos de 90% del precio justo "
                f"conjunto ({C.formato_americano(justa)})")


def redondear_prob(x: float) -> float:
    return round(x, 4) if math.isfinite(x) else x
