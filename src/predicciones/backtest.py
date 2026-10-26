"""Prueba contra el pasado ("walk-forward").

Para cada semana se ajusta el modelo solo con partidos anteriores y se predicen
los de esa semana, como si fuera en vivo. Se mide:
- calibración y precisión (log-loss, Brier) del 1X2 y de más/menos 2.5,
  comparadas contra Pinnacle de cierre sin margen;
- rentabilidad simulada (1X2 y más/menos 2.5, un solo bank de 10,000) con las
  reglas de valor y montos, apostando contra la cuota de Pinnacle de apertura
  (aprox.) y comparando contra la de cierre;
- acierto del resultado más probable del modelo vs el favorito de Pinnacle;
- calibración de córners y tarjetas (no hay cuotas históricas gratis).

Uso: python -m predicciones.backtest --liga laliga --desde 2022 --hasta 2025 [--peso-modelo 0.5]
Cuotas vacías, NaN o <= 1 (football-data a veces trae 0) cuentan como faltantes.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

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
    bank_final: float = 10_000.0
    mercados: dict = field(default_factory=dict)        # ganador, goles y total
    acierto_favorito: dict = field(default_factory=dict)  # modelo vs Pinnacle de cierre
    peso_modelo: float = V.PESO_MODELO

    def a_dict(self):
        return dict(self.__dict__)


def _cuota(x) -> float | None:
    """Cuota decimal válida o None (vacía, NaN o <= 1 cuentan como faltante)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) and x > 1 else None


def _calibracion(probs: list[float], aciertos: list[int], cubetas=10) -> list[dict]:
    if not probs:
        return []
    df = pd.DataFrame({"p": probs, "y": aciertos})
    df["c"] = pd.cut(df["p"], np.linspace(0, 1, cubetas + 1), include_lowest=True)
    g = df.groupby("c", observed=True).agg(prob=("p", "mean"), real=("y", "mean"), n=("y", "size")).reset_index(drop=True)
    return g.round(3).to_dict("records")


class _Cuenta:
    """Apuestas simuladas de un mercado."""

    def __init__(self):
        self.n = self.ganadas = 0
        self.apostado = self.ganancia = self.suma_cuotas = 0.0

    def sumar(self, monto: float, cuota: float, gano: bool, g: float):
        self.n += 1
        self.ganadas += int(gano)
        self.apostado += monto
        self.ganancia += g
        self.suma_cuotas += cuota

    def a_dict(self) -> dict:
        return {
            "apuestas": self.n,
            "acierto": round(self.ganadas / self.n, 4) if self.n else None,
            "roi": round(self.ganancia / self.apostado, 4) if self.apostado else None,
            "cuota_promedio": round(self.suma_cuotas / self.n, 3) if self.n else None,
            "ganancia": round(self.ganancia, 2),
        }


