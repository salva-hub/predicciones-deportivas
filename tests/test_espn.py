from predicciones.datos import espn


def _ev(nombre_estado, local, visitante, ls_local, ls_visit, id_="123", fecha="2026-10-20T19:00Z"):
    def comp(lado, nombre, score, ls):
        return {"homeAway": lado, "score": str(score), "team": {"displayName": nombre},
                "linescores": [{"value": v} for v in ls]}
    return {
        "id": id_, "date": fecha,
        "status": {"type": {"name": nombre_estado, "completed": True}},
        "competitions": [{"competitors": [comp("home", "Toluca", local, ls_local),
                                          comp("away", "America", visitante, ls_visit)]}],
    }


def test_partido_normal():
    p = espn._partido(_ev("STATUS_FULL_TIME", 2, 1, [1, 1], [0, 1]), "liga_mx")
    assert (p["goles_local"], p["goles_visitante"]) == (2, 1)
    assert (p["goles_local_pt"], p["goles_visitante_pt"]) == (1, 0)
    assert "prorroga" not in p


def test_prorroga_usa_90_minutos():
    # 1-1 en 90', 2-1 tras la prórroga
    p = espn._partido(_ev("STATUS_FINAL_AET", 2, 1, [0, 1, 1, 0], [1, 0, 0, 0]), "liga_mx")
    assert p["prorroga"] is True
    assert (p["goles_local"], p["goles_visitante"]) == (1, 1)


def test_penales_sin_lineas_no_califica():
    ev = _ev("STATUS_FINAL_PEN", 1, 1, [], [])
    p = espn._partido(ev, "liga_mx")
    assert p["prorroga"] is True
    assert p["goles_local"] is None and p["goles_visitante"] is None


def test_resultado_desde_summary(monkeypatch):
    ev = _ev("STATUS_FULL_TIME", 3, 0, [2, 1], [0, 0])
    comp = dict(ev["competitions"][0], date=ev["date"], status=ev["status"])
    resumen = {
        "header": {"id": "123", "competitions": [comp]},
        "boxscore": {"teams": [
            {"homeAway": "home", "statistics": [{"name": "wonCorners", "displayValue": "7"},
                                                {"name": "yellowCards", "displayValue": "2"}]},
            {"homeAway": "away", "statistics": [{"name": "wonCorners", "displayValue": "3"},
                                                {"name": "yellowCards", "displayValue": "1"},
                                                {"name": "redCards", "displayValue": "1"}]},
        ]},
    }
    monkeypatch.setattr(espn, "obtener", lambda url, params=None, **k: resumen)
    r = espn.resultado("liga_mx", "123", "2026-10-20T19:00:00+00:00")
    assert r["terminado"] and r["goles_local"] == 3 and r["goles_visitante"] == 0
    # Winpot: amarilla 1 + roja 2
    assert r["corners_local"] == 7 and r["tarjetas_visitante"] == 3 and r["tarjetas_local"] == 2


def test_resultado_cae_al_calendario(monkeypatch):
    llamadas = []

    def falso(url, params=None, **k):
        llamadas.append((url, params))
        if url.endswith("/summary"):
            if len(llamadas) == 1:
                raise RuntimeError("caído")
            return {}
        return {"events": [_ev("STATUS_FULL_TIME", 0, 2, [0, 0], [1, 1])]}

    monkeypatch.setattr(espn, "obtener", falso)
    r = espn.resultado("liga_mx", "123", "2026-10-20T19:00:00+00:00")
    assert r["goles_visitante"] == 2
    cal = next(p for u, p in llamadas if u.endswith("/scoreboard"))
    assert cal["dates"] == "202610"


def test_historial_sigue_si_falla_una_estadistica(monkeypatch):
    monkeypatch.setattr(espn, "calendario", lambda liga, a, b: [espn._partido(_ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0]), liga)])

    def falla(*a, **k):
        raise RuntimeError("caído")

    monkeypatch.setattr(espn, "estadisticas", falla)
    from datetime import date
    df = espn.historial("liga_mx", date(2026, 10, 1), date(2026, 10, 20))
    assert len(df) == 1


