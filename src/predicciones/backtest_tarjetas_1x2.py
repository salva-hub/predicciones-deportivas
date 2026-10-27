"""Prueba contra el pasado del mercado tarjetas 1X2 (qué equipo recibe más tarjetas).

Walk-forward mensual por liga: con los partidos anteriores a cada mes se ajusta el
modelo de tarjetas por equipo (el mismo de pronostico.py), el factor del árbitro sale
de sus partidos previos y el favorito del 1X2 de cierre de Pinnacle sin margen
(football-data, solo 5 ligas grandes). La recalibración (s, d0, g) también se ajusta
mes a mes con las predicciones de los meses anteriores, como se haría en vivo, y se
mide desde que hay 6 meses de predicciones previas. Al final muestra el ajuste con todo
(el que se copia a RECAL_TARJETAS_1X2).

No hay cuotas históricas gratis de este mercado: no se puede medir ganancia, solo
log-loss, Brier y calibración contra el promedio de la liga.

Uso: python -m predicciones.backtest_tarjetas_1x2 --liga premier --liga laliga ... [--desde 2023-07-01]
Lee datos/historial/<liga>.csv (ESPN, con árbitro).
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from . import equipos as E
from .modelos.conteos import (
    esperados_favorito,
    factor_arbitro,
    recalibrar_matriz,
    tarjetas_1x2_crudo,
)
from .modelos.fuerzas import ajustar_fuerzas

MESES_PREVIOS_RECAL = 6


def _cargar(liga: str) -> pd.DataFrame:
    d = pd.read_csv(f"datos/historial/{liga}.csv")
    d = d[d["terminado"] == True].dropna(subset=["tarjetas_local", "tarjetas_visitante"]).copy()  # noqa: E712
    d["fecha"] = pd.to_datetime(d["fecha"])
    d["q1"] = np.nan
    d["q2"] = np.nan
    from .datos import football_data as F
    if liga in F.LIGAS_PRINCIPALES:
        anios = range(d["fecha"].min().year - 1, d["fecha"].max().year + 1)
        fd = F.historial(liga, list(anios)).dropna(subset=["pin_c_1", "pin_c_x", "pin_c_2"]).copy()
        inv = 1 / fd[["pin_c_1", "pin_c_x", "pin_c_2"]].astype(float).to_numpy()
        fd["q1"], fd["q2"] = inv[:, 0] / inv.sum(1), inv[:, 2] / inv.sum(1)
        nombres = sorted(set(fd["local"]) | set(fd["visitante"]))
        mapa = {e: E.resolver(e, nombres) for e in set(d["local"])}
        idx = {(pd.Timestamp(f).normalize(), l): (a, b) for f, l, a, b in zip(fd["fecha"], fd["local"], fd["q1"], fd["q2"])}
        for i, f, l in zip(d.index, d["fecha"], d["local"]):
            for off in (0, -1, 1):
                k = (f.normalize() + pd.Timedelta(days=off), mapa.get(l))
                if k in idx:
                    d.loc[i, ["q1", "q2"]] = idx[k]
                    break
    return d.sort_values("fecha").reset_index(drop=True)


def predicciones_crudas(liga: str, desde: str) -> pd.DataFrame:
    """Probabilidades sin recalibrar, mes a mes (solo con información previa)."""
    d = _cargar(liga)
    out = []
    meses = pd.date_range(pd.Timestamp(desde), d["fecha"].max() + pd.offsets.MonthBegin(1), freq="MS")
    for ini, fin in zip(meses[:-1], meses[1:]):
        te = d[(d["fecha"] >= ini) & (d["fecha"] < fin)]
        tr = d[d["fecha"] < ini]
        if te.empty or len(tr) < 200:
            continue
        fz = ajustar_fuerzas(tr, "tarjetas_local", "tarjetas_visitante", ini, 270.0, 3.0)
        media = float((tr["tarjetas_local"] + tr["tarjetas_visitante"]).mean())
        arb = tr.assign(t=tr["tarjetas_local"] + tr["tarjetas_visitante"]).groupby("arbitro")["t"].agg(["mean", "size"])
        for r in te.itertuples():
            if not (fz.conoce(r.local) and fz.conoce(r.visitante)):
                continue
            ml, mv = fz.esperados(r.local, r.visitante)
            fa = factor_arbitro(arb.loc[r.arbitro, "mean"], int(arb.loc[r.arbitro, "size"]), media) if r.arbitro in arb.index else 1.0
            q1, q2 = (None, None) if np.isnan(r.q1) else (r.q1, r.q2)
            p = tarjetas_1x2_crudo(*esperados_favorito(ml * fa, mv * fa, q1, q2))
            res = 0 if r.tarjetas_local > r.tarjetas_visitante else 1 if r.tarjetas_local == r.tarjetas_visitante else 2
            out.append({"liga": liga, "fecha": r.fecha, "p1": p[0], "pX": p[1], "p2": p[2], "res": res})
    return pd.DataFrame(out)


def ajustar_recalibracion(p: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    def nll(th):
        q = recalibrar_matriz(p, tuple(th))
        return -np.mean(np.log(q[np.arange(len(y)), y]))
    return tuple(float(x) for x in minimize(nll, [1.0, 0.0, 1.0], method="BFGS").x)


def _metricas(q: np.ndarray, y: np.ndarray) -> dict:
    oh = np.eye(3)[y]
    return {"logloss": round(float(-np.mean(np.log((q * oh).sum(1)))), 4),
            "brier": round(float(np.mean(((q - oh) ** 2).sum(1))), 4),
            "empate_predicho": round(float(q[:, 1].mean()), 4)}


def correr(ligas: list[str], desde: str = "2023-07-01") -> dict:
    r = pd.concat([predicciones_crudas(l, desde) for l in ligas], ignore_index=True)
    r["mes"] = r["fecha"].dt.to_period("M")
    p = r[["p1", "pX", "p2"]].to_numpy()
    y = r["res"].to_numpy()
    q = np.full_like(p, np.nan)
    meses = sorted(r["mes"].unique())
    for mes in meses[MESES_PREVIOS_RECAL:]:
        tr, te = (r["mes"] < mes).to_numpy(), (r["mes"] == mes).to_numpy()
        th = ajustar_recalibracion(p[tr], y[tr])
        q[te] = recalibrar_matriz(p[te], th)
    ok = ~np.isnan(q[:, 0])
    base = r[ok].groupby("liga")["res"].value_counts(normalize=True).unstack().reindex(r["liga"][ok]).to_numpy()
    calib = {}
    for j, nombre in enumerate(("1", "X", "2")):
        rangos = pd.cut(q[ok, j], np.arange(0, 1.05, 0.05))
        t = pd.DataFrame({"r": rangos, "p": q[ok, j], "y": y[ok] == j}).groupby("r", observed=True)
        calib[nombre] = [{"rango": str(k), "n": int(len(g)), "pred": round(float(g["p"].mean()), 3),
                          "real": round(float(g["y"].mean()), 3)} for k, g in t if len(g) >= 20]
    return {
        "partidos_medidos": int(ok.sum()),
        "empate_real": round(float((y[ok] == 1).mean()), 4),
        "promedio_liga": _metricas(base, y[ok]),
        "sin_recalibrar": _metricas(p[ok], y[ok]),
        "recalibrado": _metricas(q[ok], y[ok]),
        "calibracion": calib,
        "recalibracion_con_todo": [round(x, 3) for x in ajustar_recalibracion(p, y)],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", action="append", required=True)
    ap.add_argument("--desde", default="2023-07-01")
    a = ap.parse_args(argv)
    print(json.dumps(correr(a.liga, a.desde), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
