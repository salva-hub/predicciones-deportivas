import json


def _apuesta(R, monto, ganancia, estado="perdida"):
    a = R.agregar_apuesta({"tipo": "sencilla", "patas": [], "cuota": 2.0, "monto": monto})
    a.update(estado=estado, ganancia=ganancia, liquidada=R.ahora().isoformat())
    R.actualizar_apuesta(a)


def test_un_archivo_por_registro(registro_tmp, tmp_path):
    R = registro_tmp
    p = R.agregar_pronostico({"partido": "A vs B"})
    a = R.agregar_apuesta({"tipo": "sencilla", "patas": [], "monto": 100})
    assert (R.CARPETA / "pronosticos" / f"{p['id']}.json").exists()
    assert (R.CARPETA / "apuestas" / f"{a['id']}.json").exists()
    p["estado"] = "calificado"
    R.actualizar_pronostico(p)
    assert R.buscar_pronostico(p["id"])["estado"] == "calificado"
    assert R.buscar_pronostico("../bank") is None
    assert [x["id"] for x in R.pronosticos()] == [p["id"]]
    R.exportar(tmp_path / "_site")
    assert json.loads((tmp_path / "_site" / "pronosticos.json").read_text())[0]["id"] == p["id"]
    assert json.loads((tmp_path / "_site" / "apuestas.json").read_text())[0]["id"] == a["id"]


def test_retiro_no_activa_pausa(registro_tmp):
    R = registro_tmp
    R.agregar_movimiento("retiro", 6000)
    assert R.bank_actual() == 4000
    assert R.resultado_apuestas_mes() == 0
    assert R.base_mes() == 4000
    assert not R.en_pausa()


def test_perdidas_de_la_mitad_activan_pausa(registro_tmp):
    R = registro_tmp
    _apuesta(R, 3000, -3000)
    assert not R.en_pausa()
    _apuesta(R, 2000, -2000)
    assert R.resultado_apuestas_mes() == -5000
    assert R.en_pausa()


def test_deposito_no_oculta_pausa(registro_tmp):
    R = registro_tmp
    _apuesta(R, 5000, -5000)
    R.agregar_movimiento("deposito", 5000)
    # base = 10,000 + 5,000; perdió 5,000 (33%): aún no es pausa
    assert R.base_mes() == 15000
    assert not R.en_pausa()
    _apuesta(R, 2500, -2500)
    assert R.en_pausa()  # 7,500 de 15,000
