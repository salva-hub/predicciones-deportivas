"""Tarjetas con la regla de Winpot: amarilla 1, roja 2, doble amarilla 3."""
import pandas as pd

from predicciones.datos import football_data, historiales


def test_leer_recalcula_csv_viejo(tmp_path, monkeypatch):
    # CSV guardado con la fórmula vieja (amarillas + rojas)
    pd.DataFrame({
        "espn_id": ["1", "2"], "fecha": ["2026-10-01", "2026-10-02"], "local": ["A", "B"], "visitante": ["B", "A"],
        "amarillas_local": [2, 1], "rojas_local": [1, 0], "amarillas_visitante": [3, None], "rojas_visitante": [0, 0],
        "tarjetas_local": [3, 1], "tarjetas_visitante": [3, None],
    }).to_csv(tmp_path / "laliga.csv", index=False)
    monkeypatch.setattr(historiales, "CARPETA", tmp_path)
    df = historiales.leer("laliga")
    assert list(df["tarjetas_local"]) == [4, 1]
    assert df["tarjetas_visitante"].iloc[0] == 3 and pd.isna(df["tarjetas_visitante"].iloc[1])


def test_leer_usa_segundas_amarillas(tmp_path, monkeypatch):
    pd.DataFrame({
        "espn_id": ["1"], "fecha": ["2026-10-01"], "local": ["A"], "visitante": ["B"],
        "amarillas_local": [1], "rojas_local": [1], "segundas_amarillas_local": [1],
        "amarillas_visitante": [0], "rojas_visitante": [0], "segundas_amarillas_visitante": [None],
    }).to_csv(tmp_path / "premier.csv", index=False)
    monkeypatch.setattr(historiales, "CARPETA", tmp_path)
    df = historiales.leer("premier")
    assert df["tarjetas_local"].iloc[0] == 4 and df["tarjetas_visitante"].iloc[0] == 0


def test_football_data_para_backtest():
    crudo = pd.DataFrame({"Date": ["17/10/2026"], "HomeTeam": ["Brentford"], "AwayTeam": ["Liverpool"],
                          "FTHG": [1], "FTAG": [2], "HY": [2], "AY": [3], "HR": [2], "AR": [0]})
    out = football_data.normalizar_principal(crudo, "premier", "2627")
    assert out["tarjetas_local"].iloc[0] == 6 and out["tarjetas_visitante"].iloc[0] == 3


def test_promedio_de_liga_para_arbitro_usa_la_misma_regla(liga_sintetica):
    from predicciones.pronostico import ajustar_modelos
    h = liga_sintetica.copy()
    h["amarillas_local"], h["rojas_local"] = 2, 1
    h["amarillas_visitante"], h["rojas_visitante"] = 1, 0
    from predicciones.datos import columnas_tarjetas
    m = ajustar_modelos(columnas_tarjetas(h))
    assert abs(m.promedio_tarjetas - 5.0) < 1e-9   # (2 + 2×1) + 1
