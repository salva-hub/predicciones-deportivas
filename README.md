# predicciones-deportivas

Sistema de pronósticos deportivos de Salva. Calcula la probabilidad de cada mercado con un modelo estadístico,
la ajusta por bajas, árbitro, clima y altitud, y solo recomienda apostar cuando la cuota de Winpot tiene valor.
Todo con fuentes gratuitas.

**Etapa actual:** fútbol (ligas top de Europa y América, torneos de clubes y selecciones).
Siguientes: NBA, NFL, MLB y béisbol mexicano.

## Cómo se usa
Pídele a Claude, en el chat del proyecto, algo como "dame el pronóstico de Toluca vs América de hoy".
Claude sigue el procedimiento de [CLAUDE.md](CLAUDE.md): investiga el partido, corre el modelo, te da probabilidades,
cuota mínima (formato americano) y justificación, y guarda todo en `registro/`.

GitHub Actions califica cada pronóstico después del partido y actualiza el dashboard:
https://salva-hub.github.io/predicciones-deportivas/

## Estructura
| Carpeta | Contenido |
| --- | --- |
| `src/predicciones/` | Modelo de goles (Dixon-Coles), córners y tarjetas, reglas de valor y bank, línea de comandos |
| `src/predicciones/datos/` | Conectores: ESPN, football-data.co.uk, Open-Meteo, The Odds API |
| `registro/` | Pronósticos, apuestas reales y bank (el estado del sistema) |
| `datos/historial/` | Historiales de partidos, actualizados cada lunes |
| `dashboard/` | Página del dashboard |
| `.github/workflows/` | Pruebas, calificación cada 3 horas, actualización de datos y prueba contra el pasado |
| `docs/` | Configuración y seguridad del repositorio |

## Desarrollo
```bash
pip install -r requirements.txt
python -m pytest -q
PYTHONPATH=src python -m predicciones.cli --help
```
