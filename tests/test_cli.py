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
