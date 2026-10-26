"""Tiros a puerta por jugador: datos de ESPN, modelo, evaluar y calificación."""
import json
import math

import pandas as pd
import pytest

from predicciones import valor as V
from predicciones.calificar import DEVUELVE, GANA, PIERDE, SIN_DATOS, calificar
from predicciones.datos import jugadores as J
from predicciones.modelos import tiros_jugador as T

from test_cli import _salida, cli, papel  # noqa: F401 - fixtures


# ---------------------------------------------------------------- datos de ESPN

def _jug(id_, nombre, titular=True, pos="Forward", sot=0, plays=None, entra=False, sale=False, aparece=None):
    stats = [{"name": "shotsOnTarget", "value": sot}, {"name": "totalShots", "value": sot + 1}]
    if aparece is not None:
        stats.append({"name": "appearances", "value": aparece})
    return {"starter": titular, "subbedIn": entra, "subbedOut": sale, "athlete": {"id": id_, "displayName": nombre},
            "position": {"name": pos if titular else "Substitute", "abbreviation": "SUB" if not titular else "F"},
            "plays": plays or [], "stats": stats}


def _cambio(minuto):
    return {"clock": {"displayValue": minuto}, "substitution": True}


def test_minutos_con_cambios_y_roja():
    assert J.minutos(_jug("1", "A")) == 90
    assert J.minutos(_jug("1", "A", sale=True, plays=[_cambio("74'")])) == 74
    assert J.minutos(_jug("1", "A", titular=False, entra=True, plays=[_cambio("30'")])) == 60
    assert J.minutos(_jug("1", "A", titular=False, entra=True, plays=[_cambio("90'+4'")])) == 1
    assert J.minutos(_jug("1", "A", titular=False)) == 0
    roja = {"clock": {"displayValue": "40'"}, "redCard": True}
    assert J.minutos(_jug("1", "A", plays=[roja])) == 40
    # sin minuto del cambio: titular sustituido 70', suplente que entra 20'
    assert J.minutos(_jug("1", "A", sale=True)) == 70
    assert J.minutos(_jug("1", "A", titular=False, entra=True)) == 20


def test_posiciones_antes_y_despues_del_partido():
    pos = lambda n, a="": J.posicion({"position": {"name": n, "abbreviation": a}})  # noqa: E731
    assert pos("Goalkeeper", "G") == "G"
    assert pos("Defender", "D") == "D" and pos("Center Left Defender", "CD-L") == "D" and pos("Right Back", "RB") == "D"
    assert pos("Midfielder", "M") == "M" and pos("Attacking Midfielder Right", "AM-R") == "M"
    assert pos("Forward", "F") == "F" and pos("Left Forward", "LF") == "F"
    assert pos("Substitute", "SUB") is None


def _resumen(titulares_con_stats=True):
    local = [_jug(str(i), f"Local {i}", sot=i % 3) for i in range(11)]
    local += [_jug("99", "Suplente", titular=False, entra=True, plays=[_cambio("80'")], sot=1),
              _jug("98", "Banca", titular=False)]
    visita = [_jug(str(100 + i), f"Visita {i}") for i in range(11)]
    if not titulares_con_stats:
        for p in local + visita:
            p["stats"] = []
    return {"rosters": [{"homeAway": "home", "team": {"id": "1", "displayName": "Local FC"}, "roster": local},
                        {"homeAway": "away", "team": {"id": "2", "displayName": "Visita FC"}, "roster": visita}]}


def test_actuaciones_y_alineacion():
    filas = J.actuaciones(_resumen())
    sup = next(x for x in filas if x["nombre"] == "Suplente")
    banca = next(x for x in filas if x["nombre"] == "Banca")
    assert sup["participo"] and sup["minutos"] == 10 and sup["tiros_puerta"] == 1
    assert not banca["participo"] and banca["minutos"] == 0
    alin = J.alineacion(_resumen(titulares_con_stats=False))
    assert len(alin["home"]) == 11 and alin["home"][0]["posicion"] == "F"
    # sin alineación oficial (nadie marcado titular): None
    sin = _resumen()
    for t in sin["rosters"]:
        for p in t["roster"]:
            p["starter"] = False
    assert J.alineacion(sin) is None
    assert J.alineacion({}) is None