def _falso_scoreboard(eventos_por_fecha, llamadas):
    def falso(url, params=None, **k):
        llamadas.append(params["dates"])
        return {"events": eventos_por_fecha.get(params["dates"], [])}
    return falso


def test_calendario_por_meses_filtra_hora_del_este(monkeypatch):
    from datetime import date
    ev = lambda i, f: _ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0], id_=i, fecha=f)
    eventos = {
        # 2025-01-11 01:00Z es 2025-01-10 en el Este: entra
        "202501": [ev("a", "2025-01-11T01:00Z"), ev("b", "2025-01-12T20:00Z"), ev("c", "2025-01-31T20:00Z")],
        # 2025-02-01 03:00Z es 2025-01-31 en el Este: entra; se repite "c" (sin duplicar)
        "202502": [ev("c", "2025-01-31T20:00Z"), ev("d", "2025-02-01T03:00Z"), ev("e", "2025-02-01T20:00Z")],
        "202412": [ev("f", "2024-12-31T20:00Z")],
    }
    llamadas = []
    monkeypatch.setattr(espn, "obtener", _falso_scoreboard(eventos, llamadas))
    ps = espn.calendario("liga_mx", date(2025, 1, 1), date(2025, 1, 31))
    assert [p["espn_id"] for p in ps] == ["a", "b", "c", "d"]
    assert llamadas == ["202412", "202501", "202502"]
    # el día del Este manda: el 10 de enero incluye el partido de las 01:00Z del 11
    assert [p["espn_id"] for p in espn.calendario("liga_mx", date(2025, 1, 10), date(2025, 1, 10))] == ["a"]


def test_calendario_un_dia_sin_cambios(monkeypatch):
    from datetime import date
    llamadas = []
    monkeypatch.setattr(espn, "obtener", _falso_scoreboard({"20250110": [_ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0])]}, llamadas))
    assert len(espn.calendario("liga_mx", date(2025, 1, 10))) == 1
    assert llamadas == ["20250110"]


def test_calendario_mes_lleno_pide_dia_por_dia(monkeypatch):
    from datetime import date
    lleno = [_ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0], id_=f"x{i}", fecha="2025-03-15T18:00Z") for i in range(espn.TOPE)]
    eventos = {"202503": lleno, "20250315": [_ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0], id_="z", fecha="2025-03-15T18:00Z")]}
    llamadas = []
    monkeypatch.setattr(espn, "obtener", _falso_scoreboard(eventos, llamadas))
    ps = espn.calendario("liga_mx", date(2025, 3, 10), date(2025, 3, 20))
    assert [p["espn_id"] for p in ps] == ["z"]
    assert "20250309" in llamadas and "20250321" in llamadas and "20250308" not in llamadas


def test_historial_por_mes_y_arbitro(monkeypatch):
    from datetime import date
    ev = lambda i, f: _ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0], id_=i, fecha=f)
    eventos = {"202501": [ev("a", "2025-01-15T20:00Z")], "202502": [ev("b", "2025-02-15T20:00Z")]}
    resumen = {
        "boxscore": {"teams": [
            {"homeAway": "home", "statistics": [{"name": "wonCorners", "displayValue": "5"}]},
            {"homeAway": "away", "statistics": [{"name": "wonCorners", "displayValue": "4"}]}]},
        "gameInfo": {"officials": [{"displayName": "Juez Línea", "position": {"name": "Assistant Referee"}},
                                   {"displayName": "César Ramos", "position": {"name": "Referee"}}]},
    }

    def falso(url, params=None, **k):
        if url.endswith("/summary"):
            return resumen
        return {"events": eventos.get(params["dates"], [])}

    monkeypatch.setattr(espn, "obtener", falso)
    df = espn.historial("liga_mx", date(2025, 1, 1), date(2025, 2, 28))
    assert list(df["espn_id"]) == ["a", "b"]
    assert (df["arbitro"] == "César Ramos").all() and (df["corners_local"] == 5).all()


