"""Reglas de valor aprobadas (26-oct-2026, revisadas tras el backtest).

- Base: Pinnacle sin margen. En los mercados de goles la probabilidad sale de la
  matriz anclada al 1X2 y al más/menos 2.5 de Pinnacle; el modelo propio solo se
  usa como referencia (el backtest mostró que la mezcla 50/50 perdía dinero).
  Se apuesta solo si Winpot paga más que ese precio justo y además:
- Sencilla en ganador, doble oportunidad, goles, ambos anotan, hándicap: VE >= 3%.
- Sencilla en córners o tarjetas: VE >= 5% (no hay Pinnacle: el modelo
  de conteos se encoge hacia 50% porque se confiaba de más).
- Doble oportunidad: solo si ningún resultado del 1X2 llega a 55%.
- Parlay: 2 a 4 selecciones de partidos distintos, cada una con VE >= 2% y el
  parlay completo con VE >= 5%. Objetivo: +200 o más con 2 selecciones.
- Sin cuotas de Pinnacle: solo el modelo, con un nivel menos de confianza.
- Prueba en papel hasta el 26-nov-2026 (ver bank.PRUEBA_PAPEL_HASTA).

Tres categorías de apuesta (aprobadas por Salva el 26-oct-2026; se miden por
separado en la prueba en papel y se comparan el 26-nov-2026):
- "valor": las reglas de arriba (sencillas y parlays con valor).
- "sencilla_fuerte": sencilla con cuota negativa en Winpot de −200 o mejor
  (−150 sí, −250 no), probabilidad >= 60%, confianza alta o media y cuota de
  Winpot dentro de la tolerancia (como mucho 4% por debajo de la cuota justa).
  Se apuesta para GANAR un objetivo fijo (riesgo máximo: el doble del objetivo).
- "combinada": 2 o 3 patas de partidos distintos, cada una con probabilidad
  >= 55%, confianza alta o media y dentro de la tolerancia (o con valor); la
  cuota total de Winpot entre +200 y +350.
Una sencilla que cumple "sencilla_fuerte" cuenta como fuerte aunque tenga valor
(se marca con_valor); "valor" cubre el resto. Un parlay que cumple las reglas de
valor cuenta como parlay de valor, no como combinada.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, asdict

from . import cuotas as C

VE_MIN = {"goles": 0.03, "conteos": 0.05}
VE_MIN_PARLAY_PATA = 0.02
VE_MIN_PARLAY = 0.05
UMBRAL_DOBLE_OPORTUNIDAD = 0.55
PESO_MODELO = 0.0  # 0 = solo Pinnacle; la mezcla vieja 50/50 era 0.5
DESACUERDO_FUERTE = 0.08
ORDEN_CONFIANZA = ["baja", "media", "alta"]

# categorías de apuesta
VALOR, FUERTE, COMBINADA = "valor", "sencilla_fuerte", "combinada"
CATEGORIAS = (VALOR, FUERTE, COMBINADA)
TOLERANCIA_ATRACTIVA = 0.04       # Winpot puede pagar hasta 4% menos que la cuota justa (decimal)
PROB_MIN_FUERTE = 0.60
CUOTA_MIN_FUERTE = 1.5             # −200: con momio peor no va como sencilla fuerte (aprobado por Salva)
MOTIVO_MOMIO_FUERTE = "momio peor que −200: no va como sencilla fuerte (sí puede ir en combinada)"
PROB_MIN_PATA_COMBINADA = 0.55
CONFIANZAS_CATEGORIA = ("alta", "media")  # sencilla fuerte y combinada: nunca con confianza baja
COMBINADA_CUOTA_MIN = 3.0          # +200
COMBINADA_CUOTA_MAX = 4.5          # +350
COMBINADA_PATAS = (2, 3)
MAX_POR_DIA = {FUERTE: 1, COMBINADA: 2}  # por día de Sonora (día en que se evalúa)


def familia(mercado: str) -> str:
    return "conteos" if mercado.startswith(("corners", "tarjetas")) else "goles"


def bajar(confianza: str, niveles: int = 1) -> str:
    i = max(0, ORDEN_CONFIANZA.index(confianza) - niveles)
    return ORDEN_CONFIANZA[i]


@dataclass
class Seleccion:
    partido: str
    mercado: str          # p. ej. "goles", "corners", "tarjetas", "hándicap"
    seleccion: str        # p. ej. "1", "mas_2.5", "local_-0.5"
    prob: float           # probabilidad final de ganar
    prob_devolucion: float = 0.0  # hándicap de línea entera
    confianza: str = "media"
    cuota_ofrecida: float | None = None  # decimal, la de Winpot
    notas: list[str] = field(default_factory=list)

    @property
    def ve_minimo(self) -> float:
        return VE_MIN[familia(self.mercado)]

    @property
    def prob_pierde(self) -> float:
        return 1 - self.prob - self.prob_devolucion

    def ve(self, decimal: float | None = None) -> float | None:
        d = decimal or self.cuota_ofrecida
        if d is None:
            return None
        return self.prob * (d - 1) - self.prob_pierde

    def cuota_justa(self) -> float:
        # cuota con VE = 0 considerando devoluciones
        return 1 + self.prob_pierde / self.prob

    def cuota_minima(self, ve_minimo: float | None = None) -> float:
        v = self.ve_minimo if ve_minimo is None else ve_minimo
        return 1 + (v + self.prob_pierde) / self.prob

    def resumen(self) -> dict:
        d = asdict(self)
        d.update(
            cuota_justa=C.formato_americano(self.cuota_justa()),
            cuota_minima=C.formato_americano(self.cuota_minima(), minima=True),
            ve=None if self.cuota_ofrecida is None else round(self.ve(), 4),
            cuota_ofrecida_americana=None if self.cuota_ofrecida is None else C.formato_americano(self.cuota_ofrecida),
        )
        return d


def doble_oportunidad_permitida(p1: float, px: float, p2: float) -> bool:
    return max(p1, px, p2) < UMBRAL_DOBLE_OPORTUNIDAD


def tiene_valor(s: Seleccion) -> bool:
    v = s.ve()
    return v is not None and v >= s.ve_minimo


def dentro_de_tolerancia(s: Seleccion) -> bool:
    """Winpot paga la cuota justa o hasta 4% menos (en decimal)."""
    return s.cuota_ofrecida is not None and s.cuota_ofrecida >= s.cuota_justa() * (1 - TOLERANCIA_ATRACTIVA)


def _fuerte_sin_momio_minimo(s: Seleccion) -> bool:
    return (s.cuota_ofrecida is not None and s.cuota_ofrecida < 2.0  # cuota negativa
            and s.prob >= PROB_MIN_FUERTE and s.confianza in CONFIANZAS_CATEGORIA
            and dentro_de_tolerancia(s))


def es_sencilla_fuerte(s: Seleccion) -> bool:
    return _fuerte_sin_momio_minimo(s) and s.cuota_ofrecida >= CUOTA_MIN_FUERTE - 1e-9


def fuerte_salvo_momio(s: Seleccion) -> bool:
    """Cumpliría sencilla fuerte, pero el momio de Winpot es peor que −200."""
    return _fuerte_sin_momio_minimo(s) and not es_sencilla_fuerte(s)


def categoria_sencilla(s: Seleccion) -> str | None:
    """'sencilla_fuerte', 'valor' o None. Fuerte gana si cumple las dos
    (se apuesta para ganar un monto fijo); valor cubre el resto."""
    if es_sencilla_fuerte(s):
        return FUERTE
    if tiene_valor(s):
        return VALOR
    return None


def sirve_para_combinada(s: Seleccion) -> bool:
    return (s.cuota_ofrecida is not None and s.prob_devolucion == 0
            and s.prob >= PROB_MIN_PATA_COMBINADA and s.confianza in CONFIANZAS_CATEGORIA
            and (dentro_de_tolerancia(s) or tiene_valor(s)))


@dataclass
class Parlay:
    patas: list[Seleccion]

    @property
    def cuota(self) -> float:
        return C.cuota_parlay(p.cuota_ofrecida for p in self.patas)

    @property
    def prob(self) -> float:
        out = 1.0
        for p in self.patas:
            out *= p.prob
        return out

    @property
    def ve(self) -> float:
        return self.prob * self.cuota - 1

    @property
    def confianza(self) -> str:
        return min((p.confianza for p in self.patas), key=ORDEN_CONFIANZA.index)

    def resumen(self) -> dict:
        return {
            "patas": [f"{p.partido}: {p.mercado} {p.seleccion} ({C.formato_americano(p.cuota_ofrecida)})" for p in self.patas],
            "cuota": C.formato_americano(self.cuota),
            "prob": round(self.prob, 4),
            "ve": round(self.ve, 4),
            "confianza": self.confianza,
        }


def armar_parlays(candidatas: list[Seleccion], max_patas: int = 4, top: int = 5) -> list[Parlay]:
    """Parlays de partidos distintos; cada pata con VE >= 2% y el total con VE >= 5%.

    Las patas del mismo partido no se combinan (sus resultados están relacionados).
    Ordena primero los de 2 patas con cuota +200 o más, luego por VE.
    """
    patas = [s for s in candidatas if s.cuota_ofrecida and (s.ve() or 0) >= VE_MIN_PARLAY_PATA and s.prob_devolucion == 0]
    out: list[Parlay] = []
    for n in range(2, max_patas + 1):
        for combo in itertools.combinations(patas, n):
            if len({c.partido for c in combo}) < n:
                continue
            p = Parlay(list(combo))
            if p.ve >= VE_MIN_PARLAY:
                out.append(p)

    def clave(p: Parlay):
        objetivo = len(p.patas) == 2 and p.cuota >= 3.0 and p.confianza == "alta"
        return (not objetivo, -p.ve)

    out.sort(key=clave)
    # sin repetir la misma pata en varios parlays recomendados
    elegidos, usadas = [], set()
    for p in out:
        ids = {(x.partido, x.mercado, x.seleccion) for x in p.patas}
        if ids & usadas:
            continue
        elegidos.append(p)
        usadas |= ids
        if len(elegidos) >= top:
            break
    return elegidos


def armar_combinadas(candidatas: list[Seleccion], top: int = MAX_POR_DIA[COMBINADA],
                     excluir: set[frozenset] | None = None) -> list[Parlay]:
    """Combinadas cortas: 2 o 3 patas de partidos distintos, cuota total +200 a +350.

    Orden: primero las de 2 patas, luego la de mayor probabilidad conjunta. Ninguna
    pata se repite entre las elegidas. `excluir`: juegos de patas ya elegidos como
    parlay de valor (no se cuentan dos veces).
    """
    patas = [s for s in candidatas if sirve_para_combinada(s)]
    excluir = excluir or set()
    out: list[Parlay] = []
    for n in COMBINADA_PATAS:
        for combo in itertools.combinations(patas, n):
            if len({c.partido for c in combo}) < n:
                continue
            p = Parlay(list(combo))
            if COMBINADA_CUOTA_MIN <= p.cuota <= COMBINADA_CUOTA_MAX and clave_patas(p) not in excluir:
                out.append(p)
    out.sort(key=lambda p: (len(p.patas), -p.prob))
    elegidas, usadas = [], set()
    for p in out:
        ids = set(clave_patas(p))
        if ids & usadas:
            continue
        elegidas.append(p)
        usadas |= ids
        if len(elegidas) >= top:
            break
    return elegidas


def clave_patas(p: Parlay) -> frozenset:
    return frozenset((x.partido, x.mercado, x.seleccion) for x in p.patas)
