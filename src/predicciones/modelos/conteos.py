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

# Recalibración (29-oct-2026). Antes se encogía cada probabilidad hacia 50%
# (p' = 0.5 + 0.7·(p − 0.5)), pero eso inflaba el lado poco probable: "menos de 7.5
# córners" en Liga MX salía 39% y pasa 25.8%. Ahora se acerca a lo que pasa en esa
# línea en la liga (último año): logit p' = c + a·logit p + b·logit(frecuencia de la liga).
# Walk-forward de 6 ligas (`python -m predicciones.backtest_conteos`): con datos limpios
# y esta recalibración queda calibrado de 10% a 90%; ver el docstring de ese módulo.
# Medido desde jul-2024 a oct-2026 (4,641 partidos), con (c, a, b) ajustado mes a mes:
# - córners: log-loss 0.6502 antes -> 0.6370 (solo la frecuencia de la liga 0.6414);
#   "menos de 7.5" en Liga MX: antes 39.9%, ahora 26.4%, real 25.6%.
# - tarjetas (con el factor del árbitro, como en producción): log-loss 0.5685 antes -> 0.5461
#   (solo la frecuencia de la liga 0.5609);
#   antes el lado de 25% pasaba 16% y el de 75%, 84%.
# Los valores son el "recalibracion_con_todo" del backtest: (c, a, b).
RECAL_CONTEOS = {
    "corners": (-0.003, 0.422, 0.524),
    "tarjetas": (-0.088, 0.662, 0.343),
}
DIAS_FRECUENCIA_LIGA = 365
MIN_PARTIDOS_FRECUENCIA = 50
# Sin frecuencia de la liga (pocos partidos) se encoge hacia 50% como antes.
ENCOGER_CONTEOS = 0.7


def encoger(p: float, factor: float = ENCOGER_CONTEOS) -> float:
    return 0.5 + factor * (p - 0.5)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return float(np.log(p / (1 - p)))


def recalibrar_conteo(p: float, frecuencia_liga: float, params: tuple[float, float, float]) -> float:
    c, a, b = params
    z = c + a * _logit(p) + b * _logit(min(max(frecuencia_liga, 0.01), 0.99))
    return float(1 / (1 + np.exp(-z)))


def filas_con_estadisticas(df: pd.DataFrame) -> pd.DataFrame:
    """Quita los partidos que ESPN publicó sin estadísticas.

    El historial los guarda con 0 en tiros, córners y tarjetas (un partido real nunca
    tiene 0 tiros); contarlos como 0 córners / 0 tarjetas bajaba el promedio.
    """
    if {"tiros_local", "tiros_visitante"} <= set(df.columns):
        tiros = df["tiros_local"].fillna(0) + df["tiros_visitante"].fillna(0)
        return df[tiros > 0]
    return df


@dataclass
class ModeloConteo:
    nombre: str
    fuerzas: Fuerzas
    dispersion_k: float  # var = mu + mu^2 / k ; k grande -> Poisson
    # totales de la liga en el último año (para la frecuencia real de cada línea)
    totales_liga: np.ndarray | None = None

    def esperados(self, local: str, visitante: str, neutral: bool = False) -> tuple[float, float]:
        return self.fuerzas.esperados(local, visitante, neutral)

    def prob_mas(self, mu_total: float, linea: float) -> float:
        """P(total > linea) para línea media (9.5, 4.5...)."""
        k = self.dispersion_k
        p = k / (k + mu_total)
        return float(nbinom.sf(int(np.floor(linea)), k, p))

    def mercados(self, mu_total: float, lineas) -> dict[str, float]:
        out = {}
        params = RECAL_CONTEOS.get(self.nombre)
        hay_liga = params is not None and self.totales_liga is not None and len(self.totales_liga) >= MIN_PARTIDOS_FRECUENCIA
        for l in lineas:
            crudo = self.prob_mas(mu_total, l)
            mas = recalibrar_conteo(crudo, self.frecuencia_liga(l), params) if hay_liga else encoger(crudo)
            out[f"mas_{l}"] = mas
            out[f"menos_{l}"] = 1 - mas
        return out

    def frecuencia_liga(self, linea: float) -> float:
        """Parte de los partidos de la liga (último año) con más de `linea`."""
        if self.totales_liga is None or not len(self.totales_liga):
            return 0.5
        return float(np.mean(self.totales_liga > linea))


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
    datos = filas_con_estadisticas(df.dropna(subset=[col_local, col_visitante])).copy()
    if referencia is None:
        referencia = pd.to_datetime(datos["fecha"]).max()
    fuerzas = ajustar_fuerzas(datos, col_local, col_visitante, referencia, vida_media_dias, ridge)
    esp = np.array([sum(fuerzas.esperados(l, v)) for l, v in zip(datos["local"], datos["visitante"])])
    tot = (datos[col_local] + datos[col_visitante]).to_numpy(dtype=float)
    w = pesos_por_fecha(datos["fecha"], referencia, vida_media_dias)
    k = estimar_dispersion(tot, esp, w)
    fechas = pd.to_datetime(datos["fecha"])
    ultimo_anio = (fechas >= referencia - pd.Timedelta(days=DIAS_FRECUENCIA_LIGA)) & (fechas < referencia)
    if ultimo_anio.sum() < MIN_PARTIDOS_FRECUENCIA:  # p. ej. referencia = último partido
        ultimo_anio = (fechas >= referencia - pd.Timedelta(days=DIAS_FRECUENCIA_LIGA)) & (fechas <= referencia)
    return ModeloConteo(nombre=nombre, fuerzas=fuerzas, dispersion_k=k, totales_liga=tot[ultimo_anio.to_numpy()])


