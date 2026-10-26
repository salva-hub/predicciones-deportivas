"""Modelos de córners y tarjetas.

Mismo esquema de fuerzas que los goles (a favor / en contra por equipo, local y
visitante), y el total se reparte con una binomial negativa porque córners y
tarjetas varían más de lo que permite una Poisson.

Tarjetas: el esperado se multiplica por el factor del árbitro
(su promedio en la competición / promedio de la competición), "encogido" hacia 1
cuando el árbitro ha pitado pocos partidos.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import nbinom

from .fuerzas import Fuerzas, ajustar_fuerzas, pesos_por_fecha


@dataclass
class ModeloConteo:
    nombre: str
    fuerzas: Fuerzas
    dispersion_k: float  # var = mu + mu^2 / k ; k grande -> Poisson

    def esperados(self, local: str, visitante: str, neutral: bool = False) -> tuple[float, float]:
        return self.fuerzas.esperados(local, visitante, neutral)

    def prob_mas(self, mu_total: float, linea: float) -> float:
        """P(total > linea) para línea media (9.5, 4.5...)."""
        k = self.dispersion_k
        p = k / (k + mu_total)
        return float(nbinom.sf(int(np.floor(linea)), k, p))

    def mercados(self, mu_total: float, lineas) -> dict[str, float]:
        out = {}
        for l in lineas:
            mas = self.prob_mas(mu_total, l)
            out[f"mas_{l}"] = mas
            out[f"menos_{l}"] = 1 - mas
        return out


def estimar_dispersion(totales: np.ndarray, esperados: np.ndarray, pesos: np.ndarray) -> float:
    """Método de momentos: k = mu^2 / (var - mu), con límites razonables."""
    resid2 = (totales - esperados) ** 2
    exceso = np.average(resid2 - esperados, weights=pesos)
    mu2 = np.average(esperados ** 2, weights=pesos)
    if exceso <= 1e-6:
        return 1000.0
    return float(np.clip(mu2 / exceso, 2.0, 1000.0))


def ajustar_modelo_conteo(
    df: pd.DataFrame,
    col_local: str,
    col_visitante: str,
    nombre: str,
    referencia: pd.Timestamp | None = None,
    vida_media_dias: float = 270.0,
    ridge: float = 3.0,
) -> ModeloConteo:
    datos = df.dropna(subset=[col_local, col_visitante]).copy()
    if referencia is None:
        referencia = pd.to_datetime(datos["fecha"]).max()
    fuerzas = ajustar_fuerzas(datos, col_local, col_visitante, referencia, vida_media_dias, ridge)
    esp = np.array([sum(fuerzas.esperados(l, v)) for l, v in zip(datos["local"], datos["visitante"])])
    tot = (datos[col_local] + datos[col_visitante]).to_numpy(dtype=float)
    w = pesos_por_fecha(datos["fecha"], referencia, vida_media_dias)
    k = estimar_dispersion(tot, esp, w)
    return ModeloConteo(nombre=nombre, fuerzas=fuerzas, dispersion_k=k)


def factor_arbitro(prom_arbitro: float | None, partidos_arbitro: int, prom_competicion: float, encogimiento: int = 10) -> float:
    """Factor del árbitro encogido hacia 1: con 10 partidos pesa la mitad."""
    if not prom_arbitro or prom_competicion <= 0 or partidos_arbitro <= 0:
        return 1.0
    crudo = prom_arbitro / prom_competicion
    peso = partidos_arbitro / (partidos_arbitro + encogimiento)
    return float(1 + peso * (crudo - 1))
