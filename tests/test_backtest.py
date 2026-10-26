import numpy as np

from predicciones.backtest import correr


def test_backtest_sintetico(liga_sintetica):
    df = liga_sintetica.copy()
    rng = np.random.default_rng(1)
    # columnas con los nombres reales de football_data (pin_1, pin_x, pin_2, pin_c_*)
    for k, base in (("1", 2.3), ("x", 3.4), ("2", 3.3)):
        df[f"pin_{k}"] = base * rng.uniform(0.95, 1.05, len(df))
        df[f"pin_c_{k}"] = base
    # partidos sin goles (aplazados) no rompen la prueba
    df.loc[df.index[-5:], ["goles_local", "goles_visitante"]] = np.nan
    res = correr(df, desde=str(df["fecha"].iloc[200].date()), min_partidos_previos=150)
    assert res.n_partidos > 150
    assert res.logloss_modelo < 1.2
    assert res.logloss_pinnacle is not None  # sí lee las cuotas de Pinnacle
    assert res.apuestas > 0
    assert res.calibracion and res.calibracion_corners
