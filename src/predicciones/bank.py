"""Reglas de montos aprobadas por Salva (26-oct-2026).

Todos los montos son porcentajes del bank ACTUAL (si el bank sube, suben):
- Sencilla: hasta 5% (500 MXN con 10,000). Menos si el valor es pequeño.
- Parlay: 3% con confianza alta, 2% con confianza media o baja.
- Límite diario: 10% del bank; con ganancia neta cobrada ese día, 10% + la mitad
  de esa ganancia, hasta 20%.
- Pausa: si el bank cae 50% respecto al inicio del mes, no se apuesta.
"""
from __future__ import annotations

from dataclasses import dataclass

CONFIANZAS = ("alta", "media", "baja")


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

    - 5%: confianza alta y valor de al menos el doble del mínimo (p. ej. 6% si el
      mínimo es 3%), o cualquier cuota de +200 con confianza alta y valor.
    - 3%: confianza alta o media con valor suficiente.
    - 2%: confianza baja.
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


def en_pausa(bank_actual: float, bank_inicio_mes: float, r: ReglasBank = REGLAS) -> bool:
    return bank_actual <= bank_inicio_mes * (1 - r.pausa_caida_mensual)


def ajustar_al_limite(montos: list[float], disponible: float, r: ReglasBank = REGLAS) -> list[float]:
    """Si la suma pasa del disponible del día, reduce todo en proporción."""
    total = sum(montos)
    if total <= disponible or total == 0:
        return montos
    f = disponible / total
    return [_redondear(m * f, r.redondeo) for m in montos]
