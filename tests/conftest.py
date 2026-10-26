import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="session")
def liga_sintetica():
    """Liga simulada de 12 equipos, 3 temporadas, con fuerzas conocidas."""
    rng = np.random.default_rng(7)
    equipos = [f"Equipo {i}" for i in range(12)]
    ataque = np.linspace(-0.4, 0.4, 12)
    defensa = np.linspace(0.3, -0.3, 12)
    filas = []
    fecha = pd.Timestamp("2023-08-01")
    for temporada in range(3):
        for i, l in enumerate(equipos):
            for j, v in enumerate(equipos):
                if i == j:
                    continue
                mu_l = np.exp(0.15 + 0.25 + ataque[i] + defensa[j])
                mu_v = np.exp(0.15 + ataque[j] + defensa[i])
                cl = rng.poisson(5.5 * np.exp(ataque[i] / 2))
                cv = rng.poisson(4.5 * np.exp(ataque[j] / 2))
                filas.append({
                    "fecha": fecha, "local": l, "visitante": v,
                    "goles_local": rng.poisson(mu_l), "goles_visitante": rng.poisson(mu_v),
                    "corners_local": cl, "corners_visitante": cv,
                    "tarjetas_local": rng.negative_binomial(6, 6 / (6 + 2.1)),
                    "tarjetas_visitante": rng.negative_binomial(6, 6 / (6 + 2.4)),
                })
                fecha += pd.Timedelta(hours=20)
    return pd.DataFrame(filas)


@pytest.fixture
def registro_tmp(tmp_path, monkeypatch):
    """Registro vacío en una carpeta temporal (sin tocar el del repositorio)."""
    import importlib
    R = importlib.import_module("predicciones.registro")
    monkeypatch.setattr(R, "CARPETA", tmp_path / "registro")
    return R
