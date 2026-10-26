"""Pago anticipado de Winpot: ganador 1/2 pagado si el equipo va 2 goles arriba."""
import itertools
import json
from datetime import date

import numpy as np
import pytest

from predicciones import pago_anticipado as PA
from predicciones.calificar import calificar
from predicciones.datos import espn
from predicciones.pronostico import ajustar_modelos, pronosticar


def _fuerza_bruta(x, y):
    """Fracción de órdenes distintos de x goles L y y goles V que tocan L +2."""
    ordenes = set(itertools.permutations("L" * x + "V" * y))
    return sum(PA.ventajas_maximas(o)[0] >= 2 for o in ordenes) / len(ordenes)


def test_prob_toca_dos_a_mano():
    assert PA.prob_toca_dos(2, 2) == pytest.approx(1 / 6)    # solo LLVV
    assert PA.prob_toca_dos(2, 3) == pytest.approx(1 / 10)   # solo LLVVV
    assert PA.prob_toca_dos(3, 3) == pytest.approx(6 / 20)
    assert PA.prob_toca_dos(1, 1) == 0 and PA.prob_toca_dos(0, 3) == 0
    with pytest.raises(ValueError):
        PA.prob_toca_dos(3, 1)


def test_prob_toca_dos_contra_fuerza_bruta():
    for x in range(0, 6):
        for y in range(x, 6):
            assert PA.prob_toca_dos(x, y) == pytest.approx(_fuerza_bruta(x, y)), (x, y)


def test_extra_con_matriz_a_mano():
    m = np.zeros((6, 6))
    m[2, 2] = 0.5   # 2-2: cada equipo fue +2 en 1/6 de los órdenes
    m[3, 1] = 0.3   # el local gana: no suma al local; el visitante (1 gol) nunca fue +2
    m[0, 0] = 0.2
    el, ev = PA.extra(m, calibracion=1.0)
    assert el == pytest.approx(0.5 / 6) and ev == pytest.approx(0.5 / 6)
    assert PA.extra(m)[0] == pytest.approx(0.5 / 6 * PA.CALIBRACION)
    g = PA.aplicar({"1": 0.3, "X": 0.5, "2": 0.2}, m, calibracion=1.0)
    assert g == pytest.approx({"1": 0.3 + 0.5 / 6, "X": 0.5, "2": 0.2 + 0.5 / 6})


def test_ventajas_maximas():
    assert PA.ventajas_maximas([]) == (0, 0)
    assert PA.ventajas_maximas(list("LLVV")) == (2, 0)
    assert PA.ventajas_maximas(list("VLLLVVV")) == (2, 1)


@pytest.fixture(scope="module")
def modelos(liga_sintetica):
    return ajustar_modelos(liga_sintetica)


PIN = {"1": 1.85, "X": 3.7, "2": 4.6, "mas_2.5": 1.95, "menos_2.5": 1.95}


def test_pronostico_con_y_sin_pago(modelos):
    con = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=PIN, confianza_datos="alta")
    sin = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=PIN, confianza_datos="alta",
                      pago_anticipado=False)
    g, base = con.probabilidades["ganador"], con.ganador_sin_pago
    assert base == pytest.approx(sin.probabilidades["ganador"])
    assert 0.01 < g["1"] - base["1"] < 0.06 and 0.005 < g["2"] - base["2"] < 0.05
    assert g["X"] == base["X"]  # el empate no tiene pago anticipado
    # nada más cambia: doble oportunidad, hándicap 0, primer tiempo
    for mercado in ("doble_oportunidad", "handicap", "primer_tiempo", "goles"):
        assert con.probabilidades[mercado] == pytest.approx(sin.probabilidades[mercado])
    assert con.probabilidades["doble_oportunidad"]["12"] == pytest.approx(base["1"] + base["2"])
    assert any(n.startswith("Pago anticipado de Winpot") for n in con.notas)
    assert PA.NOTA_SIN in sin.notas
    filas = {(f["mercado"], f["seleccion"]): f for f in con.tabla()}
    assert filas[("ganador", "1")]["prob_sin_pago_anticipado"] == round(base["1"], 3)
    assert "prob_sin_pago_anticipado" not in filas[("ganador", "X")]
    assert all("prob_sin_pago_anticipado" not in f for f in sin.tabla())


def test_doble_oportunidad_mira_el_1x2_real(modelos):
    # parejo: ningún resultado llega a 55% aunque con el pago anticipado el local se acerque
    pin = {"1": 1.95, "X": 3.6, "2": 4.0, "mas_2.5": 1.9, "menos_2.5": 2.0}
    p = pronosticar(modelos, "Equipo 6", "Equipo 5", cuotas_pinnacle=pin, confianza_datos="alta")
    assert any(s.mercado == "doble_oportunidad" for s in p.selecciones()) == (max(p.ganador_sin_pago.values()) < 0.55)


