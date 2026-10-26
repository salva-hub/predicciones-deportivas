"""Flujo completo sin red: pronóstico -> evaluar con cuotas de Winpot -> apostar -> bank."""
import importlib
import json
from datetime import datetime, timedelta, timezone

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
    (tmp_path / "datos" / "historial").mkdir(parents=True)
    liga_sintetica.assign(espn_id=range(len(liga_sintetica))).to_csv(tmp_path / "datos" / "historial" / "premier.csv", index=False)
    return cli


def _salida(capsys):
    return json.loads(capsys.readouterr().out)


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
