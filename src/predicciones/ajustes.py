"""Ajustes externos sobre los esperados del modelo, cada uno con su explicación.

Valores iniciales aprobados en la metodología; la prueba contra el pasado los afina.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ajuste:
    objetivo: str   # goles_local, goles_visitante, corners, tarjetas
    factor: float
    motivo: str


@dataclass
class Ajustes:
    lista: list[Ajuste] = field(default_factory=list)

    def agregar(self, objetivo: str, factor: float, motivo: str) -> None:
        if not 0.5 <= factor <= 1.5:
            raise ValueError(f"Factor fuera de rango razonable: {factor} ({motivo})")
        self.lista.append(Ajuste(objetivo, factor, motivo))

    def factor(self, objetivo: str) -> float:
        f = 1.0
        for a in self.lista:
            if a.objetivo == objetivo:
                f *= a.factor
        return f

    def explicaciones(self) -> list[str]:
        return [f"{a.motivo}: {a.objetivo} x{a.factor:.2f}" for a in self.lista]


def por_clima(clima: dict | None, techado: bool, aj: Ajustes) -> None:
    if techado or not clima:
        return
    lluvia = clima.get("lluvia_mm_h") or 0
    viento = clima.get("viento_kmh") or 0
    temp = clima.get("temperatura_c")
    if lluvia > 5 or viento > 30:
        motivo = f"Clima: lluvia {lluvia:.1f} mm/h, viento {viento:.0f} km/h"
        aj.agregar("goles_local", 0.93, motivo)
        aj.agregar("goles_visitante", 0.93, motivo)
    if temp is not None and temp > 32:
        motivo = f"Calor: {temp:.0f} °C"
        aj.agregar("goles_local", 0.95, motivo)
        aj.agregar("goles_visitante", 0.95, motivo)


def parsear(texto: str) -> Ajuste:
    """Formato de línea de comandos: objetivo:factor:motivo"""
    objetivo, factor, motivo = texto.split(":", 2)
    return Ajuste(objetivo.strip(), float(factor), motivo.strip())
