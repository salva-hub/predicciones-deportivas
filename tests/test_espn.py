from predicciones.datos import espn


def _ev(nombre_estado, local, visitante, ls_local, ls_visit):
    def comp(lado, nombre, score, ls):
        return {"homeAway": lado, "score": str(score), "team": {"displayName": nombre},
                "linescores": [{"value": v} for v in ls]}
    return {
        "id": "123", "date": "2026-10-20T19:00Z",
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
    assert r["corners_local"] == 7 and r["tarjetas_visitante"] == 2


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
    assert cal["dates"] == "20261019-20261021"


def test_historial_sigue_si_falla_una_estadistica(monkeypatch):
    monkeypatch.setattr(espn, "calendario", lambda liga, a, b: [espn._partido(_ev("STATUS_FULL_TIME", 1, 0, [1, 0], [0, 0]), liga)])

    def falla(*a, **k):
        raise RuntimeError("caído")

    monkeypatch.setattr(espn, "estadisticas", falla)
    from datetime import date
    df = espn.historial("liga_mx", date(2026, 10, 1), date(2026, 10, 20))
    assert len(df) == 1
