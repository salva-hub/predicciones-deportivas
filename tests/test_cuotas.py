import pytest

from predicciones import cuotas as C


@pytest.mark.parametrize("am,dec", [(200, 3.0), (-150, 1 + 100 / 150), (-110, 1 + 100 / 110), (100, 2.0)])
def test_conversion(am, dec):
    assert C.americana_a_decimal(am) == pytest.approx(dec)
    assert C.decimal_a_americana(dec) == am


def test_formato():
    assert C.formato_americano(3.0) == "+200"
    assert C.formato_americano(1.5) == "-200"


def test_quitar_margen_suma_uno():
    p = C.quitar_margen([2.10, 3.40, 3.60])
    assert sum(p) == pytest.approx(1, abs=1e-6)
    assert p[0] > p[1] > p[2]


def test_valor_y_cuota_minima():
    assert C.valor_esperado(0.5, 2.2) == pytest.approx(0.10)
    d = C.cuota_minima(0.62, 0.05)
    assert C.valor_esperado(0.62, d) == pytest.approx(0.05)
    assert C.formato_americano(d, minima=True) == "-144"
    assert C.formato_americano(2.239, minima=True) == "+124"
