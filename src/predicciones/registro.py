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
from .datos.espn import SONORA

RAIZ = Path(os.environ.get("PRED_RAIZ", Path(__file__).resolve().parents[2]))
CARPETA = RAIZ / "registro"
LIQUIDADAS = ("ganada", "perdida", "devuelta")
_ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]+$")


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


def guardar_parlays_papel(dia, nuevos: list[dict]) -> list[dict]:
    """Guarda los parlays en papel de un día. Los ya calificados se conservan; los
    pendientes se reemplazan por los nuevos (volver a correr parlays no duplica)."""
    did = f"parlays-{dia:%Y%m%d}"
    ruta = _ruta_item("papel", did)
    doc = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {"id": did, "fecha": str(dia), "creado": ahora().isoformat()}
    calificados = [x for x in doc.get("parlays", []) if x.get("estado") != "pendiente"]
    claves = {x["clave"] for x in calificados}
    doc["parlays"] = calificados + [x for x in nuevos if x["clave"] not in claves]
    actualizar_parlays_papel(doc)
    return doc["parlays"]


def exportar(salida: str | Path) -> None:
    """Junta todo en pronosticos.json, apuestas.json, papel.json y bank.json (para el dashboard)."""
    salida = Path(salida)
    _escribir_ruta(salida / "pronosticos.json", pronosticos())
    _escribir_ruta(salida / "apuestas.json", apuestas())
    _escribir_ruta(salida / "papel.json", parlays_papel())
    _escribir_ruta(salida / "bank.json", bank_config())


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


def apostado_papel_hoy(excluir: str | None = None, con_parlays: bool = True) -> float:
    """Montos en papel de hoy (prueba sin dinero): sencillas, sin las del pronóstico
    `excluir`, y parlays (salvo con_parlays=False)."""
    hoy = ahora().date()
    total = sum(
        x.get("monto", 0.0) for p in pronosticos() if p.get("id") != excluir
        for x in p.get("apuestas_papel", []) if _fecha_sonora(x["evaluado"]).date() == hoy
    )
    if con_parlays:
        total += sum(x.get("monto", 0.0) for x in parlays_papel() if _fecha_sonora(x["creado"]).date() == hoy)
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
