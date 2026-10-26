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
    g = p.ganador_sin_pago  # 1X2 real; con pago anticipado "1" y "2" suman de más
    assert sum(g.values()) == pytest.approx(1, abs=1e-6)
    assert g["1"] > 0.5
    assert p.probabilidades["ganador"]["1"] > g["1"] and p.probabilidades["ganador"]["X"] == g["X"]
    assert "corners" in p.probabilidades and "tarjetas" in p.probabilidades
    assert any("Baja del delantero" in n for n in p.notas)
    tabla = p.tabla()
    assert all(f["cuota_minima"].startswith(("+", "-")) for f in tabla)
    # doble oportunidad no se ofrece si el favorito supera 55%
    assert not any(f["mercado"] == "doble_oportunidad" for f in tabla) or g["1"] < 0.55


def test_equipo_desconocido_baja_confianza(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    pin = {"1": 3.2, "X": 3.4, "2": 2.3}
    p = pronosticar(modelos, "Recien Ascendido", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
    assert p.confianza_base == "media"


def test_sin_pinnacle_solo_modelo_baja_confianza(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", confianza_datos="alta")
    assert p.confianza_base == "media"
    from predicciones.pronostico import NOTA_SIN_PINNACLE
    assert p.notas.count(NOTA_SIN_PINNACLE) == 1
    assert p.probabilidades["ganador"] == p.prob_modelo["ganador"]


def _pin(p1, px, p2, margen=1.05):
    return {"1": 1 / (p1 * margen), "X": 1 / (px * margen), "2": 1 / (p2 * margen)}


def test_anclaje_reproduce_pinnacle_1x2(liga_sintetica):
    from predicciones import cuotas as C
    modelos = ajustar_modelos(liga_sintetica)
    # el modelo ve favorito al local; Pinnacle ve un partido parejo
    pin = _pin(0.38, 0.29, 0.33)
    justo = C.quitar_margen([pin["1"], pin["X"], pin["2"]])
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
    g = p.ganador_sin_pago
    for k, q in zip(("1", "X", "2"), justo):
        assert g[k] == pytest.approx(q, abs=0.01)
    assert any("Base: Pinnacle sin margen" in n for n in p.notas)
    assert p.prob_modelo["ganador"]["1"] > g["1"] + 0.1  # el modelo queda como referencia
    assert p.probabilidades["doble_oportunidad"]["1X"] == pytest.approx(g["1"] + g["X"])
    # sin línea de goles: el total queda en el del modelo y bajan los mercados que dependen de él
    assert any("Sin línea de goles de Pinnacle" in n for n in p.notas)
    assert sum(p.goles_esperados) == pytest.approx(sum(_mu_modelo(p)), rel=0.08)
    assert {"goles", "ambos_anotan", "goles_equipo", "primer_tiempo", "handicap"} <= p.menos_confianza
    assert "ganador" not in p.menos_confianza
    assert _conf(p, "goles", "mas_2.5") != "alta"


def test_anclaje_con_mas_menos_25(liga_sintetica):
    from predicciones import cuotas as C
    modelos = ajustar_modelos(liga_sintetica)
    pin = {**_pin(0.45, 0.27, 0.28), "mas_2.5": 1 / (0.40 * 1.04), "menos_2.5": 1 / (0.60 * 1.04)}
    p = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=pin, confianza_datos="alta")
    pm = C.quitar_margen([pin["mas_2.5"], pin["menos_2.5"]])[0]
    assert p.probabilidades["goles"]["mas_2.5"] == pytest.approx(pm, abs=0.01)
    assert p.ganador_sin_pago["1"] == pytest.approx(0.45, abs=0.015)
    # con 1X2 y línea la matriz cuadra: hándicap 0 coincide con el ganador (sin pago anticipado)
    assert p.probabilidades["handicap"]["local_+0"] == pytest.approx(p.ganador_sin_pago["1"], abs=0.005)
    assert not any("no cuadra" in n for n in p.notas)


def _mu_modelo(p):
    nota = next(n for n in p.notas if n.startswith("Goles esperados del modelo solo"))
    return [float(x) for x in nota.split(": ")[1].split(" – ")]


def test_solo_1x2_gran_favorito_no_infla_goles(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    for c in ((1.25, 6.5, 12), (1.10, 11, 26)):
        pin = dict(zip(("1", "X", "2"), c))
        p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
        assert sum(p.goles_esperados) == pytest.approx(sum(_mu_modelo(p)), rel=0.12)
        assert sum(p.goles_esperados) < 4.0


def test_favorito_extremo_error_relativo_en_no_favorito(liga_sintetica):
    from predicciones import cuotas as C
    modelos = ajustar_modelos(liga_sintetica)
    pin = {"1": 1.05, "X": 17, "2": 41, "mas_2.5": 1.3, "menos_2.5": 3.6}
    justo = C.quitar_margen([1.05, 17, 41])
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
    assert p.ganador_sin_pago["2"] == pytest.approx(justo[2], rel=0.05)  # ~0.015, no 0.022
    assert p.prob_mercado["handicap"]["visitante_+0"] == pytest.approx(justo[2], rel=0.15)


def test_cuotas_contradictorias_bajan_confianza(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    # empate muy probable con más de 2.5 muy probable: no cuadra en ninguna matriz razonable
    pin = {"1": 2.1, "X": 2.6, "2": 4.0, "mas_2.5": 1.3, "menos_2.5": 3.6}
    p = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=pin, confianza_datos="alta")
    assert any("no cuadra" in n for n in p.notas)
    assert p.confianza_base == "media"


def test_ajustes_mueven_el_anclaje(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    pin = _pin(0.40, 0.28, 0.32)
    base = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=pin, confianza_datos="alta")
    aj = Ajustes()
    aj.agregar("goles_visitante", 0.90, "Baja tardía del delantero")
    p = pronosticar(modelos, "Equipo 6", "Equipo 5", aj, cuotas_pinnacle=pin, confianza_datos="alta")
    assert p.probabilidades["ganador"]["1"] > base.probabilidades["ganador"]["1"] + 0.01
    assert p.goles_esperados[1] == pytest.approx(base.goles_esperados[1] * 0.90, abs=0.011)


def test_peso_modelo_mezcla_en_escala_log(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    pin = _pin(0.38, 0.29, 0.33)
    ancla = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
    mitad = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta", peso_modelo=0.5)
    solo = ancla.prob_modelo["ganador"]["1"]
    assert ancla.probabilidades["ganador"]["1"] < mitad.probabilidades["ganador"]["1"] < solo


def test_conteos_encogidos_hacia_50():
    from predicciones.modelos.conteos import ENCOGER_CONTEOS, encoger
    assert ENCOGER_CONTEOS == 0.7
    assert encoger(0.83) == pytest.approx(0.731)
    assert encoger(0.35) == pytest.approx(0.395)
    assert encoger(0.5) == 0.5


def test_conteos_suman_uno(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    c = modelos.corners.mercados(10.0, (7.5, 9.5, 11.5))
    for l in (7.5, 9.5, 11.5):
        assert c[f"mas_{l}"] + c[f"menos_{l}"] == pytest.approx(1)
    assert 0.15 < c["mas_11.5"] and c["mas_7.5"] < 0.85


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


def test_sin_handicap_de_media_linea(liga_sintetica):
    p = pronosticar(ajustar_modelos(liga_sintetica), "Equipo 11", "Equipo 0", confianza_datos="alta")
    assert not any(k.endswith(("+0.5", "-0.5")) for k in p.probabilidades["handicap"])
    assert "local_-1.5" in p.probabilidades["handicap"]


def _conf(p, mercado, nombre):
    return next(s.confianza for s in p.selecciones() if s.mercado == mercado and s.seleccion == nombre)


def test_desacuerdo_con_pinnacle_baja_mercados_relacionados(liga_sintetica):
    from predicciones import cuotas as C
    modelos = ajustar_modelos(liga_sintetica)
    base = pronosticar(modelos, "Equipo 11", "Equipo 0", confianza_datos="alta")
    pm = base.prob_modelo
    # Pinnacle de acuerdo con el modelo: no se baja nada
    igual = {k: 1 / pm["ganador"][k] for k in ("1", "X", "2")}
    igual.update({"mas_2.5": 1 / pm["goles"]["mas_2.5"], "menos_2.5": 1 / pm["goles"]["menos_2.5"]})
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=igual, confianza_datos="alta")
    assert not p.desacuerdos
    assert _conf(p, "handicap", "local_-1.5") == "alta"
    # Pinnacle ve favorito al visitante y pocos goles: discrepan mucho
    contrario = {"1": C.americana_a_decimal(+400), "X": C.americana_a_decimal(+300), "2": C.americana_a_decimal(-150),
                 "mas_2.5": C.americana_a_decimal(+150), "menos_2.5": C.americana_a_decimal(-180)}
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=contrario, confianza_datos="alta")
    assert p.desacuerdos == {"1x2", "goles_2.5"}
    assert _conf(p, "handicap", "local_-1.5") == "media"
    assert _conf(p, "goles_equipo", "local_mas_1.5") == "media"
    assert _conf(p, "goles", "mas_3.5") == "media"
    assert _conf(p, "ambos_anotan", "si") == "media"


def test_cancha_neutral_quita_ventaja_local(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    casa = pronosticar(modelos, "Equipo 5", "Equipo 6", confianza_datos="alta")
    neutral = pronosticar(modelos, "Equipo 5", "Equipo 6", confianza_datos="alta", neutral=True)
    assert neutral.goles_esperados[0] < casa.goles_esperados[0]
    assert neutral.goles_esperados[1] == casa.goles_esperados[1]
    assert any("neutral" in n for n in neutral.notas)


def test_handicap_cero_coherente_con_ganador(liga_sintetica):
    modelos = ajustar_modelos(liga_sintetica)
    pin = _pin(0.38, 0.29, 0.33)  # solo 1X2: la matriz no cuadra exacto
    p = pronosticar(modelos, "Equipo 11", "Equipo 0", cuotas_pinnacle=pin, confianza_datos="alta")
    # el hándicap 0 (empate no apuesta) no recibe el pago anticipado: va con el 1X2 real
    g, hc = p.ganador_sin_pago, p.probabilidades["handicap"]
    assert hc["local_+0"] == pytest.approx(g["1"]) and hc["visitante_+0"] == pytest.approx(g["2"])
    assert p.devoluciones["handicap:local_+0"] == pytest.approx(g["X"])
    s = next(x for x in p.selecciones() if x.mercado == "handicap" and x.seleccion == "local_+0")
    assert s.prob + s.prob_devolucion + s.prob_pierde == pytest.approx(1)
    assert s.prob_pierde == pytest.approx(g["2"])
