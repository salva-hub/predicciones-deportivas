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
HORAS_VENTANA_DESPERTADOR = 12  # el despertador solo sigue partidos que empezaron hace menos de esto


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


def segundos_para_calificar(carpeta: Path, ahora: datetime,
                            ventana_h: float = HORAS_VENTANA_DESPERTADOR) -> int | None:
    """Segundos hasta que un pronóstico pendiente se pueda calificar (0 = ya), o None si no hay.

    Lo usa el despertador. Solo mira partidos que empezaron hace menos de `ventana_h` horas
    (o que aún no empiezan): lo que lleva más tiempo sin datos lo siguen reintentando las
    corridas programadas, para que el despertador no se quede encendido 48 h.
    """
    menor = None
    for f in carpeta.glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            if p.get("estado") != "pendiente" or not p.get("espn_id") or not p.get("inicio_utc"):
                continue
            inicio = datetime.fromisoformat(p["inicio_utc"])
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=timezone.utc)
        except (ValueError, OSError, AttributeError):
            continue
        if inicio + timedelta(hours=ventana_h) <= ahora:
            continue
        faltan = max(0, int((inicio + timedelta(hours=HORAS_ESPERA) - ahora).total_seconds()))
        menor = faltan if menor is None else min(menor, faltan)
    return menor


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--espera":
        carpeta = Path(sys.argv[2] if len(sys.argv) > 2 else "registro/pronosticos")
        espera = segundos_para_calificar(carpeta, datetime.now(timezone.utc))
        print("" if espera is None else espera)
        sys.exit(0)
    carpeta = Path(sys.argv[1] if len(sys.argv) > 1 else "registro/pronosticos")
    si = hay_pendientes(carpeta, datetime.now(timezone.utc))
    print("Hay pronósticos por calificar" if si else "Nada por calificar: no se consulta ninguna API")
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as fh:
            fh.write(f"si={'true' if si else 'false'}\n")