# ------------------------------------------------------------------ calificación

def test_calificar_pago_anticipado():
    r = {"goles_local": 2, "goles_visitante": 2, "ventaja_max_local": 2, "ventaja_max_visitante": 0}
    assert calificar("ganador", "1", r, pago_anticipado=True) == "ganada"    # iba 2-0 y empató 2-2
    assert calificar("ganador", "1", r) == "perdida"                          # sin pago anticipado
    assert calificar("ganador", "2", r, pago_anticipado=True) == "perdida"   # el visitante nunca fue +2
    assert calificar("ganador", "X", r, pago_anticipado=True) == "ganada"    # el empate se paga normal
    assert calificar("handicap", "local_+0", r, pago_anticipado=True) == "devuelta"
    # perdió 2-3 tras ir 2-0: gana el 1; el empate no recibe nada
    r = {"goles_local": 2, "goles_visitante": 3, "ventaja_max_local": 2, "ventaja_max_visitante": 1}
    assert calificar("ganador", "1", r, pago_anticipado=True) == "ganada"
    assert calificar("ganador", "X", r, pago_anticipado=True) == "perdida"
    assert calificar("doble_oportunidad", "1X", r, pago_anticipado=True) == "perdida"


def test_calificar_pago_anticipado_sin_secuencia():
    # sin la secuencia de goles no se adivina: queda para revisión
    assert calificar("ganador", "1", {"goles_local": 2, "goles_visitante": 2}, True) == "sin_datos"
    # con menos de 2 goles nunca fue 2 arriba: no hace falta la secuencia
    assert calificar("ganador", "1", {"goles_local": 1, "goles_visitante": 1}, True) == "perdida"
    # si ganó, gana aunque no haya secuencia
    assert calificar("ganador", "2", {"goles_local": 0, "goles_visitante": 3}, True) == "ganada"


# ------------------------------------------------------------------ ESPN

def _gol(equipo, periodo, minuto, texto="Goal!"):
    valor, _, extra = minuto.partition("+")
    base = 2700.0 if periodo == 1 and extra else 5400.0 if periodo == 2 and extra else float(valor) * 60
    return {"scoringPlay": True, "team": {"id": equipo}, "period": {"number": periodo},
            "clock": {"value": base, "displayValue": f"{minuto}'"}, "text": texto}


def _summary(eventos):
    return {"header": {"competitions": [{"competitors": [
        {"homeAway": "home", "team": {"id": "1", "displayName": "Local"}},
        {"homeAway": "away", "team": {"id": "2", "displayName": "Visita"}}]}]},
        "keyEvents": eventos}


def test_secuencia_goles_espn():
    eventos = [
        _gol("1", 1, "10"),
        _gol("1", 1, "45+3"),                       # añadido del primer tiempo
        _gol("2", 2, "90+4"),                       # ESPN deja clock en 5400: el +4 ordena
        _gol("2", 2, "90+1"),
        {"scoringPlay": False, "team": {"id": "1"}, "period": {"number": 2}, "clock": {"value": 3000}},
        _gol("2", 2, "60", "Own Goal by X, Local."),  # autogol: ESPN pone al equipo beneficiado
        _gol("1", 3, "100"),                        # prórroga: no cuenta
    ]
    sec = espn.secuencia_goles(_summary(eventos), 2, 3)
    assert sec == list("LLVVV")
    assert PA.ventajas_maximas(sec) == (2, 1)
    assert espn.secuencia_goles(_summary(eventos), 2, 2) is None   # no cuadra con el marcador
    assert espn.secuencia_goles(_summary([]), 1, 0) is None
    assert espn.secuencia_goles(_summary([]), 0, 0) == []


def test_resultado_trae_ventajas(monkeypatch):
    datos = _summary([_gol("1", 1, "10"), _gol("1", 1, "20"), _gol("2", 2, "70"), _gol("2", 2, "80")])
    comp = datos["header"]["competitions"][0]
    comp.update(date="2026-10-20T19:00Z", status={"type": {"name": "STATUS_FULL_TIME", "completed": True}})
    for c, g in zip(comp["competitors"], (2, 2)):
        c.update(score=str(g), linescores=[{"value": g}, {"value": 0}])
    monkeypatch.setattr(espn, "obtener", lambda *a, **k: datos)
    r = espn.resultado("premier", "123")
    assert (r["goles_local"], r["goles_visitante"]) == (2, 2)
    assert (r["ventaja_max_local"], r["ventaja_max_visitante"]) == (2, 0)


# ------------------------------------------------------------------ CLI

