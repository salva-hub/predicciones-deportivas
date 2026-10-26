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
- Sencilla fuerte (cuota negativa, probabilidad >= 60%): el monto es lo necesario
  para GANAR 5% del bank con confianza alta (500 MXN con 10,000; a -120 se ponen
  600) o 2.5% con confianza media. Máximo 1 por día.
- Combinada (2 o 3 patas, +200 a +350): 5% del bank si todas las patas son de
  confianza alta, 3% si alguna es media. Máximo 2 por día.
- El límite diario y la pausa cubren las tres categorías juntas. Si los montos
  pasan del disponible, se recortan en orden de prioridad (cada uno toma lo que
  queda; cuando no queda nada, esa apuesta no se hace).
- Prueba en papel: hasta el 26-nov-2026 (fecha de Sonora, inclusive) no se
  apuesta dinero real; las selecciones con valor se guardan como apuestas en
  papel y se califican igual, para medir la regla nueva.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

CONFIANZAS = ("alta", "media", "baja")
PRUEBA_PAPEL_HASTA = date(2026, 11, 26)  # inclusive, fecha de Sonora
MENSAJE_PAPEL = "Prueba en papel hasta 26-nov-2026: no apostar dinero"
# categorías que pueden recomendarse con dinero real (las demás esperan a que Salva
# las apruebe al revisar la prueba en papel)
CATEGORIAS_DINERO_REAL = {"valor"}
MENSAJE_CATEGORIA_PENDIENTE = "categoría pendiente de aprobación tras la prueba en papel"


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
    fuerte_ganar_alta: float = 0.05    # lo que se busca GANAR, no lo que se apuesta
    fuerte_ganar_media: float = 0.025
    combinada_alta: float = 0.05
    combinada_media: float = 0.03
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


def monto_sencilla_fuerte(bank: float, confianza: str, cuota_decimal: float, r: ReglasBank = REGLAS) -> float:
    """Monto para ganar 5% del bank (alta) o 2.5% (media) a esa cuota. Baja: 0.
    Se redondea hacia arriba a 10 MXN para que la ganancia llegue al objetivo."""
    objetivo = {"alta": r.fuerte_ganar_alta, "media": r.fuerte_ganar_media}.get(confianza, 0.0) * bank
    if objetivo <= 0 or cuota_decimal <= 1:
        return 0.0
    pasos = math.ceil(objetivo / (cuota_decimal - 1) / r.redondeo - 1e-6)
    return float(pasos * r.redondeo)


def porcentaje_combinada(confianza: str, r: ReglasBank = REGLAS) -> float:
    """confianza = la más baja de las patas: alta -> 5%, media -> 3%, baja -> 0."""
    return {"alta": r.combinada_alta, "media": r.combinada_media}.get(confianza, 0.0)


def monto(bank: float, porcentaje: float, r: ReglasBank = REGLAS) -> float:
    return _redondear(bank * porcentaje, r.redondeo)


def limite_del_dia(bank: float, ganancia_neta_cobrada_hoy: float, r: ReglasBank = REGLAS) -> float:
    extra = max(0.0, ganancia_neta_cobrada_hoy) * r.parte_ganancia
    return min(bank * r.limite_diario + extra, bank * r.limite_diario_max)


def en_pausa(resultado_apuestas_mes: float, base_mes: float, r: ReglasBank = REGLAS) -> bool:
    """Pausa si lo perdido en apuestas este mes llega al 50% de la base del mes."""
    return base_mes > 0 and resultado_apuestas_mes <= -base_mes * r.pausa_caida_mensual


def ajustar_al_limite(montos: list[float], disponible: float, r: ReglasBank = REGLAS) -> list[float]:
    """Recorta en orden de prioridad: cada monto toma lo que queda del disponible del
    día; cuando ya no queda nada, los siguientes quedan en 0 (no se apuestan).
    Es la misma regla que usa `evaluar` entre varias selecciones."""
    out = []
    for m in montos:
        x = min(m, _redondear(max(0.0, disponible), r.redondeo))
        out.append(x)
        disponible -= x
    return out
