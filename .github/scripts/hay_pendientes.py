"""Dice si hay algo que calificar, sin llamar a ninguna API.

Escribe "si=true" en $GITHUB_OUTPUT cuando hay al menos un pronóstico pendiente
cuyo partido empezó hace 2.5 h o más; si no, "si=false". Igual que `calificar`, ignora los
pronósticos sin `espn_id` o sin `inicio_utc` (no se pueden calificar solos). Un archivo que no
se puede leer cuenta como "sí", para no dejar de calificar por un error.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HORAS_ESPERA = 2.5  # igual que cmd_calificar


def hay_pendientes(carpeta: Path, ahora: datetime) -> bool:
    for f in carpeta.glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            if p.get("estado") != "pendiente" or not p.get("espn_id") or not p.get("inicio_utc"):
                continue
            inicio = datetime.fromisoformat(p["inicio_utc"])
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=timezone.utc)
        except (ValueError, OSError, AttributeError):
            return True
        if inicio + timedelta(hours=HORAS_ESPERA) <= ahora:
            return True
    return False


if __name__ == "__main__":
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "registro/pronosticos")
    si = hay_pendientes(carpeta, datetime.now(timezone.utc))
    print("Hay pronósticos por calificar" if si else "Nada por calificar: no se consulta ninguna API")
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as fh:
            fh.write(f"si={'true' if si else 'false'}\n")
