"""Cuotas de referencia (Pinnacle) con The Odds API, plan gratis (500 créditos al mes).

La clave se lee de la variable de entorno THE_ODDS_API_KEY (nunca en el código).
Cada consulta cuesta (mercados x regiones) créditos; pedimos solo una liga a la vez.
"""
from __future__ import annotations

import os

from .http import obtener

BASE = "https://api.the-odds-api.com/v4/sports"

DEPORTES = {
    "premier": "soccer_epl",
    "laliga": "soccer_spain_la_liga",
    "serie_a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue_1": "soccer_france_ligue_one",
    "liga_mx": "soccer_mexico_ligamx",
    "mls": "soccer_usa_mls",
    "brasil": "soccer_brazil_campeonato",
    "argentina": "soccer_argentina_primera_division",
    "champions": "soccer_uefa_champs_league",
    "europa_league": "soccer_uefa_europa_league",
    "libertadores": "soccer_conmebol_copa_libertadores",
}


def cuotas_pinnacle(liga: str, mercados: str = "h2h,totals") -> list[dict] | None:
    clave = os.environ.get("THE_ODDS_API_KEY")
    if not clave or liga not in DEPORTES:
        return None
    datos = obtener(f"{BASE}/{DEPORTES[liga]}/odds", {
        "apiKey": clave, "regions": "eu", "markets": mercados,
        "bookmakers": "pinnacle", "oddsFormat": "decimal",
    }, horas_cache=0.5)
    out = []
    for ev in datos:
        fila = {"local": ev["home_team"], "visitante": ev["away_team"], "inicio_utc": ev["commence_time"]}
        for casa in ev.get("bookmakers", []):
            for m in casa.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "h2h":
                        lado = "1" if o["name"] == ev["home_team"] else "2" if o["name"] == ev["away_team"] else "X"
                        fila[f"pin_{lado}"] = o["price"]
                    elif m["key"] == "totals":
                        fila[f"pin_{o['name'].lower()}_{o.get('point')}"] = o["price"]
        out.append(fila)
    return out
