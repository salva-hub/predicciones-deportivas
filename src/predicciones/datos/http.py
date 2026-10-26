"""Descargas con caché local y reintentos sencillos."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

CACHE = Path(os.environ.get("PRED_CACHE", "cache"))
AGENTE = {"User-Agent": "predicciones-deportivas/0.1 (uso personal, no comercial)"}


def destino(url: str) -> str:
    """Solo host y ruta: nunca la query (puede llevar apiKey)."""
    u = urlsplit(url)
    return f"{u.netloc}{u.path}"


def limpiar(texto: str) -> str:
    """Quita cualquier query string o clave de un texto antes de mostrarlo."""
    texto = re.sub(r"(?i)(api_?key|key|token)=[^&\s'\"]+", r"\1=***", str(texto))
    return re.sub(r"\?[^\s'\"]*", "", texto)


def obtener(url: str, params: dict | None = None, horas_cache: float = 6, como_json: bool = True, timeout: int = 30):
    clave = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    ruta = CACHE / clave
    if horas_cache > 0 and ruta.exists() and time.time() - ruta.stat().st_mtime < horas_cache * 3600:
        texto = ruta.read_text(encoding="utf-8")
    else:
        texto = _descargar(url, params, timeout)
        _guardar(ruta, texto)
    return json.loads(texto) if como_json else texto


def _guardar(ruta: Path, texto: str) -> None:
    """Escritura atómica: archivo temporal en la misma carpeta y luego os.replace.

    Así otro proceso nunca lee un archivo de caché a medio escribir.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(texto)
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _descargar(url: str, params: dict | None, timeout: int) -> str:
    ultimo = "error"
    for intento in range(3):
        try:
            r = requests.get(url, params=params, headers=AGENTE, timeout=timeout)
        except requests.RequestException as e:
            # el texto de la excepción trae la URL completa: solo el tipo
            ultimo = type(e).__name__
        else:
            if r.status_code < 400:
                return r.text
            ultimo = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404):
                break  # reintentar no lo arregla
        time.sleep(2 ** intento)
    raise RuntimeError(f"No se pudo descargar {destino(url)}: {ultimo}")
