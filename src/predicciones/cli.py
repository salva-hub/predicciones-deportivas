"""Línea de comandos del sistema. Uso: python -m predicciones.cli <comando> ...

Comandos principales (ver CLAUDE.md para el flujo completo):
  partidos          Lista los partidos de una liga en una fecha (hora de Sonora)
  pronostico        Calcula y guarda el pronóstico de un partido
  evaluar           Con las cuotas de Winpot, decide qué apostar y cuánto
  parlays           Arma parlays con las selecciones con valor del día
  apostar           Registra una apuesta real que Salva confirmó
  movimiento        Registra un retiro o depósito
  bank              Estado del bank y límite del día
  calificar         Califica pronósticos y apuestas de partidos terminados
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
from .calificar import calificar, estado_parlay, ganancia, SIN_DATOS
from .datos import clima as clima_mod
from .datos import cuotas_api, espn, historiales
from .datos.http import limpiar
from .equipos import coincide, normalizar
from .modelos.conteos import factor_arbitro
from .pronostico import ajustar_modelos, pronosticar

TECHADOS = {normalizar(x) for x in (
    "Estadio Santiago Bernabéu", "Veltins-Arena", "Merkur Spiel-Arena", "Deutsche Bank Park",
    "Stade Pierre-Mauroy", "Johan Cruijff ArenA", "BC Place", "Mercedes-Benz Stadium",
    "Ligga Arena", "Estadio La Cartuja",
)}
HORAS_ESPERA_DATOS = 48  # tras el inicio, se sigue buscando lo que falte (córners, tarjetas)
MENSAJE_PAUSA = "PAUSA: las apuestas de este mes perdieron 50% de la base del mes. No se recomiendan apuestas; hay que revisar el modelo."
NOTA_SIN_PINNACLE = "Sin cuotas de referencia de Pinnacle: probabilidades solo del modelo"
LIGAS_DATOS_LIMITADOS = {"liga_mx", "mls", "brasil", "argentina", "libertadores", "concacaf"}


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
    # ESPN agrupa por fecha UTC; buscamos del día anterior al siguiente
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


def cmd_partidos(a) -> None:
    dia = _fecha(a.fecha)
    _imprimir([{k: p[k] for k in ("espn_id", "inicio_sonora", "local", "visitante", "estadio", "ciudad")}
               for p in espn.calendario(a.liga, dia)])


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
    por_clima(info_clima, techado, aj)

    f_arb = 1.0
    if a.arbitro_prom and modelos.promedio_tarjetas:
        f_arb = factor_arbitro(a.arbitro_prom, a.arbitro_partidos or 0, modelos.promedio_tarjetas)

    pin = _pinnacle_manual(a.pinnacle) or (None if a.sin_red else _pinnacle_api(a.liga, a.local, a.visitante))
    confianza = a.confianza
    if a.liga in LIGAS_DATOS_LIMITADOS:
        confianza = V.bajar(confianza)
    if not pin:
        confianza = V.bajar(confianza)
    p = pronosticar(modelos, a.local, a.visitante, aj, f_arb, pin, confianza, neutral=a.neutral)
    if not pin:
        p.notas.append(NOTA_SIN_PINNACLE)
    if not partido:
        p.notas.append("AVISO: no se encontró el partido en ESPN; no se podrá calificar automáticamente")

    registro = {
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
        "clima": info_clima,
        "arbitro": a.arbitro, "factor_arbitro": round(f_arb, 3),
        "pinnacle": pin,
        "goles_esperados": p.goles_esperados,
        "marcadores_probables": p.marcadores,
        "notas": p.notas,
        "confianza": p.confianza_base,
        "selecciones": p.tabla(),
        "estado": "pendiente",
    }
    if not a.no_guardar:
        registro = R.agregar_pronostico(registro)
    _imprimir(registro)


def _parsear_cuota(texto: str) -> tuple[str, str, float]:
    """'goles:mas_2.5=-130' -> ('goles', 'mas_2.5', 1.769)"""
    izq, cuota = texto.split("=")
    mercado, seleccion = izq.split(":", 1)
    v = float(cuota)
    return mercado.strip(), seleccion.strip(), C.americana_a_decimal(v) if abs(v) >= 100 else v


def cmd_evaluar(a) -> None:
    p = R.buscar_pronostico(a.id)
    if not p:
        sys.exit(f"No existe el pronóstico {a.id}")
    bank_hoy = R.bank_actual()
    pausa = R.en_pausa()
    if pausa:
        print(MENSAJE_PAUSA, file=sys.stderr)
    disponible = max(0.0, B.limite_del_dia(bank_hoy, R.ganancia_cobrada_hoy()) - R.apostado_hoy_pendiente())
    resultado = []
    for texto in a.winpot:
        mercado, seleccion, dec = _parsear_cuota(texto)
        fila = next((s for s in p["selecciones"] if s["mercado"] == mercado and s["seleccion"] == seleccion), None)
        if not fila:
            resultado.append({"mercado": mercado, "seleccion": seleccion, "error": "no está en el pronóstico (o no se ofrece por reglas)"})
            continue
        dev = fila.get("devolucion", 0.0)
        s = V.Seleccion(p["partido"], mercado, seleccion, fila["prob"], dev, fila["confianza"], dec)
        ve = s.ve()
        pct = B.porcentaje_sencilla(ve, s.confianza, s.ve_minimo)
        # cada sencilla, como mucho lo que queda del límite del día
        monto = min(B.monto(bank_hoy, pct), B.monto(disponible, 1.0))
        fila_res = {
            "mercado": mercado, "seleccion": seleccion,
            "cuota_winpot": C.formato_americano(dec), "cuota_minima": fila["cuota_minima"],
            "prob": fila["prob"], "ve": round(ve, 4), "confianza": s.confianza,
            "apostar": pct > 0 and monto > 0, "monto_sencilla": monto,
            "sirve_para_parlay": ve >= V.VE_MIN_PARLAY_PATA,
        }
        if dev:
            fila_res["devolucion"] = dev
        if pct > 0 and monto <= 0:
            fila_res["motivo"] = "límite del día alcanzado"
        if pausa:
            fila_res.update(apostar=False, monto_sencilla=0, sirve_para_parlay=False, motivo="pausa")
        resultado.append(fila_res)
    p["cuotas_winpot"] = {f"{r['mercado']}:{r['seleccion']}": r.get("cuota_winpot") for r in resultado if "error" not in r}
    p["evaluacion"] = resultado
    R.actualizar_pronostico(p)
    _imprimir({"bank_actual": bank_hoy, "disponible_hoy": disponible, "en_pausa": pausa, "evaluacion": resultado})


def cmd_parlays(a) -> None:
    if R.en_pausa():
        _imprimir(MENSAJE_PAUSA)
        return
    dia = _fecha(a.fecha)
    candidatas = []
    for p in R.pronosticos():
        if not str(p.get("inicio_sonora", "")).startswith(str(dia)):
            continue
        for r in p.get("evaluacion", []):
            if r.get("sirve_para_parlay"):
                fila = next((s for s in p.get("selecciones", [])
                             if s["mercado"] == r["mercado"] and s["seleccion"] == r["seleccion"]), {})
                candidatas.append(V.Seleccion(p["partido"], r["mercado"], r["seleccion"], r["prob"],
                                              fila.get("devolucion", r.get("devolucion", 0.0)), r["confianza"],
                                              C.americana_a_decimal(float(r["cuota_winpot"]))))
    bank_hoy = R.bank_actual()
    disponible = max(0.0, B.limite_del_dia(bank_hoy, R.ganancia_cobrada_hoy()) - R.apostado_hoy_pendiente())
    parlays = V.armar_parlays(candidatas, max_patas=a.max_patas)
    montos = B.ajustar_al_limite([B.monto(bank_hoy, B.porcentaje_parlay(x.confianza)) for x in parlays], disponible)
    out = []
    for par, monto in zip(parlays, montos):
        d = par.resumen()
        d["monto"] = min(monto, B.monto(disponible, 1.0))
        out.append(d)
    _imprimir(out or "Sin parlays con valor para ese día")


def cmd_apostar(a) -> None:
    patas = []
    for texto in a.pata:
        pid, resto = texto.split(":", 1)
        mercado, seleccion, dec = _parsear_cuota(resto)
        p = R.buscar_pronostico(pid)
        if not p:
            sys.exit(f"No existe el pronóstico {pid}")
        patas.append({"pronostico": pid, "partido": p["partido"], "liga": p["liga"], "espn_id": p.get("espn_id"),
                      "mercado": mercado, "seleccion": seleccion, "cuota": dec, "estado": "pendiente"})
    cuota = C.cuota_parlay(x["cuota"] for x in patas)
    ap = R.agregar_apuesta({
        "tipo": "sencilla" if len(patas) == 1 else "parlay",
        "patas": patas, "cuota": round(cuota, 4), "cuota_americana": C.formato_americano(cuota),
        "monto": float(a.monto), "casa": a.casa,
    })
    _imprimir(ap)


def cmd_movimiento(a) -> None:
    _imprimir(R.agregar_movimiento(a.tipo, a.monto, a.nota or ""))


def cmd_bank(a) -> None:
    actual = R.bank_actual()
    inicio_mes = R.bank_inicio_mes()
    gan = R.ganancia_cobrada_hoy()
    limite = B.limite_del_dia(actual, gan)
    pendientes = [x for x in R.apuestas() if x.get("estado") == "pendiente"]
    _imprimir({
        "bank_actual": actual, "bank_inicio_mes": inicio_mes,
        "ganancia_cobrada_hoy": gan, "limite_del_dia": limite,
        "apostado_hoy": R.apostado_hoy_pendiente(),
        "disponible_hoy": max(0, limite - R.apostado_hoy_pendiente()),
        "resultado_apuestas_mes": R.resultado_apuestas_mes(), "base_mes": R.base_mes(),
        "en_pausa": R.en_pausa(),
        "apuestas_pendientes": len(pendientes),
        "monto_sencilla_max": B.monto(actual, B.REGLAS.sencilla_max),
        "monto_parlay_alta": B.monto(actual, B.REGLAS.parlay_alta),
        "monto_parlay_media_baja": B.monto(actual, B.REGLAS.parlay_media_baja),
    })


def _horas_desde(inicio_utc: str, ahora: datetime) -> float:
    return (ahora - datetime.fromisoformat(inicio_utc)).total_seconds() / 3600


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
        if r.get("prorroga"):
            p["resultado"]["prorroga"] = True
        for s in p["selecciones"]:
            s["estado"] = calificar(s["mercado"], s["seleccion"], r)
        # si falta algún dato, se reintenta en las siguientes corridas hasta 48 h
        if not any(s["estado"] == SIN_DATOS for s in p["selecciones"]) or horas >= HORAS_ESPERA_DATOS:
            p["estado"] = "calificado"
        R.actualizar_pronostico(p)
        cambios += 1

    por_id = {p["id"]: p for p in lista}
    for ap in R.apuestas():
        if ap.get("estado") != "pendiente":
            continue
        antes = [x.get("estado") for x in ap["patas"]]
        for pata in ap["patas"]:
            if pata.get("estado") not in ("pendiente", SIN_DATOS):
                continue
            p = por_id.get(pata["pronostico"])
            if not p or not p.get("resultado"):
                continue
            est = calificar(pata["mercado"], pata["seleccion"], p["resultado"])
            # sin datos todavía: queda pendiente para volver a calificarla
            pata["estado"] = "pendiente" if est == SIN_DATOS and p.get("estado") != "calificado" else est
        estados = [x["estado"] for x in ap["patas"]]
        if "pendiente" in estados or estado_parlay(estados) == SIN_DATOS:
            # pasada la ventana de 48 h, una pata sin datos ya no se va a calificar sola
            sin_datos = [x for x in ap["patas"] if x["estado"] == SIN_DATOS]
            for x in sin_datos:
                partido = (por_id.get(x["pronostico"]) or {}).get("partido", "?")
                print(f"Revisar a mano: apuesta {ap['id']} ({partido}, {x['mercado']})", file=sys.stderr)
            marcar = bool(sin_datos) and not ap.get("requiere_revision")
            if marcar:
                ap["requiere_revision"] = True
            if estados != antes or marcar:
                R.actualizar_apuesta(ap)
            continue
        est = estado_parlay(estados)
        cuota_real = 1.0
        for x in ap["patas"]:
            if x["estado"] == "ganada":
                cuota_real *= x["cuota"]
        ap["estado"] = est
        ap["ganancia"] = ganancia(est, ap["monto"], cuota_real)
        ap["liquidada"] = R.ahora().isoformat()
        R.actualizar_apuesta(ap)
        cambios += 1
    print(f"Calificados/liquidados: {cambios}")


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
    s.add_argument("--arbitro-prom", type=float, help="tarjetas por partido del árbitro en la competición")
    s.add_argument("--arbitro-partidos", type=int)
    s.add_argument("--pinnacle", help="cuotas manuales: 1=-150,X=+280,2=+400,mas_2.5=-110,menos_2.5=-110")
    s.add_argument("--confianza", required=True, choices=V.ORDEN_CONFIANZA)
    s.add_argument("--techado", action="store_true")
    s.add_argument("--neutral", action="store_true", help="cancha neutral: sin ventaja de local")
    s.add_argument("--sin-red", action="store_true")
    s.add_argument("--no-guardar", action="store_true")
    s.set_defaults(f=cmd_pronostico)

    s = sub.add_parser("evaluar")
    s.add_argument("--id", required=True)
    s.add_argument("--winpot", action="append", required=True, help="mercado:seleccion=cuota  (ej. goles:mas_2.5=-130)")
    s.set_defaults(f=cmd_evaluar)

    s = sub.add_parser("parlays")
    s.add_argument("--fecha")
    s.add_argument("--max-patas", type=int, default=4)
    s.set_defaults(f=cmd_parlays)

    s = sub.add_parser("apostar")
    s.add_argument("--pata", action="append", required=True, help="ID_PRONOSTICO:mercado:seleccion=cuota")
    s.add_argument("--monto", type=float, required=True)
    s.add_argument("--casa", default="Winpot")
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