def correr(hist: pd.DataFrame, desde: str, hasta: str | None = None, min_partidos_previos: int = 300,
           peso_modelo: float = V.PESO_MODELO) -> Resultado:
    hist = hist.dropna(subset=["goles_local", "goles_visitante"]).sort_values("fecha").reset_index(drop=True)
    hist["semana"] = hist["fecha"].dt.to_period("W")
    prueba = hist[(hist["fecha"] >= pd.Timestamp(desde)) & ((hist["fecha"] < pd.Timestamp(hasta)) if hasta else True)]
    ll_m, ll_p, br_m, br_p = [], [], [], []
    cal_p, cal_y = [], []
    cor_p, cor_y, tar_p, tar_y = [], [], [], []
    bank_sim = 10_000.0
    cuentas = {"ganador": _Cuenta(), "goles": _Cuenta(), "total": _Cuenta()}
    clv = []
    fav_m = fav_p = n_fav = 0
    for semana, grupo in prueba.groupby("semana"):
        previos = hist[hist["fecha"] < grupo["fecha"].min()]
        if len(previos) < min_partidos_previos:
            continue
        modelos = ajustar_modelos(previos)
        for _, f in grupo.iterrows():
            # football_data escribe las columnas en minúsculas (pin_x, pin_c_x)
            pin_abre = {k: _cuota(f.get(f"pin_{k.lower()}")) for k in ("1", "X", "2")}
            pin_cierre = [_cuota(f.get(f"pin_c_{k.lower()}")) for k in ("1", "X", "2")]
            ou_abre = {k: _cuota(f.get(f"pin_{k}")) for k in ("mas_2.5", "menos_2.5")}
            ou_cierre = {k: _cuota(f.get(f"pin_c_{k}")) for k in ("mas_2.5", "menos_2.5")}
            hay_1x2 = all(pin_abre.values())
            hay_ou = all(ou_abre.values())
            ref = {**(pin_abre if hay_1x2 else {}), **(ou_abre if hay_ou else {})}
            p = pronosticar(modelos, f["local"], f["visitante"], cuotas_pinnacle=ref or None,
                            confianza_datos="alta", peso_modelo=peso_modelo)
            real = "1" if f["goles_local"] > f["goles_visitante"] else "2" if f["goles_visitante"] > f["goles_local"] else "X"
            total = f["goles_local"] + f["goles_visitante"]
            pm = p.probabilidades["ganador"]
            vec = np.array([pm["1"], pm["X"], pm["2"]])
            y = np.array([real == k for k in ("1", "X", "2")], dtype=float)
            ll_m.append(-np.log(max(vec[y == 1][0], 1e-9)))
            br_m.append(float(np.sum((vec - y) ** 2)))
            for k, prob in pm.items():
                cal_p.append(prob)
                cal_y.append(int(real == k))
            if all(pin_cierre):
                pj = np.array(C.quitar_margen(pin_cierre))
                ll_p.append(-np.log(max(pj[y == 1][0], 1e-9)))
                br_p.append(float(np.sum((pj - y) ** 2)))
                # acierto del favorito: el más probable del modelo vs el de Pinnacle
                solo = p.prob_modelo["ganador"]
                n_fav += 1
                fav_m += int(max(solo, key=solo.get) == real)
                fav_p += int(("1", "X", "2")[int(np.argmin(pin_cierre))] == real)
            # córners y tarjetas: calibración de la línea más común
            for mercado, linea, col_l, col_v, acc_p, acc_y in (
                ("corners", 9.5, "corners_local", "corners_visitante", cor_p, cor_y),
                ("tarjetas", 4.5, "tarjetas_local", "tarjetas_visitante", tar_p, tar_y),
            ):
                if mercado in p.probabilidades and pd.notna(f.get(col_l)):
                    acc_p.append(p.probabilidades[mercado][f"mas_{linea}"])
                    acc_y.append(int(f[col_l] + f[col_v] > linea))
            # apuestas simuladas con la cuota de apertura de Pinnacle (un solo bank)
            candidatas = []
            if hay_1x2:
                candidatas += [("ganador", k, pm[k], pin_abre[k], pin_cierre[i], real == k)
                               for i, k in enumerate(("1", "X", "2"))]
            if hay_ou:
                pg = p.probabilidades["goles"]
                candidatas += [("goles", k, pg[k], ou_abre[k], ou_cierre[k], (total > 2.5) == (k == "mas_2.5"))
                               for k in ("mas_2.5", "menos_2.5")]
            for mercado, k, prob, cuota, cierre, gano in candidatas:
                s = V.Seleccion(p.partido, mercado, k, prob, confianza=p.confianza_base, cuota_ofrecida=cuota)
                pct = B.porcentaje_sencilla(s.ve(), s.confianza, s.ve_minimo)
                if pct <= 0:
                    continue
                monto = B.monto(bank_sim, pct)
                g = monto * (cuota - 1) if gano else -monto
                bank_sim += g
                for c in (cuentas[mercado], cuentas["total"]):
                    c.sumar(monto, cuota, gano, g)
                if cierre:
                    clv.append(cuota / cierre - 1)
    tot = cuentas["total"]
    return Resultado(
        n_partidos=len(ll_m),
        logloss_modelo=round(float(np.mean(ll_m)), 4) if ll_m else float("nan"),
        logloss_pinnacle=round(float(np.mean(ll_p)), 4) if ll_p else None,
        brier_modelo=round(float(np.mean(br_m)), 4) if br_m else float("nan"),
        brier_pinnacle=round(float(np.mean(br_p)), 4) if br_p else None,
        apuestas=tot.n,
        roi=round(tot.ganancia / tot.apostado, 4) if tot.apostado else None,
        clv_promedio=round(float(np.mean(clv)), 4) if clv else None,
        calibracion=_calibracion(cal_p, cal_y),
        calibracion_corners=_calibracion(cor_p, cor_y, 5),
        calibracion_tarjetas=_calibracion(tar_p, tar_y, 5),
        bank_final=round(bank_sim, 2),
        mercados={k: c.a_dict() for k, c in cuentas.items()},
        acierto_favorito={
            "partidos": n_fav,
            "modelo": round(fav_m / n_fav, 4) if n_fav else None,
            "pinnacle": round(fav_p / n_fav, 4) if n_fav else None,
        },
        peso_modelo=peso_modelo,
    )


def main(argv=None):
    from .datos import football_data
    ap = argparse.ArgumentParser()
    ap.add_argument("--liga", required=True, choices=sorted(football_data.LIGAS_PRINCIPALES))
    ap.add_argument("--desde", type=int, default=2022, help="primera temporada de prueba (año de inicio)")
    ap.add_argument("--hasta", type=int, default=2025, help="última temporada de prueba (año de inicio)")
    ap.add_argument("--previas", type=int, default=3, help="temporadas previas para entrenar")
    ap.add_argument("--peso-modelo", type=float, default=V.PESO_MODELO,
                    help="peso del modelo al mezclar con Pinnacle (0 a 1)")
    ap.add_argument("--salida")
    a = ap.parse_args(argv)
    temporadas = list(range(a.desde - a.previas, a.hasta + 1))
    hist = football_data.historial(a.liga, temporadas)
    res = correr(hist, desde=f"{a.desde}-07-01", hasta=f"{a.hasta + 1}-07-01",
                 peso_modelo=a.peso_modelo)
    texto = json.dumps(res.a_dict(), ensure_ascii=False, indent=2)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            fh.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
