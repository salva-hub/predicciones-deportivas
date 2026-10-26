"""Ajuste de "fuerzas" de ataque y defensa por equipo (regresión de Poisson ponderada).

Sirve para goles (o xG), córners y tarjetas: para cada partido se modela

    esperado_local     = exp(c + ventaja_local + ataque[local] + defensa[visitante])
    esperado_visitante = exp(c + ataque[visitante] + defensa[local])

Los partidos recientes pesan más (vida media configurable) y una penalización
ridge acerca al promedio a los equipos con pocos partidos (p. ej. ascendidos).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize


@dataclass
class Fuerzas:
    equipos: list[str]
    ataque: dict[str, float]
    defensa: dict[str, float]
    constante: float
    ventaja_local: float
    partidos: dict[str, float] = field(default_factory=dict)

    def esperados(self, local: str, visitante: str) -> tuple[float, float]:
        al = self.ataque.get(local, 0.0)
        dl = self.defensa.get(local, 0.0)
        av = self.ataque.get(visitante, 0.0)
        dv = self.defensa.get(visitante, 0.0)
        mu_l = math.exp(self.constante + self.ventaja_local + al + dv)
        mu_v = math.exp(self.constante + av + dl)
        return mu_l, mu_v

    def conoce(self, equipo: str) -> bool:
        return equipo in self.ataque


def pesos_por_fecha(fechas: pd.Series, referencia: pd.Timestamp, vida_media_dias: float) -> np.ndarray:
    dias = (referencia - pd.to_datetime(fechas)).dt.days.clip(lower=0).to_numpy(dtype=float)
    return np.power(0.5, dias / vida_media_dias)


def ajustar_fuerzas(
    df: pd.DataFrame,
    col_local: str,
    col_visitante: str,
    referencia: pd.Timestamp | None = None,
    vida_media_dias: float = 180.0,
    ridge: float = 2.0,
) -> Fuerzas:
    """df necesita columnas: fecha, local, visitante y las dos columnas objetivo."""
    datos = df.dropna(subset=[col_local, col_visitante]).copy()
    if datos.empty:
        raise ValueError("No hay partidos para ajustar el modelo")
    if referencia is None:
        referencia = pd.to_datetime(datos["fecha"]).max()
    w = pesos_por_fecha(datos["fecha"], referencia, vida_media_dias)

    equipos = sorted(set(datos["local"]) | set(datos["visitante"]))
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)
    il = datos["local"].map(idx).to_numpy()
    iv = datos["visitante"].map(idx).to_numpy()
    yl = datos[col_local].to_numpy(dtype=float)
    yv = datos[col_visitante].to_numpy(dtype=float)

    media = max((np.average(yl, weights=w) + np.average(yv, weights=w)) / 2, 1e-6)

    def objetivo(theta: np.ndarray):
        a = theta[:n]
        d = theta[n:2 * n]
        c, h = theta[2 * n], theta[2 * n + 1]
        eta_l = c + h + a[il] + d[iv]
        eta_v = c + a[iv] + d[il]
        mu_l = np.exp(eta_l)
        mu_v = np.exp(eta_v)
        # log-verosimilitud de Poisson (vale también para xG no entero)
        ll = np.sum(w * (yl * eta_l - mu_l)) + np.sum(w * (yv * eta_v - mu_v))
        pen = ridge * (np.sum(a ** 2) + np.sum(d ** 2))
        rl = w * (yl - mu_l)
        rv = w * (yv - mu_v)
        g_a = np.bincount(il, rl, n) + np.bincount(iv, rv, n) - 2 * ridge * a
        g_d = np.bincount(iv, rl, n) + np.bincount(il, rv, n) - 2 * ridge * d
        g_c = rl.sum() + rv.sum()
        g_h = rl.sum()
        grad = np.concatenate([g_a, g_d, [g_c, g_h]])
        return -(ll - pen), -grad

    x0 = np.zeros(2 * n + 2)
    x0[2 * n] = math.log(media)
    res = minimize(objetivo, x0, jac=True, method="L-BFGS-B")
    theta = res.x
    a = theta[:n]
    d = theta[n:2 * n]
    partidos = (np.bincount(il, w, n) + np.bincount(iv, w, n))
    return Fuerzas(
        equipos=equipos,
        ataque={e: float(a[i]) for e, i in idx.items()},
        defensa={e: float(d[i]) for e, i in idx.items()},
        constante=float(theta[2 * n]),
        ventaja_local=float(theta[2 * n + 1]),
        partidos={e: float(partidos[i]) for e, i in idx.items()},
    )
