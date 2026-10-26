"""Clima con Open-Meteo (gratis, uso no comercial, sin clave)."""
from __future__ import annotations

from datetime import datetime

from .http import obtener

PRONOSTICO = "https://api.open-meteo.com/v1/forecast"
GEOCODIFICACION = "https://geocoding-api.open-meteo.com/v1/search"


def ubicar(ciudad: str, pais: str | None = None) -> dict | None:
    datos = obtener(GEOCODIFICACION, {"name": ciudad, "count": 5, "language": "es"}, horas_cache=24 * 30)
    res = datos.get("results") or []
    if pais:
        filtrados = [r for r in res if pais.lower() in (r.get("country") or "").lower()]
        res = filtrados or res
    if not res:
        return None
    r = res[0]
    return {"lat": r["latitude"], "lon": r["longitude"], "altitud_m": r.get("elevation")}


def clima_partido(lat: float, lon: float, inicio_utc: str) -> dict | None:
    """Temperatura, lluvia y viento a la hora del partido."""
    inicio = datetime.fromisoformat(inicio_utc)
    datos = obtener(PRONOSTICO, {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation,wind_speed_10m",
        "timezone": "UTC", "forecast_days": 16,
    }, horas_cache=1)
    horas = datos.get("hourly", {})
    tiempos = horas.get("time", [])
    objetivo = inicio.strftime("%Y-%m-%dT%H:00")
    if objetivo not in tiempos:
        return None
    i = tiempos.index(objetivo)
    # promedio de las 2 horas del partido
    j = min(i + 2, len(tiempos))
    def prom(clave):
        vals = [v for v in horas.get(clave, [])[i:j] if v is not None]
        return sum(vals) / len(vals) if vals else None
    return {
        "temperatura_c": prom("temperature_2m"),
        "lluvia_mm_h": prom("precipitation"),
        "viento_kmh": prom("wind_speed_10m"),
        "altitud_m": datos.get("elevation"),
    }
