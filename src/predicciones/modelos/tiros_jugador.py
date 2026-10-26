"""Tiros a puerta de un jugador (mercado de Winpot "tiros a puerta del jugador").

Esperado = tasa por 90 min × minutos esperados / 90 × factor del rival × factor de sede.
- Tasa: tiros a puerta por 90 del jugador con este equipo (temporada actual y la
  anterior), encogida hacia el promedio de su posición en el equipo con
  PRIOR_MINUTOS minutos "prestados": con 450 minutos jugados pesa la mitad.
- Minutos esperados: su promedio como titular (con al menos MIN_TITULARIDADES),
  con tope de TOPE_MINUTOS; si no, un valor por posición.
- Rival: tiros a puerta que concede por partido / promedio de la liga, encogido
  hacia 1 (con ENCOGER_RIVAL partidos pesa la mitad). Sede: promedio de la liga
  de local (o de visitante) / promedio general.
- Distribución: Poisson (la prueba histórica no mostró sobredispersión clara por
  jugador una vez fijados los minutos; ver backtest_tiros).
La confianza es siempre "baja" (CLAUDE.md, "Tiros al arco de jugador").
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import poisson

PRIOR_MINUTOS = 450.0
TOPE_MINUTOS = 85.0
MIN_TITULARIDADES = 3
MINUTOS_DEFECTO = {"F": 75.0, "M": 78.0, "D": 84.0, "G": 85.0}
ENCOGER_RIVAL = 10.0
LINEAS = (0.5, 1.5, 2.5)
PROB_MIN_LINEA = 0.05          # líneas con menos probabilidad no se muestran
MAX_POR_EQUIPO = 6
POSICIONES_PREFERIDAS = ("F", "M")
# Tasa por 90 de respaldo por posición si el equipo no tiene datos (aprox. ligas top).
TASA_LIGA = {"F": 0.80, "M": 0.35, "D": 0.15, "G": 0.0}
# Calibración (backtest_tiros, LaLiga y Premier 2025/26, 15,020 titulares de campo):
# en promedio cuadra (pred. 0.273 / real 0.272 en más de 0.5), pero en los extremos
# se confía de más (0.84 -> 0.79; 0.55 -> 0.51; 0.06 -> 0.09). El log-loss mínimo
# sale con un factor ~0.9: p' = base + 0.9 · (p − base), base = tasa de la línea.
ENCOGER_TIROS = 0.9


@dataclass
class EsperadoJugador:
    mu: float
    tasa90: float
    minutos_esperados: float
    minutos_historial: float
    titularidades: int


def posicion_principal(act: pd.DataFrame) -> dict[str, str]:
    """Posición más frecuente de cada jugador como titular (G, D, M, F)."""
    tit = act[act["titular"] & act["posicion"].notna()]
    if tit.empty:
        return {}
    return tit.groupby("jugador_id")["posicion"].agg(lambda s: s.value_counts().index[0]).to_dict()


def tasas_por_posicion(act: pd.DataFrame, principal: dict[str, str] | None = None) -> dict[str, float]:
    """Tiros a puerta por 90 de cada posición en el equipo (todas las actuaciones)."""
    principal = principal if principal is not None else posicion_principal(act)
    jug = act[(act["minutos"] > 0) & act["tiros_puerta"].notna()]
    if jug.empty:
        return dict(TASA_LIGA)
    pos = jug["jugador_id"].map(principal)
    out = dict(TASA_LIGA)
    for p, g in jug.groupby(pos):
        mins = g["minutos"].sum()
        if mins >= 900:  # al menos 10 partidos completos de esa posición
            out[p] = float(g["tiros_puerta"].sum() / mins * 90)
    return out


def esperado(act: pd.DataFrame, jugador_id: str, pos: str | None, f_rival: float = 1.0, f_sede: float = 1.0,
             tasas: dict[str, float] | None = None, principal: dict[str, str] | None = None) -> EsperadoJugador:
    """Tiros a puerta esperados del jugador (act: actuaciones de su equipo, solo partidos anteriores)."""
    if principal is None:
        principal = posicion_principal(act)
    tasas = tasas or tasas_por_posicion(act, principal)
    pos = pos or principal.get(jugador_id) or "M"
    base = tasas.get(pos, TASA_LIGA.get(pos, 0.35))
    j = act[(act["jugador_id"] == jugador_id) & (act["minutos"] > 0) & act["tiros_puerta"].notna()]
    mins = float(j["minutos"].sum())
    tiros = float(j["tiros_puerta"].sum())
    tasa = (tiros + base * PRIOR_MINUTOS / 90) / (mins + PRIOR_MINUTOS) * 90
    tit = j[j["titular"]]
    if len(tit) >= MIN_TITULARIDADES:
        m_esp = min(float(tit["minutos"].mean()), TOPE_MINUTOS)
    else:
        m_esp = MINUTOS_DEFECTO.get(pos, 78.0)
    mu = tasa * m_esp / 90 * f_rival * f_sede
    return EsperadoJugador(mu=float(mu), tasa90=float(tasa), minutos_esperados=m_esp,
                           minutos_historial=mins, titularidades=int(len(tit)))


@dataclass
class FactoresLiga:
    concede: dict[str, float]   # equipo -> factor de tiros a puerta que concede
    sede_local: float
    sede_visitante: float

    def rival(self, equipo: str) -> float:
        return self.concede.get(equipo, 1.0)

    def sede(self, local: bool, neutral: bool = False) -> float:
        if neutral:
            return 1.0
        return self.sede_local if local else self.sede_visitante


def factores_liga(hist: pd.DataFrame, antes: pd.Timestamp | None = None, dias: int = 365) -> FactoresLiga:
    """Factor de cada equipo por los tiros a puerta que concede, y de sede, del último año."""
    cols = {"tiros_puerta_local", "tiros_puerta_visitante"}
    if hist.empty or not cols <= set(hist.columns):
        return FactoresLiga({}, 1.0, 1.0)
    f = pd.to_datetime(hist["fecha"])
    antes = pd.Timestamp(antes) if antes is not None else f.max() + pd.Timedelta(days=1)
    h = hist[(f < antes) & (f >= antes - pd.Timedelta(days=dias))].dropna(subset=list(cols))
    if len(h) < 20:
        return FactoresLiga({}, 1.0, 1.0)
    prom_l, prom_v = float(h["tiros_puerta_local"].mean()), float(h["tiros_puerta_visitante"].mean())
    prom = (prom_l + prom_v) / 2
    if prom <= 0:
        return FactoresLiga({}, 1.0, 1.0)
    # lo que concede cada equipo, corregido por sede (de local concede menos)
    filas = pd.concat([
        pd.DataFrame({"equipo": h["local"], "rel": h["tiros_puerta_visitante"] / prom_v}),
        pd.DataFrame({"equipo": h["visitante"], "rel": h["tiros_puerta_local"] / prom_l}),
    ])
    g = filas.groupby("equipo")["rel"].agg(["mean", "size"])
    peso = g["size"] / (g["size"] + ENCOGER_RIVAL)
    concede = (1 + peso * (g["mean"] - 1)).to_dict()
    return FactoresLiga({k: float(v) for k, v in concede.items()}, prom_l / prom, prom_v / prom)


def encoger(p: float, base: float, factor: float = ENCOGER_TIROS) -> float:
    return base + factor * (p - base)


# tasa base de cada línea (titulares de campo, ligas top): hacia ella se encoge
BASE_LINEA = {0.5: 0.30, 1.5: 0.07, 2.5: 0.015}


def mercados(mu: float, lineas=LINEAS, factor: float | None = None) -> dict[str, float]:
    """P(más de la línea) con Poisson, encogida hacia la tasa base de la línea."""
    k = ENCOGER_TIROS if factor is None else factor
    out = {}
    for l in lineas:
        p = float(poisson.sf(int(np.floor(l)), max(mu, 1e-9)))
        out[f"mas_{l}"] = encoger(p, BASE_LINEA.get(l, p), k)
    return out


def pronostico_partido(alineacion: dict[str, list[dict]], act_local: pd.DataFrame, act_visitante: pd.DataFrame,
                       factores: FactoresLiga, local: str, visitante: str, neutral: bool = False,
                       max_por_equipo: int = MAX_POR_EQUIPO) -> list[dict]:
    """Tiros a puerta de los titulares de campo, los de mayor esperado de cada equipo.

    Prefiere delanteros y medios; completa con defensas solo si faltan. Devuelve
    filas con jugador, equipo, posición, esperado y probabilidades por línea.
    """
    out = []
    for lado, act, rival, es_local in (("home", act_local, visitante, True), ("away", act_visitante, local, False)):
        tit = [j for j in alineacion.get(lado, []) if j.get("posicion") != "G"]
        if act is None or act.empty:
            act = pd.DataFrame(columns=["jugador_id", "titular", "posicion", "minutos", "tiros_puerta"])
        principal = posicion_principal(act)
        tasas = tasas_por_posicion(act, principal)
        f_r, f_s = factores.rival(rival), factores.sede(es_local, neutral)
        filas = []
        for j in tit:
            e = esperado(act, j["jugador_id"], j.get("posicion"), f_r, f_s, tasas, principal)
            pos = j.get("posicion") or principal.get(j["jugador_id"]) or "M"
            filas.append({
                "jugador": j["nombre"], "jugador_id": j["jugador_id"], "equipo": j.get("equipo"),
                "lado": "local" if es_local else "visitante", "posicion": pos,
                "tiros_esperados": round(e.mu, 3), "tasa90": round(e.tasa90, 3),
                "minutos_esperados": round(e.minutos_esperados, 1), "minutos_historial": round(e.minutos_historial),
                "probabilidades": mercados(e.mu),
            })
        filas.sort(key=lambda x: (x["posicion"] not in POSICIONES_PREFERIDAS, -x["tiros_esperados"]))
        out += filas[:max_por_equipo]
    return out
