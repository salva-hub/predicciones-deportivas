import pytest

from predicciones import registro as R
from predicciones import valor as V
from predicciones.calificar import DEVUELVE, GANA, PIERDE, SIN_DATOS, calificar
from predicciones.modelos.conteos import (
    esperados_favorito,
    recalibrar_1x2,
    tarjetas_1x2,
    tarjetas_1x2_crudo,
)
from predicciones.pronostico import ajustar_modelos, pronosticar


def test_crudo_suma_uno_y_simetrico():
    p = tarjetas_1x2_crudo(2.0, 2.0)
    assert sum(p) == pytest.approx(1)
    assert p[0] == pytest.approx(p[2])
    assert 0.18 < p[1] < 0.22  # dos Poisson independientes: empate bajo


def test_recalibrado_sube_el_empate_y_encoge():
    crudo = tarjetas_1x2_crudo(2.3, 1.7)
    p = recalibrar_1x2(crudo)
    assert sum(p) == pytest.approx(1)
    assert p[1] > crudo[1] + 0.03          # el empate sube a ~24-26%
    assert p[0] > p[2]                     # el orden se mantiene
    assert p[0] / p[2] < crudo[0] / crudo[2]  # pero menos extremo (s < 1)


def test_favorito_recibe_menos():
    ml, mv = esperados_favorito(2.0, 2.0, 0.6, 0.15)
    assert ml < 2.0 < mv
    assert esperados_favorito(2.0, 2.0, None, None) == (2.0, 2.0)
    p = tarjetas_1x2(2.0, 2.0, 0.6, 0.15)
    assert p["2"] > p["1"]


def test_pronostico_incluye_tarjetas_1x2(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle={"1": 1.45, "X": 4.6, "2": 7.5})
    t = p.probabilidades["tarjetas_1x2"]
    assert set(t) == {"1", "X", "2"} and sum(t.values()) == pytest.approx(1)
    filas = [f for f in p.tabla() if f["mercado"] == "tarjetas_1x2"]
    assert len(filas) == 3
    assert V.familia("tarjetas_1x2") == "conteos"  # VE mínimo 5%, como córners y tarjetas


@pytest.mark.parametrize("tl,tv,sel,esperado", [
    (3, 1, "1", GANA), (3, 1, "2", PIERDE), (2, 2, "X", GANA), (1, 4, "2", GANA), (1, 4, "X", PIERDE),
])
def test_calificar(tl, tv, sel, esperado):
    r = {"goles_local": 1, "goles_visitante": 0, "tarjetas_local": tl, "tarjetas_visitante": tv}
    assert calificar("tarjetas_1x2", sel, r) == esperado


def test_calificar_cuenta_como_winpot_y_sin_datos():
    # local: 2 amarillas + 1 roja directa = 4; visitante: 4 amarillas = 4 -> mismas
    r = {"goles_local": 0, "goles_visitante": 0, "amarillas_local": 2, "rojas_local": 1,
         "amarillas_visitante": 4, "rojas_visitante": 0}
    assert calificar("tarjetas_1x2", "X", r) == GANA
    assert calificar("tarjetas_1x2", "1", {"goles_local": 0, "goles_visitante": 0}) == SIN_DATOS
    con_prorroga = {"goles_local": 1, "goles_visitante": 1, "tarjetas_local": 3, "tarjetas_visitante": 1, "prorroga": True}
    assert calificar("tarjetas_1x2", "1", con_prorroga) == SIN_DATOS
    assert DEVUELVE != calificar("tarjetas_1x2", "1", {"goles_local": 0, "goles_visitante": 0,
                                                       "tarjetas_local": 1, "tarjetas_visitante": 0})


def test_texto_y_calibracion():
    assert R.texto_seleccion("Torino vs Como", "tarjetas_1x2", "1") == "Más tarjetas: Torino"
    assert R.texto_seleccion("Torino vs Como", "tarjetas_1x2", "X") == "Mismas tarjetas los dos equipos"
    assert "tarjetas_1x2" in R.MERCADOS_CALIBRACION
    # 1, X y 2 son un solo grupo: en calibración solo cuenta el más probable
    assert R._grupo_calibracion("tarjetas_1x2", "1") == R._grupo_calibracion("tarjetas_1x2", "2")