def test_arbitro_sin_puesto_toma_el_primero():
    assert espn._arbitro({"gameInfo": {"officials": [{"displayName": "Uno", "position": None},
                                                     {"displayName": "Dos"}]}}) == "Uno"
    assert espn._arbitro({}) is None


def test_arbitro_por_order_como_espn_real():
    datos = {"gameInfo": {"officials": [
        {"displayName": "Asistente Uno", "order": 2},
        {"displayName": "Central", "order": 1},
        {"displayName": "Cuarto", "order": 4},
    ]}}
    assert espn._arbitro(datos) == "Central"
    sin_order = {"gameInfo": {"officials": [{"displayName": "X"}, {"displayName": "Y", "order": "3"}]}}
    assert espn._arbitro(sin_order) == "Y"


def test_doble_amarilla_cuenta_tres():
    # ESPN real (Everton-Ipswich, 19-sep-2026): Ipswich yellowCards 1, redCards 1, y en keyEvents
    # Fatawu ve amarilla (35') y "Second yellow card" (67') + Flemming amarilla (90+2').
    # Winpot: Flemming 1 + Fatawu 3 = 4.
    datos = {
        "boxscore": {"teams": [
            {"homeAway": "home", "team": {"id": "368", "displayName": "Everton"},
             "statistics": [{"name": "yellowCards", "displayValue": "2"}, {"name": "redCards", "displayValue": "0"}]},
            {"homeAway": "away", "team": {"id": "373", "displayName": "Ipswich Town"},
             "statistics": [{"name": "yellowCards", "displayValue": "1"}, {"name": "redCards", "displayValue": "1"}]},
        ]},
        "keyEvents": [
            {"type": {"type": "yellow-card"}, "text": "Abdul Fatawu (Ipswich Town) is shown the yellow card.",
             "team": {"id": "373"}},
            {"type": {"type": "red-card"}, "text": "Second yellow card to Abdul Fatawu (Ipswich Town).",
             "team": {"id": "373", "displayName": "Ipswich Town"}},
        ],
    }
    r = espn._estadisticas(datos)
    assert r["segundas_amarillas_visitante"] == 1 and r["segundas_amarillas_local"] == 0
    assert r["tarjetas_visitante"] == 4 and r["tarjetas_local"] == 2


def test_roja_directa_y_sin_eventos():
    datos = {"boxscore": {"teams": [
        {"homeAway": "home", "team": {"id": "1"}, "statistics": [{"name": "yellowCards", "displayValue": "1"},
                                                                 {"name": "redCards", "displayValue": "1"}]},
        {"homeAway": "away", "team": {"id": "2"}, "statistics": []},
    ]}}
    r = espn._estadisticas(datos)
    assert r["tarjetas_local"] == 3          # roja directa = 2
    assert r["tarjetas_visitante"] is None   # sin amarillas no hay dato
    assert "segundas_amarillas_local" not in r


def test_boxscore_vacio_es_sin_datos():
    """ESPN a veces publica el boxscore todo en 0: no son 0 córners ni 0 tarjetas."""
    vacio = [{"name": n, "displayValue": "0"} for n in ("wonCorners", "yellowCards", "redCards", "totalShots", "shotsOnTarget")]
    datos = {"boxscore": {"teams": [
        {"homeAway": "home", "team": {"id": "1"}, "statistics": vacio},
        {"homeAway": "away", "team": {"id": "2"}, "statistics": vacio},
    ]}}
    r = espn._estadisticas(datos)
    assert r["corners_local"] is None and r["tarjetas_visitante"] is None and r["tiros_local"] is None
    from predicciones.calificar import SIN_DATOS, calificar
    r.update(goles_local=1, goles_visitante=0)
    assert calificar("corners", "menos_9.5", r) == SIN_DATOS
    assert calificar("tarjetas", "menos_4.5", r) == SIN_DATOS
    assert calificar("tarjetas_1x2", "X", r) == SIN_DATOS