def test_tiros_por_jugador_para_calificar():
    t = J.tiros_por_jugador(_resumen())
    assert t["2"] == {"nombre": "Local 2", "equipo": "Local FC", "participo": True, "tiros_puerta": 2}
    assert t["98"]["participo"] is False
    assert J.tiros_por_jugador(_resumen(titulares_con_stats=False)) is None


def test_resumen_cache_larga_solo_si_terminado(tmp_path, monkeypatch):
    from predicciones.datos import http
    monkeypatch.setattr(http, "CACHE", tmp_path)
    vivo = {"header": {"competitions": [{"status": {"type": {"completed": False}}}]}, "rosters": []}
    final = {"header": {"competitions": [{"status": {"type": {"completed": True}}}]}, "rosters": [{"x": 1}]}
    descargas = []
    respuestas = iter([vivo, final])

    def bajar(url, params, timeout):
        descargas.append(url)
        return json.dumps(next(respuestas))

    monkeypatch.setattr(http, "_descargar", bajar)
    # antes del partido (caché corta) se guarda la versión sin terminar en la misma URL
    assert not J.terminado(J.resumen("laliga", "1", horas_cache=0.1))
    # luego, con caché larga: lo guardado no está terminado -> se baja otra vez
    assert J.terminado(J.resumen("laliga", "1")) and len(descargas) == 2
    # ya terminado en caché: no se descarga más
    assert J.resumen("laliga", "1") == final and len(descargas) == 2


def test_inicio_temporada_anterior():
    from datetime import date
    assert J.inicio_temporada_anterior(date(2026, 10, 26), "laliga") == date(2025, 7, 1)
    assert J.inicio_temporada_anterior(date(2026, 3, 1), "premier") == date(2024, 7, 1)
    assert J.inicio_temporada_anterior(date(2026, 10, 26), "mls") == date(2025, 1, 1)


# ---------------------------------------------------------------- modelo

def _act(partidos=20):
    """Equipo con un delantero (0.9 tiros a puerta por 90), dos medios y un fichaje sin minutos."""
    filas = []
    for k in range(partidos):
        fecha = pd.Timestamp("2026-01-01") + pd.Timedelta(days=7 * k)
        for jid, pos, sot, mins in (("9", "F", 1 if k % 10 else 0, 80), ("8", "M", 0 if k % 3 else 1, 90),
                                    ("7", "M", 0, 90), ("5", "D", 0, 90)):
            filas.append({"jugador_id": jid, "nombre": jid, "equipo": "Local FC", "titular": True, "posicion": pos,
                          "minutos": mins, "tiros_puerta": sot, "espn_id": str(k), "fecha": fecha})
    return pd.DataFrame(filas)


def test_esperado_encoge_hacia_la_posicion_y_tope_de_minutos():
    act = _act()
    tasas = T.tasas_por_posicion(act)
    assert tasas["F"] == pytest.approx(18 / 1600 * 90)
    # delantero con 1,600 minutos: cerca de su propia tasa, encogido un poco hacia la de su posición (la misma)
    e = T.esperado(act, "9", "F")
    assert e.tasa90 == pytest.approx(tasas["F"])
    assert e.minutos_esperados == 80 and e.titularidades == 20
    # medio con 90' de promedio: tope de 85
    m = T.esperado(act, "8", "M")
    assert m.minutos_esperados == T.TOPE_MINUTOS
    propia = 7 / 1800 * 90
    assert min(propia, tasas["M"]) <= m.tasa90 <= max(propia, tasas["M"])
    # fichaje sin minutos: tasa de su posición en el equipo y minutos por defecto
    n = T.esperado(act, "nuevo", "F")
    assert n.tasa90 == pytest.approx(tasas["F"]) and n.minutos_esperados == T.MINUTOS_DEFECTO["F"]
    # con 450 minutos propios pesa la mitad
    pocos = pd.DataFrame([{"jugador_id": "x", "titular": True, "posicion": "F", "minutos": 90, "tiros_puerta": 2}] * 5)
    e = T.esperado(pd.concat([act, pocos]), "x", "F", tasas=tasas)
    assert e.tasa90 == pytest.approx((2.0 + tasas["F"]) / 2, rel=0.05)
    # rival y sede multiplican
    base = T.esperado(act, "9", "F").mu
    assert base > 0 and T.esperado(act, "9", "F", f_rival=1.2, f_sede=1.1).mu == pytest.approx(base * 1.32)


