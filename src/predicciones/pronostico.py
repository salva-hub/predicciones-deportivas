"""Motor del pronóstico de un partido de fútbol.

Junta el modelo de goles, los de córners y tarjetas, los ajustes externos y las
cuotas de referencia, y devuelve probabilidades, cuotas justas y cuotas mínimas
(en formato americano) para cada mercado.

Regla vigente (26-oct-2026): Pinnacle sin margen es la base. Con su 1X2 (y su
más/menos 2.5 si lo hay) se buscan los goles esperados que lo reproducen, y de
esa matriz salen todos los mercados de goles; encima se aplican los ajustes de
noticias. El modelo propio queda como referencia (prob_modelo). Sin Pinnacle se
usa solo el modelo y se baja un nivel la confianza.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import cuotas as C
from . import valor as V
from .ajustes import Ajustes
from .equipos import resolver
from .modelos.conteos import ModeloConteo, ajustar_modelo_conteo
from .modelos.goles import (
    ModeloGoles,
    ajustar_modelo_goles,
    anclar_a_mercado,
    mezclar_goles,
    marcador_mas_probable,
    matriz_marcadores,
    mercados_de_goles,
    mercados_primer_tiempo,
    resultado_handicap,
)

LINEAS_CORNERS = (7.5, 8.5, 9.5, 10.5, 11.5)
LINEAS_TARJETAS = (2.5, 3.5, 4.5, 5.5, 6.5)
# Sin ±0.5: duplican 1 / 1X / X2 / 2 y se saltarían la mezcla y la regla de doble oportunidad.
LINEAS_HANDICAP = (-1.5, -1.0, 0.0, 1.0, 1.5)
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
    prob_modelo: dict[str, dict[str, float]]        # modelo solo (con ajustes), referencia
    devoluciones: dict[str, float] = field(default_factory=dict)  # hándicap entero
    marcadores: list[tuple[str, float]] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    confianza_base: str = "media"
    # mercados de referencia ("1x2", "goles_2.5") en que el modelo y Pinnacle discrepan mucho
    desacuerdos: set[str] = field(default_factory=set)
    # probabilidades ancladas solo a Pinnacle (con ajustes), para comparar con el modelo
    prob_mercado: dict[str, dict[str, float]] = field(default_factory=dict)
    # mercados con un nivel menos de confianza (p. ej. sin línea de goles de Pinnacle)
    menos_confianza: set[str] = field(default_factory=set)

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
                c = V.bajar(conf) if mercado in self.menos_confianza else conf
                pm = self.prob_modelo.get(mercado, {}).get(nombre)
                pa = self.prob_mercado.get(mercado, {}).get(nombre)
                if pm is not None and pa is not None and abs(pm - pa) > V.DESACUERDO_FUERTE:
                    c = V.bajar(c)  # modelo y Pinnacle discrepan mucho en esta selección
                elif referencia_de(mercado, nombre) in self.desacuerdos:
                    c = V.bajar(c)  # su mercado de referencia discrepa
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


def referencia_de(mercado: str, seleccion: str) -> str | None:
    """Mercado de referencia de Pinnacle (1X2 o más/menos 2.5) del que depende una selección.

    Hándicap, goles por equipo y 1X2 del primer tiempo dependen del 1X2; las demás
    líneas de goles, ambos anotan y totales del primer tiempo dependen de más/menos 2.5.
    """
    if mercado in ("handicap", "goles_equipo"):
        return "1x2"
    if mercado == "primer_tiempo":
        return "1x2" if seleccion in ("pt_1", "pt_X", "pt_2") else "goles_2.5"
    if mercado == "ambos_anotan" or (mercado == "goles" and seleccion not in ("mas_2.5", "menos_2.5")):
        return "goles_2.5"
    return None


def _mercados_goles(mu_l: float, mu_v: float, rho: float) -> tuple[dict[str, dict[str, float]], dict[str, float], np.ndarray]:
    """Todos los mercados de goles de una matriz: probabilidades, devoluciones y matriz."""
    m = matriz_marcadores(mu_l, mu_v, rho)
    g = mercados_de_goles(m)
    out: dict[str, dict[str, float]] = {
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
    out["handicap"] = hc
    return out, devoluciones, m


# única nota (y única baja de confianza) cuando falta el 1X2 de Pinnacle
NOTA_SIN_PINNACLE = "Sin cuotas de referencia de Pinnacle: probabilidades solo del modelo"

MERCADOS_DE_TOTAL = {"goles", "ambos_anotan", "goles_equipo", "primer_tiempo", "handicap"}


def _handicap_cero(probs: dict[str, dict[str, float]], devoluciones: dict[str, float] | None) -> None:
    g = probs["ganador"]
    probs["handicap"]["local_+0"], probs["handicap"]["visitante_+0"] = g["1"], g["2"]
    if devoluciones is not None:
        devoluciones["handicap:local_+0"] = devoluciones["handicap:visitante_+0"] = g["X"]


def _doble(g: dict[str, float]) -> dict[str, float]:
    return {"1X": g["1"] + g["X"], "12": g["1"] + g["2"], "X2": g["X"] + g["2"]}


def _ganador_pinnacle(p_1x2, con_aj: dict[str, float], sin_aj: dict[str, float]) -> dict[str, float]:
    """1X2 de Pinnacle sin margen, movido por los ajustes en la proporción con_aj / sin_aj."""
    crudo = {k: p * con_aj[k] / max(sin_aj[k], 1e-12) for k, p in zip(("1", "X", "2"), p_1x2)}
    total = sum(crudo.values())
    return {k: x / total for k, x in crudo.items()}


def _pinnacle_sin_margen(cuotas_pinnacle: dict[str, float] | None) -> tuple[tuple | None, float | None]:
    if not cuotas_pinnacle:
        return None, None
    p_1x2 = p_mas = None
    c = [cuotas_pinnacle.get(k) for k in ("1", "X", "2")]
    if all(c):
        p_1x2 = tuple(C.quitar_margen(c))
    mas, menos = cuotas_pinnacle.get("mas_2.5"), cuotas_pinnacle.get("menos_2.5")
    if mas and menos:
        p_mas = C.quitar_margen([mas, menos])[0]
    return p_1x2, p_mas


def pronosticar(
    modelos: Modelos,
    local: str,
    visitante: str,
    ajustes: Ajustes | None = None,
    factor_arbitro: float = 1.0,
    cuotas_pinnacle: dict[str, float] | None = None,
    confianza_datos: str = "media",
    neutral: bool = False,
    peso_modelo: float = V.PESO_MODELO,
) -> Pronostico:
    """cuotas_pinnacle: cuotas decimales con claves "1","X","2","mas_2.5","menos_2.5".

    neutral=True: cancha neutral, sin ventaja de local.
    peso_modelo: 0 (regla vigente) = goles anclados solo a Pinnacle; con más de 0
    se mezclan, en escala log, los goles del modelo y los anclados.
    """
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

    rho = modelos.goles.rho
    f_l, f_v = aj.factor("goles_local"), aj.factor("goles_visitante")
    mod_l, mod_v = modelos.goles.esperados(l, v, neutral)
    if neutral:
        notas.append("Cancha neutral: sin ventaja de local")
    modelo, dev_modelo, _ = _mercados_goles(mod_l * f_l, mod_v * f_v, rho)

    p_1x2, p_mas = _pinnacle_sin_margen(cuotas_pinnacle)
    desacuerdos: set[str] = set()
    menos_confianza: set[str] = set()
    prob_mercado: dict[str, dict[str, float]] = {}
    if p_1x2 is None and p_mas is None:
        mu_l, mu_v = mod_l, mod_v
        finales, devoluciones, m = modelo, dev_modelo, None
        notas.append(NOTA_SIN_PINNACLE)
        confianza_datos = V.bajar(confianza_datos)
    else:
        anc = anclar_a_mercado((mod_l, mod_v), rho, p_1x2, p_mas)
        prob_mercado, _, _ = _mercados_goles(anc.mu_l * f_l, anc.mu_v * f_v, anc.rho)
        if p_1x2:
            # el 1X2 sale directo de Pinnacle; los ajustes lo mueven en la misma proporción que a la matriz
            sin_aj, _, _ = _mercados_goles(anc.mu_l, anc.mu_v, anc.rho)
            prob_mercado["ganador"] = _ganador_pinnacle(p_1x2, prob_mercado["ganador"], sin_aj["ganador"])
            prob_mercado["doble_oportunidad"] = _doble(prob_mercado["ganador"])
        mu_l, mu_v = mezclar_goles((mod_l, mod_v), (anc.mu_l, anc.mu_v), peso_modelo) if peso_modelo > 0 else (anc.mu_l, anc.mu_v)
        w = min(max(peso_modelo, 0.0), 1.0)
        rho = w * rho + (1 - w) * anc.rho
        finales, devoluciones, m = _mercados_goles(mu_l * f_l, mu_v * f_v, rho)
        if p_1x2 and w == 0:
            finales["ganador"] = dict(prob_mercado["ganador"])
            finales["doble_oportunidad"] = dict(prob_mercado["doble_oportunidad"])
        if anc.impreciso:
            motivo = "rho en su límite, cuotas contradictorias" if anc.rho_en_limite else f"error {anc.error:.3f}"
            notas.append(f"El ajuste a Pinnacle no cuadra bien ({motivo}): revisa las cuotas; menos confianza")
            confianza_datos = V.bajar(confianza_datos)
        if p_1x2 and p_mas is None:
            notas.append("Sin línea de goles de Pinnacle: el total sale del modelo; menos confianza en goles, "
                         "ambos anotan, goles por equipo, primer tiempo y hándicap")
            menos_confianza |= MERCADOS_DE_TOTAL
        if peso_modelo > 0:
            notas.append(f"Base: Pinnacle sin margen, mezclado con el modelo (peso del modelo {round(peso_modelo * 100)}%)")
        else:
            notas.append("Base: Pinnacle sin margen (modelo solo como referencia)")
        notas.append(f"Goles esperados del modelo solo: {mod_l * f_l:.2f} – {mod_v * f_v:.2f}")
        if not p_1x2:
            # sin 1X2 no hay referencia para el ganador: misma nota y misma baja que sin Pinnacle
            notas.append(NOTA_SIN_PINNACLE)
            confianza_datos = V.bajar(confianza_datos)
        # desacuerdo: modelo (sin ajustes, igual que Pinnacle) contra Pinnacle sin margen
        crudo, _, _ = _mercados_goles(mod_l, mod_v, modelos.goles.rho)
        if p_1x2 and max(abs(crudo["ganador"][k] - p) for k, p in zip(("1", "X", "2"), p_1x2)) > V.DESACUERDO_FUERTE:
            desacuerdos.add("1x2")
        if p_mas is not None and abs(crudo["goles"]["mas_2.5"] - p_mas) > V.DESACUERDO_FUERTE:
            desacuerdos.add("goles_2.5")
    mu_l, mu_v = mu_l * f_l, mu_v * f_v
    if m is None:
        m = matriz_marcadores(mu_l, mu_v, rho)
    finales = {k: dict(x) for k, x in finales.items()}
    # hándicap 0 (empate no apuesta) coherente con el ganador final: gana = 1 (o 2), devuelve = X
    for probs, dev in ((finales, devoluciones), (prob_mercado, None)):
        if probs:
            _handicap_cero(probs, dev)

    if modelos.corners:
        cl, cv = modelos.corners.esperados(l, v, neutral)
        tot = (cl + cv) * aj.factor("corners")
        modelo["corners"] = finales["corners"] = modelos.corners.mercados(tot, LINEAS_CORNERS)
        notas.append(f"Córners esperados: {tot:.1f}")
    if modelos.tarjetas:
        tl, tv = modelos.tarjetas.esperados(l, v, neutral)
        tot = (tl + tv) * factor_arbitro * aj.factor("tarjetas")
        modelo["tarjetas"] = finales["tarjetas"] = modelos.tarjetas.mercados(tot, LINEAS_TARJETAS)
        notas.append(f"Tarjetas esperadas: {tot:.1f} (factor árbitro x{factor_arbitro:.2f})")

    if desacuerdos:
        notas.append("Modelo y Pinnacle discrepan mucho en " + ", ".join(sorted(desacuerdos))
                     + ": baja la confianza de los mercados relacionados")

    notas.extend(aj.explicaciones())
    return Pronostico(
        partido=f"{local} vs {visitante}", local=l, visitante=v,
        goles_esperados=(round(mu_l, 2), round(mu_v, 2)),
        probabilidades=finales, prob_modelo=modelo, devoluciones=devoluciones,
        marcadores=marcador_mas_probable(m), notas=notas, confianza_base=confianza_datos,
        desacuerdos=desacuerdos, prob_mercado=prob_mercado, menos_confianza=menos_confianza,
    )
