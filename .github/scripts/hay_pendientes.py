"""Dice si hay algo que calificar, sin llamar a ninguna API.

Escribe "si=true" en $GITHUB_OUTPUT cuando hay al menos un pronóstico pendiente
cuyo partido empezó hace 2.5 h o más (o sin hora de inicio); si no, "si=false".
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HORAS_ESPERA = 2.5  # igual que cmd_calificar


def hay_pendientes(carpeta: Path, ahora: datetime) -> bool:
    for f in carpeta.glob("*.json"):
        p = json.loads(f.read_text(encoding="utf-8"))
        if p.get("estado") != "pendiente":
            continue
        inicio = p.get("inicio_utc")
        if not inicio:
            return True
        if datetime.fromisoformat(inicio) + timedelta(hours=HORAS_ESPERA) <= ahora:
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
