import numpy as np
import pytest

from predicciones.modelos.goles import ajustar_modelo_goles, matriz_marcadores, mercados_de_goles, resultado_handicap
from predicciones.modelos.conteos import ajustar_modelo_conteo, factor_arbitro


def test_matriz_suma_uno_y_mercados_coherentes():
    m = matriz_marcadores(1.6, 1.1, -0.05)
    g = mercados_de_goles(m)
    assert m.sum() == pytest.approx(1)
    assert g["1"] + g["X"] + g["2"] == pytest.approx(1)
    assert g["mas_2.5"] + g["menos_2.5"] == pytest.approx(1)
    assert g["1"] > g["2"]
    gana, dev, pierde = resultado_handicap(m, -1.0, True)
    assert gana + dev + pierde == pytest.approx(1)
    assert resultado_handicap(m, -0.5, True)[0] == pytest.approx(g["1"])


def test_recupera_fuerzas(liga_sintetica):
    mod = ajustar_modelo_goles(liga_sintetica, vida_media_dias=10_000, ridge=0.5)
    at = [mod.fuerzas.ataque[f"Equipo {i}"] for i in range(12)]
    assert np.corrcoef(at, np.linspace(-0.4, 0.4, 12))[0, 1] > 0.9
    assert mod.fuerzas.ventaja_local == pytest.approx(0.25, abs=0.1)
    assert -0.2 <= mod.rho <= 0.2


def test_conteos(liga_sintetica):
    c = ajustar_modelo_conteo(liga_sintetica, "corners_local", "corners_visitante", "corners", vida_media_dias=10_000)
    mu = sum(c.esperados("Equipo 11", "Equipo 0"))
    assert 8 < mu < 13
    p = c.mercados(mu, (9.5,))
    assert 0 < p["mas_9.5"] < 1
    t = ajustar_modelo_conteo(liga_sintetica, "tarjetas_local", "tarjetas_visitante", "tarjetas", vida_media_dias=10_000)
    assert t.dispersion_k < 100  # detecta sobredispersión


def test_factor_arbitro():
    assert factor_arbitro(5.4, 30, 4.6) == pytest.approx(1 + 0.75 * (5.4 / 4.6 - 1))
    assert factor_arbitro(None, 0, 4.6) == 1.0


def test_conteos_sin_partidos_sin_estadisticas(liga_sintetica):
    """Los partidos que ESPN publica sin estadísticas (0 tiros) no cuentan como 0 córners."""
    from predicciones.modelos.conteos import filas_con_estadisticas
    h = liga_sintetica.assign(tiros_local=10, tiros_visitante=10)
    sucio = h.copy()
    idx = sucio.index[::10]
    sucio.loc[idx, ["corners_local", "corners_visitante", "tiros_local", "tiros_visitante"]] = 0
    assert len(filas_con_estadisticas(sucio)) == len(h) - len(idx)
    limpio = ajustar_modelo_conteo(h.drop(idx), "corners_local", "corners_visitante", "corners", vida_media_dias=10_000)
    filtrado = ajustar_modelo_conteo(sucio, "corners_local", "corners_visitante", "corners", vida_media_dias=10_000)
    assert sum(filtrado.esperados("Equipo 3", "Equipo 4")) == pytest.approx(sum(limpio.esperados("Equipo 3", "Equipo 4")))


def test_conteos_recalibrados_hacia_frecuencia_de_la_liga():
    """Con frecuencia de la liga ya no se encoge hacia 50%: el lado raro no se infla."""
    from predicciones.modelos.conteos import RECAL_CONTEOS, recalibrar_conteo, encoger
    params = RECAL_CONTEOS["corners"]
    # liga donde "más de 7.5" pasa 74%; el modelo da 77%
    p = recalibrar_conteo(0.77, 0.74, params)
    assert 0.72 < p < 0.80
    assert 1 - p < 0.30 < 1 - encoger(0.77)  # menos 7.5 ya no sube a ~31%
    # la línea más común queda cerca de lo que dice el modelo
    assert recalibrar_conteo(0.5, 0.5, params) == pytest.approx(0.5, abs=0.01)


def test_conteos_mercados_usan_frecuencia_de_la_liga(liga_sintetica):
    import numpy as np
    c = ajustar_modelo_conteo(liga_sintetica, "corners_local", "corners_visitante", "corners", vida_media_dias=10_000)
    assert c.totales_liga is not None and len(c.totales_liga) >= 50
    from predicciones.modelos.conteos import RECAL_CONTEOS, recalibrar_conteo, encoger
    mu = sum(c.esperados("Equipo 11", "Equipo 0"))
    p = c.mercados(mu, (7.5,))["mas_7.5"]
    assert p == pytest.approx(recalibrar_conteo(c.prob_mas(mu, 7.5), c.frecuencia_liga(7.5), RECAL_CONTEOS["corners"]))
    assert c.frecuencia_liga(7.5) == pytest.approx(float(np.mean(c.totales_liga > 7.5)))
    # sin frecuencia de la liga, como antes
    c.totales_liga = None
    assert c.mercados(mu, (7.5,))["mas_7.5"] == pytest.approx(encoger(c.prob_mas(mu, 7.5)))


def test_conteos_frecuencia_solo_con_partidos_previos(liga_sintetica):
    import numpy as np
    import pandas as pd
    fechas = pd.to_datetime(liga_sintetica["fecha"])
    ref = fechas.sort_values().iloc[len(fechas) // 2]
    c = ajustar_modelo_conteo(liga_sintetica, "corners_local", "corners_visitante", "corners", referencia=ref)
    previos = liga_sintetica[(fechas < ref) & (fechas >= ref - pd.Timedelta(days=365))]
    esperado = (previos["corners_local"] + previos["corners_visitante"]).to_numpy()
    assert sorted(c.totales_liga) == sorted(esperado)


def test_conteos_con_pocos_partidos_encoge_hacia_50(liga_sintetica):
    from predicciones.modelos.conteos import MIN_PARTIDOS_FRECUENCIA, encoger
    c = ajustar_modelo_conteo(liga_sintetica, "corners_local", "corners_visitante", "corners", vida_media_dias=10_000)
    c.totales_liga = c.totales_liga[: MIN_PARTIDOS_FRECUENCIA - 1]
    mu = 10.0
    assert c.mercados(mu, (9.5,))["mas_9.5"] == pytest.approx(encoger(c.prob_mas(mu, 9.5)))


def test_promedio_tarjetas_sin_partidos_sin_estadisticas(liga_sintetica):
    from predicciones.pronostico import ajustar_modelos
    h = liga_sintetica.assign(tiros_local=10, tiros_visitante=10)
    sucio = h.copy()
    idx = sucio.index[::10]
    sucio.loc[idx, ["tarjetas_local", "tarjetas_visitante", "tiros_local", "tiros_visitante"]] = 0
    limpio = h.drop(idx)
    assert ajustar_modelos(sucio).promedio_tarjetas == pytest.approx(
        float((limpio["tarjetas_local"] + limpio["tarjetas_visitante"]).mean()))
