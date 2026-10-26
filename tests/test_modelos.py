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
