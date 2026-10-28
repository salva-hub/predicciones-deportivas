"""Flujo completo sin red: pronóstico -> evaluar con cuotas de Winpot -> apostar -> bank."""
import importlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture
def cli(tmp_path, monkeypatch, liga_sintetica):
    monkeypatch.setenv("PRED_RAIZ", str(tmp_path))
    from predicciones import registro
    from predicciones.datos import historiales
    importlib.reload(registro)
    importlib.reload(historiales)
    from predicciones import cli
    importlib.reload(cli)
    # por defecto, fuera de la prueba en papel (dinero real); las pruebas del papel la activan
    monkeypatch.setattr(cli.B, "PRUEBA_PAPEL_HASTA", date(2000, 1, 1))
    (tmp_path / "datos" / "historial").mkdir(parents=True)
    liga_sintetica.assign(espn_id=range(len(liga_sintetica))).to_csv(tmp_path / "datos" / "historial" / "premier.csv", index=False)
    return cli


def _salida(capsys):
    return json.loads(capsys.readouterr().out)


def _sin_pick(p):
    """Apuestas en papel del pronóstico sin el pick del modelo (que evaluar agrega siempre)."""
    return [x for x in (p or {}).get("apuestas_papel", []) if x.get("categoria") != "pick_modelo"]


def _pronostico(cli, capsys, *extra):
    cli.main(["pronostico", "--liga", "premier", "--local", "Equipo 11", "--visitante", "Equipo 0", "--sin-red",
              "--confianza", "alta", "--arbitro-prom", "5.5", "--arbitro-partidos", "20",
              "--ajuste", "goles_visitante:0.93:Baja del delantero titular", *extra])
    return _salida(capsys)


def test_flujo(cli, capsys):
    p = _pronostico(cli, capsys)
    assert p["id"].startswith("P-")

    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "ganador:1=-1000"])
    ev = _salida(capsys)
    assert ev["evaluacion"][0]["apostar"] is True
    assert ev["evaluacion"][0]["monto_sencilla"] in (200, 300, 500)
    assert ev["evaluacion"][1]["apostar"] is False

    cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+400", "--monto", "300"])
    capsys.readouterr()
    cli.main(["movimiento", "--tipo", "retiro", "--monto", "1000"])
    capsys.readouterr()
    cli.main(["bank"])
    b = _salida(capsys)
    assert b["bank_actual"] == 9000
    assert b["apostado_hoy"] == 300
    assert b["disponible_hoy"] == 600
    assert b["en_pausa"] is False


def test_confianza_obligatoria(cli):
    with pytest.raises(SystemExit):
        cli.main(["pronostico", "--liga", "premier", "--local", "Equipo 11", "--visitante", "Equipo 0", "--sin-red"])


def test_neutral(cli, capsys):
    p = _pronostico(cli, capsys, "--neutral", "--no-guardar")
    assert p["neutral"] is True
    assert any("neutral" in n for n in p["notas"])


def test_techado_sin_acentos(cli):
    assert cli.es_techado("Estadio Santiago Bernabéu")
    assert cli.es_techado("VELTINS-Arena")
    assert not cli.es_techado("Estadio Azteca")


def test_monto_tope_disponible(cli, capsys):
    p = _pronostico(cli, capsys)
    cli.main(["apostar", "--pata", f"{p['id']}:ganador:1=-200", "--monto", "920"])
    capsys.readouterr()
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    ev = _salida(capsys)
    assert ev["disponible_hoy"] == 80
    assert ev["evaluacion"][0]["monto_sencilla"] == 80


def test_pausa_bloquea_recomendaciones(cli, capsys):
    R = cli.R
    p = _pronostico(cli, capsys)
    a = R.agregar_apuesta({"tipo": "sencilla", "patas": [], "cuota": 2.0, "monto": 5000})
    a.update(estado="perdida", ganancia=-5000, liquidada=R.ahora().isoformat())
    R.actualizar_apuesta(a)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    ev = _salida(capsys)
    assert ev["en_pausa"] is True
    assert ev["evaluacion"][0]["apostar"] is False and ev["evaluacion"][0]["monto_sencilla"] == 0
    cli.main(["parlays"])
    assert "PAUSA" in capsys.readouterr().out


def _evaluado(R, partido, mercado, seleccion, cuota, devolucion=0.0):
    sel = {"mercado": mercado, "seleccion": seleccion, "prob": 0.62, "confianza": "alta"}
    if devolucion:
        sel["devolucion"] = devolucion
    return R.agregar_pronostico({
        "partido": partido, "liga": "premier", "inicio_sonora": f"{R.ahora().date()} 12:00",
        "selecciones": [sel],
        "evaluacion": [{"mercado": mercado, "seleccion": seleccion, "prob": 0.62, "confianza": "alta",
                        "cuota_winpot": cuota, "sirve_para_parlay": True}],
    })


def test_parlays_respetan_devolucion(cli, capsys):
    R = cli.R
    _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    _evaluado(R, "C vs D", "ganador", "1", "+100")
    _evaluado(R, "E vs F", "handicap", "local_-1", "+100", devolucion=0.2)
    cli.main(["parlays"])
    out = _salida(capsys)
    assert out and all("E vs F" not in pata for par in out for pata in par["patas"])
    assert out[0]["monto"] == 300


def _pronostico_espn(R, horas, selecciones):
    inicio = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()
    return R.agregar_pronostico({
        "partido": "A vs B", "liga": "premier", "espn_id": "999", "inicio_utc": inicio,
        "selecciones": [{"mercado": m, "seleccion": s} for m, s in selecciones], "estado": "pendiente",
    })


def test_calificar_reintenta_sin_datos(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 3, [("goles", "mas_2.5"), ("corners", "mas_9.5")])
    R.agregar_apuesta({"tipo": "parlay", "monto": 100, "cuota": 4.0, "patas": [
        {"pronostico": p["id"], "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0, "estado": "pendiente"},
        {"pronostico": p["id"], "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "pendiente"}]})
    sin_corners = {"terminado": True, "goles_local": 2, "goles_visitante": 1}
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: dict(sin_corners))
    cli.main(["calificar"])
    capsys.readouterr()
    p1 = R.buscar_pronostico(p["id"])
    assert p1["estado"] == "pendiente"  # faltan córners: se reintenta
    assert {s["estado"] for s in p1["selecciones"]} == {"ganada", "sin_datos"}
    ap = R.apuestas()[0]
    assert ap["estado"] == "pendiente" and ap["patas"][1]["estado"] == "pendiente"

    completo = dict(sin_corners, corners_local=6, corners_visitante=5)
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: dict(completo))
    cli.main(["calificar"])
    capsys.readouterr()
    assert R.buscar_pronostico(p["id"])["estado"] == "calificado"
    ap = R.apuestas()[0]
    assert ap["estado"] == "ganada" and ap["ganancia"] == 300


def test_calificar_tras_48h_cierra_aunque_falten_datos(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 50, [("corners", "mas_9.5")])
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 0, "goles_visitante": 0})
    cli.main(["calificar"])
    capsys.readouterr()
    p1 = R.buscar_pronostico(p["id"])
    assert p1["estado"] == "calificado" and p1["selecciones"][0]["estado"] == "sin_datos"


def test_calificar_sigue_si_falla_un_partido(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 3, [("goles", "mas_2.5")])
    otro = _pronostico_espn(R, 3, [("goles", "mas_2.5")])
    otro["espn_id"] = "1000"
    R.actualizar_pronostico(otro)

    def resultado(liga, espn_id, inicio):
        if espn_id == "999":
            raise RuntimeError("site.api.espn.com/apis/x: HTTP 500")
        return {"terminado": True, "goles_local": 3, "goles_visitante": 0}

    monkeypatch.setattr(cli.espn, "resultado", resultado)
    cli.main(["calificar"])
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "http" not in err.lower()
    assert R.buscar_pronostico(p["id"])["estado"] == "pendiente"
    assert R.buscar_pronostico(otro["id"])["estado"] == "calificado"


def test_exportar(cli, capsys, tmp_path):
    _pronostico(cli, capsys)
    cli.main(["exportar", "--salida", str(tmp_path / "_site" / "registro")])
    capsys.readouterr()
    datos = json.loads((tmp_path / "_site" / "registro" / "pronosticos.json").read_text())
    assert len(datos) == 1
    assert (tmp_path / "_site" / "registro" / "bank.json").exists()


# ---------------------------------------------------------------- prueba en papel

@pytest.fixture
def papel(cli, monkeypatch):
    monkeypatch.setattr(cli.B, "PRUEBA_PAPEL_HASTA", date(2999, 12, 31))
    return cli


@pytest.fixture
def papel_con_limite(papel, monkeypatch):
    """Papel con el límite del día y los máximos por día activados (hoy están apagados en papel)."""
    monkeypatch.setattr(papel.B, "LIMITE_EN_PAPEL", True)
    monkeypatch.setattr(papel.B, "MAXIMOS_EN_PAPEL", True)
    return papel


def test_papel_no_recomienda_dinero_y_guarda(papel, capsys):
    cli, R = papel, papel.R
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "ganador:1=-1000"])
    out = capsys.readouterr()
    ev = json.loads(out.out)
    assert "Prueba en papel hasta 26-nov-2026" in out.err and ev["prueba_papel"] is True
    bueno, malo = ev["evaluacion"]
    assert bueno["apostar"] is False and bueno["monto_sencilla"] == 0
    assert bueno["apostar_papel"] is True and bueno["monto_papel"] in (200, 300, 500)
    assert malo["apostar_papel"] is False
    guardado = R.buscar_pronostico(p["id"])
    assert [(x["mercado"], x["seleccion"]) for x in _sin_pick(guardado)] == [("goles", "mas_2.5")]
    assert guardado["apuestas_papel"][0]["cuota_americana"] == "+400"


