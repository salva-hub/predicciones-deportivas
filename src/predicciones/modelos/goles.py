"""Modelo de goles Dixon-Coles y todos los mercados que salen de él.

De la matriz de marcadores se obtienen, de forma coherente entre sí:
ganador (1X2), doble oportunidad, total de goles, ambos anotan, goles por
equipo, hándicap asiático y total del primer tiempo (aproximado).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import least_squares, minimize_scalar
from scipy.stats import poisson

from .fuerzas import Fuerzas, ajustar_fuerzas, pesos_por_fecha

MAX_GOLES = 10
FRACCION_PRIMER_TIEMPO = 0.45  # ~45% de los goles caen en el primer tiempo


def tau(x: int, y: int, mu_l: float, mu_v: float, rho: float) -> float:
    """Corrección de Dixon-Coles para marcadores bajos."""
    if x == 0 and y == 0:
        return 1 - mu_l * mu_v * rho
    if x == 0 and y == 1:
        return 1 + mu_l * rho
    if x == 1 and y == 0:
        return 1 + mu_v * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def matriz_marcadores(mu_l: float, mu_v: float, rho: float = 0.0, max_goles: int = MAX_GOLES) -> np.ndarray:
    g = np.arange(max_goles + 1)
    m = np.outer(poisson.pmf(g, mu_l), poisson.pmf(g, mu_v))
    for x in (0, 1):
        for y in (0, 1):
            m[x, y] *= max(tau(x, y, mu_l, mu_v, rho), 1e-9)
    return m / m.sum()


@dataclass
class ModeloGoles:
    fuerzas: Fuerzas
    rho: float

    def esperados(self, local: str, visitante: str, neutral: bool = False) -> tuple[float, float]:
        return self.fuerzas.esperados(local, visitante, neutral)

    def matriz(self, local: str, visitante: str, mult_local: float = 1.0, mult_visitante: float = 1.0) -> np.ndarray:
        mu_l, mu_v = self.esperados(local, visitante)
        return matriz_marcadores(mu_l * mult_local, mu_v * mult_visitante, self.rho)


def ajustar_rho(df: pd.DataFrame, fuerzas: Fuerzas, referencia: pd.Timestamp, vida_media_dias: float) -> float:
    datos = df.dropna(subset=["goles_local", "goles_visitante"])
    w = pesos_por_fecha(datos["fecha"], referencia, vida_media_dias)
    mus = [fuerzas.esperados(l, v) for l, v in zip(datos["local"], datos["visitante"])]
    xs = datos["goles_local"].astype(int).to_numpy()
    ys = datos["goles_visitante"].astype(int).to_numpy()

    def nll(rho: float) -> float:
        total = 0.0
        for (ml, mv), x, y, wi in zip(mus, xs, ys, w):
            if x <= 1 and y <= 1:
                t = tau(x, y, ml, mv, rho)
                if t <= 0:
                    return 1e12
                total -= wi * math.log(t)
        return total

    res = minimize_scalar(nll, bounds=(-0.2, 0.2), method="bounded")
    return float(res.x)


def ajustar_modelo_goles(
    df: pd.DataFrame,
    referencia: pd.Timestamp | None = None,
    vida_media_dias: float = 180.0,
    peso_xg: float = 0.5,
    ridge: float = 2.0,
) -> ModeloGoles:
    """df: fecha, local, visitante, goles_local, goles_visitante y opcional xg_local, xg_visitante.

    Si hay xG, el objetivo mezcla goles y xG (peso_xg) porque el xG predice mejor
    el futuro que los goles solos.
    """
    datos = df.copy()
    if referencia is None:
        referencia = pd.to_datetime(datos["fecha"]).max()
    if {"xg_local", "xg_visitante"} <= set(datos.columns) and peso_xg > 0:
        xl = datos["xg_local"].fillna(datos["goles_local"])
        xv = datos["xg_visitante"].fillna(datos["goles_visitante"])
        datos["obj_l"] = (1 - peso_xg) * datos["goles_local"] + peso_xg * xl
        datos["obj_v"] = (1 - peso_xg) * datos["goles_visitante"] + peso_xg * xv
    else:
        datos["obj_l"] = datos["goles_local"]
        datos["obj_v"] = datos["goles_visitante"]
    fuerzas = ajustar_fuerzas(datos, "obj_l", "obj_v", referencia, vida_media_dias, ridge)
    rho = ajustar_rho(datos, fuerzas, referencia, vida_media_dias)
    return ModeloGoles(fuerzas=fuerzas, rho=rho)


# ---------------------------------------------------------------- anclaje al mercado

TIRON_MODELO = 0.02        # tirón débil (escala log) hacia el reparto y la rho del modelo
TIRON_TOTAL_SIN_LINEA = 3.0  # sin más/menos 2.5 el total casi no se mueve del modelo
LIMITE_RHO = 0.3
ERROR_MAXIMO = 0.01        # error de ajuste tolerado en probabilidad


@dataclass
class Anclaje:
    mu_l: float
    mu_v: float
    rho: float
    error: float = 0.0          # mayor diferencia (en probabilidad) contra Pinnacle
    rho_en_limite: bool = False  # cuotas contradictorias: rho topó con su límite
    con_linea: bool = True       # hubo más/menos 2.5 de Pinnacle

    @property
    def impreciso(self) -> bool:
        """Sin línea de goles el 1X2 no cuadra exacto a propósito (el total queda
        cerca del modelo); eso ya baja la confianza por otro lado."""
        return (self.con_linea and self.error > ERROR_MAXIMO) or self.rho_en_limite


def anclar_a_mercado(
    mu_modelo: tuple[float, float],
    rho: float,
    p_1x2: tuple[float, float, float] | None = None,
    p_mas_25: float | None = None,
) -> Anclaje:
    """Goles esperados (local, visitante) y rho que reproducen las probabilidades de Pinnacle.

    Mínimos cuadrados en escala log (error relativo, para que un 1.5% de un
    no favorito pese como debe) sobre el 1X2 y el más/menos 2.5 sin margen.
    Con los dos mercados también se ajusta rho, porque con solo dos mu el empate
    no siempre cuadra. Sin más/menos 2.5 el total de goles se queda casi en el
    del modelo (el 1X2 de un gran favorito no dice cuántos goles habrá); sin 1X2,
    el reparto es el del modelo. Devuelve también el error del ajuste.
    """
    ml, mv = max(mu_modelo[0], 0.05), max(mu_modelo[1], 0.05)
    if p_1x2 is None and p_mas_25 is None:
        return Anclaje(ml, mv, rho)
    log_tot, log_rep = np.log(ml + mv), np.log(ml / mv)
    libre_rho = p_1x2 is not None and p_mas_25 is not None
    tiron_total = TIRON_MODELO if p_mas_25 is not None else TIRON_TOTAL_SIN_LINEA
    objetivo = list(p_1x2 or []) + ([p_mas_25, 1 - p_mas_25] if p_mas_25 is not None else [])
    log_obj = np.log(np.clip(objetivo, 1e-6, None))
    g = np.arange(MAX_GOLES + 1)
    total = g[:, None] + g[None, :]

    def probs(x):
        a, b = np.exp(x[:2])
        m = matriz_marcadores(a, b, x[2] if libre_rho else rho)
        q = []
        if p_1x2 is not None:
            q += [np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()]
        if p_mas_25 is not None:
            mas = m[total > 2.5].sum()
            q += [mas, 1 - mas]
        return np.array(q)

    def residuos(x):
        a, b = np.exp(x[:2])
        r = list(np.log(np.clip(probs(x), 1e-9, None)) - log_obj)
        r += [tiron_total * (np.log(a + b) - log_tot), TIRON_MODELO * (np.log(a / b) - log_rep)]
        if libre_rho:
            r.append(TIRON_MODELO * (x[2] - rho))
        return np.array(r)

    x0 = [np.log(ml), np.log(mv)] + ([float(np.clip(rho, -LIMITE_RHO, LIMITE_RHO))] if libre_rho else [])
    lo = [np.log(0.05)] * 2 + ([-LIMITE_RHO] if libre_rho else [])
    hi = [np.log(8.0)] * 2 + ([LIMITE_RHO] if libre_rho else [])
    res = least_squares(residuos, x0, bounds=(lo, hi))
    a, b = np.exp(res.x[:2])
    r_fin = float(res.x[2]) if libre_rho else rho
    error = float(np.max(np.abs(probs(res.x) - np.array(objetivo))))
    en_limite = libre_rho and abs(r_fin) > LIMITE_RHO - 1e-3
    return Anclaje(float(a), float(b), r_fin, error, en_limite, con_linea=p_mas_25 is not None)


def mezclar_goles(modelo: tuple[float, float], mercado: tuple[float, float], peso_modelo: float) -> tuple[float, float]:
    """Mezcla en escala log: mu = exp(w·log(modelo) + (1 − w)·log(mercado))."""
    w = min(max(peso_modelo, 0.0), 1.0)
    return tuple(float(np.exp(w * np.log(x) + (1 - w) * np.log(y))) for x, y in zip(modelo, mercado))


# ---------------------------------------------------------------- mercados

def resultado_handicap(m: np.ndarray, linea: float, para_local: bool = True) -> tuple[float, float, float]:
    """(gana, empate_devuelve, pierde) para hándicap asiático de línea entera o media.

    linea es el hándicap aplicado al equipo elegido (p. ej. -1.5, -1, -0.5, 0, +0.5).
    """
    n = m.shape[0]
    gana = devuelve = pierde = 0.0
    for x in range(n):
        for y in range(n):
            dif = (x - y) if para_local else (y - x)
            ajustado = dif + linea
            if abs(ajustado) < 1e-9:
                devuelve += m[x, y]
            elif ajustado > 0:
                gana += m[x, y]
            else:
                pierde += m[x, y]
    return gana, devuelve, pierde


def handicap_asiatico(m: np.ndarray, linea: float, para_local: bool = True) -> dict:
    """Soporta líneas de cuarto (-0.25, -0.75...) como media apuesta en cada línea vecina."""
    frac = abs(linea * 4) % 2
    if abs(frac - 1) < 1e-9:  # línea de cuarto
        a = resultado_handicap(m, linea - 0.25, para_local)
        b = resultado_handicap(m, linea + 0.25, para_local)
        return {"partes": [a, b]}
    return {"partes": [resultado_handicap(m, linea, para_local)]}


def mercados_de_goles(m: np.ndarray, lineas_total=(0.5, 1.5, 2.5, 3.5, 4.5), lineas_equipo=(0.5, 1.5, 2.5)) -> dict[str, float]:
    n = m.shape[0]
    x = np.arange(n)[:, None]
    y = np.arange(n)[None, :]
    total = x + y
    out: dict[str, float] = {}
    p1 = float(m[x > y].sum())
    px = float(np.trace(m))
    p2 = float(m[x < y].sum())
    out.update({"1": p1, "X": px, "2": p2, "1X": p1 + px, "12": p1 + p2, "X2": px + p2})
    for l in lineas_total:
        mas = float(m[total > l].sum())
        out[f"mas_{l}"] = mas
        out[f"menos_{l}"] = 1 - mas
    out["ambos_si"] = float(m[1:, 1:].sum())
    out["ambos_no"] = 1 - out["ambos_si"]
    gl = m.sum(axis=1)
    gv = m.sum(axis=0)
    for l in lineas_equipo:
        out[f"local_mas_{l}"] = float(gl[np.arange(n) > l].sum())
        out[f"local_menos_{l}"] = 1 - out[f"local_mas_{l}"]
        out[f"visitante_mas_{l}"] = float(gv[np.arange(n) > l].sum())
        out[f"visitante_menos_{l}"] = 1 - out[f"visitante_mas_{l}"]
    return out


def mercados_primer_tiempo(mu_l: float, mu_v: float, lineas=(0.5, 1.5)) -> dict[str, float]:
    """Aproximación: goles del primer tiempo como Poisson con ~45% de los esperados."""
    m = matriz_marcadores(mu_l * FRACCION_PRIMER_TIEMPO, mu_v * FRACCION_PRIMER_TIEMPO, 0.0)
    base = mercados_de_goles(m, lineas_total=lineas, lineas_equipo=())
    return {f"pt_{k}": v for k, v in base.items() if k in {"1", "X", "2"} or k.startswith(("mas_", "menos_"))}


def marcador_mas_probable(m: np.ndarray, top: int = 3) -> list[tuple[str, float]]:
    planos = [((x, y), m[x, y]) for x in range(m.shape[0]) for y in range(m.shape[1])]
    planos.sort(key=lambda t: -t[1])
    return [(f"{x}-{y}", float(p)) for (x, y), p in planos[:top]]
