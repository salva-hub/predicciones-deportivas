"""Reglas de montos aprobadas por Salva (26-oct-2026).

Todos los montos son porcentajes del bank ACTUAL (si el bank sube, suben):
- Sencilla: 5% como máximo (500 MXN con 10,000) con confianza alta y valor alto;
  3% con valor normal; 2% con confianza baja.
- Parlay: 3% con confianza alta, 2% con confianza media o baja.
- Límite diario: 10% del bank; con ganancia neta cobrada ese día, 10% + la mitad
  de esa ganancia, hasta 20%.
- Pausa: si las apuestas del mes pierden 50% o más de la base del mes (bank al
  inicio del mes + depósitos − retiros del mes), no se apuesta. Los retiros y
  depósitos no activan ni ocultan la pausa.
- Prueba en papel: hasta el 26-nov-2026 (fecha de Sonora, inclusive) no se
  apuesta dinero real; las selecciones con valor se guardan como apuestas en
  papel y se califican igual, para medir la regla nueva.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

CONFIANZAS = ("alta", "media", "baja")
PRUEBA_PAPEL_HASTA = date(2026, 11, 26)  # inclusive, fecha de Sonora
MENSAJE_PAPEL = "Prueba en papel hasta 26-nov-2026: no apostar dinero"


def en_prueba_papel(hoy: date) -> bool:
    """True mientras dure el mes de prueba sin dinero real."""
    return hoy <= PRUEBA_PAPEL_HASTA


@dataclass(frozen=True)
class ReglasBank:
    sencilla_max: float = 0.05
    sencilla_media: float = 0.03
    sencilla_baja: float = 0.02
    parlay_alta: float = 0.03
    parlay_media_baja: float = 0.02
    limite_diario: float = 0.10
    limite_diario_max: float = 0.20
    parte_ganancia: float = 0.5
    pausa_caida_mensual: float = 0.50
    redondeo: int = 10  # MXN


REGLAS = ReglasBank()


def _redondear(monto: float, paso: int) -> float:
    return float(int(monto // paso) * paso)


def porcentaje_sencilla(ve: float, confianza: str, ve_minimo: float, r: ReglasBank = REGLAS) -> float:
    """Porcentaje del bank para una sencilla.

    - 5% (máximo de cualquier sencilla): confianza alta y valor de al menos el
      doble del mínimo (p. ej. VE >= 6% si el mínimo es 3%).
    - 3%: confianza alta o media con valor suficiente.
    - 2%: confianza baja con valor suficiente.
    - 0%: sin valor suficiente.
    """
    if confianza not in CONFIANZAS:
        raise ValueError(confianza)
    if ve < ve_minimo:
        return 0.0
    if confianza == "alta" and ve >= 2 * ve_minimo:
        return r.sencilla_max
    if confianza in ("alta", "media"):
        return r.sencilla_media
    return r.sencilla_baja


def porcentaje_parlay(confianza: str, r: ReglasBank = REGLAS) -> float:
    if confianza not in CONFIANZAS:
        raise ValueError(confianza)
    return r.parlay_alta if confianza == "alta" else r.parlay_media_baja


def monto(bank: float, porcentaje: float, r: ReglasBank = REGLAS) -> float:
    return _redondear(bank * porcentaje, r.redondeo)


def limite_del_dia(bank: float, ganancia_neta_cobrada_hoy: float, r: ReglasBank = REGLAS) -> float:
    extra = max(0.0, ganancia_neta_cobrada_hoy) * r.parte_ganancia
    return min(bank * r.limite_diario + extra, bank * r.limite_diario_max)


def en_pausa(resultado_apuestas_mes: float, base_mes: float, r: ReglasBank = REGLAS) -> bool:
    """Pausa si lo perdido en apuestas este mes llega al 50% de la base del mes."""
    return base_mes > 0 and resultado_apuestas_mes <= -base_mes * r.pausa_caida_mensual


def ajustar_al_limite(montos: list[float], disponible: float, r: ReglasBank = REGLAS) -> list[float]:
    """Si la suma pasa del disponible del día, reduce todo en proporción."""
    total = sum(montos)
    if total <= disponible or total == 0:
        return montos
    f = disponible / total
    return [_redondear(m * f, r.redondeo) for m in montos]
