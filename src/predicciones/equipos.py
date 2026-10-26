"""Empata nombres de equipos entre fuentes (ESPN, football-data, The Odds API...)."""
from __future__ import annotations

import difflib
import json
import unicodedata
from pathlib import Path

ALIAS = Path(__file__).resolve().parents[2] / "config" / "alias_equipos.json"
RUIDO = {"fc", "cf", "club", "de", "the", "sc", "ac", "afc", "cd", "ud", "sd", "ca", "cr", "deportivo", "futbol"}


def normalizar(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    s = "".join(ch if ch.isalnum() or ch == " " else " " for ch in s)
    palabras = [p for p in s.split() if p not in RUIDO]
    return " ".join(palabras) or s.strip()


def _alias() -> dict[str, str]:
    if ALIAS.exists():
        return {normalizar(k): v for k, v in json.loads(ALIAS.read_text(encoding="utf-8")).items()}
    return {}


def resolver(nombre: str, conocidos: list[str], minimo: float = 0.6) -> str | None:
    """Devuelve el nombre de `conocidos` que corresponde a `nombre`, o None."""
    alias = _alias()
    n = normalizar(nombre)
    if n in alias and alias[n] in conocidos:
        return alias[n]
    por_norm = {normalizar(c): c for c in conocidos}
    if n in por_norm:
        return por_norm[n]
    # contiene (p. ej. "america" en "club america")
    contiene = [c for k, c in por_norm.items() if n in k.split() or k in n.split() or (len(n) > 4 and n in k)]
    if len(contiene) == 1:
        return contiene[0]
    mejor = difflib.get_close_matches(n, list(por_norm), n=1, cutoff=minimo)
    return por_norm[mejor[0]] if mejor else None