@pytest.fixture
def cli(tmp_path, monkeypatch, liga_sintetica):
    import importlib
    monkeypatch.setenv("PRED_RAIZ", str(tmp_path))
    from predicciones import registro
    from predicciones.datos import historiales
    importlib.reload(registro)
    importlib.reload(historiales)
    from predicciones import cli
    importlib.reload(cli)
    monkeypatch.setattr(cli.B, "PRUEBA_PAPEL_HASTA", date(2999, 12, 31))
    (tmp_path / "datos" / "historial").mkdir(parents=True)
    liga_sintetica.assign(espn_id=range(len(liga_sintetica))).to_csv(tmp_path / "datos" / "historial" / "premier.csv", index=False)
    return cli


def _pron(cli, capsys, *extra):
    cli.main(["pronostico", "--liga", "premier", "--local", "Equipo 6", "--visitante", "Equipo 5", "--sin-red",
              "--confianza", "alta", "--pinnacle", "1=-118,X=+270,2=+360,mas_2.5=-105,menos_2.5=-105", *extra])
    return json.loads(capsys.readouterr().out)


def test_cli_bandera(cli, capsys):
    con = _pron(cli, capsys)
    sin = _pron(cli, capsys, "--sin-pago-anticipado")
    assert con["pago_anticipado"] is True and sin["pago_anticipado"] is False
    p_con = next(s for s in con["selecciones"] if s["mercado"] == "ganador" and s["seleccion"] == "1")
    p_sin = next(s for s in sin["selecciones"] if s["mercado"] == "ganador" and s["seleccion"] == "1")
    assert p_con["prob"] > p_sin["prob"] and p_con["prob_sin_pago_anticipado"] == p_sin["prob"]


def _con_apuestas(R, pago):
    from datetime import datetime, timedelta, timezone
    inicio = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    p = {"partido": "A vs B", "liga": "premier", "espn_id": "999", "inicio_utc": inicio, "estado": "pendiente",
         "selecciones": [{"mercado": "ganador", "seleccion": k} for k in ("1", "X", "2")],
         "apuestas_papel": [{"mercado": "ganador", "seleccion": "1", "cuota": 2.0, "monto": 100,
                             "evaluado": R.ahora().isoformat(), "estado": "pendiente"}]}
    if pago is not None:
        p["pago_anticipado"] = pago
    p = R.agregar_pronostico(p)
    R.agregar_apuesta({"tipo": "sencilla", "monto": 100, "cuota": 2.0, "patas": [
        {"pronostico": p["id"], "mercado": "ganador", "seleccion": "1", "cuota": 2.0, "estado": "pendiente"}]})
    R.guardar_parlays_papel(date.today(), [{"clave": "k", "tipo": "parlay", "categoria": "valor", "cuota": 4.0,
                                            "monto": 100, "creado": R.ahora().isoformat(), "estado": "pendiente",
                                            "patas": [{"pronostico": p["id"], "mercado": "ganador", "seleccion": "1",
                                                       "cuota": 2.0, "estado": "pendiente"},
                                                      {"pronostico": p["id"], "mercado": "goles",
                                                       "seleccion": "mas_2.5", "cuota": 2.0, "estado": "pendiente"}]}])
    return p


@pytest.mark.parametrize("pago,esperado", [(True, "ganada"), (False, "perdida"), (None, "perdida")])
def test_cli_califica_2_2_tras_ir_2_0(cli, capsys, monkeypatch, pago, esperado):
    R = cli.R
    p = _con_apuestas(R, pago)  # None: pronóstico viejo, sin el campo → sin pago anticipado
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {
        "terminado": True, "goles_local": 2, "goles_visitante": 2, "ventaja_max_local": 2, "ventaja_max_visitante": 0})
    cli.main(["calificar"])
    capsys.readouterr()
    pr = R.buscar_pronostico(p["id"])
    assert pr["resultado"]["ventaja_max_local"] == 2
    estados = {s["seleccion"]: s["estado"] for s in pr["selecciones"]}
    assert estados == {"1": esperado, "X": "ganada", "2": "perdida"}
    assert pr["apuestas_papel"][0]["estado"] == esperado          # sencilla en papel
    assert R.apuestas()[0]["estado"] == esperado                   # apuesta real
    assert R.parlays_papel()[0]["estado"] == esperado              # parlay en papel (la otra pata: 4 goles)


def test_cli_sin_secuencia_queda_para_revision(cli, capsys, monkeypatch):
    R = cli.R
    p = _con_apuestas(R, True)
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {"terminado": True, "goles_local": 2, "goles_visitante": 2})
    cli.main(["calificar"])
    capsys.readouterr()
    pr = R.buscar_pronostico(p["id"])
    assert pr["estado"] == "pendiente"  # se reintenta hasta 48 h y luego se revisa a mano
    assert {s["seleccion"]: s["estado"] for s in pr["selecciones"]}["1"] == "sin_datos"
    assert R.apuestas()[0]["estado"] == "pendiente"
