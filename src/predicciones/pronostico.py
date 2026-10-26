"""Motor del pronóstico de un partido de fútbol.

Junta el modelo de goles, los de córners y tarjetas, los ajustes externos y las
cuotas de referencia, y devuelve probabilidades, cuotas justas y cuotas mínimas
(en formato americano) para cada mercado.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import cuotas as C
from . import valor as V
from .ajustes import Ajustes
from .equipos import resolver
from .modelos.conteos import ModeloConteo, ajustar_modelo_conteo
from .modelos.goles import (
    ModeloGoles,
    ajustar_modelo_goles,
    marcador_mas_probable,
    matriz_marcadores,
    mercados_de_goles,
    mercados_primer_tiempo,
    resultado_handicap,
)

LINEAS_CORNERS = (7.5, 8.5, 9.5, 10.5, 11.5)
LINEAS_TARJETAS = (2.5, 3.5, 4.5, 5.5, 6.5)
LINEAS_HANDICAP = (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)
MIN_PARTIDOS_EQUIPO = 5.0


@dataclass
class Modelos:
    goles: ModeloGoles
    corners: ModeloConteo | None = None
    tarjetas: ModeloConteo | None = None
    promedio_tarjetas: float | None = None


def ajustar_modelos(historial: pd.DataFrame, referencia: pd.Timestamp | None = None) -> Modelos:
    hist = historial[pd.to_datetime(historial["fecha"]) < referencia] if referencia is not None else historial
    goles = ajustar_modelo_goles(hist, referencia)
    corners = tarjetas = None
    prom_t = None
    if {"corners_local", "corners_visitante"} <= set(hist.columns) and hist["corners_local"].notna().sum() > 50:
        corners = ajustar_modelo_conteo(hist, "corners_local", "corners_visitante", "corners", referencia)
    if {"tarjetas_local", "tarjetas_visitante"} <= set(hist.columns) and hist["tarjetas_local"].notna().sum() > 50:
        tarjetas = ajustar_modelo_conteo(hist, "tarjetas_local", "tarjetas_visitante", "tarjetas", referencia)
        t = hist.dropna(subset=["tarjetas_local"])
        prom_t = float((t["tarjetas_local"] + t["tarjetas_visitante"]).mean())
    return Modelos(goles, corners, tarjetas, prom_t)


@dataclass
class Pronostico:
    partido: str
    local: str
    visitante: str
    goles_esperados: tuple[float, float]
    probabilidades: dict[str, dict[str, float]]   # mercado -> seleccion -> prob final
    prob_modelo: dict[str, dict[str, float]]
    devoluciones: dict[str, float] = field(default_factory=dict)  # hándicap entero
    marcadores: list[tuple[str, float]] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    confianza_base: str = "media"

    def selecciones(self, confianza: str | None = None) -> list[V.Seleccion]:
        conf = confianza or self.confianza_base
        out = []
        p = self.probabilidades["ganador"]
        permitir_do = V.doble_oportunidad_permitida(p["1"], p["X"], p["2"])
        for mercado, sel in self.probabilidades.items():
            for nombre, prob in sel.items():
                if mercado == "doble_oportunidad" and not permitir_do:
                    continue
                if prob <= 0.01 or prob >= 0.99:
                    continue
                c = conf
                pm = self.prob_modelo.get(mercado, {}).get(nombre)
                if pm is not None and abs(pm - prob) > V.DESACUERDO_FUERTE / 2:
                    c = V.bajar(c)  # modelo y mercado discrepan mucho
                out.append(V.Seleccion(
                    partido=self.partido, mercado=mercado, seleccion=nombre, prob=prob,
                    prob_devolucion=self.devoluciones.get(f"{mercado}:{nombre}", 0.0), confianza=c,
                ))
        return out

    def tabla(self) -> list[dict]:
        filas = []
        for s in self.selecciones():
            filas.append({
                "mercado": s.mercado, "seleccion": s.seleccion,
                "prob": round(s.prob, 3),
                "cuota_justa": C.formato_americano(s.cuota_justa()),
                "cuota_minima": C.formato_americano(s.cuota_minima(), minima=True),
                "confianza": s.confianza,
                **({"devolucion": round(s.prob_devolucion, 3)} if s.prob_devolucion else {}),
            })
        return filas


def _mezclar_mercado(probs: dict[str, float], mercado: dict[str, float] | None) -> dict[str, float]:
    if not mercado:
        return dict(probs)
    return {k: V.mezclar(v, mercado.get(k)) for k, v in probs.items()}


def pronosticar(
    modelos: Modelos,
    local: str,
    visitante: str,
    ajustes: Ajustes | None = None,
    factor_arbitro: float = 1.0,
    cuotas_pinnacle: dict[str, float] | None = None,
    confianza_datos: str = "media",
) -> Pronostico:
    """cuotas_pinnacle: cuotas decimales con claves "1","X","2","mas_2.5","menos_2.5"."""
    aj = ajustes or Ajustes()
    notas: list[str] = []
    conocidos = modelos.goles.fuerzas.equipos
    l = resolver(local, conocidos) or local
    v = resolver(visitante, conocidos) or visitante
    for nombre, real in ((local, l), (visitante, v)):
        n = modelos.goles.fuerzas.partidos.get(real, 0.0)
        if not modelos.goles.fuerzas.conoce(real):
            notas.append(f"{nombre}: sin historial en esta liga; se usa el promedio (más incertidumbre)")
            confianza_datos = V.bajar(confianza_datos)
        elif n < MIN_PARTIDOS_EQUIPO:
            notas.append(f"{nombre}: pocos partidos recientes ({n:.1f} ponderados)")
            confianza_datos = V.bajar(confianza_datos)

    mu_l, mu_v = modelos.goles.esperados(l, v)
    mu_l *= aj.factor("goles_local")
    mu_v *= aj.factor("goles_visitante")
    m = matriz_marcadores(mu_l, mu_v, modelos.goles.rho)
    g = mercados_de_goles(m)

    modelo: dict[str, dict[str, float]] = {
        "ganador": {k: g[k] for k in ("1", "X", "2")},
        "doble_oportunidad": {k: g[k] for k in ("1X", "12", "X2")},
        "goles": {k: g[k] for k in g if k.startswith(("mas_", "menos_"))},
        "ambos_anotan": {"si": g["ambos_si"], "no": g["ambos_no"]},
        "goles_equipo": {k: g[k] for k in g if k.startswith(("local_", "visitante_"))},
        "primer_tiempo": mercados_primer_tiempo(mu_l, mu_v),
    }
    devoluciones: dict[str, float] = {}
    hc: dict[str, float] = {}
    for linea in LINEAS_HANDICAP:
        for lado, es_local in (("local", True), ("visitante", False)):
            gana, dev, _ = resultado_handicap(m, linea, es_local)
            clave = f"{lado}_{linea:+g}"
            hc[clave] = gana
            if dev > 0:
                devoluciones[f"handicap:{clave}"] = dev
    modelo["handicap"] = hc

    if modelos.corners:
        cl, cv = modelos.corners.esperados(l, v)
        tot = (cl + cv) * aj.factor("corners")
        modelo["corners"] = modelos.corners.mercados(tot, LINEAS_CORNERS)
        notas.append(f"Córners esperados: {tot:.1f}")
    if modelos.tarjetas:
        tl, tv = modelos.tarjetas.esperados(l, v)
        tot = (tl + tv) * factor_arbitro * aj.factor("tarjetas")
        modelo["tarjetas"] = modelos.tarjetas.mercados(tot, LINEAS_TARJETAS)
        notas.append(f"Tarjetas esperadas: {tot:.1f} (factor árbitro x{factor_arbitro:.2f})")

    finales = {k: dict(v_) for k, v_ in modelo.items()}
    if cuotas_pinnacle:
        claves_1x2 = [cuotas_pinnacle.get(k) for k in ("1", "X", "2")]
        if all(claves_1x2):
            justo = C.quitar_margen(claves_1x2)
            mercado_1x2 = dict(zip(("1", "X", "2"), justo))
            finales["ganador"] = _mezclar_mercado(modelo["ganador"], mercado_1x2)
            p1, px, p2 = (finales["ganador"][k] for k in ("1", "X", "2"))
            finales["doble_oportunidad"] = {"1X": p1 + px, "12": p1 + p2, "X2": px + p2}
            notas.append("Ganador mezclado 50/50 con Pinnacle sin margen")
        mas, menos = cuotas_pinnacle.get("mas_2.5"), cuotas_pinnacle.get("menos_2.5")
        if mas and menos:
            pm, pmn = C.quitar_margen([mas, menos])
            mezcla = V.mezclar(modelo["goles"]["mas_2.5"], pm)
            finales["goles"]["mas_2.5"], finales["goles"]["menos_2.5"] = mezcla, 1 - mezcla

    notas.extend(aj.explicaciones())
    return Pronostico(
        partido=f"{local} vs {visitante}", local=l, visitante=v,
        goles_esperados=(round(mu_l, 2), round(mu_v, 2)),
        probabilidades=finales, prob_modelo=modelo, devoluciones=devoluciones,
        marcadores=marcador_mas_probable(m), notas=notas, confianza_base=confianza_datos,
    )
