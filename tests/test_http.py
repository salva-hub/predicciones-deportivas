import pytest

from predicciones.datos import http


class _Resp:
    status_code = 401
    text = "no autorizado"


def test_error_no_muestra_la_clave(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CACHE", tmp_path)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError) as e:
        http.obtener("https://api.the-odds-api.com/v4/sports/soccer_epl/odds", {"apiKey": "SECRETO123"}, horas_cache=0)
    texto = str(e.value)
    assert "SECRETO123" not in texto and "apiKey" not in texto
    assert "api.the-odds-api.com/v4/sports/soccer_epl/odds" in texto and "401" in texto
    assert e.value.__cause__ is None


def test_error_de_conexion_no_muestra_la_clave(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CACHE", tmp_path)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    def falla(url, params=None, **k):
        raise http.requests.ConnectionError(f"Max retries exceeded with url: {url}?apiKey={params['apiKey']}")

    monkeypatch.setattr(http.requests, "get", falla)
    with pytest.raises(RuntimeError) as e:
        http.obtener("https://api.the-odds-api.com/v4/x", {"apiKey": "SECRETO123"}, horas_cache=0)
    assert "SECRETO123" not in str(e.value)


def test_limpiar():
    assert "SECRETO" not in http.limpiar("fallo en https://h/x?apiKey=SECRETO&regions=eu")
