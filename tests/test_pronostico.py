import pytest

from predicciones import valor as V
from predicciones.ajustes import Ajustes, por_clima
from predicciones.pronostico import ajustar_modelos, pronosticar


def test_pronostico_completo(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    aj = Ajustes()
    aj.agregar("goles_visitante", 0.93, "Baja del delantero titular")
    por_clima({"lluvia_mm_h": 8, "viento_kmh": 10, "temperatura_c": 18}, techado=False, aj=aj)
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", aj, factor_arbitro=1.15,
                    cuotas_pinnacle={"1": 1.45, "X": 4.6, "2": 7.5, "mas_2.5": 1.8, "menos_2.5": 2.05})
    g = p.probabilidades["ganador"]
    assert sum(g.values()) == pytest.approx(1, abs=1e-6)
    assert g["1"] > 0.5
    assert "corners" in p.probabilidades and "tarjetas" in p.probabilidades
    assert any("Baja del delantero" in n for n in p.notas)
    tabla = p.tabla()
    assert all(f["cuota_minima"].startswith(("+", "-")) for f in tabla)
    # doble oportunidad no se ofrece si el favorito supera 55%
    assert not any(f["mercado"] == "doble_oportunidad" for f in tabla) or g["1"] < 0.55


def test_equipo_desconocido_baja_confianza(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    p = pronosticar(modelos, "Recien Ascendido", "Equipo 0", confianza_datos="alta")
    assert p.confianza_base == "media"


def test_parlays():
    s1 = V.Seleccion("A vs B", "ganador", "1", 0.60, confianza="alta", cuota_ofrecida=1.80)
    s2 = V.Seleccion("C vs D", "goles", "mas_2.5", 0.58, confianza="alta", cuota_ofrecida=1.85)
    s3 = V.Seleccion("A vs B", "goles", "mas_2.5", 0.55, confianza="media", cuota_ofrecida=1.90)
    ps = V.armar_parlays([s1, s2, s3])
    assert ps and len(ps[0].patas) == 2
    assert len({x.partido for x in ps[0].patas}) == 2
    assert ps[0].ve >= V.VE_MIN_PARLAY


def test_seleccion_handicap_con_devolucion():
    s = V.Seleccion("A vs B", "handicap", "local_-1", prob=0.45, prob_devolucion=0.2, cuota_ofrecida=2.0)
    assert s.ve() == pytest.approx(0.45 * 1 - 0.35)
    assert s.ve(s.cuota_minima()) == pytest.approx(s.ve_minimo)
