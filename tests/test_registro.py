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


def test_calibracion(registro_tmp, tmp_path):
    """De cada pronóstico y línea solo cuenta el lado más probable (elegido antes de calificar)."""
    R = registro_tmp
    sel = lambda m, s, prob, **k: {"mercado": m, "seleccion": s, "prob": prob, "confianza": "alta", **k}
    # 3-1, el local fue 2 arriba
    R.agregar_pronostico({"partido": "A vs B", "pago_anticipado": True, "estado": "calificado",
        "resultado": {"goles_local": 3, "goles_visitante": 1, "ventaja_max_local": 2, "ventaja_max_visitante": 0},
        "selecciones": [sel("ganador", "1", 0.55), sel("ganador", "X", 0.2), sel("ganador", "2", 0.25),  # cuenta "1"
                        sel("goles", "mas_2.5", 0.6), sel("goles", "menos_2.5", 0.4),     # cuenta más (gana)
                        sel("goles", "mas_3.5", 0.35), sel("goles", "menos_3.5", 0.65),   # cuenta menos (pierde)
                        # misma línea vista desde el local (−2 / +2): el favorito queda devuelto y no cuenta
                        sel("handicap", "local_-2", 0.3, devolucion=0.2), sel("handicap", "visitante_+2", 0.5, devolucion=0.2),
                        sel("handicap", "visitante_+0.5", 0.3), sel("handicap", "local_-0.5", 0.7),
                        sel("goles_equipo", "local_mas_1.5", 0.45), sel("goles_equipo", "local_menos_1.5", 0.55),
                        sel("goles_equipo", "visitante_mas_0.5", 0.6),
                        sel("tiros_jugador", "Juan Perez:mas_0.5", 0.5)]})  # sin datos de tiros: no cuenta
    # 2-3 pero el local fue 2-0 arriba: con pago anticipado el "1" gana
    R.agregar_pronostico({"partido": "C vs D", "pago_anticipado": True, "estado": "calificado",
        "resultado": {"goles_local": 2, "goles_visitante": 3, "ventaja_max_local": 2, "ventaja_max_visitante": 1},
        "selecciones": [sel("ganador", "1", 0.5), sel("ganador", "X", 0.2), sel("ganador", "2", 0.35)]})
    R.agregar_pronostico({"partido": "E vs F", "estado": "pendiente", "selecciones": [sel("goles", "mas_2.5", 0.9)]})
    c = R.calibracion()
    m = c["mercados"]
    assert (m["ganador"]["n"], m["ganador"]["prob_media"], m["ganador"]["acierto"]) == (2, 0.525, 1.0)
    assert abs(m["ganador"]["brier"] - (0.45 ** 2 + 0.5 ** 2) / 2) < 1e-4
    assert (m["goles"]["n"], m["goles"]["prob_media"], m["goles"]["acierto"]) == (2, 0.625, 0.5)
    assert (m["handicap"]["n"], m["handicap"]["prob_media"], m["handicap"]["acierto"]) == (1, 0.7, 1.0)
    assert (m["goles_equipo"]["n"], m["goles_equipo"]["acierto"]) == (2, 0.5)
    assert m["tiros_jugador"]["n"] == 0 and m["tarjetas"]["prob_media"] is None
    assert c["total"]["n"] == 7
    assert len(c["rangos"]) == 10
    assert [(r["n"], r["acierto"]) for r in c["rangos"][5:8]] == [(3, 0.6667), (3, 0.6667), (1, 1.0)]
    R.exportar(tmp_path / "_site")
    assert json.loads((tmp_path / "_site" / "calibracion.json").read_text())["total"]["n"] == 7


def test_calibracion_quita_la_devolucion(registro_tmp):
    R = registro_tmp
    R.agregar_pronostico({"partido": "A vs B", "resultado": {"goles_local": 1, "goles_visitante": 0},
        "selecciones": [{"mercado": "handicap", "seleccion": "local_0", "prob": 0.4, "devolucion": 0.2},
                        {"mercado": "tarjetas", "seleccion": "menos_7.5", "prob": None}]})  # sin prob: no cuenta
    c = R.calibracion()
    assert c["mercados"]["handicap"]["prob_media"] == 0.5  # 0.4 / (1 − 0.2)
    assert c["total"]["n"] == 1


def test_parlay_de_salva_con_pata_sin_prob(registro_tmp, tmp_path):
    R = registro_tmp
    R.guardar_parlays_papel(R.ahora().date(), [{"clave": "k", "tipo": "parlay", "categoria": "eleccion_salva",
        "patas": [{"pronostico": "P-x", "mercado": "tarjetas", "seleccion": "menos_7.5", "cuota": 1.5, "prob": None,
                   "estado": "ganada"}],
        "cuota": 1.5, "prob": None, "monto": 100, "estado": "ganada", "ganancia": 50, "creado": R.ahora().isoformat()}])
    assert R.resumen_papel()["eleccion_salva"]["ganancia"] == 50
    R.exportar(tmp_path / "_site")
    assert json.loads((tmp_path / "_site" / "parlays.json").read_text())[0]["categoria"] == "Elección de Salva"
