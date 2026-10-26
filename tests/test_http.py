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


class _Ok:
    status_code = 200
    text = '{"a": 1}'


def test_cache_atomica(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(http.requests, "get", lambda *a, **k: _Ok())
    reemplazos = []
    real = http.os.replace
    monkeypatch.setattr(http.os, "replace", lambda a, b: reemplazos.append((a, b)) or real(a, b))
    assert http.obtener("https://x/y", horas_cache=1) == {"a": 1}
    archivos = list((tmp_path / "cache").iterdir())
    assert len(archivos) == 1 and not archivos[0].name.endswith(".tmp")
    assert len(reemplazos) == 1 and str(reemplazos[0][1]) == str(archivos[0])
    assert str(reemplazos[0][0]).startswith(str(tmp_path / "cache"))  # temporal en la misma carpeta
    # si falla la escritura no queda basura
    monkeypatch.setattr(http.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disco")))
    with pytest.raises(OSError):
        http.obtener("https://x/z", horas_cache=0)
    assert len(list((tmp_path / "cache").iterdir())) == 1