def test_papel_apostar_pide_forzar(papel, capsys):
    cli, R = papel, papel.R
    p = _pronostico(cli, capsys)
    with pytest.raises(SystemExit) as e:
        cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+400", "--monto", "300"])
    assert "papel" in str(e.value)
    assert R.apuestas() == []
    cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+400", "--monto", "300", "--forzar"])
    out = capsys.readouterr()
    assert "--forzar" in out.err and len(R.apuestas()) == 1


def test_papel_parlays_marcados(papel, capsys):
    R = papel.R
    _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    _evaluado(R, "C vs D", "ganador", "1", "+100")
    papel.main(["parlays"])
    out = _salida(capsys)
    assert "papel" in out["aviso"]
    par = out["parlays_papel"][0]
    assert par["papel"] is True and par["monto"] == 0 and par["monto_papel"] == 300


def test_papel_se_califica(papel, capsys, monkeypatch):
    cli, R = papel, papel.R
    p = _pronostico_espn(R, 3, [("goles", "mas_2.5"), ("corners", "mas_9.5")])
    p["apuestas_papel"] = [
        {"mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.5, "monto": 200, "evaluado": R.ahora().isoformat(), "estado": "pendiente"},
        {"mercado": "ganador", "seleccion": "2", "cuota": 3.0, "monto": 100, "evaluado": R.ahora().isoformat(), "estado": "pendiente"},
        {"mercado": "corners", "seleccion": "mas_9.5", "cuota": 1.9, "monto": 100, "evaluado": R.ahora().isoformat(), "estado": "pendiente"},
    ]
    R.actualizar_pronostico(p)
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 2, "goles_visitante": 1})
    cli.main(["calificar"])
    capsys.readouterr()
    gan, perd, sin = R.buscar_pronostico(p["id"])["apuestas_papel"]
    assert (gan["estado"], gan["ganancia"]) == ("ganada", 300)
    assert (perd["estado"], perd["ganancia"]) == ("perdida", -100)
    assert sin["estado"] == "pendiente"  # faltan córners: se reintenta
    # las apuestas en papel no tocan el bank real
    assert R.bank_actual() == 10000


def test_partidos_incluye_noche_de_sonora(cli, capsys, monkeypatch):
    pedidos = []

    def calendario(liga, dia, hasta=None):
        pedidos.append((dia, hasta))
        base = {"espn_id": "", "local": "A", "visitante": "B", "estadio": None, "ciudad": None}
        return [dict(base, espn_id="tarde", inicio_sonora="2026-10-26 13:00"),
                # 22:30 de Sonora = día siguiente en el Este: ESPN lo lista el 27
                dict(base, espn_id="noche", inicio_sonora="2026-10-26 22:30"),
                dict(base, espn_id="otro_dia", inicio_sonora="2026-10-27 12:00")]

    monkeypatch.setattr(cli.espn, "calendario", calendario)
    cli.main(["partidos", "--liga", "liga_mx", "--fecha", "2026-10-26"])
    ids = [p["espn_id"] for p in _salida(capsys)]
    assert pedidos == [(date(2026, 10, 26), date(2026, 10, 27))]
    assert ids == ["tarde", "noche"]


def test_limite_del_dia_cubre_varias_selecciones(cli, capsys):
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "goles:mas_1.5=+400",
              "--winpot", "goles:mas_3.5=+900", "--winpot", "ambos_anotan:si=+400"])
    ev = _salida(capsys)
    aprobadas = [r["monto_sencilla"] for r in ev["evaluacion"] if r.get("apostar")]
    assert len(aprobadas) >= 3
    assert sum(aprobadas) <= 1000  # 10% de 10,000


def test_papel_limite_del_dia_y_bank(papel_con_limite, capsys):
    papel = papel_con_limite
    cli = papel
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "goles:mas_1.5=+400",
              "--winpot", "goles:mas_3.5=+900", "--winpot", "ambos_anotan:si=+400"])
    ev = _salida(capsys)
    total = sum(r["monto_papel"] for r in ev["evaluacion"])
    assert 0 < total <= 1000
    cli.main(["bank"])
    b = _salida(capsys)
    assert b["prueba_papel"] is True and b["apostado_papel_hoy"] == total
    assert b["disponible_hoy"] == 1000 - total
    # volver a evaluar no duplica: reemplaza las pendientes
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    capsys.readouterr()
    sel = [(x["mercado"], x["seleccion"]) for x in _sin_pick(cli.R.buscar_pronostico(p["id"]))]
    assert len(sel) == len(set(sel))


def test_papel_reevaluar_no_pisa_calificadas_ni_agrega_tras_resultado(papel, capsys):
    cli, R = papel, papel.R
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    capsys.readouterr()
    q = R.buscar_pronostico(p["id"])
    _sin_pick(q)[0].update(estado="ganada", ganancia=999)
    q.update(estado="calificado", resultado={"goles_local": 0, "goles_visitante": 0})
    R.actualizar_pronostico(q)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "ambos_anotan:no=+400"])
    ev = _salida(capsys)
    assert ev["evaluacion"][0]["motivo"] == "ya calificada en papel"
    papel_ = {x["seleccion"]: x for x in _sin_pick(R.buscar_pronostico(p["id"]))}
    assert papel_["mas_2.5"]["ganancia"] == 999  # no se pisó
    # con resultado ya conocido no se agregan apuestas en papel (sería trampa)
    assert "no" not in papel_
    assert all(x["seleccion"] != "no" for x in R.buscar_pronostico(p["id"])["apuestas_papel"])  # ni como pick
    assert ev["evaluacion"][1]["apostar_papel"] is False and "ya empezó" in ev["evaluacion"][1]["motivo"]
    assert "nota" in ev