def test_mercados_poisson_encogidos():
    p = T.mercados(1.0, factor=1.0)
    assert p["mas_0.5"] == pytest.approx(1 - math.exp(-1))
    assert p["mas_1.5"] == pytest.approx(1 - 2 * math.exp(-1))
    q = T.mercados(1.0)  # con el encogimiento vigente, más cerca de la tasa base
    assert abs(q["mas_0.5"] - T.BASE_LINEA[0.5]) < abs(p["mas_0.5"] - T.BASE_LINEA[0.5])
    assert q["mas_0.5"] > q["mas_1.5"] > q["mas_2.5"]


def test_factores_liga():
    filas = []
    for k in range(40):
        filas.append({"fecha": pd.Timestamp("2026-01-01") + pd.Timedelta(days=k), "local": "Coladera" if k % 2 else "Muro",
                      "visitante": "Otro", "tiros_puerta_local": 5, "tiros_puerta_visitante": 8 if k % 2 else 2})
    f = T.factores_liga(pd.DataFrame(filas))
    assert f.rival("Coladera") > 1 > f.rival("Muro")
    assert f.rival("Desconocido") == 1.0
    assert f.sede(True, neutral=True) == 1.0
    assert T.factores_liga(pd.DataFrame()).rival("x") == 1.0


def test_pronostico_partido_prefiere_ataque_y_limita():
    alin = {"home": [{"jugador_id": "1", "nombre": "Portero", "posicion": "G", "equipo": "L"}]
            + [{"jugador_id": str(i), "nombre": f"J{i}", "posicion": p, "equipo": "L"}
               for i, p in zip(range(2, 12), "DDDDMMMMFF")],
            "away": []}
    filas = T.pronostico_partido(alin, _act(), None, T.FactoresLiga({}, 1.0, 1.0), "L", "V")
    assert len(filas) == T.MAX_POR_EQUIPO
    assert all(f["posicion"] in ("F", "M") for f in filas)
    assert filas[0]["posicion"] == "F" and "mas_0.5" in filas[0]["probabilidades"]


def test_valor_minimo_10_por_ciento_y_confianza_baja():
    s = V.Seleccion("A vs B", "tiros_jugador", "J:mas_0.5", prob=0.5, confianza="baja", cuota_ofrecida=2.15)
    assert s.ve_minimo == 0.10 and not V.tiene_valor(s)  # VE 7.5%
    s.cuota_ofrecida = 2.25
    assert V.tiene_valor(s) and V.categoria_sencilla(s) == V.VALOR
    assert not V.sirve_para_combinada(s)  # confianza baja: nunca combinada
    assert V.ve_min_parlay_pata("tiros_jugador") == 0.10 and V.ve_min_parlay_pata("goles") == V.VE_MIN_PARLAY_PATA


# ---------------------------------------------------------------- calificación (reglas de Winpot)

def test_calificar_tiros_jugador():
    r = {"goles_local": 1, "goles_visitante": 0, "tiros_jugadores": {
        "10": {"nombre": "Kylian Mbappé", "participo": True, "tiros_puerta": 2},
        "11": {"nombre": "Banca", "participo": False, "tiros_puerta": 0}}}
    assert calificar("tiros_jugador", "Kylian Mbappé:mas_1.5", r) == GANA
    assert calificar("tiros_jugador", "kylian mbappe:mas_2.5", r) == PIERDE
    # no participó o no está en el acta: devuelta
    assert calificar("tiros_jugador", "Banca:mas_0.5", r) == DEVUELVE
    assert calificar("tiros_jugador", "Nadie:mas_0.5", r) == DEVUELVE
    # sin datos por jugador, o con prórroga (ESPN la incluye): se liquida a mano
    assert calificar("tiros_jugador", "Kylian Mbappé:mas_0.5", {"goles_local": 1, "goles_visitante": 0}) == SIN_DATOS
    assert calificar("tiros_jugador", "Kylian Mbappé:mas_0.5", {**r, "prorroga": True}) == SIN_DATOS


