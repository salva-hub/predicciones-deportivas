"""Historiales guardados en el repositorio (datos/historial/<liga>.csv).

Se actualizan con GitHub Actions cada semana (sin gastar tokens), así un pedido
solo descarga lo fresco del partido (clima, cuotas) y no todo el historial.
Todas las ligas usan ESPN para que los nombres de equipos sean consistentes.
"""
from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import espn
from .http import obtener

import os

RAIZ = Path(os.environ.get("PRED_RAIZ", Path(__file__).resolve().parents[3]))
CARPETA = RAIZ / "datos" / "historial"
ANIOS_HISTORIAL = 4
SELECCIONES_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

# Torneos que mezclan equipos de varias ligas: se ajusta un modelo conjunto.
CONJUNTOS = {
    "champions": ["premier", "laliga", "serie_a", "bundesliga", "ligue_1", "champions", "europa_league"],
    "europa_league": ["premier", "laliga", "serie_a", "bundesliga", "ligue_1", "champions", "europa_league"],
    "libertadores": ["brasil", "argentina", "libertadores"],
    "concacaf": ["liga_mx", "mls", "concacaf"],
}
SELECCIONES = {"mundial", "eliminatorias_conmebol", "eliminatorias_concacaf", "nations_league",
               "copa_america", "eurocopa", "amistosos", "selecciones"}


def ruta(liga: str) -> Path:
    return CARPETA / f"{liga}.csv"


def leer(liga: str) -> pd.DataFrame:
    r = ruta(liga)
    if not r.exists():
        return pd.DataFrame()
    df = pd.read_csv(r, dtype={"espn_id": str})
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


def actualizar(liga: str, hoy: date | None = None) -> pd.DataFrame:
    """Agrega los partidos nuevos desde la última fecha guardada."""
    hoy = hoy or date.today()
    actual = leer(liga)
    if actual.empty:
        desde = hoy - timedelta(days=365 * ANIOS_HISTORIAL)
    else:
        desde = (actual["fecha"].max() - pd.Timedelta(days=3)).date()
    nuevos = espn.historial(liga, desde, hoy - timedelta(days=1))
    df = pd.concat([actual, nuevos], ignore_index=True) if not actual.empty else nuevos
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["espn_id"], keep="last").sort_values("fecha")
    limite = pd.Timestamp(hoy - timedelta(days=365 * ANIOS_HISTORIAL + 30))
    df = df[df["fecha"] >= limite]
    CARPETA.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta(liga), index=False)
    return df


def selecciones() -> pd.DataFrame:
    texto = obtener(SELECCIONES_URL, horas_cache=24, como_json=False)
    df = pd.read_csv(io.StringIO(texto))
    df = df.rename(columns={"date": "fecha", "home_team": "local", "away_team": "visitante",
                            "home_score": "goles_local", "away_score": "goles_visitante"})
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df[df["fecha"] >= pd.Timestamp.today() - pd.Timedelta(days=365 * 8)]
    # en cancha neutral no hay ventaja de local: se duplica el partido invertido
    neutral = df[df["neutral"] == True]  # noqa: E712
    invertido = neutral.rename(columns={"local": "visitante", "visitante": "local",
                                        "goles_local": "goles_visitante", "goles_visitante": "goles_local"})
    return pd.concat([df, invertido], ignore_index=True).dropna(subset=["goles_local"])


def para_modelo(liga: str) -> pd.DataFrame:
    """Historial con el que se ajusta el modelo de una liga o torneo."""
    if liga in SELECCIONES:
        return selecciones()
    ligas = CONJUNTOS.get(liga, [liga])
    partes = [leer(l) for l in ligas]
    partes = [p for p in partes if not p.empty]
    if not partes:
        raise FileNotFoundError(
            f"No hay historial guardado para {liga}. Ejecuta: python -m predicciones.cli actualizar-datos --liga {liga}")
    return pd.concat(partes, ignore_index=True).sort_values("fecha")
