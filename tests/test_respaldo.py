"""Respaldo estadístico (forma, historial directo, últimos 10): solo se muestra."""
import pandas as pd

from predicciones import respaldo as RS


def _hist():
    filas = []
    # A gana 2-1 sus 6 partidos de local contra B..G; B pierde 0-3 de visitante
    for k, (l, v, gl, gv, c) in enumerate([
        ("A", "B", 2, 1, 10), ("C", "A", 0, 0, 7), ("A", "D", 3, 1, 12), ("B", "C", 1, 1, 9),
        ("E", "B", 3, 0, 8), ("A", "B", 1, 1, 11), ("B", "D", 0, 2, 6)]):
        filas.append({"fecha": f"2026-10-{10 + k:02d}", "local": l, "visitante": v, "goles_local": gl,
                      "goles_visitante": gv, "terminado": True, "corners_local": c / 2, "corners_visitante": c / 2,
                      "tarjetas_local": 2, "tarjetas_visitante": 2, "tiros_local": 10, "tiros_visitante": 8})
    return pd.DataFrame(filas)


def test_forma_y_historial_directo():
    d = RS._terminados(_hist())
    f = RS.forma(d, "A")
    assert f["racha"] == "GEGE" and f["puntos"] == 8 and (f["goles_favor"], f["goles_contra"]) == (6, 3)
    h = RS.historial_directo(d, "A", "B")
    assert [x["marcador"] for x in h] == ["2-1", "1-1"]


def test_ultimos10_por_seleccion():
    sels = [{"mercado": "goles", "seleccion": "mas_2.5"}, {"mercado": "ganador", "seleccion": "1"},
            {"mercado": "corners", "seleccion": "mas_8.5"}, {"mercado": "goles_equipo", "seleccion": "local_mas_0.5"},
            {"mercado": "tiros_jugador", "seleccion": "X:mas_0.5"}]
    out = RS.respaldo(_hist(), "A", "B", sels)
    goles, ganador, corners, gl, tiros = sels
    # partidos de A: 2-1, 0-0, 3-1, 1-1 -> más de 2.5 en 2; de B: 2-1, 1-1, 3-0, 1-1, 0-2 -> 2
    assert goles["ultimos10"] == {"local": "2/4", "visitante": "2/5"}
    # "1" = gana A: A ganó 2 de 4; B (como visitante del pronóstico) perdió 3 de 5
    assert ganador["ultimos10"] == {"local": "2/4", "visitante": "3/5"}
    assert corners["ultimos10"]["local"] == "3/4"
    # goles del local: A anotó en 3 de 4; B recibió gol en sus 5
    assert gl["ultimos10"] == {"local": "3/4", "visitante": "5/5"}
    assert "ultimos10" not in tiros
    assert out["forma_local"]["racha"] == "GEGE" and len(out["historial_directo"]) == 2


def test_sin_historial_no_falla():
    sels = [{"mercado": "goles", "seleccion": "mas_2.5"}]
    assert "nota" in RS.respaldo(_hist(), "Equipo Fantasma", "B", sels) and "ultimos10" not in sels[0]