def test_calificar_tiros_jugador_por_id_con_nombres_repetidos():
    r = {"tiros_jugadores": {
        "1": {"nombre": "Rodri", "participo": True, "tiros_puerta": 2},
        "2": {"nombre": "Rodri", "participo": True, "tiros_puerta": 0},
        "3": {"nombre": "J. Pérez", "participo": True, "tiros_puerta": 1}}}
    # el id manda: cada Rodri se califica con sus propios tiros
    assert calificar("tiros_jugador", "Rodri:mas_0.5", r, jugador_id="1") == GANA
    assert calificar("tiros_jugador", "Rodri:mas_0.5", r, jugador_id="2") == PIERDE
    # aunque ESPN escriba distinto el nombre
    assert calificar("tiros_jugador", "Juan Pérez:mas_0.5", r, jugador_id="3") == GANA
    # sin id y con nombre repetido: no se adivina (a mano)
    assert calificar("tiros_jugador", "Rodri:mas_0.5", r) == SIN_DATOS
    # sin id, nombre único: por nombre
    assert calificar("tiros_jugador", "j perez:mas_0.5", r) == GANA


def test_texto_seleccion(registro_tmp):
    assert registro_tmp.texto_seleccion("A vs B", "tiros_jugador", "Budimir:mas_0.5") == "Budimir: más de 0.5 tiros a puerta"


# ---------------------------------------------------------------- flujo en la línea de comandos

PARTIDO = {"espn_id": "777", "liga": "premier", "inicio_utc": "2099-01-01T20:00:00+00:00",
           "inicio_sonora": "2099-01-01 13:00", "local": "Equipo 11", "visitante": "Equipo 0",
           "estadio": None, "ciudad": None, "pais": None}


def _alin():
    def lado(eq, pref):
        return ([{"jugador_id": f"{pref}1", "nombre": f"Portero {eq}", "posicion": "G", "equipo": eq}]
                + [{"jugador_id": f"{pref}{i}", "nombre": n, "posicion": p, "equipo": eq}
                   for i, (n, p) in enumerate([("Ángel Pérez", "F"), ("Juan Gol", "F"), ("Medio Uno", "M"),
                                               ("Medio Dos", "M"), ("Defensa Uno", "D")], start=2)])
    return {"home": lado("Equipo 11", "L"), "away": lado("Equipo 0", "V")}


def _act_equipo(equipo):
    pref = "L" if equipo == "Equipo 11" else "V"
    act = _act()
    act["jugador_id"] = act["jugador_id"].map({"9": f"{pref}2", "8": f"{pref}4", "7": f"{pref}5", "5": f"{pref}6"})
    act["equipo"] = equipo
    return act


@pytest.fixture
def con_alineacion(cli, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "_buscar_partido", lambda *a: dict(PARTIDO))
    monkeypatch.setattr(cli.jugadores, "alineacion_partido", lambda liga, eid: _alin())
    monkeypatch.setattr(cli.jugadores, "actuaciones_equipo", lambda liga, hist, eq, antes: _act_equipo(eq))
    return cli


def _pronostico_red(cli, capsys):  # noqa: F811
    cli.main(["pronostico", "--liga", "premier", "--local", "Equipo 11", "--visitante", "Equipo 0", "--confianza", "alta",
              "--pinnacle", "1=-150,X=+280,2=+400,mas_2.5=-110,menos_2.5=-110"])
    return _salida(capsys)


def test_pronostico_con_alineacion_agrega_tiros(con_alineacion, capsys):
    p = _pronostico_red(con_alineacion, capsys)
    tiros = [s for s in p["selecciones"] if s["mercado"] == "tiros_jugador"]
    assert tiros and all(s["confianza"] == "baja" for s in tiros)
    jugadores = {s["jugador"] for s in tiros}
    assert "Portero Equipo 11" not in jugadores and "Ángel Pérez" in jugadores
    assert any("alineación oficial de ESPN" in n for n in p["notas"])
    assert {t["equipo"] for t in p["tiros_jugador"]} == {"Equipo 11", "Equipo 0"}


