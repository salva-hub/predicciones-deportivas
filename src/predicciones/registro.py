"""Estado persistente del sistema, guardado en el repositorio (carpeta registro/).

Nada depende del historial de chats: cualquier sesión nueva lee estos archivos.
- pronosticos.json : cada pronóstico emitido, con sus probabilidades y resultado.
- apuestas.json    : apuestas reales confirmadas por Salva (monto, cuota, resultado).
- bank.json        : bank inicial y movimientos (retiros / depósitos).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from .datos.espn import SONORA

import os

RAIZ = Path(os.environ.get("PRED_RAIZ", Path(__file__).resolve().parents[2]))
CARPETA = RAIZ / "registro"


def _leer(nombre: str, defecto):
    ruta = CARPETA / nombre
    if not ruta.exists():
        return defecto
    return json.loads(ruta.read_text(encoding="utf-8"))


def _escribir(nombre: str, datos) -> None:
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / nombre
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(ruta)


def ahora() -> datetime:
    return datetime.now(SONORA)


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}-{ahora():%Y%m%d}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- pronósticos

def pronosticos() -> list[dict]:
    return _leer("pronosticos.json", [])


def guardar_pronosticos(lista: list[dict]) -> None:
    _escribir("pronosticos.json", lista)


def agregar_pronostico(p: dict) -> dict:
    lista = pronosticos()
    p.setdefault("id", nuevo_id("P"))
    p.setdefault("creado", ahora().isoformat())
    lista.append(p)
    guardar_pronosticos(lista)
    return p


def buscar_pronostico(pid: str) -> dict | None:
    return next((p for p in pronosticos() if p["id"] == pid), None)


def actualizar_pronostico(p: dict) -> None:
    lista = [x if x["id"] != p["id"] else p for x in pronosticos()]
    guardar_pronosticos(lista)


# ---------------------------------------------------------------- apuestas

def apuestas() -> list[dict]:
    return _leer("apuestas.json", [])


def guardar_apuestas(lista: list[dict]) -> None:
    _escribir("apuestas.json", lista)


def agregar_apuesta(a: dict) -> dict:
    lista = apuestas()
    a.setdefault("id", nuevo_id("A"))
    a.setdefault("creado", ahora().isoformat())
    a.setdefault("estado", "pendiente")
    lista.append(a)
    guardar_apuestas(lista)
    return a


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


def bank_actual(hasta: datetime | None = None) -> float:
    cfg = bank_config()
    total = float(cfg["bank_inicial"])
    for m in cfg["movimientos"]:
        if hasta and _fecha_sonora(m["fecha"]) >= hasta:
            continue
        total += m["monto"] if m["tipo"] == "deposito" else -m["monto"]
    for a in apuestas():
        if a.get("estado") in ("ganada", "perdida", "devuelta"):
            liquidada = a.get("liquidada") or a["creado"]
            if hasta and _fecha_sonora(liquidada) >= hasta:
                continue
            total += a.get("ganancia", 0.0)
    return round(total, 2)


def ganancia_cobrada_hoy() -> float:
    hoy = ahora().date()
    return round(sum(
        a.get("ganancia", 0.0) for a in apuestas()
        if a.get("estado") in ("ganada", "perdida", "devuelta")
        and _fecha_sonora(a.get("liquidada") or a["creado"]).date() == hoy
    ), 2)


def apostado_hoy_pendiente() -> float:
    hoy = ahora().date()
    return sum(a["monto"] for a in apuestas() if _fecha_sonora(a["creado"]).date() == hoy)


def bank_inicio_mes() -> float:
    a = ahora()
    inicio = a.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return bank_actual(hasta=inicio)
