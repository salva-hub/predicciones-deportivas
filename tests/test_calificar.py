from predicciones.calificar import calificar, estado_parlay

R = {"goles_local": 2, "goles_visitante": 1, "goles_local_pt": 1, "goles_visitante_pt": 1,
     "corners_local": 6, "corners_visitante": 4, "tarjetas_local": 3, "tarjetas_visitante": 2}


def test_mercados():
    assert calificar("ganador", "1", R) == "ganada"
    assert calificar("doble_oportunidad", "X2", R) == "perdida"
    assert calificar("goles", "mas_2.5", R) == "ganada"
    assert calificar("ambos_anotan", "si", R) == "ganada"
    assert calificar("goles_equipo", "visitante_menos_1.5", R) == "ganada"
    assert calificar("primer_tiempo", "pt_X", R) == "ganada"
    assert calificar("handicap", "local_-1", R) == "devuelta"
    assert calificar("handicap", "visitante_+0.5", R) == "perdida"
    assert calificar("corners", "mas_9.5", R) == "ganada"
    assert calificar("tarjetas", "menos_4.5", R) == "perdida"
    assert calificar("ganador", "1", {}) == "sin_datos"


def test_parlay():
    assert estado_parlay(["ganada", "devuelta"]) == "ganada"
    assert estado_parlay(["ganada", "perdida"]) == "perdida"


def test_parlay_perdido_aunque_falten_patas():
    assert estado_parlay(["perdida", "sin_datos"]) == "perdida"
    assert estado_parlay(["pendiente", "perdida"]) == "perdida"
    assert estado_parlay(["ganada", "sin_datos"]) == "sin_datos"
    assert estado_parlay(["ganada", "pendiente"]) == "sin_datos"
    assert estado_parlay(["devuelta", "devuelta"]) == "devuelta"


def test_tarjetas_regla_winpot():
    from predicciones.datos import tarjetas_winpot
    assert tarjetas_winpot(3, 1) == 5
    assert tarjetas_winpot(1, 1, 1) == 4     # otra amarilla + doble amarilla (1 + 3)
    assert tarjetas_winpot(None, 1) is None
    base = {"goles_local": 1, "goles_visitante": 0}
    # 2 amarillas + 1 roja directa = 4 tarjetas: más de 3.5 gana (antes contaba 3)
    r = {**base, "amarillas_local": 2, "rojas_local": 1, "amarillas_visitante": 0, "rojas_visitante": 0,
         "tarjetas_local": 3, "tarjetas_visitante": 0}
    assert calificar("tarjetas", "mas_3.5", r) == "ganada"
    r2 = {**r, "rojas_local": 0, "segundas_amarillas_local": 0}
    assert calificar("tarjetas", "mas_3.5", r2) == "perdida"
    # doble amarilla: ESPN da amarillas 0, rojas 1 → 3 tarjetas, la línea 3 se devuelve
    r3 = {**base, "amarillas_local": 0, "rojas_local": 1, "segundas_amarillas_local": 1,
          "amarillas_visitante": 0, "rojas_visitante": 0}
    assert calificar("tarjetas", "mas_3", r3) == "devuelta"
    # sin detalle se usa tarjetas_* tal cual
    assert calificar("tarjetas", "menos_4.5", {**base, "tarjetas_local": 2, "tarjetas_visitante": 2}) == "ganada"
