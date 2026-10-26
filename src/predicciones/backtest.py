"""Prueba contra el pasado ("walk-forward").

Para cada semana se ajusta el modelo solo con partidos anteriores y se predicen
los de esa semana, como si fuera en vivo. Se mide:
- calibración y precisión (log-loss, Brier) del 1X2 y de más/menos 2.5,
  comparadas contra Pinnacle de cierre sin margen;
- rentabilidad simulada con las reglas de valor y montos, apostando contra la
  cuota de Pinnacle de apertura (aprox.) y cerrando contra la de cierre;
- calibración de córners y tarjetas (no hay cuotas históricas gratis).

Uso: python -m predicciones.backtest --liga laliga --desde 2022 --hasta 2025
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import bank as B
from . import cuotas as C
from . import valor as V
from .pronostico import ajustar_modelos, pronosticar


@dataclass
class Resultado:
    n_partidos: int
    logloss_modelo: float
    logloss_pinnacle: float | None
    brier_modelo: float
    brier_pinnacle: float | None
    apuestas: int
    roi: float | None
    clv_promedio: float | None
    calibracion: list[dict]
    calibracion_corners: list[dict]
    calibracion_tarjetas: list[dict]

    def a_dict(self):
        return self.__dict__


def _calibracion(probs: list[float], aciertos: list[int], cubetas=10) -> list[dict]:
    if not probs:
        return []
    df = pd.DataFrame({"p": probs, "y": aciertos})
    df["c"] = pd.cut(df["p"], np.linspace(0, 1, cubetas + 1), include_lowest=True)
    g = df.groupby("c", observed=True).agg(prob=("p", "mean"), real=("y", "mean"), n=("y", "size")).reset_index(drop=True)
    return g.round(3).to_dict("records")


def correr(hist: pd.DataFrame, desde: str, hasta: str | None = None, min_partidos_previos: int = 300) -> Resultado:
    hist = hist.sort_values("fecha").reset_index(drop=True)
    hist["semana"] = hist["fecha"].dt.to_period("W")
    prueba = hist[(hist["fecha"] >= pd.Timestamp(desde)) & ((hist["fecha"] < pd.Timestamp(hasta)) if hasta else True)]
    ll_m, ll_p, br_m, br_p = [], [], [], []
    cal_p, cal_y = [], []
    cor_p, cor_y, tar_p, tar_y = [], [], [], []
    bank_sim = 10_000.0
    apostado = ganado = 0.0
    n_ap = 0
    clv = []
    for semana, grupo in prueba.groupby("semana"):
        previos = hist[hist["fecha"] < grupo["fecha"].min()]
        if len(previos) < min_partidos_previos:
            continue
        modelos = ajustar_modelos(previos)
        for _, f in grupo.iterrows():
            pin_abre = {k: f.get(f"pin_{k}") for k in ("1", "X", "2")}
            pin_cierre = [f.get(f"pin_c_{k}") for k in ("1", "X", "2")]
            p = pronosticar(modelos, f["local"], f["visitante"],
                            cuotas_pinnacle={**pin_abre, "mas_2.5": f.get("pin_mas_2.5"), "menos_2.5": f.get("pin_menos_2.5")}
                            if all(pd.notna(v) for v in pin_abre.values()) else None,
                            confianza_datos="alta")
            real = "1" if f["goles_local"] > f["goles_visitante"] else "2" if f["goles_visitante"] > f["goles_local"] else "X"
            pm = p.probabilidades["ganador"]
            vec = np.array([pm["1"], pm["X"], pm["2"]])
            y = np.array([real == k for k in ("1", "X", "2")], dtype=float)
            ll_m.append(-np.log(max(vec[y == 1][0], 1e-9)))
            br_m.append(float(np.sum((vec - y) ** 2)))
            for k, prob in pm.items():
                cal_p.append(prob)
                cal_y.append(int(real == k))
            if all(pd.notna(c) for c in pin_cierre):
                pj = np.array(C.quitar_margen(pin_cierre))
                ll_p.append(-np.log(max(pj[y == 1][0], 1e-9)))
                br_p.append(float(np.sum((pj - y) ** 2)))
            # córners y tarjetas: calibración de la línea más común
            for mercado, linea, col_l, col_v, acc_p, acc_y in (
                ("corners", 9.5, "corners_local", "corners_visitante", cor_p, cor_y),
                ("tarjetas", 4.5, "tarjetas_local", "tarjetas_visitante", tar_p, tar_y),
            ):
                if mercado in p.probabilidades and pd.notna(f.get(col_l)):
                    acc_p.append(p.probabilidades[mercado][f"mas_{linea}"])
                    acc_y.append(int(f[col_l] + f[col_v] > linea))
            # apuestas simuladas en 1X2 con la cuota de apertura de Pinnacle
            if not all(pd.notna(v) for v in pin_abre.values()):
                continue
            for i, k in enumerate(("1", "X", "2")):
                s = V.Seleccion(p.partido, "ganador", k, pm[k], confianza=p.confianza_base, cuota_ofrecida=float(pin_abre[k]))
                ve = s.ve()
                pct = B.porcentaje_sencilla(ve, s.confianza, s.ve_minimo)
                if pct <= 0:
                    continue
                monto = B.monto(bank_sim, pct)
                n_ap += 1
                apostado += monto
                g = monto * (s.cuota_ofrecida - 1) if real == k else -monto
                ganado += g
                bank_sim += g
                if pd.notna(pin_cierre[i]):
                    clv.append(s.cuota_ofrecida / float(pin_cierre[i]) - 1)
    return Resultado(
        n_partidos=len(ll_m),
        logloss_modelo=round(float(np.mean(ll_m)), 4) if ll_m else float("nan"),
        logloss_pinnacle=round(float(np.mean(ll_p)), 4) if ll_p else None,
        brier_modelo=round(float(np.mean(br_m)), 4) if br_m else float("nan"),
        brier_pinnacle=round(float(np.mean(br_p)), 4) if br_p else None,
        apuestas=n_ap,
        roi=round(ganado / apostado, 4) if apostado else None,
        clv_promedio=round(float(np.mean(clv)), 4) if clv else None,
        calibracion=_calibracion(cal_p, cal_y),
        calibracion_corners=_calibracion(cor_p, cor_y, 5),
        calibracion_tarjetas=_calibracion(tar_p, tar_y, 5),
    )


def main(argv=None):
    from .datos import football_data
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", required=True, choices=sorted(football_data.LIGAS_PRINCIPALES))
    ap.add_argument("--desde", type=int, default=2022, help="primera temporada de prueba (año de inicio)")
    ap.add_argument("--hasta", type=int, default=2025, help="última temporada de prueba (año de inicio)")
    ap.add_argument("--previas", type=int, default=3, help="temporadas previas para entrenar")
    ap.add_argument("--salida")
    a = ap.parse_args(argv)
    temporadas = list(range(a.desde - a.previas, a.hasta + 1))
    hist = football_data.historial(a.liga, temporadas)
    res = correr(hist, desde=f"{a.desde}-07-01", hasta=f"{a.hasta + 1}-07-01")
    texto = json.dumps(res.a_dict(), ensure_ascii=False, indent=2)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            fh.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
