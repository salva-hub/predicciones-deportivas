"""CSV gratuitos de football-data.co.uk.

Ligas principales (resultados, tiros, córners, faltas, tarjetas, árbitro y cuotas):
https://www.football-data.co.uk/mmz4281/<temporada>/<código>.csv  (temporada "2526")
Ligas extra (México, EE. UU., Brasil, Argentina...): solo resultado y cuotas de cierre:
https://www.football-data.co.uk/new/<PAÍS>.csv
"""
from __future__ import annotations

import io

import pandas as pd

from . import columnas_tarjetas
from .http import obtener

BASE = "https://www.football-data.co.uk"

LIGAS_PRINCIPALES = {
    "premier": "E0",
    "laliga": "SP1",
    "serie_a": "I1",
    "bundesliga": "D1",
    "ligue_1": "F1",
}
LIGAS_EXTRA = {
    "liga_mx": "MEX",
    "mls": "USA",
    "brasil": "BRA",
    "argentina": "ARG",
}


def codigo_temporada(anio_inicio: int) -> str:
    return f"{anio_inicio % 100:02d}{(anio_inicio + 1) % 100:02d}"


def _leer_csv(texto: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(texto), encoding_errors="ignore", on_bad_lines="skip")


def normalizar_principal(df: pd.DataFrame, liga: str, temporada: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "liga": liga,
        "temporada": temporada,
        "fecha": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
        "local": df["HomeTeam"],
        "visitante": df["AwayTeam"],
        "goles_local": df["FTHG"],
        "goles_visitante": df["FTAG"],
    })
    opcionales = {
        "goles_local_pt": "HTHG", "goles_visitante_pt": "HTAG",
        "tiros_local": "HS", "tiros_visitante": "AS",
        "tiros_puerta_local": "HST", "tiros_puerta_visitante": "AST",
        "corners_local": "HC", "corners_visitante": "AC",
        "amarillas_local": "HY", "amarillas_visitante": "AY",
        "rojas_local": "HR", "rojas_visitante": "AR",
        "arbitro": "Referee",
        # Pinnacle (apertura aprox. y cierre)
        "pin_1": "PSH", "pin_x": "PSD", "pin_2": "PSA",
        "pin_c_1": "PSCH", "pin_c_x": "PSCD", "pin_c_2": "PSCA",
        "pin_mas_2.5": "P>2.5", "pin_menos_2.5": "P<2.5",
        "pin_c_mas_2.5": "PC>2.5", "pin_c_menos_2.5": "PC<2.5",
        "prom_1": "AvgH", "prom_x": "AvgD", "prom_2": "AvgA",
    }
    for nuevo, viejo in opcionales.items():
        if viejo in df.columns:
            out[nuevo] = df[viejo]
    # tarjetas como Winpot: amarilla 1, roja 2 (HY no trae la primera amarilla de una
    # doble amarilla, igual que ESPN: esa expulsión cuenta 2 aquí y 3 en Winpot)
    columnas_tarjetas(out)
    return out.dropna(subset=["fecha", "local", "visitante"])


def descargar_principal(liga: str, anio_inicio: int) -> pd.DataFrame:
    cod = LIGAS_PRINCIPALES[liga]
    temp = codigo_temporada(anio_inicio)
    texto = obtener(f"{BASE}/mmz4281/{temp}/{cod}.csv", horas_cache=12, como_json=False)
    return normalizar_principal(_leer_csv(texto), liga, temp)


def descargar_extra(liga: str) -> pd.DataFrame:
    cod = LIGAS_EXTRA[liga]
    texto = obtener(f"{BASE}/new/{cod}.csv", horas_cache=12, como_json=False)
    df = _leer_csv(texto)
    out = pd.DataFrame({
        "liga": liga,
        "temporada": df["Season"].astype(str),
        "fecha": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
        "local": df["Home"],
        "visitante": df["Away"],
        "goles_local": df["HG"],
        "goles_visitante": df["AG"],
    })
    for nuevo, viejo in {"pin_c_1": "PSCH", "pin_c_x": "PSCD", "pin_c_2": "PSCA",
                         "prom_c_1": "AvgCH", "prom_c_x": "AvgCD", "prom_c_2": "AvgCA"}.items():
        if viejo in df.columns:
            out[nuevo] = df[viejo]
    return out.dropna(subset=["fecha", "local", "visitante"])


def historial(liga: str, temporadas: list[int]) -> pd.DataFrame:
    if liga in LIGAS_PRINCIPALES:
        partes = [descargar_principal(liga, t) for t in temporadas]
        return pd.concat(partes, ignore_index=True).sort_values("fecha")
    if liga in LIGAS_EXTRA:
        return descargar_extra(liga).sort_values("fecha")
    raise KeyError(f"Liga sin datos en football-data: {liga}")
