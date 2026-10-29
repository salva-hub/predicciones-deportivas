"""Línea de comandos del sistema. Uso: python -m predicciones.cli <comando> ...

Comandos principales (ver CLAUDE.md para el flujo completo):
  partidos          Lista los partidos de una liga en una fecha (hora de Sonora)
  pronostico        Calcula y guarda el pronóstico de un partido
  evaluar           Con las cuotas de Winpot, decide qué apostar y cuánto
  parlays           Arma parlays de valor y combinadas cortas (+200 a +350) del día
  apostar           Registra una apuesta real que Salva confirmó
  movimiento        Registra un retiro o depósito
  bank              Estado del bank y límite del día
  calificar         Califica pronósticos y apuestas de partidos terminados
  liquidar          Liquida a mano una apuesta (ganada, perdida o devuelta)
  actualizar-datos  Actualiza los historiales guardados en datos/historial
  exportar          Junta el registro en pronosticos.json y apuestas.json (dashboard)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

import pandas as pd

from . import bank as B
from . import cuotas as C
from . import registro as R
from . import valor as V
from .ajustes import Ajustes, parsear, por_clima
from .calificar import DEVUELVE, GANA, PIERDE, SIN_DATOS, calificar, estado_parlay, ganancia
from .datos import clima as clima_mod
from .datos import cuotas_api, espn, historiales
from .datos import jugadores
from .datos.http import limpiar
from .equipos import coincide, normalizar
from .modelos import tiros_jugador as TJ
from .modelos.conteos import factor_arbitro
from .pronostico import NOTA_SIN_PINNACLE, _pinnacle_sin_margen, ajustar_modelos, filas_tiros_jugador, pronosticar  # noqa: F401

TECHADOS = {normalizar(x) for x in (
    "Estadio Santiago Bernabéu", "Veltins-Arena", "Merkur Spiel-Arena", "Deutsche Bank Park",
    "Stade Pierre-Mauroy", "Johan Cruijff ArenA", "BC Place", "Mercedes-Benz Stadium",
    "Ligga Arena", "Estadio La Cartuja",
)}
HORAS_ESPERA_DATOS = 48  # tras el inicio, se sigue buscando lo que falte (córners, tarjetas)
MENSAJE_PAUSA = "PAUSA: las apuestas de este mes perdieron 50% de la base del mes. No se recomiendan apuestas; hay que revisar el modelo."
LIGAS_DATOS_LIMITADOS = {"liga_mx", "mls", "brasil", "argentina", "libertadores", "concacaf"}
NOTA_SIN_ALINEACION = ("Tiros a puerta de jugador: ESPN aún no publica la alineación oficial; no se calculan "
                       "(se calculan en el pronóstico definitivo, con alineaciones)")
MIN_PARTIDOS_TIROS = 3  # partidos con estadísticas por jugador que necesita cada equipo


def es_techado(estadio: str | None) -> bool:
    return bool(estadio) and normalizar(estadio) in TECHADOS


def _aviso(texto: str, e: Exception | None = None) -> None:
    detalle = f": {limpiar(e)}" if e is not None else ""
    print(f"Aviso: {texto}{detalle}", file=sys.stderr)


def _imprimir(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _fecha(texto: str | None) -> date:
    if not texto or texto == "hoy":
        return R.ahora().date()
    if texto == "manana":
        return R.ahora().date() + timedelta(days=1)
    return date.fromisoformat(texto)


def _buscar_partido(liga: str, local: str, visitante: str, dia: date) -> dict | None:
    # ESPN agrupa por fecha del Este de EE. UU.; buscamos del día anterior al siguiente
    try:
        candidatos = espn.calendario(liga, dia - timedelta(days=1), dia + timedelta(days=1))
    except Exception as e:  # pragma: no cover - red
        _aviso("no se pudo leer el calendario de ESPN", e)
        return None
    hallados = [p for p in candidatos if coincide(local, p["local"]) and coincide(visitante, p["visitante"])]
    # si hay varios (ida y vuelta, etc.), el del día pedido
    hallados.sort(key=lambda p: not p["inicio_sonora"].startswith(str(dia)))
    return hallados[0] if hallados else None


def _pinnacle_manual(texto: str | None) -> dict | None:
    if not texto:
        return None
    out = {}
    for parte in texto.split(","):
        k, v = parte.split("=")
        v = float(v)
        out[k.strip()] = C.americana_a_decimal(v) if abs(v) >= 100 else v
    return out


def _pinnacle_api(liga: str, local: str, visitante: str) -> dict | None:
    try:
        eventos = cuotas_api.cuotas_pinnacle(liga)
    except Exception as e:  # pragma: no cover - red
        _aviso("no se pudieron leer cuotas de referencia", e)
        return None
    for ev in eventos or []:
        if coincide(local, ev["local"]) and coincide(visitante, ev["visitante"]):
            out = {k.removeprefix("pin_"): v for k, v in ev.items() if k.startswith("pin_")}
            out = {("mas_" + k.split("_")[1] if k.startswith("over") else "menos_" + k.split("_")[1] if k.startswith("under") else k): v
                   for k, v in out.items()}
            return out
    return None


def partidos_del_dia(liga: str, dia: date) -> list[dict]:
    """Partidos cuya fecha en Sonora es `dia`.

    ESPN agrupa por fecha del Este de EE. UU., que va 2 o 3 horas adelante: un
    partido de las 21:00 a 23:59 de Sonora aparece en el día siguiente de ESPN.
    Por eso se piden dia y dia+1 y se filtra por la fecha de Sonora.
    """
    return [p for p in espn.calendario(liga, dia, dia + timedelta(days=1))
            if p["inicio_sonora"].startswith(str(dia))]


def tiene_1x2(pin: dict | None) -> bool:
    return bool(pin) and all(pin.get(k) for k in ("1", "X", "2"))


def _tiros_jugador(liga: str, partido: dict, hist: pd.DataFrame, neutral: bool) -> tuple[list[dict], list[str]]:
    """Tiros a puerta esperados de los titulares (alineación oficial de ESPN). Devuelve (filas, notas)."""
    try:
        alin = jugadores.alineacion_partido(liga, partido["espn_id"])
    except Exception as e:  # noqa: BLE001 - sin red o ESPN cambió: el resto del pronóstico sigue
        return [], [f"Tiros a puerta de jugador: no se pudo leer la alineación de ESPN ({type(e).__name__})"]
    if not alin:
        return [], [NOTA_SIN_ALINEACION]
    antes = pd.Timestamp(partido["inicio_utc"])
    antes = antes.tz_convert(None) if antes.tzinfo else antes
    actos, notas = {}, []
    for lado, equipo in (("home", partido["local"]), ("away", partido["visitante"])):
        try:
            act = jugadores.actuaciones_equipo(liga, hist, equipo, antes)
        except Exception as e:  # noqa: BLE001
            _aviso(f"sin estadísticas por jugador de {equipo}", e)
            act = pd.DataFrame()
        n = act["espn_id"].nunique() if not act.empty else 0
        if n < MIN_PARTIDOS_TIROS:
            notas.append(f"Tiros a puerta de jugador: ESPN no trae estadísticas por jugador de {equipo} "
                         f"({n} partidos); no se calculan para ese equipo")
            alin[lado] = []
        else:
            notas.append(f"Tiros a puerta de jugador de {equipo}: {n} partidos (temporada actual y anterior)")
        actos[lado] = act
    if not alin["home"] and not alin["away"]:
        return [], notas
    fac = TJ.factores_liga(hist, antes)
    tiros = TJ.pronostico_partido(alin, actos["home"], actos["away"], fac, partido["local"], partido["visitante"], neutral)
    notas.append("Tiros a puerta de jugador: alineación oficial de ESPN; modelo Poisson con tasa por 90, minutos "
                 "y rival; confianza siempre baja; solo con valor de 10% o más")
    return tiros, notas


def _resolver_jugador(mercado: str, seleccion: str, selecciones: list[dict]) -> str:
    """Nombre del jugador como lo guarda el pronóstico ('mbappe:mas_0.5' -> 'Kylian Mbappé:mas_0.5').

    Acepta el nombre sin acentos ni mayúsculas, o solo una parte (el apellido) si no se repite.
    Si no lo encuentra, devuelve la selección tal cual.
    """
    if mercado != "tiros_jugador" or ":" not in seleccion:
        return seleccion
    nombre, linea = seleccion.rsplit(":", 1)
    linea = linea.strip()
    nombres = {s.get("jugador") or s["seleccion"].rsplit(":", 1)[0] for s in selecciones if s["mercado"] == mercado}
    n = normalizar(nombre)
    iguales = [x for x in nombres if normalizar(x) == n]
    if not iguales:
        iguales = [x for x in nombres if n and all(w in normalizar(x).split() for w in n.split())]
    return f"{iguales[0]}:{linea}" if len(iguales) == 1 else f"{nombre.strip()}:{linea}"


def cmd_partidos(a) -> None:
    dia = _fecha(a.fecha)
    _imprimir([{k: p[k] for k in ("espn_id", "inicio_sonora", "local", "visitante", "estadio", "ciudad")}
               for p in partidos_del_dia(a.liga, dia)])


def cmd_pronostico(a) -> None:
    dia = _fecha(a.fecha)
    partido = None if a.sin_red else _buscar_partido(a.liga, a.local, a.visitante, dia)
    hist = historiales.para_modelo(a.liga)
    modelos = ajustar_modelos(hist)
    aj = Ajustes()
    for texto in a.ajuste or []:
        x = parsear(texto)
        aj.agregar(x.objetivo, x.factor, x.motivo)

    info_clima = None
    techado = a.techado
    if partido and partido.get("estadio"):
        techado = techado or es_techado(partido["estadio"])
        if partido.get("ciudad") and not a.sin_red:
            try:
                ub = clima_mod.ubicar(partido["ciudad"], partido.get("pais"))
                if ub:
                    info_clima = clima_mod.clima_partido(ub["lat"], ub["lon"], partido["inicio_utc"])
            except Exception as e:  # pragma: no cover - red
                _aviso("sin clima", e)
    pin = _pinnacle_manual(a.pinnacle) or (None if a.sin_red else _pinnacle_api(a.liga, a.local, a.visitante))
    hay_pinnacle = _pinnacle_sin_margen(pin) != (None, None)
    nota_clima = None
    if hay_pinnacle:
        # el clima previsto ya está en el precio de Pinnacle: no se ajusta otra vez
        if info_clima and not techado:
            nota_clima = "Clima ya incluido en el precio de Pinnacle: no se ajusta"
    else:
        por_clima(info_clima, techado, aj)

    f_arb = 1.0
    if a.arbitro_prom and modelos.promedio_tarjetas:
        f_arb = factor_arbitro(a.arbitro_prom, a.arbitro_partidos or 0, modelos.promedio_tarjetas)

    confianza = a.confianza
    if a.liga in LIGAS_DATOS_LIMITADOS:
        confianza = V.bajar(confianza)
    # sin 1X2 de Pinnacle, pronosticar() baja la confianza un nivel y agrega NOTA_SIN_PINNACLE (una sola vez)
    p = pronosticar(modelos, a.local, a.visitante, aj, f_arb, pin, confianza, neutral=a.neutral,
                    pago_anticipado=not a.sin_pago_anticipado)
    if nota_clima:
        p.notas.append(nota_clima)
    if not partido:
        p.notas.append("AVISO: no se encontró el partido en ESPN; no se podrá calificar automáticamente")
    tiros: list[dict] = []
    if partido and not a.sin_red:
        tiros, notas_tiros = _tiros_jugador(a.liga, partido, hist, a.neutral)
        p.notas.extend(notas_tiros)

    registro = {
        "deporte": "futbol",
        "liga": a.liga,
        "partido": p.partido,
        "local": partido["local"] if partido else a.local,
        "visitante": partido["visitante"] if partido else a.visitante,
        "espn_id": partido["espn_id"] if partido else None,
        "inicio_utc": partido["inicio_utc"] if partido else None,
        "inicio_sonora": partido["inicio_sonora"] if partido else str(dia),
        "estadio": partido.get("estadio") if partido else None,
        "techado": techado,
        "neutral": a.neutral,
        # pago anticipado de Winpot (ganador 1/2 se paga si va 2 goles arriba); los viejos sin campo: no
        "pago_anticipado": not a.sin_pago_anticipado,
        "clima": info_clima,
        "arbitro": a.arbitro, "factor_arbitro": round(f_arb, 3),
        "pinnacle": pin,
        "goles_esperados": p.goles_esperados,
        # solo como referencia: la base es Pinnacle sin margen
        "modelo_1x2": {k: round(x, 3) for k, x in p.prob_modelo["ganador"].items()},
        "marcadores_probables": p.marcadores,
        "notas": p.notas,
        "confianza": p.confianza_base,
        "selecciones": p.tabla() + filas_tiros_jugador(p.partido, tiros),
        "estado": "pendiente",
    }
    if tiros:
        registro["tiros_jugador"] = [{k: t[k] for k in ("jugador", "equipo", "posicion", "tiros_esperados", "tasa90",
                                                         "minutos_esperados", "minutos_historial")} for t in tiros]
    if not a.no_guardar:
        registro = R.agregar_pronostico(registro)
    _imprimir(registro)


def _parsear_cuota(texto: str) -> tuple[str, str, float]:
    """'goles:mas_2.5=-130' -> ('goles', 'mas_2.5', 1.769)"""
    izq, cuota = texto.split("=")
    mercado, seleccion = izq.split(":", 1)
    v = float(cuota)
    return mercado.strip(), seleccion.strip(), C.americana_a_decimal(v) if abs(v) >= 100 else v


def _bank_del_dia(papel: bool) -> tuple[float, float, float, bool]:
    """(bank, ganancia cobrada hoy, apostado real pendiente hoy, pausa) con que se calculan montos y límite.
    En la prueba en papel manda el bank del sistema en papel (sus 3 categorías); el dinero real no cuenta."""
    if papel:
        return R.bank_papel("sistema"), R.ganancia_papel_hoy("sistema"), 0.0, R.en_pausa_papel()
    return R.bank_actual(), R.ganancia_cobrada_hoy(), R.apostado_hoy_pendiente(), R.en_pausa()


def cmd_evaluar(a) -> None:
    p = R.buscar_pronostico(a.id)
    if not p:
        sys.exit(f"No existe el pronóstico {a.id}")
    papel = B.en_prueba_papel(R.ahora().date())
    bank_hoy, gan_hoy, real_hoy, pausa = _bank_del_dia(papel)
    if pausa:
        print(MENSAJE_PAUSA, file=sys.stderr)
    elif papel:
        print(B.MENSAJE_PAPEL, file=sys.stderr)
    cuotas = [(m, _resolver_jugador(m, s_, p["selecciones"]), d) for m, s_, d in (_parsear_cuota(t) for t in a.winpot)]
    # en papel: lo ya calificado no se toca; se reemplazan solo las selecciones que se vuelven a evaluar
    evaluadas = {(m, s_) for m, s_, _ in cuotas}
    todas_previas = p.get("apuestas_papel", [])
    previas = [x for x in todas_previas if x.get("categoria") != V.PICK_MODELO]
    calificadas = {(x["mercado"], x["seleccion"]) for x in previas if x.get("estado") != "pendiente"}
    conservar = [x for x in previas if (x["mercado"], x["seleccion"]) in calificadas
                 or (x["mercado"], x["seleccion"]) not in evaluadas]
    # sin trampa: una vez empezado el partido no se agregan apuestas en papel
    iniciado = bool(p.get("resultado")) or bool(
        p.get("inicio_utc") and datetime.fromisoformat(p["inicio_utc"]) <= R.ahora())
    # pick del modelo: uno por partido; volver a evaluar reemplaza el pendiente de este pronóstico
    # entre todas las cuotas de Winpot conocidas del pronóstico (las guardadas y las de ahora; gana la más nueva)
    conocidas = {}
    for k, v in p.get("cuotas_winpot", {}).items():
        m, _, s_ = k.partition(":")
        if v:
            conocidas[(m, s_)] = C.americana_a_decimal(float(str(v).replace("\u2212", "-")))
    conocidas.update({(m, s_): d for m, s_, d in cuotas})
    pick = V.elegir_pick_modelo([
        V.Seleccion(p["partido"], m, s_, f["prob"], f.get("devolucion", 0.0), f["confianza"], d)
        for (m, s_), d in conocidas.items() for f in p["selecciones"] if f["mercado"] == m and f["seleccion"] == s_])
    picks_previos = [x for x in todas_previas if x.get("categoria") == V.PICK_MODELO]
    pick_fijo = any(x.get("estado") != "pendiente" for x in picks_previos) or any(
        x.get("categoria") == V.PICK_MODELO and any(q.get("pronostico") == p["id"] for q in x.get("patas", []))
        for x in R.parlays_papel())
    nuevo_pick = papel and not pausa and not iniciado and not pick_fijo and pick is not None
    if not nuevo_pick:
        conservar += picks_previos
    disponible = max(0.0, B.limite_del_dia(bank_hoy, gan_hoy) - real_hoy)
    if papel:
        # en papel, el límite del día descuenta lo "apostado" en papel hoy por el sistema
        # (la elección de Salva y el pick del modelo tienen su propio bank y no usan el límite)
        hoy = R.ahora().date()
        propio = sum(x.get("monto", 0.0) for x in conservar
                     if x.get("categoria") not in R.APARTE and R._fecha_sonora(x["evaluado"]).date() == hoy)
        disponible = max(0.0, disponible - R.apostado_papel_hoy(excluir=p["id"]) - propio)
    sin_limite = papel and not B.LIMITE_EN_PAPEL
    if sin_limite:
        disponible = B.SIN_LIMITE
    disponible_inicial = None if sin_limite else disponible
    # máximo por día de cada categoría: se cuenta lo ya puesto hoy (en papel o real)
    cuenta = R.categorias_hoy(excluir=p["id"] if papel else None)
    if papel:
        hoy = R.ahora().date()
        for x in conservar:
            if R._fecha_sonora(x["evaluado"]).date() == hoy:
                c = x.get("categoria") or V.VALOR
                cuenta[c] = cuenta.get(c, 0) + 1
    filas = []  # (mercado, seleccion, Seleccion | None, categoria)
    for mercado, seleccion, dec in cuotas:
        fila = next((s for s in p["selecciones"] if s["mercado"] == mercado and s["seleccion"] == seleccion), None)
        if not fila:
            filas.append((mercado, seleccion, None, None, None))
            continue
        s = V.Seleccion(p["partido"], mercado, seleccion, fila["prob"], fila.get("devolucion", 0.0), fila["confianza"], dec)
        filas.append((mercado, seleccion, s, fila, V.categoria_sencilla(s)))
    # con dinero real y la categoría fuerte sin aprobar, una selección con valor es "valor"
    if not papel and V.FUERTE not in B.CATEGORIAS_DINERO_REAL:
        filas = [(*f[:4], V.VALOR) if f[4] == V.FUERTE and V.tiene_valor(f[2]) else f for f in filas]
    # regla temporal (29-oct-2026): un "menos" de córners/tarjetas con Winpot muy en contra
    # del modelo no entra en ninguna categoría ni en parlays o combinadas
    dec_sel = {(m, s_): d for m, s_, d in cuotas}
    previas_winpot = p.get("cuotas_winpot", {})

    def _contrario(m: str, s_: str) -> float | None:
        otro = "mas_" + s_[len("menos_"):]
        if (m, otro) in dec_sel:
            return dec_sel[(m, otro)]
        txt = previas_winpot.get(f"{m}:{otro}")
        return C.americana_a_decimal(float(txt)) if txt else None

    discrepan = {i for i, f in enumerate(filas) if f[2] is not None and f[1].startswith("menos_")
                 and V.discrepa_menos(f[0], f[1], f[2].prob, f[2].cuota_ofrecida, _contrario(f[0], f[1]))}
    filas = [(*f[:4], None) if i in discrepan else f for i, f in enumerate(filas)]
    # sencilla fuerte: como mucho MAX_POR_DIA al día; si hay varias, la de mayor probabilidad.
    # Sin cupo, si tiene valor pasa a la categoría valor
    fuertes = sorted((i for i, f in enumerate(filas) if f[4] == V.FUERTE), key=lambda i: -filas[i][2].prob)
    libres = max(0, V.max_por_dia(V.FUERTE, papel) - cuenta.get(V.FUERTE, 0))
    sin_cupo = set(fuertes[libres:])
    for i in sin_cupo:
        filas[i] = (*filas[i][:4], V.VALOR if V.tiene_valor(filas[i][2]) else None)
    # prioridad para el límite del día: fuerte y valor juntas, primero la de mayor valor esperado
    orden = sorted(range(len(filas)), key=lambda i: (filas[i][4] is None, -(filas[i][2].ve() if filas[i][2] else 0)))
    resultado: list[dict | None] = [None] * len(filas)
    en_papel = []
    for i in orden:
        mercado, seleccion, s, fila, cat = filas[i]
        if s is None:
            resultado[i] = {"mercado": mercado, "seleccion": seleccion, "error": "no está en el pronóstico (o no se ofrece por reglas)"}
            continue
        dec, ve, dev = s.cuota_ofrecida, s.ve(), s.prob_devolucion
        motivo = None
        if i in discrepan:
            motivo = V.MOTIVO_DISCREPANCIA
        elif i in sin_cupo and cat is None:
            motivo = "ya hay una sencilla fuerte hoy (máximo 1 por día)"
        elif cat is None and V.fuerte_salvo_momio(s):
            motivo = V.MOTIVO_MOMIO_FUERTE
        if cat == V.VALOR:
            pedido = B.monto(bank_hoy, B.porcentaje_sencilla(ve, s.confianza, s.ve_minimo))
        elif cat == V.FUERTE:
            pedido = B.monto_sencilla_fuerte(bank_hoy, s.confianza, dec)
        else:
            pedido = 0.0
        # cada sencilla, como mucho lo que queda del límite del día
        monto = min(pedido, B.monto(disponible, 1.0))
        fila_res = {
            "mercado": mercado, "seleccion": seleccion,
            "cuota_winpot": C.formato_americano(dec), "cuota_minima": fila["cuota_minima"],
            "prob": fila["prob"], "ve": round(ve, 4), "confianza": s.confianza,
            "categoria": cat, "con_valor": V.tiene_valor(s),
            "apostar": cat is not None and monto > 0, "monto_sencilla": monto,
            "sirve_para_parlay": ve >= V.ve_min_parlay_pata(mercado),
            "sirve_para_combinada": V.sirve_para_combinada(s),
        }
        if i in discrepan:
            fila_res.update(sirve_para_parlay=False, sirve_para_combinada=False)
        if dev:
            fila_res["devolucion"] = dev
        if 0 < monto < pedido:
            fila_res["recortada"] = True  # el límite del día no alcanzó para el monto completo
        if motivo:
            fila_res["motivo"] = motivo
        elif cat and monto <= 0:
            fila_res["motivo"] = "límite del día alcanzado"
        if pausa:
            fila_res.update(apostar=False, monto_sencilla=0, sirve_para_parlay=False, sirve_para_combinada=False, motivo="pausa")
        elif papel and (mercado, seleccion) in calificadas:
            fila_res.update(apostar=False, monto_sencilla=0, apostar_papel=False, monto_papel=0,
                            motivo="ya calificada en papel")
        elif papel and iniciado:
            fila_res.update(apostar=False, monto_sencilla=0, apostar_papel=False, monto_papel=0,
                            sirve_para_parlay=False, sirve_para_combinada=False,
                            motivo="el partido ya empezó: no cuenta en papel")
        elif papel:
            pasa = fila_res["apostar"]
            fila_res.update(apostar=False, monto_sencilla=0, apostar_papel=pasa, monto_papel=monto if pasa else 0)
            if pasa:
                fila_res["motivo"] = "prueba en papel"
                en_papel.append((i, {
                    "mercado": mercado, "seleccion": seleccion, "categoria": cat,
                    "con_valor": fila_res["con_valor"], "recortada": monto < pedido, "cuota": round(dec, 4),
                    "cuota_americana": C.formato_americano(dec), "monto": monto, "ve": round(ve, 4),
                    "prob": fila["prob"], "confianza": s.confianza, "evaluado": R.ahora().isoformat(),
                    "estado": "pendiente",
                }))
        elif fila_res["apostar"] and cat not in B.CATEGORIAS_DINERO_REAL:
            fila_res.update(apostar=False, monto_sencilla=0, monto_si_se_aprueba=monto,
                            motivo=B.MENSAJE_CATEGORIA_PENDIENTE)
        if fila_res.get("apostar") or fila_res.get("apostar_papel"):
            disponible = max(0.0, disponible - monto)  # el límite del día cubre todas las selecciones
        resultado[i] = fila_res
    en_papel = [x for _, x in sorted(en_papel, key=lambda t: t[0])]
    # se mezcla por selección: lo evaluado antes y no repetido ahora se conserva
    nuevas = {(r["mercado"], r["seleccion"]) for r in resultado}
    p["evaluacion"] = [r for r in p.get("evaluacion", []) if (r["mercado"], r["seleccion"]) not in nuevas] + resultado
    p["cuotas_winpot"] = {**p.get("cuotas_winpot", {}),
                          **{f"{r['mercado']}:{r['seleccion']}": r.get("cuota_winpot") for r in resultado if "error" not in r}}
    pick_res = None
    if nuevo_pick:
        # solo medición: monto fijo, no usa ni respeta el límite del día
        dec = pick.cuota_ofrecida
        pick_res = {
            "mercado": pick.mercado, "seleccion": pick.seleccion, "categoria": V.PICK_MODELO,
            "con_valor": V.tiene_valor(pick), "cuota": round(dec, 4), "cuota_americana": C.formato_americano(dec),
            "monto": V.MONTO_PICK_MODELO, "ve": round(pick.ve(), 4), "prob": pick.prob,
            "confianza": pick.confianza,
            "evaluado": R.ahora().isoformat(), "estado": "pendiente",
        }
        en_papel.append(pick_res)
    elif picks_previos:
        pick_res = {**picks_previos[-1], "nota": "ya había pick del modelo de este partido"}
    elif pick_fijo:
        pick_res = {"nota": "ya hay pick del modelo de este partido en los parlays en papel"}
    if papel and not pausa:
        p["apuestas_papel"] = conservar + ([] if iniciado else en_papel)
        if p.get("resultado"):
            _calificar_papel(p)  # el partido ya se calificó: se califican ya
    R.actualizar_pronostico(p)
    salida = {"bank_actual": bank_hoy, "disponible_hoy": disponible_inicial, "en_pausa": pausa, "prueba_papel": papel}
    if papel and not pausa:
        salida["aviso"] = B.MENSAJE_PAPEL
        if iniciado:
            salida["nota"] = "El partido ya empezó o terminó: la evaluación se muestra, pero no se agregan apuestas en papel"
    if pick_res:
        salida["pick_modelo"] = pick_res
    _imprimir({**salida, "evaluacion": resultado})


def _candidatas_del_dia(dia: date, campo: str) -> tuple[list, dict]:
    """Selecciones evaluadas del día marcadas con `campo` (sirve_para_parlay o
    sirve_para_combinada), solo de partidos que aún no empiezan. Si un partido tiene
    varios pronósticos, vale el más reciente."""
    candidatas, ids = [], {}  # id(selección) -> su pronóstico
    ultimos: dict[str, dict] = {}
    for p in R.pronosticos():  # ordenados por fecha de creación
        if str(p.get("inicio_sonora", "")).startswith(str(dia)):
            ultimos[p.get("espn_id") or p["partido"]] = p
    for p in ultimos.values():
        if R.partido_iniciado(p):
            continue  # partido empezado o jugado: sus selecciones ya no entran a parlays nuevos
        for r in p.get("evaluacion", []):
            if r.get(campo):
                fila = next((s for s in p.get("selecciones", [])
                             if s["mercado"] == r["mercado"] and s["seleccion"] == r["seleccion"]), {})
                sel = V.Seleccion(p["partido"], r["mercado"], r["seleccion"], r["prob"],
                                  fila.get("devolucion", r.get("devolucion", 0.0)), r["confianza"],
                                  C.americana_a_decimal(float(r["cuota_winpot"])))
                ids[id(sel)] = p
                candidatas.append(sel)
    return candidatas, ids


def cmd_parlays(a) -> None:
    papel = B.en_prueba_papel(R.ahora().date())
    bank_hoy, gan_hoy, real_hoy, pausa = _bank_del_dia(papel)
    if pausa:
        _imprimir(MENSAJE_PAUSA)
        return
    dia = _fecha(a.fecha)
    cand_valor, ids = _candidatas_del_dia(dia, "sirve_para_parlay")
    cand_comb, ids_comb = _candidatas_del_dia(dia, "sirve_para_combinada")
    ids.update(ids_comb)
    disponible = max(0.0, B.limite_del_dia(bank_hoy, gan_hoy) - real_hoy)
    if papel:
        # todo lo puesto hoy en papel, salvo los parlays pendientes de este día (se reemplazan)
        disponible = max(0.0, disponible - R.apostado_papel_hoy(reemplazar_parlays_de=dia))
    if papel and not B.LIMITE_EN_PAPEL:
        disponible = B.SIN_LIMITE
    # 1) parlays de valor; 2) combinadas cortas (sin repetir un parlay de valor idéntico)
    valor = V.armar_parlays(cand_valor, max_patas=a.max_patas)
    cuenta = R.categorias_hoy(reemplazar_parlays_de=dia if papel else None)
    cupo = max(0, V.max_por_dia(V.COMBINADA, papel) - cuenta.get(V.COMBINADA, 0))
    combinadas = V.armar_combinadas(cand_comb, top=cupo, excluir={V.clave_patas(x) for x in valor}) if cupo else []
    lista = [(x, V.VALOR, B.monto(bank_hoy, B.porcentaje_parlay(x.confianza))) for x in valor]
    lista += [(x, V.COMBINADA, B.monto(bank_hoy, B.porcentaje_combinada(x.confianza))) for x in combinadas]
    # dinero real: las categorías aún no aprobadas se muestran pero no usan el límite
    cuenta_limite = [papel or cat in B.CATEGORIAS_DINERO_REAL for _, cat, _ in lista]
    recortados = iter(B.ajustar_al_limite([m for (_, _, m), c in zip(lista, cuenta_limite) if c], disponible))
    out, guardar = [], []
    for (par, cat, pedido), c in zip(lista, cuenta_limite):
        d = {"categoria": cat, **par.resumen()}
        d["monto"] = next(recortados) if c else 0
        if 0 < d["monto"] < pedido:
            d["recortada"] = True
        if papel:
            d.update(papel=True, monto_papel=d["monto"], monto=0)
            if d["monto_papel"] > 0:
                patas = []
                for x in par.patas:
                    pr = ids[id(x)]
                    patas.append({"pronostico": pr["id"], "partido": x.partido, "liga": pr.get("liga"),
                                  "mercado": x.mercado, "seleccion": x.seleccion, "cuota": x.cuota_ofrecida,
                                  "prob": x.prob, "confianza": x.confianza, "estado": "pendiente"})
                guardar.append({"clave": "|".join(sorted(f"{q['pronostico']}:{q['mercado']}:{q['seleccion']}" for q in patas)),
                                "tipo": "parlay", "categoria": cat, "patas": patas, "cuota": round(par.cuota, 4),
                                "cuota_americana": C.formato_americano(par.cuota), "prob": round(par.prob, 4),
                                "monto": d["monto_papel"], "recortada": d["monto_papel"] < pedido,
                                "creado": R.ahora().isoformat(), "estado": "pendiente"})
            else:
                d["motivo"] = "límite del día alcanzado"
        elif not c:
            d.update(monto_si_se_aprueba=pedido, motivo=B.MENSAJE_CATEGORIA_PENDIENTE)
        elif d["monto"] <= 0:
            d["motivo"] = "límite del día alcanzado"
        out.append(d)
    if papel:
        R.guardar_parlays_papel(dia, guardar)
        _imprimir({"aviso": B.MENSAJE_PAPEL, "parlays_papel": out or "Sin parlays ni combinadas para ese día"})
        return
    _imprimir(out or "Sin parlays ni combinadas para ese día")


def cmd_apostar(a) -> None:
    if a.categoria == V.PICK_MODELO:
        sys.exit("El pick del modelo es solo en papel: `evaluar` lo registra solo; nunca se apuesta con dinero real.")
    if B.en_prueba_papel(R.ahora().date()):
        if not a.forzar:
            sys.exit(B.MENSAJE_PAPEL + ". No se registra la apuesta (usa --forzar solo si Salva de verdad apostó).")
        print(f"Aviso: {B.MENSAJE_PAPEL}. Se registra igual porque se usó --forzar.", file=sys.stderr)
    patas = []
    for texto in a.pata:
        pid, resto = texto.split(":", 1)
        mercado, seleccion, dec = _parsear_cuota(resto)
        p = R.buscar_pronostico(pid)
        if not p:
            sys.exit(f"No existe el pronóstico {pid}")
        seleccion = _resolver_jugador(mercado, seleccion, p.get("selecciones", []))
        patas.append({"pronostico": pid, "partido": p["partido"], "liga": p["liga"],
                      "deporte": p.get("deporte") or R.DEPORTE_DEFECTO, "espn_id": p.get("espn_id"),
                      "mercado": mercado, "seleccion": seleccion, "cuota": dec, "estado": "pendiente"})
    cuota = C.cuota_parlay(x["cuota"] for x in patas)
    ap = R.agregar_apuesta({
        "tipo": "sencilla" if len(patas) == 1 else "parlay",
        "patas": patas, "cuota": round(cuota, 4), "cuota_americana": C.formato_americano(cuota),
        "monto": float(a.monto), "casa": a.casa,
        **({"categoria": a.categoria} if a.categoria else {}),
    })
    _imprimir(ap)


def cmd_movimiento(a) -> None:
    _imprimir(R.agregar_movimiento(a.tipo, a.monto, a.nota or ""))


def cmd_bank(a) -> None:
    papel = B.en_prueba_papel(R.ahora().date())
    real = R.bank_actual()
    # en papel mandan los montos y el límite del bank del sistema en papel
    actual, gan, real_hoy, pausa = _bank_del_dia(papel)
    inicio_mes = R.bank_papel("sistema", hasta=R._inicio_mes()) if papel else R.bank_inicio_mes()
    limite = B.limite_del_dia(actual, gan)
    pendientes = [x for x in R.apuestas() if x.get("estado") == "pendiente"]
    # en la prueba en papel, el disponible descuenta también lo "apostado" en papel hoy
    en_papel = R.apostado_papel_hoy() if papel else 0.0
    hoy = R.categorias_hoy()
    _imprimir({
        "bank_actual": actual, "bank_inicio_mes": inicio_mes,
        "ganancia_cobrada_hoy": gan, "limite_del_dia": limite,
        "apostado_hoy": R.apostado_hoy_pendiente(),
        "prueba_papel": papel, "apostado_papel_hoy": en_papel,
        # en papel no hay límite del día (None = sin límite)
        "disponible_hoy": None if papel and not B.LIMITE_EN_PAPEL else max(0, limite - real_hoy - en_papel),
        "limite_en_papel": B.LIMITE_EN_PAPEL,
        # dinero real (p. ej. `apostar --forzar` durante el papel): siempre con límite
        "disponible_hoy_real": max(0, B.limite_del_dia(*_bank_del_dia(False)[:2]) - R.apostado_hoy_pendiente()),
        "resultado_apuestas_mes": R.resultado_papel_mes("sistema") if papel else R.resultado_apuestas_mes(),
        "base_mes": R.base_mes_papel("sistema") if papel else R.base_mes(),
        "en_pausa": pausa,
        **({"banks_papel": {b: R.bank_papel(b) for b in R.BANKS_PAPEL}, "bank_real": real} if papel else {}),
        "apuestas_pendientes": len(pendientes),
        "monto_sencilla_max": B.monto(actual, B.REGLAS.sencilla_max),
        "monto_parlay_alta": B.monto(actual, B.REGLAS.parlay_alta),
        "monto_parlay_media_baja": B.monto(actual, B.REGLAS.parlay_media_baja),
        # categorías nuevas (en papel hasta el 26-nov-2026)
        "categorias_dinero_real": sorted(B.CATEGORIAS_DINERO_REAL),
        "usadas_hoy": {c: hoy.get(c, 0) for c in V.CATEGORIAS_REGISTRO},
        "max_por_dia": None if papel and not B.MAXIMOS_EN_PAPEL else V.MAX_POR_DIA,
        "sencilla_fuerte_ganar": {"alta": B.monto(actual, B.REGLAS.fuerte_ganar_alta),
                                  "media": B.monto(actual, B.REGLAS.fuerte_ganar_media)},
        "monto_combinada": {"alta": B.monto(actual, B.REGLAS.combinada_alta),
                            "media": B.monto(actual, B.REGLAS.combinada_media)},
    })


def _horas_desde(inicio_utc: str, ahora: datetime) -> float:
    return (ahora - datetime.fromisoformat(inicio_utc)).total_seconds() / 3600


def _id_jugador(p: dict, mercado: str, seleccion: str) -> str | None:
    """Id de ESPN del jugador de una selección de tiros_jugador (lo guarda la fila del pronóstico)."""
    if mercado != "tiros_jugador":
        return None
    fila = next((s for s in p.get("selecciones", []) if s["mercado"] == mercado and s["seleccion"] == seleccion), {})
    return fila.get("jugador_id")


def _pago(p: dict) -> bool:
    """Pago anticipado del pronóstico. Los guardados antes de esta regla no traen el campo: no."""
    return bool(p.get("pago_anticipado", False))


def _calificar_papel(p: dict) -> None:
    """Califica las apuestas en papel de un pronóstico que ya tiene resultado."""
    for x in p.get("apuestas_papel", []):
        if x.get("estado") not in ("pendiente", SIN_DATOS):
            continue
        est = calificar(x["mercado"], x["seleccion"], p["resultado"], _pago(p),
                        jugador_id=_id_jugador(p, x["mercado"], x["seleccion"]))
        if est == SIN_DATOS and p.get("estado") != "calificado":
            continue  # se reintenta en la siguiente corrida
        x["estado"] = est
        x["ganancia"] = ganancia(est, x["monto"], x["cuota"])


def cmd_calificar(a) -> None:
    ahora = datetime.now().astimezone()
    cache: dict[str, dict | None] = {}
    lista = R.pronosticos()
    cambios = 0
    for p in lista:
        if p.get("estado") != "pendiente" or not p.get("espn_id") or not p.get("inicio_utc"):
            continue
        horas = _horas_desde(p["inicio_utc"], ahora)
        if horas < 2.5:
            continue
        clave = p["espn_id"]
        if clave not in cache:
            try:
                cache[clave] = espn.resultado(p["liga"], p["espn_id"], p["inicio_utc"])
            except Exception as e:  # noqa: BLE001 - un partido no detiene a los demás
                print(f"Aviso: no se pudo leer {p['partido']} ({type(e).__name__})", file=sys.stderr)
                cache[clave] = None
        r = cache[clave]
        if not r or not r.get("terminado"):
            continue
        p["resultado"] = {k: r.get(k) for k in ("goles_local", "goles_visitante", "goles_local_pt", "goles_visitante_pt",
                                                 "corners_local", "corners_visitante", "tarjetas_local", "tarjetas_visitante")}
        # detalle de tarjetas (Winpot: amarilla 1, roja 2, doble amarilla 3)
        p["resultado"].update({k: r[k] for k in ("amarillas_local", "amarillas_visitante", "rojas_local", "rojas_visitante",
                                                 "segundas_amarillas_local", "segundas_amarillas_visitante")
                               if r.get(k) is not None})
        if r.get("prorroga"):
            p["resultado"]["prorroga"] = True
        # mayor ventaja de cada equipo en tiempo reglamentario (pago anticipado)
        p["resultado"].update({k: r[k] for k in ("ventaja_max_local", "ventaja_max_visitante") if r.get(k) is not None})
        # tiros a puerta por jugador (solo si el pronóstico tiene ese mercado)
        if r.get("tiros_jugadores") and any(s["mercado"] == "tiros_jugador" for s in p["selecciones"]):
            p["resultado"]["tiros_jugadores"] = r["tiros_jugadores"]
        for s in p["selecciones"]:
            s["estado"] = calificar(s["mercado"], s["seleccion"], r, _pago(p), jugador_id=s.get("jugador_id"))
        # si falta algún dato, se reintenta en las siguientes corridas hasta 48 h
        if not any(s["estado"] == SIN_DATOS for s in p["selecciones"]) or horas >= HORAS_ESPERA_DATOS:
            p["estado"] = "calificado"
        _calificar_papel(p)
        R.actualizar_pronostico(p)
        cambios += 1

    por_id = {p["id"]: p for p in lista}
    for ap in R.apuestas():
        if ap.get("estado") != "pendiente":
            continue
        cambio = _liquidar(ap, por_id)
        if cambio:
            R.actualizar_apuesta(ap)
            cambios += cambio == "liquidada"
    # parlays de la prueba en papel: igual que los reales, pero no tocan el bank
    for doc in R.archivos_parlays_papel():
        cambio_doc = False
        for par in doc.get("parlays", []):
            if par.get("estado", "pendiente") == "pendiente":  # sin estado (registrado a mano): pendiente
                cambio = _liquidar(par, por_id, "parlay en papel")
                cambio_doc |= bool(cambio)
                cambios += cambio == "liquidada"
        if cambio_doc:
            R.actualizar_parlays_papel(doc)
    print(f"Calificados/liquidados: {cambios}")


def _liquidar(ap: dict, por_id: dict, etiqueta: str = "apuesta") -> str | None:
    """Califica las patas de una apuesta (real o en papel) y la liquida si ya se puede.

    Devuelve "liquidada", "patas" (cambió alguna pata) o None.
    """
    antes = [x.get("estado") for x in ap["patas"]]
    for pata in ap["patas"]:
        pata.setdefault("estado", "pendiente")  # pata sin estado (registrada a mano): pendiente
        if pata["estado"] not in ("pendiente", SIN_DATOS):
            continue
        p = por_id.get(pata["pronostico"])
        if not p or not p.get("resultado"):
            continue
        est = calificar(pata["mercado"], pata["seleccion"], p["resultado"], _pago(p),
                        jugador_id=_id_jugador(p, pata["mercado"], pata["seleccion"]))
        # sin datos todavía: queda pendiente para volver a calificarla
        pata["estado"] = "pendiente" if est == SIN_DATOS and p.get("estado") != "calificado" else est
    estados = [x["estado"] for x in ap["patas"]]
    # una pata perdida liquida el parlay sin esperar a las demás
    if estado_parlay(estados) == SIN_DATOS:
        # pasada la ventana de 48 h, una pata sin datos ya no se va a calificar sola: revisión a mano
        sin_datos = [x for x in ap["patas"] if x["estado"] == SIN_DATOS]
        marcar = bool(sin_datos) and not ap.get("requiere_revision")
        if marcar:
            ap["requiere_revision"] = True
            for x in sin_datos:
                partido = (por_id.get(x["pronostico"]) or {}).get("partido", "?")
                print(f"Revisar a mano: {etiqueta} {ap.get('id', ap.get('clave', '?'))} ({partido}, {x['mercado']})",
                      file=sys.stderr)
        return "patas" if estados != antes or marcar else None
    est = estado_parlay(estados)
    cuota_real = 1.0
    for x in ap["patas"]:
        if x["estado"] == "ganada":
            cuota_real *= x["cuota"]
    ap["estado"] = est
    ap["ganancia"] = ganancia(est, ap["monto"], cuota_real)
    ap["liquidada"] = R.ahora().isoformat()
    ap.pop("requiere_revision", None)
    return "liquidada"


def _cuota_real(texto: str) -> float:
    """Cuota americana (−110, +250, 100) o decimal (2.5); acepta el signo menos tipográfico."""
    try:
        v = float(str(texto).replace("\u2212", "-").strip())
    except ValueError:
        sys.exit(f"Cuota real inválida: {texto}")
    cuota = C.americana_a_decimal(v) if abs(v) >= 100 else v
    if not cuota > 1 or cuota == float("inf"):
        sys.exit("La cuota real debe ser mayor que 1.0 (decimal) o americana (−110, +200)")
    return cuota


def cmd_liquidar(a) -> None:
    ap = R.buscar_apuesta(a.id)
    if not ap:
        sys.exit(f"No existe la apuesta {a.id}")
    if ap.get("estado") != "pendiente":
        _aviso(f"la apuesta ya estaba liquidada como {ap.get('estado')}; se corrige")
    if a.cuota_real is not None:
        cuota = _cuota_real(a.cuota_real)
        ap["cuota_real"] = round(cuota, 4)
    else:
        # una corrección sin --cuota-real respeta la cuota real ya registrada
        cuota = float(ap.get("cuota_real") or ap["cuota"])
    ap["estado"] = a.estado
    ap["ganancia"] = ganancia(a.estado, float(ap["monto"]), cuota)
    # el día de la liquidación decide en qué día y mes cuenta la ganancia: una corrección no lo mueve
    if ap.get("liquidada"):
        ap["corregida"] = R.ahora().isoformat()
    else:
        ap["liquidada"] = R.ahora().isoformat()
    ap["liquidada_a_mano"] = True
    ap.pop("requiere_revision", None)
    R.actualizar_apuesta(ap)
    _imprimir(ap)


def cmd_actualizar_datos(a) -> None:
    ligas = a.liga or [l for l in espn.LIGAS if l not in historiales.SELECCIONES]
    for liga in ligas:
        try:
            df = historiales.actualizar(liga)
            print(f"{liga}: {len(df)} partidos")
        except Exception as e:  # pragma: no cover - red
            print(f"{liga}: ERROR {limpiar(e)}", file=sys.stderr)


def cmd_exportar(a) -> None:
    R.exportar(a.salida)
    print(f"Registro exportado en {a.salida}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="predicciones")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("partidos")
    s.add_argument("--liga", required=True, choices=sorted(espn.LIGAS))
    s.add_argument("--fecha")
    s.set_defaults(f=cmd_partidos)

    s = sub.add_parser("pronostico")
    s.add_argument("--liga", required=True, choices=sorted(espn.LIGAS))
    s.add_argument("--local", required=True)
    s.add_argument("--visitante", required=True)
    s.add_argument("--fecha")
    s.add_argument("--ajuste", action="append", help="objetivo:factor:motivo (objetivo: goles_local, goles_visitante, corners, tarjetas)")
    s.add_argument("--arbitro")
    s.add_argument("--arbitro-prom", type=float, help="tarjetas por partido del árbitro en la competición contando roja = 2 "
                   "(amarillas por partido + 2 × rojas por partido)")
    s.add_argument("--arbitro-partidos", type=int)
    s.add_argument("--pinnacle", help="cuotas manuales: 1=-150,X=+280,2=+400,mas_2.5=-110,menos_2.5=-110")
    s.add_argument("--confianza", required=True, choices=V.ORDEN_CONFIANZA)
    s.add_argument("--techado", action="store_true")
    s.add_argument("--neutral", action="store_true", help="cancha neutral: sin ventaja de local")
    s.add_argument("--sin-pago-anticipado", action="store_true",
                   help="Winpot no ofrece pago anticipado en este partido (por defecto sí)")
    s.add_argument("--sin-red", action="store_true")
    s.add_argument("--no-guardar", action="store_true")
    s.set_defaults(f=cmd_pronostico)

    s = sub.add_parser("evaluar")
    s.add_argument("--id", required=True)
    s.add_argument("--winpot", action="append", required=True, help="mercado:seleccion=cuota  (ej. goles:mas_2.5=-130; "
                   "tiros de jugador: tiros_jugador:Nombre Jugador:mas_0.5=+150)")
    s.set_defaults(f=cmd_evaluar)

    s = sub.add_parser("parlays")
    s.add_argument("--fecha")
    s.add_argument("--max-patas", type=int, default=4)
    s.set_defaults(f=cmd_parlays)

    s = sub.add_parser("apostar")
    s.add_argument("--pata", action="append", required=True, help="ID_PRONOSTICO:mercado:seleccion=cuota")
    s.add_argument("--monto", type=float, required=True)
    s.add_argument("--casa", default="Winpot")
    s.add_argument("--forzar", action="store_true", help="registrar aunque estemos en la prueba en papel")
    s.add_argument("--categoria", choices=V.CATEGORIAS_REGISTRO,
                   help="categoría de la apuesta (valor, sencilla_fuerte, combinada; eleccion_salva: la eligió Salva; "
                        "pick_modelo: solo lo registra evaluar, en papel; aquí se rechaza)")
    s.set_defaults(f=cmd_apostar)

    s = sub.add_parser("movimiento")
    s.add_argument("--tipo", required=True, choices=["retiro", "deposito"])
    s.add_argument("--monto", type=float, required=True)
    s.add_argument("--nota")
    s.set_defaults(f=cmd_movimiento)

    s = sub.add_parser("bank")
    s.set_defaults(f=cmd_bank)

    s = sub.add_parser("calificar")
    s.set_defaults(f=cmd_calificar)

    s = sub.add_parser("liquidar")
    s.add_argument("--id", required=True, help="id de la apuesta (A-...)")
    s.add_argument("--estado", required=True, choices=[GANA, PIERDE, DEVUELVE])
    s.add_argument("--cuota-real", help="cuota pagada si difiere (americana o decimal)")
    s.set_defaults(f=cmd_liquidar)

    s = sub.add_parser("actualizar-datos")
    s.add_argument("--liga", action="append")
    s.set_defaults(f=cmd_actualizar_datos)

    s = sub.add_parser("exportar")
    s.add_argument("--salida", required=True)
    s.set_defaults(f=cmd_exportar)

    args = ap.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
