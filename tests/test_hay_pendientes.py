import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hay_pendientes", Path(__file__).resolve().parents[1] / ".github/scripts/hay_pendientes.py")
hp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hp)

AHORA = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)


def _guardar(carpeta, nombre, **datos):
    (carpeta / f"{nombre}.json").write_text(json.dumps(datos), encoding="utf-8")


def test_sin_pronosticos(tmp_path):
    assert not hp.hay_pendientes(tmp_path, AHORA)


def test_calificado_o_reciente_no_cuenta(tmp_path):
    _guardar(tmp_path, "a", estado="calificado", inicio_utc="2026-10-26T18:00:00+00:00")
    _guardar(tmp_path, "b", estado="pendiente", espn_id="1", inicio_utc="2026-10-26T21:00:00+00:00")  # 2 h: aún no
    assert not hp.hay_pendientes(tmp_path, AHORA)


def test_pendiente_terminado_cuenta(tmp_path):
    _guardar(tmp_path, "a", estado="pendiente", espn_id="1", inicio_utc="2026-10-26T20:00:00+00:00")  # 3 h
    assert hp.hay_pendientes(tmp_path, AHORA)


def test_sin_espn_id_o_inicio_no_cuenta(tmp_path):
    _guardar(tmp_path, "a", estado="pendiente", inicio_utc="2026-10-26T18:00:00+00:00")
    _guardar(tmp_path, "b", estado="pendiente", espn_id="1")
    assert not hp.hay_pendientes(tmp_path, AHORA)


def test_hora_sin_zona_y_archivo_roto(tmp_path):
    _guardar(tmp_path, "a", estado="pendiente", espn_id="1", inicio_utc="2026-10-26T18:00:00")
    assert hp.hay_pendientes(tmp_path, AHORA)
    (tmp_path / "a.json").write_text("{roto", encoding="utf-8")
    assert hp.hay_pendientes(tmp_path, AHORA)
