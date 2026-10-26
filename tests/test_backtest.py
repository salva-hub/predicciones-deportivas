import json

import numpy as np

from predicciones.backtest import _cuota, correr


def _con_cuotas(df):
    rng = np.random.default_rng(1)
    # columnas con los nombres reales de football_data (pin_1, pin_x, pin_2, pin_c_*)
    for k, base in (("1", 2.3), ("x", 3.4), ("2", 3.3), ("mas_2.5", 1.95), ("menos_2.5", 1.95)):
        df[f"pin_{k}"] = base * rng.uniform(0.9, 1.1, len(df))
        df[f"pin_c_{k}"] = base
    return df


def test_backtest_sintetico(liga_sintetica):
    df = _con_cuotas(liga_sintetica.copy())
    # partidos sin goles (aplazados) no rompen la prueba
    df.loc[df.index[-5:], ["goles_local", "goles_visitante"]] = np.nan
    # football-data a veces trae cuotas en 0: cuentan como faltantes
    df.loc[df.index[250:255], "pin_1"] = 0
    df.loc[df.index[256:260], "pin_c_x"] = 0
    df.loc[df.index[261:265], "pin_mas_2.5"] = 1.0
    res = correr(df, desde=str(df["fecha"].iloc[200].date()), min_partidos_previos=150)
    assert res.n_partidos > 150
    assert res.logloss_modelo < 1.2
    assert res.logloss_pinnacle is not None  # sí lee las cuotas de Pinnacle
    assert res.apuestas > 0
    assert res.calibracion and res.calibracion_corners
    m = res.mercados
    assert m["ganador"]["apuestas"] > 0 and m["goles"]["apuestas"] > 0
    assert m["total"]["apuestas"] == m["ganador"]["apuestas"] + m["goles"]["apuestas"] == res.apuestas
    assert 0 <= m["total"]["acierto"] <= 1 and m["total"]["cuota_promedio"] > 1
    assert abs(res.bank_final - (10_000 + m["total"]["ganancia"])) < 0.05
    fav = res.acierto_favorito
    assert fav["partidos"] > 0 and 0 <= fav["modelo"] <= 1 and 0 <= fav["pinnacle"] <= 1
    json.dumps(res.a_dict())  # se puede guardar como JSON


def test_peso_modelo_cambia_las_apuestas(liga_sintetica):
    df = _con_cuotas(liga_sintetica.copy())
    desde = str(df["fecha"].iloc[200].date())
    solo_pinnacle = correr(df, desde=desde, min_partidos_previos=150, peso_modelo=0.0)
    # con peso 0 la probabilidad es la de Pinnacle sin margen: casi nunca hay valor
    normal = correr(df, desde=desde, min_partidos_previos=150)
    assert solo_pinnacle.peso_modelo == 0.0
    assert solo_pinnacle.apuestas < normal.apuestas


def test_cuota_invalida():
    assert _cuota(0) is None and _cuota(1.0) is None and _cuota(float("nan")) is None and _cuota(None) is None
    assert _cuota("2.1") == 2.1
