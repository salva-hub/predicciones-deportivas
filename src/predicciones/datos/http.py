"""Descargas con caché local y reintentos sencillos."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

CACHE = Path(os.environ.get("PRED_CACHE", "cache"))
AGENTE = {"User-Agent": "predicciones-deportivas/0.1 (uso personal, no comercial)"}


def obtener(url: str, params: dict | None = None, horas_cache: float = 6, como_json: bool = True, timeout: int = 30):
    clave = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    ruta = CACHE / clave
    if horas_cache > 0 and ruta.exists() and time.time() - ruta.stat().st_mtime < horas_cache * 3600:
        texto = ruta.read_text(encoding="utf-8")
    else:
        ultimo = None
        for intento in range(3):
            try:
                r = requests.get(url, params=params, headers=AGENTE, timeout=timeout)
                r.raise_for_status()
                texto = r.text
                break
            except requests.RequestException as e:  # pragma: no cover - red
                ultimo = e
                time.sleep(2 ** intento)
        else:
            raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")
        CACHE.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")
    return json.loads(texto) if como_json else texto
