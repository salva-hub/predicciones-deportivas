"""Categorías de apuesta: valor, sencilla fuerte y combinada (sin red)."""
from predicciones import bank as B
from predicciones import cuotas as C
from predicciones import valor as V


def _s(prob, americana, confianza="alta", partido="A vs B", mercado="ganador", seleccion="1"):
    return V.Seleccion(partido, mercado, seleccion, prob, 0.0, confianza, C.americana_a_decimal(americana))


def test_clasificacion_sencillas():
    # -120 con 62%: VE +13.7%, pero cumple fuerte -> fuerte (fuerte gana sobre valor)
    assert V.categoria_sencilla(_s(0.62, -120)) == V.FUERTE
    assert V.tiene_valor(_s(0.62, -120))
    # -160 con 62%: sin valor (VE +0.75%) pero dentro de la tolerancia -> fuerte
    assert V.categoria_sencilla(_s(0.62, -160)) == V.FUERTE
    # -170 con 62%: VE -1.5%, aún dentro del 4% -> fuerte
    assert V.categoria_sencilla(_s(0.62, -170)) == V.FUERTE
    # -200 con 62%: paga 7% menos que la justa -> nada
    assert V.categoria_sencilla(_s(0.62, -200)) is None
    # 58%: no llega a 60% -> nada (sin valor a -160); con valor a -120 -> valor
    assert V.categoria_sencilla(_s(0.58, -160)) is None
    assert V.categoria_sencilla(_s(0.58, -120)) == V.VALOR
    # confianza baja con valor -> valor
    assert V.categoria_sencilla(_s(0.62, -120, "baja")) == V.VALOR
    # confianza baja nunca es fuerte
    assert V.categoria_sencilla(_s(0.62, -160, "baja")) is None
    assert V.categoria_sencilla(_s(0.62, -160, "media")) == V.FUERTE
    # cuota positiva: nunca fuerte (con valor es valor)
    assert not V.es_sencilla_fuerte(_s(0.62, +110))
    assert V.categoria_sencilla(_s(0.62, +110)) == V.VALOR
    assert V.categoria_sencilla(_s(0.47, +105)) is None


def test_fuerte_solo_con_momio_de_menos_200_o_mejor():
    # 72%: justa -257; -150, -200 y -250 están dentro de la tolerancia
    assert V.categoria_sencilla(_s(0.72, -150)) == V.FUERTE
    assert V.es_sencilla_fuerte(_s(0.72, -200))
    s = _s(0.72, -250)
    assert V.dentro_de_tolerancia(s) and not V.es_sencilla_fuerte(s)
    assert V.fuerte_salvo_momio(s) and V.sirve_para_combinada(s)
    assert V.categoria_sencilla(s) is None
    # con valor y momio peor que -200 -> valor
    assert V.categoria_sencilla(_s(0.80, -250)) == V.VALOR
    # caso real 26-oct-2026: menos 3.5 goles a -434, 78.9%, confianza media -> no es fuerte
    r = _s(0.789, -434, "media", mercado="goles", seleccion="menos_3.5")
    assert not V.es_sencilla_fuerte(r) and V.fuerte_salvo_momio(r)
    assert V.categoria_sencilla(r) is None and V.sirve_para_combinada(r)


def test_tolerancia_exacta():
    s = _s(0.625, -100)  # justa 1.60
    s.cuota_ofrecida = 1.60 * 0.96
    assert V.dentro_de_tolerancia(s)
    s.cuota_ofrecida = 1.60 * 0.96 - 0.001
    assert not V.dentro_de_tolerancia(s)


def test_monto_para_ganar():
    assert B.monto_sencilla_fuerte(10_000, "alta", C.americana_a_decimal(-120)) == 600  # gana 500
    assert B.monto_sencilla_fuerte(10_000, "media", C.americana_a_decimal(-120)) == 300  # gana 250
    assert B.monto_sencilla_fuerte(10_000, "alta", C.americana_a_decimal(-150)) == 750
    assert B.monto_sencilla_fuerte(12_000, "alta", C.americana_a_decimal(-200)) == 1200
    assert B.monto_sencilla_fuerte(10_000, "baja", C.americana_a_decimal(-120)) == 0
    # se redondea hacia arriba: siempre gana al menos el objetivo
    m = B.monto_sencilla_fuerte(10_000, "media", C.americana_a_decimal(-110))
    assert m == 280 and m * (C.americana_a_decimal(-110) - 1) >= 250


def test_monto_combinada():
    assert B.monto(10_000, B.porcentaje_combinada("alta")) == 500
    assert B.monto(10_000, B.porcentaje_combinada("media")) == 300
    assert B.porcentaje_combinada("baja") == 0


def test_combinadas_ventana_de_cuota_y_patas():
    # dos patas a -110: +264 -> dentro
    (c,) = V.armar_combinadas([_s(0.6, -110, partido="A"), _s(0.6, -110, partido="B")])
    assert len(c.patas) == 2 and 3.0 <= c.cuota <= 4.5
    # dos patas a -150: +178 -> fuera; tres a -150: +363 -> fuera
    assert V.armar_combinadas([_s(0.62, -150, partido=x) for x in "AB"]) == []
    assert V.armar_combinadas([_s(0.62, -150, partido=x) for x in "ABC"]) == []
    # tres a -170: +300 -> dentro
    (c,) = V.armar_combinadas([_s(0.62, -170, partido=x) for x in "ABC"])
    assert len(c.patas) == 3
    # mismo partido: no se combina
    assert V.armar_combinadas([_s(0.6, -110, partido="A"), _s(0.6, -110, partido="A", mercado="goles")]) == []
    # pata con menos de 55%, con confianza baja o fuera de tolerancia: no entra
    assert V.armar_combinadas([_s(0.54, -110, partido="A"), _s(0.6, -110, partido="B")]) == []
    assert V.armar_combinadas([_s(0.6, -110, "baja", partido="A"), _s(0.6, -110, partido="B")]) == []
    # +200 con 60% está muy por debajo de su cuota justa? No: paga más (valor) -> sí entra
    assert V.armar_combinadas([_s(0.6, -400, partido="A"), _s(0.6, +200, partido="B")]) == []  # -400 fuera de tolerancia


def test_combinadas_orden_y_sin_repetir_patas():
    patas = [_s(0.58, -110, partido="A"), _s(0.62, -110, partido="B"), _s(0.60, -105, partido="C"),
             _s(0.62, -170, partido="D", confianza="media")]
    elegidas = V.armar_combinadas(patas)
    assert len(elegidas) == 2  # máximo 2 por día
    assert all(len(c.patas) == 2 for c in elegidas)  # se prefieren 2 patas
    usadas = [x.partido for c in elegidas for x in c.patas]
    assert len(usadas) == len(set(usadas))
    # mayor probabilidad conjunta primero: B+D (0.384), luego A+C (0.348)
    assert [{x.partido for x in c.patas} for c in elegidas] == [{"B", "D"}, {"A", "C"}]
    assert elegidas[0].confianza == "media" and elegidas[1].confianza == "alta"


def test_recorte_en_orden():
    assert B.ajustar_al_limite([600, 500, 500], 1000) == [600, 400, 0]
    assert B.ajustar_al_limite([300, 300], 1000) == [300, 300]
