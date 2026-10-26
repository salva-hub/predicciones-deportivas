"""Prueba histórica de los tiros a puerta por jugador (calibración).

Para cada partido de la temporada de prueba y cada titular de campo, se calcula
P(más de 0.5) y P(más de 1.5) solo con partidos anteriores (temporada actual y la
anterior, como en vivo) y se compara con lo que pasó: log-loss, Brier y tabla de
calibración por cubetas. Se mide también la sobredispersión (varianza de los
residuos / media; 1 = Poisson) y el factor de encogimiento que minimiza el log-loss.

Uso: python -m predicciones.backtest_tiros --liga laliga --temporada 2025
(descarga ~2 temporadas de resúmenes de ESPN; usa la caché, PRED_CACHE).
"""
from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import poisson

from .backtest import _calibracion
from .datos import historiales
from .datos import jugadores as J
from .modelos import tiros_jugador as T


def _metricas(p: np.ndarray, y: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"n": int(len(y)), "prob_media": round(float(p.mean()), 4), "real": round(float(y.mean()), 4),
            "brier": round(float(np.mean((p - y) ** 2)), 4),
            "logloss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "brier_base": round(float(np.mean((y.mean() - y) ** 2)), 4)}


def predicciones(liga: str, temporada: int, hist: pd.DataFrame | None = None, act: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una fila por titular de campo de la temporada `temporada` (año de inicio), con mu y resultado."""
    hist = historiales.leer(liga) if hist is None else hist
    hist = hist.dropna(subset=["espn_id"]).copy()
    hist["espn_id"] = hist["espn_id"].astype(str).str.split(".").str[0]
    f = pd.to_datetime(hist["fecha"])
    ini_prueba, fin_prueba = pd.Timestamp(date(temporada, 7, 1)), pd.Timestamp(date(temporada + 1, 7, 1))
    ini_datos = pd.Timestamp(J.inicio_temporada_anterior(ini_prueba.date() + pd.Timedelta(days=1), liga))
    usar = hist[(f >= ini_datos) & (f < fin_prueba)]
    if act is None:
        act = J.actuaciones_partidos(usar, liga)
    act = act[act["tiros_puerta"].notna()].copy()
    act["fecha"] = pd.to_datetime(act["fecha"])
    prueba = usar[pd.to_datetime(usar["fecha"]) >= ini_prueba].sort_values("fecha")
    filas = []
    for _, m in prueba.iterrows():
        fecha = pd.Timestamp(m["fecha"])
        desde = pd.Timestamp(J.inicio_temporada_anterior(fecha.date(), liga))
        fac = T.factores_liga(hist, fecha)
        este = act[act["espn_id"] == m["espn_id"]]
        for equipo, rival, es_local in ((m["local"], m["visitante"], True), (m["visitante"], m["local"], False)):
            previo = act[(act["equipo"] == equipo) & (act["fecha"] < fecha) & (act["fecha"] >= desde)]
            if previo["espn_id"].nunique() < 3:
                continue  # sin historial del equipo (recién ascendido al inicio): se omite
            principal = T.posicion_principal(previo)
            tasas = T.tasas_por_posicion(previo, principal)
            for _, j in este[(este["equipo"] == equipo) & este["titular"]].iterrows():
                if j["posicion"] == "G":
                    continue
                e = T.esperado(previo, j["jugador_id"], j["posicion"], fac.rival(rival), fac.sede(es_local), tasas, principal)
                filas.append({"fecha": fecha, "espn_id": m["espn_id"], "equipo": equipo, "jugador": j["nombre"],
                              "posicion": j["posicion"] or principal.get(j["jugador_id"]) or "M",
                              "mu": e.mu, "minutos_historial": e.minutos_historial,
                              "minutos": j["minutos"], "tiros_puerta": j["tiros_puerta"]})
    return pd.DataFrame(filas)


def _top_por_equipo(df: pd.DataFrame, n: int = T.MAX_POR_EQUIPO) -> pd.DataFrame:
    """Los que se mostrarían en vivo: delanteros y medios de mayor esperado, n por equipo."""
    d = df.assign(pref=~df["posicion"].isin(T.POSICIONES_PREFERIDAS)).sort_values(["pref", "mu"], ascending=[True, False])
    return d.groupby(["espn_id", "equipo"], group_keys=False).head(n)


def evaluar(df: pd.DataFrame) -> dict:
    out: dict = {}
    mu, y = df["mu"].to_numpy(), df["tiros_puerta"].to_numpy()
    out["sobredispersion"] = round(float(np.mean((y - mu) ** 2) / np.mean(mu)), 3)
    out["mu_media"], out["tiros_media"] = round(float(mu.mean()), 3), round(float(y.mean()), 3)
    for linea in (0.5, 1.5):
        p_raw = poisson.sf(int(linea), mu)
        yy = (y > linea).astype(int)
        base = T.BASE_LINEA[linea]
        mejor = min(np.linspace(0.3, 1.2, 46), key=lambda k: _metricas(base + k * (p_raw - base), yy)["logloss"])
        p_act = base + T.ENCOGER_TIROS * (p_raw - base)
        out[f"mas_{linea}"] = {
            "poisson": _metricas(p_raw, yy),
            "vigente": {**_metricas(p_act, yy), "encoger": T.ENCOGER_TIROS},
            "mejor_encoger": round(float(mejor), 2),
            "calibracion_poisson": _calibracion(list(p_raw), list(yy)),
            "calibracion_vigente": _calibracion(list(p_act), list(yy)),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", action="append", required=True)
    ap.add_argument("--temporada", type=int, default=2025, help="año de inicio de la temporada de prueba")
    ap.add_argument("--salida")
    a = ap.parse_args(argv)
    res, todas = {}, []
    for liga in a.liga:
        df = predicciones(liga, a.temporada)
        todas.append(df)
        res[liga] = {"titulares": evaluar(df), "mostrados": evaluar(_top_por_equipo(df))}
    if len(todas) > 1:
        df = pd.concat(todas)
        res["juntas"] = {"titulares": evaluar(df), "mostrados": evaluar(_top_por_equipo(df))}
    texto = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            fh.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
