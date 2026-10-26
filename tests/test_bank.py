from predicciones import bank as B


def test_montos_con_10000():
    assert B.monto(10_000, B.porcentaje_sencilla(0.08, "alta", 0.03)) == 500
    assert B.monto(10_000, B.porcentaje_sencilla(0.04, "alta", 0.03)) == 300
    assert B.monto(10_000, B.porcentaje_sencilla(0.04, "baja", 0.03)) == 200
    assert B.porcentaje_sencilla(0.02, "alta", 0.03) == 0
    assert B.monto(10_000, B.porcentaje_parlay("alta")) == 300
    assert B.monto(10_000, B.porcentaje_parlay("media")) == 200


def test_montos_crecen_con_el_bank():
    assert B.monto(15_000, B.porcentaje_sencilla(0.08, "alta", 0.03)) == 750


def test_limite_diario():
    assert B.limite_del_dia(10_000, 0) == 1000
    assert B.limite_del_dia(10_000, -500) == 1000
    assert B.limite_del_dia(10_000, 1000) == 1500
    assert B.limite_del_dia(10_000, 5000) == 2000


def test_pausa():
    assert B.en_pausa(5000, 10_000)
    assert not B.en_pausa(5100, 10_000)


def test_ajustar_al_limite():
    assert sum(B.ajustar_al_limite([500, 500, 300], 1000)) <= 1000
