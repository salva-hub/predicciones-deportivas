"""Fuentes de datos (ESPN, football-data, clima, cuotas)."""
from __future__ import annotations

# Tarjetas como las cuenta Winpot (reglas Altenar v1.29, "Mercados de tarjetas"):
# amarilla = 1, roja = 2 y amarilla + segunda amarilla = 3 en total.
PESO_ROJA = 2


def tarjetas_winpot(amarillas, rojas, segundas_amarillas=0):
    """Tarjetas de un equipo con la regla de Winpot (escalares).

    ESPN y football-data no cuentan la primera amarilla de quien se va expulsado por
    doble amarilla (`yellowCards` = 1 y `redCards` = 1 en un equipo con otra amarilla
    y una doble amarilla; verificado en 6 partidos de ESPN, oct-2026). Esa expulsión
    vale 3 en Winpot, así que se suma 1 por cada `segundas_amarillas` cuando se conoce.
    Sin amarillas devuelve None (falta el dato); sin rojas o segundas, cuentan 0.
    """
    if amarillas is None:
        return None
    return amarillas + PESO_ROJA * (rojas or 0) + (segundas_amarillas or 0)


def columnas_tarjetas(df):
    """Recalcula tarjetas_local/visitante de un DataFrame con amarillas_* y rojas_*.

    Usa segundas_amarillas_* si la columna existe (descargas nuevas de ESPN); en
    historiales viejos falta y la doble amarilla cuenta 2 en vez de 3.
    """
    for lado in ("local", "visitante"):
        a, r = f"amarillas_{lado}", f"rojas_{lado}"
        if a not in df.columns or r not in df.columns:
            continue
        seg = f"segundas_amarillas_{lado}"
        extra = df[seg].fillna(0) if seg in df.columns else 0
        df[f"tarjetas_{lado}"] = df[a] + PESO_ROJA * df[r].fillna(0) + extra
    return df