def test_papel_parlays_se_guardan_y_califican(papel, capsys, monkeypatch):
    cli, R = papel, papel.R
    a = _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    b = _evaluado(R, "C vs D", "ganador", "1", "+100")
    cli.main(["parlays"])
    capsys.readouterr()
    cli.main(["parlays"])  # correrlo dos veces no duplica
    capsys.readouterr()
    guardados = R.parlays_papel()
    assert len(guardados) == 1 and guardados[0]["monto"] == 300
    for pr, gl in ((a, 3), (b, 2)):
        pr.update(estado="calificado", resultado={"goles_local": gl, "goles_visitante": 0})
        R.actualizar_pronostico(pr)
    cli.main(["calificar"])
    capsys.readouterr()
    par = R.parlays_papel()[0]
    assert par["estado"] == "ganada" and par["ganancia"] == 900
    assert R.bank_actual() == 10000  # el papel no toca el bank
    cli.main(["exportar", "--salida", str(R.RAIZ / "_site")])
    capsys.readouterr()
    assert json.loads((R.RAIZ / "_site" / "papel.json").read_text())[0]["estado"] == "ganada"


def test_papel_no_agrega_si_el_partido_ya_empezo(papel, capsys):
    cli, R = papel, papel.R
    p = _pronostico(cli, capsys)
    q = R.buscar_pronostico(p["id"])
    q["inicio_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    R.actualizar_pronostico(q)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    ev = _salida(capsys)
    assert ev["evaluacion"][0]["ve"] > 0 and ev["evaluacion"][0]["apostar_papel"] is False
    assert R.buscar_pronostico(p["id"]).get("apuestas_papel") == []


def test_reevaluar_mezcla_por_seleccion(cli, capsys):
    R = cli.R
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400"])
    cli.main(["evaluar", "--id", p["id"], "--winpot", "ganador:1=-1000"])
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+300"])
    capsys.readouterr()
    q = R.buscar_pronostico(p["id"])
    assert [(r["mercado"], r["seleccion"]) for r in q["evaluacion"]] == [("ganador", "1"), ("goles", "mas_2.5")]
    assert q["cuotas_winpot"] == {"goles:mas_2.5": "+300", "ganador:1": "-1000"}


def test_papel_parlays_limite_cuenta_todo_lo_de_hoy(papel_con_limite, capsys):
    papel = papel_con_limite
    from datetime import date as _d
    R = papel.R
    # parlay en papel guardado hoy para otro día de partido: cuenta en el límite de hoy
    R.guardar_parlays_papel(_d(2030, 1, 1), [{"clave": "x", "tipo": "parlay", "patas": [], "cuota": 3.0,
                                              "monto": 900, "creado": R.ahora().isoformat(), "estado": "pendiente"}])
    _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    _evaluado(R, "C vs D", "ganador", "1", "+100")
    papel.main(["parlays"])
    out = _salida(capsys)
    assert out["parlays_papel"][0]["monto_papel"] == 100  # 1000 - 900


def test_parlays_varios_pronosticos_del_mismo_partido(papel, capsys):
    R = papel.R
    viejo = _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    nuevo = _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    nuevo["creado"] = viejo["creado"] + "z"  # el más reciente
    R.actualizar_pronostico(nuevo)
    _evaluado(R, "C vs D", "ganador", "1", "+100")
    papel.main(["parlays"])
    capsys.readouterr()
    (par,) = R.parlays_papel()
    ids = {x["pronostico"] for x in par["patas"]}
    assert nuevo["id"] in ids and viejo["id"] not in ids


def test_sin_pinnacle_baja_confianza_y_avisa(cli, capsys):
    sin = _pronostico(cli, capsys, "--no-guardar")
    assert cli.NOTA_SIN_PINNACLE in sin["notas"]
    assert sin["confianza"] == "media"  # alta -> media por no tener referencia
    con = _pronostico(cli, capsys, "--no-guardar", "--pinnacle", "1=-150,X=+280,2=+400")
    assert cli.NOTA_SIN_PINNACLE not in con["notas"]
    assert con["pinnacle"]


def test_calificar_marca_revision_tras_48h(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 50, [("goles", "mas_2.5"), ("corners", "mas_9.5")])
    ap = R.agregar_apuesta({"tipo": "parlay", "monto": 100, "cuota": 4.0, "patas": [
        {"pronostico": p["id"], "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0, "estado": "pendiente"},
        {"pronostico": p["id"], "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "pendiente"}]})
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 2, "goles_visitante": 1})
    cli.main(["calificar"])
    err = capsys.readouterr().err
    assert f"Revisar a mano: apuesta {ap['id']} (A vs B, corners)" in err
    guardada = R.apuestas()[0]
    assert guardada["estado"] == "pendiente" and guardada["requiere_revision"] is True


def test_calificar_no_marca_revision_antes_de_48h(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 3, [("corners", "mas_9.5")])
    R.agregar_apuesta({"tipo": "sencilla", "monto": 100, "cuota": 2.0, "patas": [
        {"pronostico": p["id"], "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "pendiente"}]})
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 2, "goles_visitante": 1})
    cli.main(["calificar"])
    assert "Revisar a mano" not in capsys.readouterr().err
    assert "requiere_revision" not in R.apuestas()[0]


def test_calificar_parlay_perdido_sin_esperar(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 3, [("goles", "mas_2.5"), ("corners", "mas_9.5")])
    otro = R.agregar_pronostico({"partido": "C vs D", "liga": "premier", "selecciones": [], "estado": "pendiente"})
    R.agregar_apuesta({"tipo": "parlay", "monto": 100, "cuota": 8.0, "patas": [
        {"pronostico": p["id"], "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0, "estado": "pendiente"},
        {"pronostico": p["id"], "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "pendiente"},
        {"pronostico": otro["id"], "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0, "estado": "pendiente"}]})
    # 1-0: el más de 2.5 pierde; faltan córners y el otro partido no se ha jugado
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 1, "goles_visitante": 0})
    cli.main(["calificar"])
    capsys.readouterr()
    ap = R.apuestas()[0]
    assert ap["estado"] == "perdida" and ap["ganancia"] == -100 and ap["liquidada"]


def test_revisar_a_mano_se_avisa_una_vez(cli, capsys, monkeypatch):
    R = cli.R
    p = _pronostico_espn(R, 50, [("corners", "mas_9.5")])
    R.agregar_apuesta({"tipo": "sencilla", "monto": 100, "cuota": 2.0, "patas": [
        {"pronostico": p["id"], "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "pendiente"}]})
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 2, "goles_visitante": 1})
    cli.main(["calificar"])
    assert "Revisar a mano" in capsys.readouterr().err
    cli.main(["calificar"])
    assert "Revisar a mano" not in capsys.readouterr().err
    assert R.apuestas()[0]["requiere_revision"] is True


def _apuesta_pendiente(R, **extra):
    return R.agregar_apuesta({"tipo": "parlay", "monto": 100, "cuota": 4.0, "requiere_revision": True, "patas": [
        {"pronostico": "P-x", "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0, "estado": "ganada"},
        {"pronostico": "P-x", "mercado": "corners", "seleccion": "mas_9.5", "cuota": 2.0, "estado": "sin_datos"}],
        **extra})


@pytest.mark.parametrize("estado,extra,ganancia", [
    ("ganada", [], 300.0),
    ("ganada", ["--cuota-real", "+250"], 250.0),
    ("ganada", ["--cuota-real", "2.5"], 150.0),
    ("perdida", [], -100.0),
    ("devuelta", [], 0.0),
])
def test_liquidar_a_mano(cli, capsys, estado, extra, ganancia):
    R = cli.R
    ap = _apuesta_pendiente(R)
    cli.main(["liquidar", "--id", ap["id"], "--estado", estado, *extra])
    out = _salida(capsys)
    guardada = R.buscar_apuesta(ap["id"])
    assert out["estado"] == guardada["estado"] == estado
    assert guardada["ganancia"] == ganancia
    assert guardada["liquidada"] and guardada["liquidada_a_mano"] is True
    assert "requiere_revision" not in guardada
    cli.main(["bank"])
    assert _salida(capsys)["bank_actual"] == 10000 + ganancia


def test_liquidar_errores(cli, capsys):
    with pytest.raises(SystemExit):
        cli.main(["liquidar", "--id", "A-no-existe", "--estado", "ganada"])
    ap = _apuesta_pendiente(cli.R)
    with pytest.raises(SystemExit):
        cli.main(["liquidar", "--id", ap["id"], "--estado", "ganadora"])
    with pytest.raises(SystemExit):
        cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada", "--cuota-real", "0.9"])
    assert cli.R.buscar_apuesta(ap["id"])["estado"] == "pendiente"


def test_pinnacle_parcial_cuenta_como_sin_referencia(cli, capsys):
    parcial = _pronostico(cli, capsys, "--no-guardar", "--pinnacle", "1=-150,2=+400,mas_2.5=-110,menos_2.5=-110")
    assert cli.NOTA_SIN_PINNACLE in parcial["notas"]
    assert parcial["confianza"] == "media"
    sin = _pronostico(cli, capsys, "--no-guardar")
    mas = lambda p: next(x["prob"] for x in p["selecciones"] if x["mercado"] == "goles" and x["seleccion"] == "mas_2.5")
    assert mas(parcial) != mas(sin)  # los totales parciales sí se mezclan


@pytest.mark.parametrize("texto,ganancia", [
    ("−110", 90.91),
    ("-110", 90.91),
    ("+250", 250.0),
    ("100", 100.0),
    ("2.5", 150.0),
    (" −110 ", 90.91),
])
def test_liquidar_cuota_real_formatos(cli, capsys, texto, ganancia):
    ap = _apuesta_pendiente(cli.R)
    cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada", "--cuota-real", texto])
    capsys.readouterr()
    assert cli.R.buscar_apuesta(ap["id"])["ganancia"] == pytest.approx(ganancia, abs=0.01)


@pytest.mark.parametrize("texto", ["abc", "1", "0.5", "-50", "nan", "inf"])
def test_liquidar_cuota_real_invalida(cli, capsys, texto):
    ap = _apuesta_pendiente(cli.R)
    with pytest.raises(SystemExit):
        cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada", "--cuota-real", texto])
    assert cli.R.buscar_apuesta(ap["id"])["estado"] == "pendiente"


def test_corregir_sin_cuota_real_usa_la_registrada(cli, capsys):
    ap = _apuesta_pendiente(cli.R)
    cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada", "--cuota-real", "+250"])
    cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada"])
    capsys.readouterr()
    assert cli.R.buscar_apuesta(ap["id"])["ganancia"] == 250.0


def test_corregir_liquidada_conserva_fecha(cli, capsys):
    R = cli.R
    liquidada = "2026-09-20T18:00:00-07:00"
    ap = _apuesta_pendiente(R, creado="2026-09-20T12:00:00-07:00")
    ap.update(estado="ganada", ganancia=100.0, liquidada=liquidada, cuota=2.0)
    ap.pop("requiere_revision")
    R.actualizar_apuesta(ap)
    hoy_antes, mes_antes, inicio_antes = R.ganancia_cobrada_hoy(), R.resultado_apuestas_mes(), R.bank_inicio_mes()
    assert inicio_antes == 10100.0
    cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada"])
    capsys.readouterr()
    guardada = R.buscar_apuesta(ap["id"])
    assert guardada["liquidada"] == liquidada and guardada["corregida"]
    assert guardada["ganancia"] == 100.0
    assert R.ganancia_cobrada_hoy() == hoy_antes == 0
    assert R.resultado_apuestas_mes() == mes_antes == 0
    assert R.bank_inicio_mes() == inicio_antes
    # si la corrección cambia la ganancia, el cambio cuenta en septiembre, no hoy
    cli.main(["liquidar", "--id", ap["id"], "--estado", "ganada", "--cuota-real", "+150"])
    capsys.readouterr()
    assert R.buscar_apuesta(ap["id"])["liquidada"] == liquidada
    assert R.ganancia_cobrada_hoy() == 0 and R.resultado_apuestas_mes() == 0
    assert R.bank_inicio_mes() == 10150.0


def test_papel_parlay_perdido_al_instante(papel, capsys):
    cli, R = papel, papel.R
    a = _evaluado(R, "A vs B", "goles", "mas_2.5", "+100")
    _evaluado(R, "C vs D", "ganador", "1", "+100")
    cli.main(["parlays"])
    capsys.readouterr()
    a.update(estado="calificado", resultado={"goles_local": 0, "goles_visitante": 0})
    R.actualizar_pronostico(a)
    cli.main(["calificar"])  # C vs D sigue pendiente, pero una pata perdida ya pierde el parlay
    capsys.readouterr()
    par = R.parlays_papel()[0]
    assert par["estado"] == "perdida" and par["ganancia"] == -300
    assert R.bank_actual() == 10000


# ---------------------------------------------------------------- categorías

def _pron_cat(R, partido, sels, espn_id=None):
    """Pronóstico mínimo de hoy con selecciones (mercado, seleccion, prob, confianza)."""
    return R.agregar_pronostico({
        "partido": partido, "liga": "premier", "espn_id": espn_id, "inicio_sonora": f"{R.ahora().date()} 12:00",
        "selecciones": [{"mercado": m, "seleccion": s, "prob": pr, "confianza": c, "cuota_minima": "-100"}
                        for m, s, pr, c in sels],
        "estado": "pendiente",
    })


def test_papel_sencilla_fuerte_monto_y_maximo_por_dia(papel_con_limite, capsys):
    papel = papel_con_limite
    R = papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta")])
    b = _pron_cat(R, "C vs D", [("ganador", "1", 0.62, "media")])
    papel.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["categoria"] == "sencilla_fuerte" and r["apostar"] is False
    assert r["apostar_papel"] is True and r["monto_papel"] == 800  # para ganar 500 a -160
    (x,) = _sin_pick(R.buscar_pronostico(a["id"]))
    assert x["categoria"] == "sencilla_fuerte" and x["monto"] == 800
    # la segunda del día ya no entra (máximo 1)
    papel.main(["evaluar", "--id", b["id"], "--winpot", "ganador:1=-170"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["apostar_papel"] is False and "máximo 1" in r["motivo"]
    # volver a evaluar la misma no cuenta como otra
    papel.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["apostar_papel"] is True
    papel.main(["bank"])
    b_ = _salida(capsys)
    assert b_["usadas_hoy"]["sencilla_fuerte"] == 1 and b_["disponible_hoy"] == 200


def test_papel_momio_peor_que_menos_200_no_es_fuerte(papel, capsys):
    R = papel.R
    a = _pron_cat(R, "A vs B", [("goles", "menos_3.5", 0.789, "media")])
    papel.main(["evaluar", "--id", a["id"], "--winpot", "goles:menos_3.5=-434"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["categoria"] is None and r["apostar_papel"] is False
    assert r["sirve_para_combinada"] is True and "peor que −200" in r["motivo"]
    assert not _sin_pick(R.buscar_pronostico(a["id"]))
    # no gasta el cupo del día: una fuerte a -150 sí entra
    b = _pron_cat(R, "C vs D", [("ganador", "1", 0.62, "alta")])
    papel.main(["evaluar", "--id", b["id"], "--winpot", "ganador:1=-150"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["categoria"] == "sencilla_fuerte" and r["monto_papel"] == 750  # para ganar 500 a -150


def test_fuerte_gana_sobre_valor_y_sin_cupo_pasa_a_valor(papel_con_limite, capsys):
    papel = papel_con_limite
    R = papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta"), ("goles", "mas_2.5", 0.64, "alta")])
    papel.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-120", "--winpot", "goles:mas_2.5=-120"])
    r1, r2 = _salida(capsys)["evaluacion"]
    # la de mayor probabilidad se lleva el cupo de fuerte (para ganar 500); la otra queda como valor
    assert (r2["categoria"], r2["con_valor"], r2["monto_papel"]) == ("sencilla_fuerte", True, 600)
    assert r1["categoria"] == "valor" and r1["monto_papel"] == 400 and r1["recortada"] is True  # 1,000 − 600
    assert "recortada" not in r2
    x = {y["mercado"]: y for y in _sin_pick(R.buscar_pronostico(a["id"]))}
    assert x["goles"]["categoria"] == "sencilla_fuerte" and x["goles"]["con_valor"] is True


def test_papel_combinadas_maximo_y_limite_entre_categorias(papel_con_limite, capsys):
    papel = papel_con_limite
    R = papel.R
    pr = [_pron_cat(R, f"E{i} vs F{i}", [("goles", "mas_1.5", 0.56, "alta")]) for i in range(6)]
    for p in pr:
        papel.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_1.5=-130"])
        (r,) = _salida(capsys)["evaluacion"]
        assert r["categoria"] is None and r["sirve_para_combinada"] is True
    papel.main(["parlays"])
    out = _salida(capsys)["parlays_papel"]
    comb = [x for x in out if x["categoria"] == "combinada"]
    assert len(comb) == 2 and all(len(x["patas"]) == 2 for x in comb)
    assert [x["monto_papel"] for x in comb] == [500, 500]
    patas = [p for x in comb for p in x["patas"]]
    assert len(patas) == len(set(patas))
    # correrlo otra vez no duplica
    papel.main(["parlays"])
    capsys.readouterr()
    assert len(R.parlays_papel()) == 2 and all(x["categoria"] == "combinada" for x in R.parlays_papel())


def test_limite_del_dia_entre_categorias(papel_con_limite, capsys):
    papel = papel_con_limite
    R = papel.R
    f = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta")])
    papel.main(["evaluar", "--id", f["id"], "--winpot", "ganador:1=-160"])  # 800 en papel
    capsys.readouterr()
    for i in range(4):
        p = _pron_cat(R, f"E{i} vs F{i}", [("goles", "mas_1.5", 0.56, "alta")])
        papel.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_1.5=-130"])
        capsys.readouterr()
    papel.main(["parlays"])
    out = _salida(capsys)["parlays_papel"]
    assert [x["monto_papel"] for x in out] == [200, 0]  # quedan 200 de 1,000; la segunda no se hace
    assert out[0]["recortada"] is True
    (par,) = R.parlays_papel()
    assert par["recortada"] is True
    assert R.resumen_papel()["combinada"]["recortadas"] == 1
    assert R.apostado_papel_hoy() == 1000


def test_categoria_se_califica_y_resume(papel, capsys, monkeypatch):
    cli, R = papel, papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta")], espn_id="1")
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160"])
    capsys.readouterr()
    q = R.buscar_pronostico(a["id"])
    q["inicio_utc"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    R.actualizar_pronostico(q)
    monkeypatch.setattr(cli.espn, "resultado", lambda *x: {"terminado": True, "goles_local": 2, "goles_visitante": 0})
    cli.main(["calificar"])
    capsys.readouterr()
    (x,) = _sin_pick(R.buscar_pronostico(a["id"]))
    assert (x["categoria"], x["estado"], x["ganancia"]) == ("sencilla_fuerte", "ganada", 500)
    cli.main(["exportar", "--salida", str(R.RAIZ / "_site")])
    capsys.readouterr()
    res = json.loads((R.RAIZ / "_site" / "resumen_papel.json").read_text())
    assert set(res) == {"valor", "sencilla_fuerte", "combinada", "eleccion_salva", "pick_modelo", "total"}
    f = res["sencilla_fuerte"]
    assert (f["apuestas"], f["acierto"], f["ganancia"], f["roi"], f["peor_racha"]) == (1, 1.0, 500, 0.625, 0)
    assert res["valor"]["apuestas"] == 0 and res["total"]["apuestas"] == 1


def test_peor_racha(cli):
    assert cli.R._peor_racha(["perdida", "perdida", "devuelta", "perdida", "ganada", "perdida"]) == 3


def test_real_solo_valor_se_recomienda(cli, capsys):
    R = cli.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta"), ("goles", "mas_2.5", 0.55, "alta")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160", "--winpot", "goles:mas_2.5=+110"])
    fuerte, valor = _salida(capsys)["evaluacion"]
    assert fuerte["categoria"] == "sencilla_fuerte" and fuerte["apostar"] is False
    assert fuerte["motivo"] == "categoría pendiente de aprobación tras la prueba en papel"
    assert fuerte["monto_si_se_aprueba"] > 0
    assert valor["categoria"] == "valor" and valor["apostar"] is True and valor["monto_sencilla"] == 500
    cli.main(["apostar", "--pata", f"{a['id']}:goles:mas_2.5=+110", "--monto", "500", "--categoria", "valor"])
    assert _salida(capsys)["categoria"] == "valor"


def test_pausa_bloquea_categorias(cli, capsys):
    R = cli.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta")])
    ap = R.agregar_apuesta({"tipo": "sencilla", "patas": [], "cuota": 2.0, "monto": 5000})
    ap.update(estado="perdida", ganancia=-5000, liquidada=R.ahora().isoformat())
    R.actualizar_apuesta(ap)
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["apostar"] is False and r["sirve_para_combinada"] is False and r["motivo"] == "pausa"


def test_real_fuerte_con_valor_cuenta_como_valor(cli, capsys, monkeypatch):
    R = cli.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta"), ("goles", "mas_2.5", 0.64, "alta")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-120", "--winpot", "goles:mas_2.5=-120"])
    r1, r2 = _salida(capsys)["evaluacion"]
    # fuerte no está aprobada con dinero real: con valor -> valor, y no gasta el cupo de fuerte
    assert all(r["categoria"] == "valor" and r["apostar"] is True and r["con_valor"] for r in (r1, r2))
    # si Salva aprueba la categoría, sí se usa (una por día, para ganar 500)
    monkeypatch.setattr(cli.B, "CATEGORIAS_DINERO_REAL", {"valor", "sencilla_fuerte"})
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-120", "--winpot", "goles:mas_2.5=-120"])
    r1, r2 = _salida(capsys)["evaluacion"]
    assert (r2["categoria"], r2["apostar"], r2["monto_sencilla"]) == ("sencilla_fuerte", True, 600)
    assert r1["categoria"] == "valor" and r1["monto_sencilla"] == 400 and r1["recortada"] is True


def test_texto_seleccion(cli):
    t = cli.R.texto_seleccion
    p = "Sevilla vs Osasuna"
    assert t(p, "goles", "mas_2.5") == "Más de 2.5 goles"
    assert t(p, "ganador", "1") == "Gana Sevilla" and t(p, "ganador", "X") == "Empate"
    assert t(p, "doble_oportunidad", "X2") == "Osasuna o empate"
    assert t(p, "ambos_anotan", "si") == "Ambos anotan: sí"
    assert t(p, "goles_equipo", "visitante_menos_1.5") == "Osasuna: menos de 1.5 goles"
    assert t(p, "primer_tiempo", "pt_mas_0.5") == "Más de 0.5 goles (1er tiempo)"
    assert t(p, "handicap", "local_-0.5") == "Sevilla −0.5 (hándicap)"
    assert t(p, "corners", "mas_9.5") == "Más de 9.5 córners"
    assert t(p, "tarjetas", "menos_4.5") == "Menos de 4.5 tarjetas"
    assert t(p, "raro", "x") == "raro x"


def test_exportar_parlays_legibles(papel, capsys, monkeypatch):
    cli, R = papel, papel.R
    a = _pron_cat(R, "Sevilla vs Osasuna", [("goles", "mas_2.5", 0.56, "alta")])
    b = _pron_cat(R, "Roma vs Lazio", [("ganador", "1", 0.56, "media")])
    for p, w in ((a, "goles:mas_2.5=-130"), (b, "ganador:1=-130")):
        cli.main(["evaluar", "--id", p["id"], "--winpot", w])
    cli.main(["parlays"])
    capsys.readouterr()
    # el parlay de Sevilla gana; Roma pierde
    for pr, res in ((a, (3, 0)), (b, (0, 1))):
        pr = R.buscar_pronostico(pr["id"])
        pr.update(estado="calificado", resultado={"goles_local": res[0], "goles_visitante": res[1]})
        R.actualizar_pronostico(pr)
    # un parlay real también aparece
    cli.main(["apostar", "--forzar", "--categoria", "valor", "--monto", "200",
              "--pata", f"{a['id']}:goles:mas_2.5=+120", "--pata", f"{b['id']}:ganador:1=+150"])
    cli.main(["calificar"])
    cli.main(["exportar", "--salida", str(R.RAIZ / "_site")])
    capsys.readouterr()
    datos = json.loads((R.RAIZ / "_site" / "parlays.json").read_text())
    assert {x["origen"] for x in datos} == {"papel", "real"}
    comb = next(x for x in datos if x["origen"] == "papel")
    assert comb["categoria"] == "Combinada corta (+200 a +350)" and comb["monto"] == 300 and comb["cuota"] == "+213"
    assert [q["texto"] for q in comb["patas"]] == ["Sevilla vs Osasuna · Más de 2.5 goles", "Roma vs Lazio · Gana Roma"]
    assert [q["cuota"] for q in comb["patas"]] == ["-130", "-130"]
    assert [q["estado"] for q in comb["patas"]] == ["ganada", "perdida"]
    assert (comb["estado"], comb["ganancia"]) == ("perdida", -300)
    real = next(x for x in datos if x["origen"] == "real")
    assert real["cuota"] == "+450" and real["categoria"] == "Cuota mejor que la justa" and real["estado"] == "perdida"
    assert [q["cuota"] for q in real["patas"]] == ["+120", "+150"]
    # cada pronóstico lleva sus apuestas con el momio total que paga la apuesta
    pron = {x["partido"]: x for x in json.loads((R.RAIZ / "_site" / "pronosticos.json").read_text())}
    ap = pron["Sevilla vs Osasuna"]["apuestas"]
    assert {(x["origen"], x["tipo"], x["momio"], x["estado"]) for x in ap if x["categoria"] != "Pick del modelo"} == \
        {("papel", "combinada", "+213", "perdida"), ("real", "parlay", "+450", "perdida")}
    # nunca cuotas decimales
    texto = json.dumps(datos)
    assert '"cuota": 1.' not in texto and '"cuota": 2.' not in texto


def test_exportar_campos_de_filtro(papel_con_limite, capsys):
    papel = papel_con_limite
    cli, R = papel, papel.R
    a = _pron_cat(R, "Sevilla vs Osasuna", [("goles", "mas_2.5", 0.56, "alta"), ("ganador", "1", 0.62, "alta")])
    viejo = R.buscar_pronostico(a["id"])
    viejo["liga"] = "laliga"  # registro viejo, sin deporte
    R.actualizar_pronostico(viejo)
    b = _pron_cat(R, "Roma vs Lazio", [("ganador", "1", 0.56, "media")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "goles:mas_2.5=-130", "--winpot", "ganador:1=-160"])
    cli.main(["evaluar", "--id", b["id"], "--winpot", "ganador:1=-130"])
    cli.main(["parlays"])
    cli.main(["apostar", "--forzar", "--monto", "100", "--pata", f"{b['id']}:ganador:1=-130"])
    cli.main(["exportar", "--salida", str(R.RAIZ / "_site")])
    capsys.readouterr()
    leer = lambda n: json.loads((R.RAIZ / "_site" / n).read_text())  # noqa: E731
    hoy = str(R.ahora().date())
    for nombre in ("pronosticos.json", "apuestas.json", "apuestas_papel.json", "parlays.json"):
        filas = leer(nombre)
        assert filas, nombre
        for x in filas:
            assert x["deporte"] == "futbol" and x["fecha"] == hoy and "liga" in x, (nombre, x)
    pr = {x["partido"]: x for x in leer("pronosticos.json")}
    assert pr["Sevilla vs Osasuna"]["liga"] == "laliga" and pr["Roma vs Lazio"]["liga"] == "premier"
    papel_ = leer("apuestas_papel.json")
    fuerte = next(x for x in papel_ if x["categoria"] == "sencilla_fuerte")
    assert (fuerte["liga"], fuerte["ligas"], fuerte["cuota"], fuerte["texto"]) == \
        ("laliga", ["laliga"], "-160", "Sevilla vs Osasuna · Gana Sevilla")
    comb = next(x for x in papel_ if x["categoria"] == "combinada")
    assert comb["liga"] == "varias" and comb["ligas"] == ["laliga", "premier"] and comb["recortada"] is True
    assert leer("parlays.json")[0]["ligas"] == ["laliga", "premier"]
    (real,) = leer("apuestas.json")
    assert real["liga"] == "premier" and real["patas"][0]["deporte"] == "futbol"
    assert real["patas"][0]["texto"] == "Roma vs Lazio · Gana Roma"
    ev = {e["mercado"]: e["texto"] for e in pr["Sevilla vs Osasuna"]["evaluacion"]}
    assert ev == {"goles": "Más de 2.5 goles", "ganador": "Gana Sevilla"}


def test_pronostico_guarda_deporte(cli, capsys):
    assert _pronostico(cli, capsys)["deporte"] == "futbol"


def test_eleccion_salva_registro_aparte(cli, capsys):
    """Elección de Salva: no ocupa el cupo de combinadas ni se reemplaza al rearmar parlays,
    pero su monto sí cuenta para el límite del día; `apostar` la acepta."""
    R = cli.R
    hoy = R.ahora().date()
    R.guardar_parlays_papel(hoy, [{"clave": "salva", "tipo": "parlay", "categoria": "eleccion_salva", "patas": [],
                                   "cuota": 2.66, "monto": 400, "creado": R.ahora().isoformat(), "estado": "pendiente"}])
    assert [x["clave"] for x in R.guardar_parlays_papel(hoy, [])] == ["salva"]  # no se borra
    assert R.categorias_hoy(reemplazar_parlays_de=hoy) == {"eleccion_salva": 1}  # no toca combinada ni fuerte
    assert R.apostado_papel_hoy(reemplazar_parlays_de=hoy) == 0  # tiene su propio bank: no usa el límite del sistema
    res = R.resumen_papel()
    assert res["eleccion_salva"]["apuestas"] == 1 and res["combinada"]["apuestas"] == 0
    assert res["total"]["apuestas"] == 0  # el total es solo de las 3 categorías del sistema
    p = _pronostico(cli, capsys)
    cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+150", "--monto", "300", "--categoria", "eleccion_salva"])
    assert _salida(capsys)["categoria"] == "eleccion_salva"
    cli.main(["bank"])
    b = _salida(capsys)
    assert b["usadas_hoy"]["eleccion_salva"] == 2 and b["usadas_hoy"]["combinada"] == 0
    assert b["apostado_hoy"] == 300  # el monto real cuenta para el límite del día


def test_eleccion_salva_en_dashboard(cli, capsys):
    R = cli.R
    p = _pronostico(cli, capsys)
    cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+150", "--pata", f"{p['id']}:ambos_anotan:si=-150",
              "--monto", "300", "--categoria", "eleccion_salva"])
    capsys.readouterr()
    (par,) = R.parlays_para_dashboard()
    assert (par["origen"], par["categoria"]) == ("real", "Elección de Salva")


def test_parlay_en_papel_sin_estado_se_califica(cli, capsys):
    R = cli.R
    p = R.agregar_pronostico({"partido": "A vs B", "estado": "calificado", "selecciones": [],
                              "resultado": {"goles_local": 2, "goles_visitante": 1}})
    R.guardar_parlays_papel(R.ahora().date(), [{"clave": "k", "tipo": "parlay", "categoria": "eleccion_salva",
        "patas": [{"pronostico": p["id"], "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0}],
        "cuota": 2.0, "monto": 100, "creado": R.ahora().isoformat()}])
    cli.main(["calificar"])
    capsys.readouterr()
    (x,) = R.parlays_papel()
    assert (x["estado"], x["ganancia"]) == ("ganada", 100)


def test_liquidar_patas_sin_estado_no_truena():
    """Parlay registrado a mano sin estado en las patas: una pata perdida liquida sin KeyError,
    y si falta el resultado de otra pata queda pendiente."""
    from predicciones import cli
    por = {"A": {"resultado": {"goles_local": 0, "goles_visitante": 0}, "estado": "calificado"}}
    ap = {"patas": [{"pronostico": "A", "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0},
                    {"pronostico": "B", "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0}], "monto": 100}
    assert cli._liquidar(ap, por) == "liquidada"
    assert ap["estado"] == "perdida" and ap["patas"][1]["estado"] == "pendiente"
    ap = {"patas": [{"pronostico": "B", "mercado": "goles", "seleccion": "mas_2.5", "cuota": 2.0}], "monto": 100}
    assert cli._liquidar(ap, por) == "patas"
    assert "estado" not in ap and ap["patas"][0]["estado"] == "pendiente"


# ---------------------------------------------------------------- pick del modelo

def test_elegir_pick_modelo():
    from predicciones import valor as V
    a = V.Seleccion("A vs B", "goles", "mas_2.5", 0.5, cuota_ofrecida=2.0)       # VE 0
    b = V.Seleccion("A vs B", "ganador", "1", 0.6, cuota_ofrecida=1.6)           # VE −0.04
    c = V.Seleccion("A vs B", "tiros_jugador", "X:mas_0.5", 0.9, cuota_ofrecida=3.0)  # se ignora
    d = V.Seleccion("A vs B", "ambos_anotan", "si", 0.4, cuota_ofrecida=2.5)     # VE 0, menos probable
    assert V.elegir_pick_modelo([b, c, d, a]) is a
    assert V.elegir_pick_modelo([c]) is None and V.elegir_pick_modelo([]) is None
    assert V.PICK_MODELO in V.CATEGORIAS_REGISTRO and V.MONTO_PICK_MODELO == 300


def test_pick_modelo_en_evaluar(papel, capsys):
    cli, R = papel, papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.55, "alta"), ("goles", "mas_2.5", 0.5, "alta")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-130", "--winpot", "goles:mas_2.5=-110"])
    out = _salida(capsys)
    pk = out["pick_modelo"]
    assert (pk["mercado"], pk["seleccion"], pk["categoria"], pk["monto"]) == ("ganador", "1", "pick_modelo", 300)
    assert pk["con_valor"] is False and pk["cuota_americana"] == "-130"
    assert all(not r["apostar_papel"] for r in out["evaluacion"])  # sin valor: solo el pick
    # volver a evaluar reemplaza el pick pendiente (uno por partido)
    cli.main(["evaluar", "--id", a["id"], "--winpot", "goles:mas_2.5=+100"])
    assert _salida(capsys)["pick_modelo"]["seleccion"] == "mas_2.5"
    (x,) = R.buscar_pronostico(a["id"])["apuestas_papel"]
    assert (x["categoria"], x["mercado"], x["monto"]) == ("pick_modelo", "goles", 300)
    assert R.apostado_papel_hoy() == 0  # solo medición: no usa el límite del día
    cli.main(["bank"])
    assert _salida(capsys)["usadas_hoy"]["pick_modelo"] == 1


def test_pick_modelo_ya_calificado_no_se_reemplaza(papel, capsys):
    cli, R = papel, papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.55, "alta"), ("goles", "mas_2.5", 0.5, "alta")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-130"])
    capsys.readouterr()
    p = R.buscar_pronostico(a["id"])
    p["apuestas_papel"][0].update(estado="ganada", ganancia=230)
    R.actualizar_pronostico(p)
    cli.main(["evaluar", "--id", a["id"], "--winpot", "goles:mas_2.5=+100"])
    assert "ya había" in _salida(capsys)["pick_modelo"]["nota"]
    (x,) = R.buscar_pronostico(a["id"])["apuestas_papel"]
    assert (x["seleccion"], x["estado"]) == ("1", "ganada")


def test_pick_modelo_fuera_del_limite_del_dia(papel_con_limite, capsys):
    papel = papel_con_limite
    cli, R = papel, papel.R
    for i in range(4):  # 4 × 300 pasaría el límite del día (1,000 con 10,000 de bank)
        a = _pron_cat(R, f"A{i} vs B{i}", [("ganador", "1", 0.55, "alta")])
        cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-130"])
        pk = _salida(capsys)["pick_modelo"]
        assert pk["monto"] == 300 and "fuera_de_limite" not in pk
    assert R.apostado_papel_hoy() == 0
    cli.main(["bank"])
    assert _salida(capsys)["disponible_hoy"] == 1000
    # una apuesta de valor del mismo día no se recorta por los picks
    b = _pron_cat(R, "C vs D", [("goles", "mas_2.5", 0.5, "alta")])
    cli.main(["evaluar", "--id", b["id"], "--winpot", "goles:mas_2.5=+150"])
    out = _salida(capsys)
    assert out["disponible_hoy"] == 1000
    (r,) = out["evaluacion"]
    assert r["apostar_papel"] and r["monto_papel"] == 500 and not r.get("recortada")
    # ni los parlays en papel del día
    R.guardar_parlays_papel(R.ahora().date(), [{"clave": "k", "tipo": "parlay", "categoria": "pick_modelo",
        "patas": [{"pronostico": b["id"], "mercado": "ganador", "seleccion": "1", "cuota": 1.77}],
        "cuota": 1.77, "monto": 300, "creado": R.ahora().isoformat(), "estado": "pendiente"}])
    assert R.apostado_papel_hoy() == 500


def test_pick_modelo_no_se_agrega_si_ya_empezo_o_hay_uno_en_parlays(papel, capsys):
    cli, R = papel, papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.55, "alta")])
    q = R.buscar_pronostico(a["id"])
    q["inicio_utc"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    R.actualizar_pronostico(q)
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-130"])
    assert "pick_modelo" not in _salida(capsys)
    assert R.buscar_pronostico(a["id"]).get("apuestas_papel", []) == []
    b = _pron_cat(R, "C vs D", [("ganador", "1", 0.55, "alta")])
    R.guardar_parlays_papel(R.ahora().date(), [{"clave": "k", "tipo": "parlay", "categoria": "pick_modelo",
        "patas": [{"pronostico": b["id"], "mercado": "ganador", "seleccion": "1", "cuota": 1.77}],
        "cuota": 1.77, "monto": 300, "creado": R.ahora().isoformat(), "estado": "pendiente"}])
    cli.main(["evaluar", "--id", b["id"], "--winpot", "ganador:1=-130"])
    assert "parlays" in _salida(capsys)["pick_modelo"]["nota"]
    assert R.buscar_pronostico(b["id"])["apuestas_papel"] == []
    assert [x["clave"] for x in R.guardar_parlays_papel(R.ahora().date(), [])] == ["k"]  # parlays no lo borra


def test_pick_modelo_registro_real_de_una_pata(cli, capsys):
    """El registro real del 27-oct (pick como parlay de una pata) se carga, se califica y se muestra
    como sencilla en papel con categoría "Pick del modelo", fuera del total."""
    from pathlib import Path
    R = cli.R
    real = Path(__file__).resolve().parents[1] / "registro" / "papel" / "parlays-20261027.json"
    doc = json.loads(real.read_text(encoding="utf-8"))
    (R.CARPETA / "papel").mkdir(parents=True)
    (R.CARPETA / "papel" / "parlays-20261027.json").write_text(json.dumps(doc), encoding="utf-8")
    R.actualizar_pronostico({"id": "P-20261027-f66b98", "partido": "Torino vs Como", "liga": "serie_a",
                             "estado": "calificado", "selecciones": [], "pago_anticipado": True,
                             "resultado": {"goles_local": 0, "goles_visitante": 2}})
    assert R.resumen_papel()["pick_modelo"]["apuestas"] == 1
    assert R.guardar_parlays_papel(date(2026, 10, 27), []) == doc["parlays"]  # parlays no lo reemplaza
    cli.main(["calificar"])
    capsys.readouterr()
    pk = next(x for x in R.parlays_papel() if x["categoria"] == "pick_modelo")
    assert pk["estado"] == "ganada" and pk["ganancia"] == round(300 * 0.6993, 2)
    res = R.resumen_papel()
    assert res["pick_modelo"]["ganadas"] == 1 and res["total"]["apuestas"] == 0
    R.exportar(R.RAIZ / "_site")
    leer = lambda n: json.loads((R.RAIZ / "_site" / n).read_text())  # noqa: E731
    (fila,) = [x for x in leer("apuestas_papel.json") if x["categoria"] == "pick_modelo"]
    assert (fila["tipo"], fila["texto"], fila["cuota"], fila["liga"]) == \
        ("sencilla", "Torino vs Como · Gana Como", "-143", "serie_a")
    assert all(x["categoria"] != "Pick del modelo" for x in leer("parlays.json"))  # no es parlay
    (pr,) = leer("pronosticos.json")
    ap = next(x for x in pr["apuestas"] if x["categoria"] == "Pick del modelo")
    assert (ap["origen"], ap["tipo"], ap["momio"]) == ("papel", "sencilla", "-143")


def test_pick_modelo_nunca_con_dinero_real(cli, capsys):
    assert "pick_modelo" not in cli.B.CATEGORIAS_DINERO_REAL
    p = _pronostico(cli, capsys)
    with pytest.raises(SystemExit) as e:
        cli.main(["apostar", "--pata", f"{p['id']}:goles:mas_2.5=+150", "--monto", "300", "--categoria", "pick_modelo"])
    assert "solo en papel" in str(e.value)
    assert cli.R.apuestas() == []
    # fuera de la prueba en papel no se genera pick
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+150"])
    assert "pick_modelo" not in _salida(capsys)


def test_pick_modelo_usa_todas_las_cuotas_conocidas(papel, capsys):
    cli, R = papel, papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.55, "alta"), ("goles", "mas_2.5", 0.5, "alta")])
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=+100"])  # VE +10%
    assert _salida(capsys)["pick_modelo"]["mercado"] == "ganador"
    # una evaluación posterior con otra selección peor no le quita el pick a la guardada
    cli.main(["evaluar", "--id", a["id"], "--winpot", "goles:mas_2.5=-110"])
    assert _salida(capsys)["pick_modelo"]["mercado"] == "ganador"
    # la cuota más nueva de una selección reemplaza a la vieja
    cli.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-200", "--winpot", "goles:mas_2.5=+100"])
    pk = _salida(capsys)["pick_modelo"]
    assert (pk["mercado"], pk["cuota_americana"]) == ("goles", "+100")
    assert [x["categoria"] for x in R.buscar_pronostico(a["id"])["apuestas_papel"]].count("pick_modelo") == 1


def test_banks_en_papel_separados(papel, capsys):
    cli = papel
    R = cli.R
    hoy = R.ahora().date()
    ahora = R.ahora().isoformat()
    R.guardar_parlays_papel(hoy, [
        {"clave": "salva", "tipo": "parlay", "categoria": "eleccion_salva", "patas": [], "cuota": 4.0,
         "monto": 300, "creado": ahora, "estado": "ganada", "ganancia": 900},
        {"clave": "pick", "tipo": "parlay", "categoria": "pick_modelo", "patas": [], "cuota": 2.0,
         "monto": 300, "creado": ahora, "estado": "perdida", "ganancia": -300},
        {"clave": "sis", "tipo": "parlay", "categoria": "combinada", "patas": [], "cuota": 3.0,
         "monto": 500, "creado": ahora, "estado": "ganada", "ganancia": 1000}])
    assert R.bank_papel("sistema") == 11000
    assert R.bank_papel("eleccion_salva") == 10900
    assert R.bank_papel("pick_modelo") == 9700
    assert R.ganancia_papel_hoy("sistema") == 1000
    assert not R.en_pausa_papel()
    cli.main(["bank"])
    b = _salida(capsys)
    assert b["bank_actual"] == 11000 and b["banks_papel"]["pick_modelo"] == 9700


def test_pausa_papel_y_apuesta_sin_categoria(papel, capsys):
    cli = papel
    R = cli.R
    ahora = R.ahora().isoformat()
    R.guardar_parlays_papel(R.ahora().date(), [
        {"clave": "v", "tipo": "parlay", "patas": [], "cuota": 2.0, "monto": 6000, "creado": ahora,
         "estado": "perdida", "ganancia": -6000}])  # sin categoria = valor
    assert R.bank_papel("sistema") == 4000
    assert R.en_pausa_papel()
    cli.main(["bank"])
    assert _salida(capsys)["en_pausa"] is True


def test_papel_sin_limite_del_dia(papel, capsys):
    """Salva (28-oct-2026): en papel cada apuesta con valor lleva su monto completo, sin límite del día."""
    cli = papel
    p = _pronostico(cli, capsys)
    cli.main(["evaluar", "--id", p["id"], "--winpot", "goles:mas_2.5=+400", "--winpot", "goles:mas_1.5=+400",
              "--winpot", "goles:mas_3.5=+900", "--winpot", "ambos_anotan:si=+400"])
    ev = _salida(capsys)
    montos = [r["monto_papel"] for r in ev["evaluacion"] if r.get("apostar_papel")]
    assert ev["disponible_hoy"] is None
    assert sum(montos) > 1000 and all(m > 0 for m in montos)
    assert not any(r.get("recortada") for r in ev["evaluacion"])
    cli.main(["bank"])
    b = _salida(capsys)
    assert b["disponible_hoy"] is None and b["limite_en_papel"] is False


def test_papel_sin_maximo_de_sencillas_fuertes(papel, capsys):
    """Salva (28-oct-2026): en papel no hay máximo por día de sencillas fuertes."""
    R = papel.R
    a = _pron_cat(R, "A vs B", [("ganador", "1", 0.62, "alta")])
    b = _pron_cat(R, "C vs D", [("ganador", "1", 0.62, "media")])
    papel.main(["evaluar", "--id", a["id"], "--winpot", "ganador:1=-160"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["categoria"] == "sencilla_fuerte" and r["apostar_papel"] is True
    papel.main(["evaluar", "--id", b["id"], "--winpot", "ganador:1=-170"])
    (r,) = _salida(capsys)["evaluacion"]
    assert r["categoria"] == "sencilla_fuerte" and r["apostar_papel"] is True and r["monto_papel"] > 0
    papel.main(["bank"])
    b_ = _salida(capsys)
    assert b_["usadas_hoy"]["sencilla_fuerte"] == 2 and b_["max_por_dia"] is None


def test_dinero_real_conserva_limite_y_maximos(cli):
    """Fuera de la prueba en papel siguen el límite del día y los máximos."""
    from predicciones import valor as V
    assert V.max_por_dia(V.FUERTE, papel=False) == 1 and V.max_por_dia(V.COMBINADA, papel=False) == 2
    assert V.max_por_dia(V.FUERTE, papel=True) == V.SIN_MAXIMO
