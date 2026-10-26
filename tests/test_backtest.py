import numpy as np

from predicciones.backtest import correr


def test_backtest_sintetico(liga_sintetica):
    df = liga_sintetica.copy()
    rng = np.random.default_rng(1)
    # cuotas de Pinnacle simuladas con 3% de margen alrededor de 1X2 fijo
    for k, base in (("1", 2.3), ("X", 3.4), ("2", 3.3)):
        df[f"pin_{k}"] = base * rng.uniform(0.95, 1.05, len(df))
        df[f"pin_c_{k}"] = base
    res = correr(df, desde=str(df["fecha"].iloc[200].date()), min_partidos_previos=150)
    assert res.n_partidos > 150
    assert res.logloss_modelo < 1.2
    assert res.calibracion and res.calibracion_corners