def test_pronostico_sin_alineacion_avisa(con_alineacion, capsys, monkeypatch):
    monkeypatch.setattr(con_alineacion.jugadores, "alineacion_partido", lambda liga, eid: None)
    p = _pronostico_red(con_alineacion, capsys)
    assert not [s for s in p["selecciones"] if s["mercado"] == "tiros_jugador"]
    assert con_alineacion.NOTA_SIN_ALINEACION in p["notas"]


def test_evaluar_tiros_jugador(con_alineacion, capsys):
    cli = con_alineacion
    p = _pronostico_red(cli, capsys)
    fila = next(s for s in p["selecciones"] if s["seleccion"] == "Ángel Pérez:mas_0.5")
    justa = cli.C.americana_a_decimal(float(fila["cuota_justa"]))
    alta = cli.C.formato_americano(justa * 1.25)   # VE 25%
    baja = cli.C.formato_americano(justa * 1.05)   # VE 5%: no alcanza el 10%
    # el nombre se acepta sin acentos ni mayúsculas, o solo el apellido
    cli.main(["evaluar", "--id", p["id"], "--winpot", f"tiros_jugador:angel perez:mas_0.5={alta}",
              "--winpot", f"tiros_jugador:Gol:mas_0.5={baja}", "--winpot", "tiros_jugador:Nadie:mas_0.5=+300"])
    ev = _salida(capsys)["evaluacion"]
    assert ev[0]["seleccion"] == "Ángel Pérez:mas_0.5"
    assert ev[0]["apostar"] is True and ev[0]["categoria"] == "valor" and ev[0]["confianza"] == "baja"
    assert ev[0]["monto_sencilla"] == 200  # 2% del bank con confianza baja
    assert ev[1]["seleccion"] == "Juan Gol:mas_0.5" and ev[1]["apostar"] is False and ev[1]["sirve_para_parlay"] is False
    assert "error" in ev[2]


def test_papel_tiros_jugador_se_califica_y_anula(con_alineacion, capsys, monkeypatch):
    cli = con_alineacion
    monkeypatch.setattr(cli.B, "PRUEBA_PAPEL_HASTA", cli.date(2999, 12, 31))
    p = _pronostico_red(cli, capsys)
    filas = {s["seleccion"]: s for s in p["selecciones"] if s["mercado"] == "tiros_jugador"}
    cuotas = []
    for sel in ("Ángel Pérez:mas_0.5", "Medio Uno:mas_0.5"):
        justa = cli.C.americana_a_decimal(float(filas[sel]["cuota_justa"]))
        cuotas += ["--winpot", f"tiros_jugador:{sel}={cli.C.formato_americano(justa * 1.3)}"]
    cli.main(["evaluar", "--id", p["id"], *cuotas])
    ev = _salida(capsys)["evaluacion"]
    assert all(x["apostar_papel"] for x in ev)
    # el partido terminó: Ángel Pérez tiró 1 a puerta y Medio Uno no jugó (devuelta)
    R = cli.R
    guardado = R.buscar_pronostico(p["id"])
    guardado["inicio_utc"] = "2020-01-01T20:00:00+00:00"
    R.actualizar_pronostico(guardado)
    monkeypatch.setattr(cli.espn, "resultado", lambda *a: {
        "terminado": True, "goles_local": 1, "goles_visitante": 0,
        # por id: ESPN puede escribir el nombre distinto que en la alineación
        "tiros_jugadores": {"L2": {"nombre": "A. Pérez", "participo": True, "tiros_puerta": 1},
                            "L4": {"nombre": "Medio Uno", "participo": False, "tiros_puerta": 0},
                            "X9": {"nombre": "Medio Uno", "participo": True, "tiros_puerta": 3}}})
    cli.main(["calificar"])
    capsys.readouterr()
    g = R.buscar_pronostico(p["id"])
    estados = {x["seleccion"]: (x["estado"], x["ganancia"]) for x in g["apuestas_papel"]}
    assert estados["Ángel Pérez:mas_0.5"][0] == "ganada" and estados["Ángel Pérez:mas_0.5"][1] > 0
    assert estados["Medio Uno:mas_0.5"] == ("devuelta", 0.0)
    assert g["resultado"]["tiros_jugadores"]["L2"]["tiros_puerta"] == 1
    assert json.dumps(g, ensure_ascii=False)  # serializable
