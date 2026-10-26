"""Empata nombres de equipos entre fuentes (ESPN, football-data, The Odds API...)."""
from __future__ import annotations

import difflib
import json
import unicodedata
from pathlib import Path

ALIAS = Path(__file__).resolve().parents[2] / "config" / "alias_equipos.json"
RUIDO = {"fc", "cf", "club", "de", "the", "sc", "ac", "afc", "cd", "ud", "sd", "ca", "cr", "deportivo", "futbol"}
MIN_LETRAS_PALABRA_SOLA = 6  # "Dortmund" ⊂ "Borussia Dortmund" sí; "Inter" ⊂ "Inter Miami" no
MARGEN = 0.1


def normalizar(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    s = "".join(ch if ch.isalnum() or ch == " " else " " for ch in s)
    palabras = [p for p in s.split() if p not in RUIDO]
    return " ".join(palabras) or s.strip()


def _alias() -> dict[str, str]:
    if ALIAS.exists():
        return {normalizar(k): normalizar(v) for k, v in json.loads(ALIAS.read_text(encoding="utf-8")).items()}
    return {}


def _puntaje(n: str, c: str, alias: dict[str, str]) -> float:
    """1.0 igual o alias; 0.9 palabras contenidas; si no, parecido de difflib (< 0.9)."""
    if n == c or alias.get(n, n) == alias.get(c, c):
        return 1.0
    a, b = set(n.split()), set(c.split())
    chico = a if len(a) <= len(b) else b
    if (a <= b or b <= a) and (len(chico) >= 2 or len(next(iter(chico))) >= MIN_LETRAS_PALABRA_SOLA):
        return 0.9
    return min(difflib.SequenceMatcher(None, n, c).ratio(), 0.89)


def coincide(nombre: str, candidato: str) -> bool:
    """Para comparar contra un solo candidato: igual, alias o palabras contenidas (sin difflib)."""
    return _puntaje(normalizar(nombre), normalizar(candidato), _alias()) >= 0.9


def resolver(nombre: str, conocidos: list[str], minimo: float = 0.6) -> str | None:
    """Devuelve el nombre de `conocidos` que corresponde a `nombre`, o None.

    Con un solo candidato se usa la regla estricta de `coincide`. Con varios, gana el
    de mayor puntaje si supera `minimo` y le saca ventaja al segundo.
    """
    if len(conocidos) == 1:
        return conocidos[0] if coincide(nombre, conocidos[0]) else None
    alias = _alias()
    n = normalizar(nombre)
    puntajes = sorted(((_puntaje(n, normalizar(c), alias), c) for c in conocidos), reverse=True)
    if not puntajes or puntajes[0][0] < minimo:
        return None
    if len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < MARGEN and puntajes[0][0] < 1.0:
        return None
    if len(puntajes) > 1 and puntajes[1][0] == 1.0:
        return None  # dos iguales: ambiguo
    return puntajes[0][1]
