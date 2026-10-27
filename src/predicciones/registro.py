"""Estado persistente del sistema, guardado en el repositorio (carpeta registro/).

Nada depende del historial de chats: cualquier sesión nueva lee estos archivos.
- pronosticos/<id>.json : un archivo por pronóstico (probabilidades y resultado).
- apuestas/<id>.json    : un archivo por apuesta real confirmada por Salva.
- bank.json             : bank inicial y movimientos (retiros / depósitos).
- papel/parlays-<AAAAMMDD>.json : parlays de la prueba en papel de ese día.
  (las sencillas en papel van dentro de su pronóstico, en apuestas_papel)

Un archivo por registro evita conflictos de git cuando el chat y GitHub Actions
escriben al mismo tiempo.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from . import bank as B
from . import cuotas as C
from .calificar import GANA, PIERDE, calificar
from .datos.espn import SONORA

RAIZ = Path(os.environ.get("PRED_RAIZ", Path(__file__).resolve().parents[2]))
CARPETA = RAIZ / "registro"
LIQUIDADAS = ("ganada", "perdida", "devuelta")
DEPORTE_DEFECTO = "futbol"  # los registros sin deporte son de fútbol (NBA, NFL, etc. vendrán después)
_ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]+$")
ELECCION_SALVA = "eleccion_salva"  # apuestas que eligió Salva: registro aparte (valor.ELECCION_SALVA)


def _leer(nombre: str, defecto):
    ruta = CARPETA / nombre
    if not ruta.exists():
        return defecto
    return json.loads(ruta.read_text(encoding="utf-8"))


def _escribir_ruta(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(ruta)


def _escribir(nombre: str, datos) -> None:
    _escribir_ruta(CARPETA / nombre, datos)


def _ruta_item(carpeta: str, iid: str) -> Path:
    if not _ID_VALIDO.match(iid or ""):
        raise ValueError(f"id inválido: {iid!r}")
    return CARPETA / carpeta / f"{iid}.json"


def _listar(carpeta: str) -> list[dict]:
    d = CARPETA / carpeta
    if not d.is_dir():
        return []
    items = [json.loads(f.read_text(encoding="utf-8")) for f in d.glob("*.json")]
    return sorted(items, key=lambda x: (str(x.get("creado", "")), x.get("id", "")))


def ahora() -> datetime:
    return datetime.now(SONORA)


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{ahora():%Y%m%d}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- pronósticos

def pronosticos() -> list[dict]:
    return _listar("pronosticos")


def actualizar_pronostico(p: dict) -> None:
    _escribir_ruta(_ruta_item("pronosticos", p["id"]), p)


def guardar_pronosticos(lista: list[dict]) -> None:
    for p in lista:
        actualizar_pronostico(p)


def agregar_pronostico(p: dict) -> dict:
    p.setdefault("id", nuevo_id("P"))
    p.setdefault("creado", ahora().isoformat())
    actualizar_pronostico(p)
    return p


def buscar_pronostico(pid: str) -> dict | None:
    try:
        ruta = _ruta_item("pronosticos", pid)
    except ValueError:
        return None
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


# ---------------------------------------------------------------- apuestas

def apuestas() -> list[dict]:
    return _listar("apuestas")


def actualizar_apuesta(a: dict) -> None:
    _escribir_ruta(_ruta_item("apuestas", a["id"]), a)


def guardar_apuestas(lista: list[dict]) -> None:
    for a in lista:
        actualizar_apuesta(a)


def buscar_apuesta(aid: str) -> dict | None:
    try:
        ruta = _ruta_item("apuestas", aid)
    except ValueError:
        return None
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def agregar_apuesta(a: dict) -> dict:
    a.setdefault("id", nuevo_id("A"))
    a.setdefault("creado", ahora().isoformat())
    a.setdefault("estado", "pendiente")
    actualizar_apuesta(a)
    return a


# ---------------------------------------------------------------- parlays en papel

def archivos_parlays_papel() -> list[dict]:
    return _listar("papel")


def parlays_papel() -> list[dict]:
    return [x for doc in archivos_parlays_papel() for x in doc.get("parlays", [])]


def actualizar_parlays_papel(doc: dict) -> None:
    _escribir_ruta(_ruta_item("papel", doc["id"]), doc)


def _se_reemplaza(x: dict) -> bool:
    """Parlay en papel pendiente que `parlays` vuelve a armar. Los de Salva nunca se reemplazan."""
    return x.get("estado") == "pendiente" and x.get("categoria") != ELECCION_SALVA


def guardar_parlays_papel(dia, nuevos: list[dict]) -> list[dict]:
    """Guarda los parlays en papel de un día. Los ya calificados se conservan; los
    pendientes se reemplazan por los nuevos (volver a correr parlays no duplica)."""
    did = f"parlays-{dia:%Y%m%d}"
    ruta = _ruta_item("papel", did)
    doc = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {"id": did, "fecha": str(dia), "creado": ahora().isoformat()}
    calificados = [x for x in doc.get("parlays", []) if not _se_reemplaza(x)]
    claves = {x["clave"] for x in calificados}
    doc["parlays"] = calificados + [x for x in nuevos if x["clave"] not in claves]
    actualizar_parlays_papel(doc)
    return doc["parlays"]


def apuestas_papel_todas() -> list[dict]:
    """Sencillas y parlays en papel en una sola lista, con su categoría y fecha.
    Lo guardado antes de las categorías cuenta como "valor"."""
    out = []
    for p in pronosticos():
        for x in p.get("apuestas_papel", []):
            out.append({**x, "tipo": "sencilla", "partido": p.get("partido"), "pronostico": p.get("id"),
                        "liga": p.get("liga", ""), "deporte": p.get("deporte") or DEPORTE_DEFECTO,
                        "categoria": x.get("categoria") or "valor", "fecha": x.get("evaluado", "")})
    for x in parlays_papel():
        out.append({**x, "categoria": x.get("categoria") or "valor", "fecha": x.get("creado", "")})
    return sorted(out, key=lambda x: str(x["fecha"]))


def _peor_racha(estados: list[str]) -> int:
    """Más pérdidas seguidas (las devueltas no cortan ni suman)."""
    peor = actual = 0
    for e in estados:
        if e == "perdida":
            actual += 1
            peor = max(peor, actual)
        elif e == "ganada":
            actual = 0
    return peor


def resumen_papel() -> dict:
    """Resultados de la prueba en papel por categoría (para comparar el 26-nov-2026).
    La elección de Salva va en su propia fila; el total incluye todo."""
    todas = apuestas_papel_todas()
    out = {}
    for cat in ("valor", "sencilla_fuerte", "combinada", ELECCION_SALVA, "total"):
        grupo = [x for x in todas if cat == "total" or x["categoria"] == cat]
        liq = [x for x in grupo if x.get("estado") in LIQUIDADAS]
        ganadas = sum(x["estado"] == "ganada" for x in liq)
        decididas = sum(x["estado"] != "devuelta" for x in liq)
        apostado = sum(float(x.get("monto", 0)) for x in liq)
        gan = sum(float(x.get("ganancia", 0)) for x in liq)
        out[cat] = {
            "apuestas": len(grupo), "liquidadas": len(liq), "ganadas": ganadas,
            "acierto": round(ganadas / decididas, 4) if decididas else None,
            "apostado": round(apostado, 2), "ganancia": round(gan, 2),
            "roi": round(gan / apostado, 4) if apostado else None,
            "peor_racha": _peor_racha([x["estado"] for x in liq]),
            "recortadas": sum(bool(x.get("recortada")) for x in grupo),  # monto bajado por el límite del día
        }
    return out


def categorias_hoy(excluir: str | None = None, reemplazar_parlays_de=None) -> dict[str, int]:
    """Cuántas apuestas de cada categoría se pusieron hoy (día de Sonora), en papel o
    reales. Mismas exclusiones que apostado_papel_hoy (lo que se va a reemplazar)."""
    hoy = ahora().date()
    cuenta: dict[str, int] = {}

    def sumar(x: dict, fecha: str | None) -> None:
        if fecha and _fecha_sonora(fecha).date() == hoy:
            c = x.get("categoria") or "valor"
            cuenta[c] = cuenta.get(c, 0) + 1

    for p in pronosticos():
        if p.get("id") != excluir:
            for x in p.get("apuestas_papel", []):
                sumar(x, x.get("evaluado"))
    omitir = f"parlays-{reemplazar_parlays_de:%Y%m%d}" if reemplazar_parlays_de else None
    for doc in archivos_parlays_papel():
        for x in doc.get("parlays", []):
            if not (doc.get("id") == omitir and _se_reemplaza(x)):
                sumar(x, x.get("creado"))
    for a in apuestas():
        if a.get("categoria"):
            sumar(a, a.get("creado"))
    return cuenta


# nombres para Salva (las claves internas no cambian)
NOMBRES_CATEGORIA = {"valor": "Cuota mejor que la justa", "sencilla_fuerte": "Sencilla fuerte (momio negativo)",
                     "combinada": "Combinada corta (+200 a +350)", ELECCION_SALVA: "Elección de Salva"}


def _equipos(partido: str) -> tuple[str, str]:
    local, _, visitante = (partido or "").partition(" vs ")
    return local or "Local", visitante or "Visitante"


def _linea(texto: str, cosa: str) -> str:
    """'mas_2.5', 'goles' -> 'Más de 2.5 goles'"""
    lado, _, n = texto.partition("_")
    return f"{'Más' if lado == 'mas' else 'Menos'} de {n} {cosa}"


def texto_seleccion(partido: str, mercado: str, seleccion: str) -> str:
    """Selección en español legible: 'Sevilla vs Osasuna' + goles mas_2.5 -> 'Más de 2.5 goles'."""
    local, visitante = _equipos(partido)
    resultado = {"1": f"Gana {local}", "X": "Empate", "2": f"Gana {visitante}"}
    try:
        if mercado == "ganador":
            return resultado[seleccion]
        if mercado == "doble_oportunidad":
            return {"1X": f"{local} o empate", "X2": f"{visitante} o empate", "12": f"{local} o {visitante}"}[seleccion]
        if mercado == "goles":
            return _linea(seleccion, "goles")
        if mercado == "ambos_anotan":
            return "Ambos anotan: sí" if seleccion == "si" else "Ambos anotan: no"
        if mercado == "goles_equipo":
            lado, resto = seleccion.split("_", 1)
            return f"{local if lado == 'local' else visitante}: {_linea(resto, 'goles').lower()}"
        if mercado == "primer_tiempo":
            sel = seleccion.removeprefix("pt_")
            base = resultado[sel] if sel in resultado else _linea(sel, "goles")
            return f"{base} (1er tiempo)"
        if mercado == "handicap":
            lado, linea = seleccion.split("_", 1)
            return f"{local if lado == 'local' else visitante} {linea.replace('-', '−')} (hándicap)"
        if mercado == "tiros_jugador":
            nombre, linea = seleccion.rsplit(":", 1)
            return f"{nombre}: {_linea(linea, 'tiros a puerta').lower()}"
        if mercado in ("corners", "tarjetas"):
            return _linea(seleccion, "córners" if mercado == "corners" else "tarjetas")
    except (KeyError, ValueError):
        pass
    return f"{mercado} {seleccion}"


def _estado_legible(estado: str | None) -> str:
    return {"sin_datos": "sin datos", None: "pendiente"}.get(estado, estado)


def parlays_para_dashboard() -> list[dict]:
    """Todos los parlays (reales y en papel) listos para mostrar: patas en español, cuotas
    americanas (nunca decimales), monto, estado y ganancia. Más recientes primero."""
    out = []
    por_id = {p["id"]: p for p in pronosticos()}
    reales = [(a, "real") for a in apuestas() if len(a.get("patas", [])) > 1]
    for par, origen in reales + [(x, "papel") for x in parlays_papel()]:
        cat = par.get("categoria")
        out.append({
            "origen": origen,
            "categoria": NOMBRES_CATEGORIA.get(cat, cat or (NOMBRES_CATEGORIA["valor"] if origen == "papel" else "Sin categoría")),
            **_campos_filtro(par, por_id),
            "patas": [{"partido": x.get("partido", ""),
                       "texto": f"{x.get('partido', '')} · {texto_seleccion(x.get('partido', ''), x['mercado'], x['seleccion'])}",
                       "cuota": C.formato_americano(float(x["cuota"])),
                       "estado": _estado_legible(x.get("estado"))} for x in par.get("patas", [])],
            "cuota": par.get("cuota_americana") or C.formato_americano(float(par["cuota"])),
            "monto": par.get("monto", 0),
            "estado": _estado_legible(par.get("estado")),
            "ganancia": par.get("ganancia"),
            "recortada": bool(par.get("recortada")),
            "_orden": str(par.get("creado", "")),
        })
    out.sort(key=lambda x: x.pop("_orden"), reverse=True)
    return out


def _fecha_texto(iso: str | None) -> str:
    """Fecha de Sonora AAAA-MM-DD de un isoformat ('' si no hay)."""
    return str(_fecha_sonora(iso).date()) if iso else ""


def _campos_filtro(ap: dict, por_id: dict) -> dict:
    """deporte, liga, ligas y fecha (Sonora, día en que se apostó) de una apuesta o parlay."""
    patas = ap.get("patas", [])
    pros = [por_id.get(x.get("pronostico"), {}) for x in patas]
    ligas = sorted({x.get("liga") or pr.get("liga") or "" for x, pr in zip(patas, pros)} - {""})
    deportes = sorted({x.get("deporte") or pr.get("deporte") or DEPORTE_DEFECTO for x, pr in zip(patas, pros)})
    return {"deporte": ap.get("deporte") or (deportes[0] if deportes else DEPORTE_DEFECTO),
            "liga": ligas[0] if len(ligas) == 1 else ("varias" if ligas else ""),
            "ligas": ligas, "fecha": _fecha_texto(ap.get("creado") or ap.get("evaluado"))}


def _momio(x: dict) -> str:
    if x.get("cuota_americana"):
        return x["cuota_americana"]
    return C.formato_americano(x["cuota"]) if x.get("cuota") else "—"


def _apuestas_por_pronostico() -> dict[str, list[dict]]:
    """Para cada pronóstico, sus apuestas (reales y en papel; sencillas y parlays) con el momio
    que paga la apuesta completa: en un parlay, el momio total."""
    out: dict[str, list[dict]] = {}

    def agregar(pid, origen, tipo, x):
        cat = x.get("categoria")
        out.setdefault(pid, []).append({
            "origen": origen, "tipo": tipo, "categoria": NOMBRES_CATEGORIA.get(cat, cat or ""),
            "momio": _momio(x), "monto": x.get("monto") or x.get("monto_papel") or 0,
            "estado": _estado_legible(x.get("estado")), "ganancia": x.get("ganancia")})

    for p in pronosticos():
        for x in p.get("apuestas_papel", []):
            agregar(p["id"], "papel", "sencilla", x)
    for par, origen in [(a, "real") for a in apuestas()] + [(x, "papel") for x in parlays_papel()]:
        patas = par.get("patas", [])
        tipo = "sencilla" if len(patas) == 1 else ("combinada" if par.get("categoria") == "combinada" else "parlay")
        for pid in dict.fromkeys(q.get("pronostico") for q in patas if q.get("pronostico")):
            agregar(pid, origen, tipo, par)
    return out


def _pronostico_exportado(p: dict, apuestas_de: dict[str, list[dict]] | None = None) -> dict:
    evaluacion = [{**e, "texto": texto_seleccion(p.get("partido", ""), e["mercado"], e["seleccion"])}
                  for e in p.get("evaluacion", [])]
    return {**p, "evaluacion": evaluacion, "apuestas": (apuestas_de or {}).get(p.get("id"), []),
            "deporte": p.get("deporte") or DEPORTE_DEFECTO, "liga": p.get("liga", ""),
            "fecha": str(p.get("inicio_sonora", ""))[:10] or _fecha_texto(p.get("creado"))}


def papel_para_dashboard() -> list[dict]:
    """Cada apuesta en papel (sencilla o parlay) con deporte, liga, fecha y categoría,
    para que el dashboard calcule las estadísticas según el filtro."""
    por_id = {p["id"]: p for p in pronosticos()}
    out = []
    for x in apuestas_papel_todas():
        if x["tipo"] == "sencilla":
            campos = {"deporte": x["deporte"], "liga": x["liga"], "ligas": [x["liga"]] if x["liga"] else [],
                      "fecha": _fecha_texto(x.get("evaluado"))}
            texto = f"{x.get('partido', '')} · {texto_seleccion(x.get('partido', ''), x['mercado'], x['seleccion'])}"
        else:
            campos = _campos_filtro(x, por_id)
            texto = f"Parlay de {len(x.get('patas', []))} patas"
        out.append({"tipo": x["tipo"], "categoria": x["categoria"], **campos, "texto": texto,
                     "mercado": x.get("mercado", "parlay"), "cuota": C.formato_americano(float(x["cuota"])),
                     "monto": x.get("monto", 0), "estado": x.get("estado", "pendiente"),
                     "ganancia": x.get("ganancia", 0) or 0, "recortada": bool(x.get("recortada")),
                     "orden": str(x.get("fecha", ""))})
    return out


def apuestas_exportadas() -> list[dict]:
    por_id = {p["id"]: p for p in pronosticos()}
    return [{**a, **_campos_filtro(a, por_id),
             "patas": [{**x, "texto": f"{x.get('partido', '')} · "
                                      f"{texto_seleccion(x.get('partido', ''), x['mercado'], x['seleccion'])}"}
                       for x in a.get("patas", [])]} for a in apuestas()]


MERCADOS_CALIBRACION = ("ganador", "doble_oportunidad", "goles", "ambos_anotan", "goles_equipo", "handicap",
                        "primer_tiempo", "corners", "tarjetas", "tiros_jugador")


def _resumen_calibracion(filas: list[tuple[float, int]]) -> dict:
    n = len(filas)
    if not n:
        return {"n": 0, "prob_media": None, "acierto": None, "brier": None}
    return {"n": n, "prob_media": round(sum(p for p, _ in filas) / n, 4),
            "acierto": round(sum(g for _, g in filas) / n, 4),
            "brier": round(sum((p - g) ** 2 for p, g in filas) / n, 4)}


def calibracion() -> dict:
    """Probabilidad pronosticada contra lo que pasó, en todas las selecciones de los pronósticos
    guardados que ya tienen resultado. Cada selección se califica con calificar() (las mismas
    reglas que las apuestas: pago anticipado, tiros de jugador solo con datos); devueltas y sin
    datos no cuentan. Con devolución posible (hándicap 0, líneas enteras) la probabilidad se toma
    sin las devueltas: prob / (1 − devolución)."""
    por_mercado: dict[str, list] = {m: [] for m in MERCADOS_CALIBRACION}
    for p in pronosticos():
        r = p.get("resultado")
        if not r:
            continue
        for s in p.get("selecciones", []):
            if s.get("prob") is None or s.get("mercado") not in por_mercado:
                continue
            est = calificar(s["mercado"], s["seleccion"], r, bool(p.get("pago_anticipado", False)),
                            jugador_id=s.get("jugador_id"))
            if est not in (GANA, PIERDE):
                continue
            no_devuelta = 1.0 - float(s.get("devolucion") or 0.0)
            prob = min(1.0, float(s["prob"]) / no_devuelta) if no_devuelta > 0 else float(s["prob"])
            por_mercado[s["mercado"]].append((prob, int(est == GANA)))
    todas = [f for filas in por_mercado.values() for f in filas]
    rangos = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        grupo = [f for f in todas if lo <= f[0] < hi or (i == 9 and f[0] == 1.0)]
        rangos.append({"desde": lo, "hasta": hi, **_resumen_calibracion(grupo)})
    return {"mercados": {m: _resumen_calibracion(f) for m, f in por_mercado.items()},
            "total": _resumen_calibracion(todas), "rangos": rangos}


def exportar(salida: str | Path) -> None:
    """Junta todo para el dashboard: pronosticos.json, apuestas.json, apuestas_papel.json,
    papel.json, resumen_papel.json, parlays.json, bank.json y calibracion.json. Cada fila de pronóstico,
    apuesta y parlay lleva deporte, liga y fecha (Sonora) para los filtros."""
    salida = Path(salida)
    apuestas_de = _apuestas_por_pronostico()
    _escribir_ruta(salida / "pronosticos.json", [_pronostico_exportado(p, apuestas_de) for p in pronosticos()])
    _escribir_ruta(salida / "apuestas.json", apuestas_exportadas())
    _escribir_ruta(salida / "apuestas_papel.json", papel_para_dashboard())
    _escribir_ruta(salida / "papel.json", parlays_papel())
    _escribir_ruta(salida / "resumen_papel.json", resumen_papel())
    _escribir_ruta(salida / "parlays.json", parlays_para_dashboard())
    _escribir_ruta(salida / "bank.json", bank_config())
    _escribir_ruta(salida / "calibracion.json", calibracion())


# ---------------------------------------------------------------- bank

def bank_config() -> dict:
    return _leer("bank.json", {"moneda": "MXN", "bank_inicial": 10000, "inicio": "2026-10-26", "movimientos": []})


def agregar_movimiento(tipo: str, monto: float, nota: str = "") -> dict:
    if tipo not in ("retiro", "deposito"):
        raise ValueError("tipo debe ser 'retiro' o 'deposito'")
    cfg = bank_config()
    mov = {"fecha": ahora().isoformat(), "tipo": tipo, "monto": float(monto), "nota": nota}
    cfg["movimientos"].append(mov)
    _escribir("bank.json", cfg)
    return mov


def _fecha_sonora(iso: str) -> datetime:
    d = datetime.fromisoformat(iso)
    return d.astimezone(SONORA) if d.tzinfo else d.replace(tzinfo=SONORA)


def _liquidacion(a: dict) -> datetime:
    return _fecha_sonora(a.get("liquidada") or a["creado"])


def bank_actual(hasta: datetime | None = None) -> float:
    cfg = bank_config()
    total = float(cfg["bank_inicial"])
    for m in cfg["movimientos"]:
        if hasta and _fecha_sonora(m["fecha"]) >= hasta:
            continue
        total += m["monto"] if m["tipo"] == "deposito" else -m["monto"]
    for a in apuestas():
        if a.get("estado") in LIQUIDADAS:
            if hasta and _liquidacion(a) >= hasta:
                continue
            total += a.get("ganancia", 0.0)
    return round(total, 2)


def ganancia_cobrada_hoy() -> float:
    hoy = ahora().date()
    return round(sum(
        a.get("ganancia", 0.0) for a in apuestas()
        if a.get("estado") in LIQUIDADAS and _liquidacion(a).date() == hoy
    ), 2)


def apostado_hoy_pendiente() -> float:
    hoy = ahora().date()
    return sum(a["monto"] for a in apuestas() if _fecha_sonora(a["creado"]).date() == hoy)


def apostado_papel_hoy(excluir: str | None = None, reemplazar_parlays_de=None) -> float:
    """Montos en papel puestos hoy (prueba sin dinero): sencillas y parlays, sin importar
    el día del partido. No cuenta las sencillas del pronóstico `excluir` ni los parlays
    pendientes del día `reemplazar_parlays_de` (se van a reemplazar)."""
    hoy = ahora().date()
    total = sum(
        x.get("monto", 0.0) for p in pronosticos() if p.get("id") != excluir
        for x in p.get("apuestas_papel", []) if _fecha_sonora(x["evaluado"]).date() == hoy
    )
    omitir = f"parlays-{reemplazar_parlays_de:%Y%m%d}" if reemplazar_parlays_de else None
    for doc in archivos_parlays_papel():
        for x in doc.get("parlays", []):
            if doc.get("id") == omitir and _se_reemplaza(x):
                continue
            if _fecha_sonora(x["creado"]).date() == hoy:
                total += x.get("monto", 0.0)
    return total


def _inicio_mes() -> datetime:
    return ahora().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def bank_inicio_mes() -> float:
    return bank_actual(hasta=_inicio_mes())


def movimientos_mes() -> float:
    """Depósitos menos retiros de este mes."""
    inicio = _inicio_mes()
    return round(sum(
        (m["monto"] if m["tipo"] == "deposito" else -m["monto"])
        for m in bank_config()["movimientos"] if _fecha_sonora(m["fecha"]) >= inicio
    ), 2)


def resultado_apuestas_mes() -> float:
    """Ganancia neta de las apuestas liquidadas este mes (sin retiros ni depósitos)."""
    inicio = _inicio_mes()
    return round(sum(
        a.get("ganancia", 0.0) for a in apuestas()
        if a.get("estado") in LIQUIDADAS and _liquidacion(a) >= inicio
    ), 2)


def base_mes() -> float:
    """Dinero puesto en juego este mes: bank al inicio + depósitos − retiros."""
    return round(bank_inicio_mes() + movimientos_mes(), 2)


def en_pausa() -> bool:
    return B.en_pausa(resultado_apuestas_mes(), base_mes())