def factor_arbitro(prom_arbitro: float | None, partidos_arbitro: int, prom_competicion: float, encogimiento: int = 10) -> float:
    """Factor del árbitro encogido hacia 1: con 10 partidos pesa la mitad.

    Ambos promedios cuentan como Winpot: amarillas + 2 × rojas por partido
    (el de la competición sale de tarjetas_local/visitante del historial).
    """
    if not prom_arbitro or prom_competicion <= 0 or partidos_arbitro <= 0:
        return 1.0
    crudo = prom_arbitro / prom_competicion
    peso = partidos_arbitro / (partidos_arbitro + encogimiento)
    return float(1 + peso * (crudo - 1))


# --- Tarjetas 1X2: qué equipo recibe más tarjetas (Winpot: amarilla 1, roja 2) ---
# Backtest walk-forward (27-oct-2026; Premier, LaLiga, Serie A, Bundesliga, Ligue 1 y
# Liga MX; predicciones desde jul-2023, medidas desde ene-2024 a oct-2026: 5,451 partidos
# con estadísticas (desde el 29-oct-2026 sin los partidos que ESPN publicó sin ellas);
# `python -m predicciones.backtest_tarjetas_1x2 --liga premier --liga laliga ...`):
# - Las tarjetas de cada equipo salen del mismo modelo de tarjetas (las que recibe, las
#   que provoca al rival y la sede), por el factor del árbitro y de los ajustes.
# - Favorito: el equipo menos favorito recibe algo más. log(esperado) ± B_FAVORITO ·
#   (prob. rival − prob. propia), con el 1X2 sin margen (Pinnacle si lo hay). Efecto chico.
# - Dos Poisson independientes dan empate ~20% y en la realidad pasa 23.5% (las tarjetas de
#   los dos equipos se mueven juntas). Una Poisson bivariada se pasa (27%). Lo que cuadró es
#   recalibrar: p ∝ [e^(s·z/2), e^(d0 + g·ln pX), e^(−s·z/2)], z = ln(p1/p2), ajustado con
#   las predicciones de los meses anteriores. Log-loss 1.0521 (promedio de la liga 1.0672,
#   sin recalibrar 1.0561); empate predicho 22.5% (real 23.5%), calibrado por rangos.
B_FAVORITO = 0.03
RECAL_TARJETAS_1X2 = (0.853, 1.142, 0.989)  # (s, d0, g): "recalibracion_con_todo" del backtest
MAX_TARJETAS_EQUIPO = 20


def tarjetas_1x2_crudo(mu_local: float, mu_visitante: float) -> tuple[float, float, float]:
    """P(local recibe más, mismas, visitante recibe más) con dos Poisson independientes."""
    from scipy.stats import poisson

    x = np.arange(MAX_TARJETAS_EQUIPO + 1)
    m = np.outer(poisson.pmf(x, max(mu_local, 1e-6)), poisson.pmf(x, max(mu_visitante, 1e-6)))
    m /= m.sum()
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def recalibrar_matriz(p: np.ndarray, params: tuple[float, float, float] = RECAL_TARJETAS_1X2) -> np.ndarray:
    """Recalibra filas (p1, pX, p2): p ∝ [e^(s·z/2), e^(d0 + g·ln pX), e^(−s·z/2)], z = ln(p1/p2)."""
    s, d0, g = params
    p = np.clip(np.atleast_2d(np.asarray(p, dtype=float)), 1e-9, None)
    z = np.log(p[:, 0] / p[:, 2])
    sc = np.column_stack([s * z / 2, d0 + g * np.log(p[:, 1]), -s * z / 2])
    e = np.exp(sc - sc.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


def recalibrar_1x2(p: tuple[float, float, float], params: tuple[float, float, float] = RECAL_TARJETAS_1X2) -> tuple[float, float, float]:
    q = recalibrar_matriz(p, params)[0]
    return float(q[0]), float(q[1]), float(q[2])


def esperados_favorito(mu_local: float, mu_visitante: float, q_local: float | None,
                       q_visitante: float | None, b: float = B_FAVORITO) -> tuple[float, float]:
    if q_local is None or q_visitante is None:
        return mu_local, mu_visitante
    f = float(np.exp(b * (q_visitante - q_local)))
    return mu_local * f, mu_visitante / f


def tarjetas_1x2(mu_local: float, mu_visitante: float, q_local: float | None = None,
                 q_visitante: float | None = None) -> dict[str, float]:
    """Mercado tarjetas 1X2: "1" = el local recibe más, "X" = mismas, "2" = el visitante recibe más.

    mu_*: tarjetas esperadas de cada equipo (ya con árbitro y ajustes); q_*: 1X2 sin margen
    del partido (quién es favorito)."""
    ml, mv = esperados_favorito(mu_local, mu_visitante, q_local, q_visitante)
    p1, px, p2 = recalibrar_1x2(tarjetas_1x2_crudo(ml, mv))
    return {"1": p1, "X": px, "2": p2}
