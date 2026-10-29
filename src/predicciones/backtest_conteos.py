"""Prueba contra el pasado de córners y tarjetas (más/menos de cada línea).

Walk-forward mensual por liga: con los partidos anteriores a cada mes se ajusta el
modelo de conteo (el mismo de pronostico.py, sin árbitro ni ajustes) y se calcula
P(más de la línea) para varias líneas. Se comparan:
- "anterior": modelo con los partidos sin estadísticas contados como 0 y encogido
  hacia 50% (como estaba hasta el 29-oct-2026);
- "crudo": modelo con datos limpios, sin recalibrar;
- "recalibrado": logit p' = c + a·logit p + b·logit(frecuencia de la línea en la liga,
  último año). (c, a, b) se ajusta mes a mes con las predicciones de los meses
  anteriores, como se haría en vivo, y se mide desde que hay 6 meses previos.
Al final da el ajuste con todo (el que se copia a RECAL_CONTEOS).

Resultado (29-oct-2026; Premier, LaLiga, Serie A, Bundesliga, Ligue 1, Liga MX;
predicciones desde ene-2024, medidas jul-2024 a oct-2026, 4,641 partidos):
- córners: log-loss anterior 0.6502, crudo 0.6444, solo frecuencia de la liga 0.6414,
  recalibrado 0.6370; "menos de 7.5" en Liga MX: anterior 39.9%, recalibrado 26.4%, real 25.6%.
- tarjetas: anterior 0.5725, crudo 0.5531, solo frecuencia 0.5609, recalibrado 0.5509.
- Recalibrado: calibrado de 10% a 90% en los dos mercados.
- Aparte (no está en este módulo): la frecuencia de la línea en los últimos 10 partidos
  de cada equipo casi no agrega nada encima del modelo recalibrado (1 a 2 puntos).

Uso: python -m predicciones.backtest_conteos --liga premier --liga liga_mx ... [--desde 2024-01-01]
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .modelos.conteos import (
    DIAS_FRECUENCIA_LIGA,
    ajustar_modelo_conteo,
    encoger,
    filas_con_estadisticas,
)

LINEAS = {"corners": (7.5, 8.5, 9.5, 10.5, 11.5), "tarjetas": (2.5, 3.5, 4.5, 5.5, 6.5)}
MESES_PREVIOS_RECAL = 6


def predicciones_crudas(liga: str, desde: str) -> pd.DataFrame:
    d = pd.read_csv(f"datos/historial/{liga}.csv")
    d["fecha"] = pd.to_datetime(d["fecha"])
    d = d.dropna(subset=["goles_local"]).sort_values("fecha").reset_index(drop=True)
    limpio = filas_con_estadisticas(d)
    out = []
    meses = pd.date_range(pd.Timestamp(desde), d["fecha"].max() + pd.offsets.MonthBegin(1), freq="MS")
    for ini, fin in zip(meses[:-1], meses[1:]):
        te = limpio[(limpio["fecha"] >= ini) & (limpio["fecha"] < fin)]
        if te.empty or (d["fecha"] < ini).sum() < 200:
            continue
        for merc, lineas in LINEAS.items():
            cl, cv = f"{merc}_local", f"{merc}_visitante"
            # anterior: con los partidos sin estadísticas como 0 (sin pasar por el filtro)
            sucio = d[d["fecha"] < ini].assign(tiros_local=1, tiros_visitante=1)
            m_ant = ajustar_modelo_conteo(sucio, cl, cv, merc, ini)
            m = ajustar_modelo_conteo(limpio[limpio["fecha"] < ini], cl, cv, merc, ini)
            for r in te.itertuples():
                tot = getattr(r, cl) + getattr(r, cv)
                mu_a = sum(m_ant.esperados(r.local, r.visitante))
                mu = sum(m.esperados(r.local, r.visitante))
                for l in lineas:
                    out.append({"liga": liga, "fecha": r.fecha, "merc": merc, "linea": l, "y": int(tot > l),
                                "p_anterior": encoger(m_ant.prob_mas(mu_a, l)), "p_crudo": m.prob_mas(mu, l),
                                "frecuencia": m.frecuencia_liga(l)})
    return pd.DataFrame(out)


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _x(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.ones(len(df)), _logit(df["p_crudo"].to_numpy()),
                            _logit(np.clip(df["frecuencia"].to_numpy(), 0.01, 0.99))])


def ajustar_recalibracion(df: pd.DataFrame) -> tuple[float, float, float]:
    x, y = _x(df), df["y"].to_numpy()

    def nll(b):
        p = np.clip(1 / (1 + np.exp(-x @ b)), 1e-9, 1 - 1e-9)
        return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
    return tuple(float(v) for v in minimize(nll, [0.0, 0.5, 0.5], method="BFGS").x)


def _metricas(p: np.ndarray, y: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"logloss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "brier": round(float(np.mean((p - y) ** 2)), 4)}


def _calibracion(p: np.ndarray, y: np.ndarray) -> list[dict]:
    # los dos lados de cada línea (más y menos), por rangos de 10 puntos
    p, y = np.r_[p, 1 - p], np.r_[y, 1 - y]
    t = pd.DataFrame({"r": pd.cut(p, np.arange(0, 1.01, 0.1)), "p": p, "y": y}).groupby("r", observed=True)
    return [{"rango": str(k), "n": int(len(g)), "pred": round(float(g["p"].mean()), 3),
             "real": round(float(g["y"].mean()), 3)} for k, g in t if len(g) >= 30]


def correr(ligas: list[str], desde: str = "2024-01-01") -> dict:
    r = pd.concat([predicciones_crudas(l, desde) for l in ligas], ignore_index=True)
    r["mes"] = r["fecha"].dt.to_period("M")
    out = {}
    for merc in LINEAS:
        t = r[r["merc"] == merc].copy()
        t["p_recal"] = np.nan
        meses = sorted(t["mes"].unique())
        for mes in meses[MESES_PREVIOS_RECAL:]:
            c, a, b = ajustar_recalibracion(t[t["mes"] < mes])
            te = t["mes"] == mes
            t.loc[te, "p_recal"] = 1 / (1 + np.exp(-_x(t[te]) @ np.array([c, a, b])))
        ok = t.dropna(subset=["p_recal"])
        y = ok["y"].to_numpy()
        menos75 = {}
        if merc == "corners":
            for liga, g in ok[ok["linea"] == 7.5].groupby("liga"):
                menos75[liga] = {"real": round(1 - g["y"].mean(), 3), "anterior": round(1 - g["p_anterior"].mean(), 3),
                                 "recalibrado": round(1 - g["p_recal"].mean(), 3)}
        out[merc] = {
            "partidos_medidos": int((ok["linea"] == LINEAS[merc][0]).sum()),
            "solo_frecuencia_liga": _metricas(ok["frecuencia"].to_numpy(), y),
            "anterior": _metricas(ok["p_anterior"].to_numpy(), y),
            "crudo": _metricas(ok["p_crudo"].to_numpy(), y),
            "recalibrado": _metricas(ok["p_recal"].to_numpy(), y),
            "calibracion_anterior": _calibracion(ok["p_anterior"].to_numpy(), y),
            "calibracion_recalibrado": _calibracion(ok["p_recal"].to_numpy(), y),
            **({"menos_7.5_por_liga": menos75} if menos75 else {}),
            "recalibracion_con_todo": [round(x, 3) for x in ajustar_recalibracion(t)],
        }
    out["dias_frecuencia_liga"] = DIAS_FRECUENCIA_LIGA
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", action="append", required=True)
    ap.add_argument("--desde", default="2024-01-01")
    a = ap.parse_args(argv)
    print(json.dumps(correr(a.liga, a.desde), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
