# Instrucciones para Claude: pronósticos deportivos de Salva

Este repositorio es el sistema completo. Todo el estado vive aquí, no en el chat:
- `registro/pronosticos/<id>.json`: un archivo por pronóstico emitido, con su resultado.
- `registro/apuestas/<id>.json`: un archivo por apuesta real que Salva confirmó.
- `registro/bank.json`: bank inicial y retiros o depósitos.
- `datos/historial/*.csv`: historiales que GitHub Actions actualiza cada lunes.

Una sesión nueva no necesita el historial del chat: lee estos archivos.

## Reglas fijas

- Responde siempre en español, de forma breve y clara.
- **Cuotas siempre en formato americano** (−110, +200). Nunca muestres cuotas decimales a Salva.
- **Horas en hora de Sonora** (UTC−7, sin horario de verano), salvo que Salva diga que está en otro lugar.
- Casa de apuestas: **Winpot**. bet365 está suspendida en México (desde nov-2025); úsala solo si Salva dice que volvió.
- No inventes datos. Si una fuente falla, dilo en la justificación y baja la confianza.
- Solo se recomienda apostar cuando hay valor (reglas abajo). "No apostar" es una respuesta válida y frecuente.
- Costo cero: no uses ni propongas servicios de pago.
- Antes de cualquier cambio al repositorio (código, CLAUDE.md, configuración), da a Salva un resumen general en lenguaje simple de lo que contiene.
- Si Claude elige partidos, pregunta antes a Salva si está de acuerdo.
- Los partidos que Salva pida por su cuenta en la prueba en papel también se registran y suman a las estadísticas.

## Preparación de cada sesión

```bash
git pull --ff-only origin main
pip install -q -r requirements.txt
export PYTHONPATH=src
```

Las claves (`THE_ODDS_API_KEY`, etc.) vienen de las variables del entorno; nunca las escribas en archivos.

## Flujo cuando Salva pide un pronóstico

Ejemplos de pedido: "dame el pronóstico de Real Madrid vs Barcelona de hoy", "pronósticos de la jornada de Liga MX de hoy".

**Cuándo pronosticar:** las alineaciones oficiales salen ~1 hora antes del partido.
- Si Salva pide antes, da un **pre-pronóstico** (informativo) con `--no-guardar` (no se registra) y dile que se rehace
  cuando salgan las alineaciones oficiales.
- El **pronóstico definitivo** se hace 45 a 60 min antes del inicio, con alineaciones oficiales (cuentas oficiales de los
  clubes en X/Twitter, ESPN, Flashscore, Sofascore, Rotowire) y cuotas de Pinnacle actualizadas. Ese es el que se guarda
  en `registro/` y cuenta en las estadísticas. Con alineación oficial y árbitro conocido: `--confianza alta`.
- El código no permite reemplazar un pronóstico. Si ya había uno guardado del mismo partido, borra antes
  `registro/pronosticos/<id>.json` (si no tiene `apuestas_papel` ni aparece en `registro/apuestas/`;
  revisa con `grep -r <id> registro/apuestas/`) para no contarlo dos veces. Si tiene apuestas, deja el viejo y no guardes otro.

1. **Identifica la liga y el partido.**
   `python -m predicciones.cli partidos --liga <liga> --fecha hoy`
   Ligas: premier, laliga, serie_a, bundesliga, ligue_1, liga_mx, mls, brasil, argentina, champions,
   europa_league, libertadores, concacaf, mundial, eliminatorias_conmebol, eliminatorias_concacaf,
   nations_league, copa_america, eurocopa, amistosos.
   Para una jornada, repite los pasos 2 a 5 por partido.

2. **Investiga solo ese partido** con búsqueda y lectura web (sin consultar toda la jornada):
   - Bajas por lesión o suspensión: sportsgambler.com/injuries/football/ (32 ligas, incluye Liga MX),
     premierinjuries.com (Premier), rotowire.com/soccer/lineups.php, prensa local (Récord, Mediotiempo, Olé, ESPN Deportes).
   - Alineación probable: starting11.com, Rotowire, Sports Mole.
   - Árbitro designado: prensa de la jornada (Récord y Mediotiempo para Liga MX, premierleague.com, etc.).
   - Tarjetas por partido del árbitro en esa competición: playerstats.football, valuestats.com.
   - Contexto: tabla, eliminatoria (resultado de ida), descanso, viajes, altitud.
   Anota la fuente de cada dato importante.

3. **Convierte lo investigado en ajustes** (`--ajuste objetivo:factor:motivo`).
   Con cuotas de Pinnacle, los goles parten de su precio, que ya incluye las bajas conocidas.
   Aplica ajustes de goles **solo por noticias que el mercado quizá no ha cobrado**: alineación confirmada
   en la última hora, lesión de último momento, rotación anunciada tarde. Una baja conocida desde hace días
   ya está en el precio: no la ajustes otra vez (menciónala en la justificación). Sin Pinnacle, usa la tabla completa.
   Córners y tarjetas no tienen Pinnacle: ahí sí aplica la tabla siempre. Valores de partida:

   | Situación | Ajuste |
   | --- | --- |
   | Baja del delantero estrella o máximo goleador | goles del equipo ×0.90 a ×0.95 |
   | Baja de 2 o más titulares ofensivos | goles del equipo ×0.88 a ×0.93 |
   | Baja del portero titular | goles del rival ×1.05 a ×1.08 |
   | Baja de 1 o 2 defensas titulares | goles del rival ×1.03 a ×1.06 |
   | Menos de 3 días de descanso o rotación anunciada | goles del equipo ×0.97 |
   | Local a 2,000 m o más contra visitante de menos de 1,000 m (Toluca, Pachuca, CDMX, Puebla, La Paz, Quito, Bogotá) — **solo sin Pinnacle** | goles_local ×1.05 y goles_visitante ×0.95 |
   | Clásico o rivalidad fuerte | tarjetas ×1.10 |
   | Partido decisivo (final, eliminación, descenso) | tarjetas ×1.05 |
   | Equipo sin nada en juego | goles del equipo ×0.95 |

   Objetivos válidos: `goles_local`, `goles_visitante`, `corners`, `tarjetas`.
   El clima (lluvia fuerte, viento, calor) se ajusta solo **y solo sin Pinnacle** (con Pinnacle ya está en el precio;
   el programa lo anota). La altitud tampoco se ajusta con Pinnacle. El factor del árbitro se calcula con `--arbitro-prom` y `--arbitro-partidos`.

4. **Elige la confianza de los datos** (`--confianza`): `alta` si la alineación está confirmada o es muy probable y se conoce al árbitro;
   `media` si falta uno de los dos; `baja` si faltan varios datos. El programa baja un nivel más en Liga MX, MLS,
   Brasil, Argentina, Libertadores y Concacaf (menos datos), en equipos sin historial, **sin cuotas de Pinnacle**
   (solo el modelo) y cuando el modelo y Pinnacle sin margen discrepan mucho (más de 8 puntos).
   Con Pinnacle también baja un nivel: sin línea de goles de Pinnacle (solo 1X2), en goles, ambos anotan, goles por
   equipo, primer tiempo y hándicap ("Sin línea de goles de Pinnacle"); y si las cuotas no cuadran entre sí
   ("El ajuste a Pinnacle no cuadra bien"): revisa que estén bien copiadas.

5. **Calcula y guarda el pronóstico:**
   ```bash
   python -m predicciones.cli pronostico --liga liga_mx --local "Toluca" --visitante "America" --fecha hoy \
     --ajuste "goles_visitante:0.93:Baja de su delantero titular (Sportsgambler)" \
     --arbitro "Nombre" --arbitro-prom 5.4 --arbitro-partidos 30 --confianza alta
   ```
   `--confianza` es obligatoria (paso 4). En cancha neutral (Mundial, Copa América, finales en sede neutral)
   agrega `--neutral`: quita la ventaja de local.
   **Pinnacle es la base del pronóstico**: siempre hacen falta sus cuotas. Si no hay clave de The Odds API (o no trae
   el partido), léelas en la web (1X2 y, si se puede, más/menos 2.5) y pásalas con
   `--pinnacle "1=-150,X=+280,2=+400,mas_2.5=-110,menos_2.5=-110"`. Sin ellas el programa usa solo el modelo,
   baja la confianza un nivel y lo anota ("Sin cuotas de referencia de Pinnacle"); dilo en la justificación.
   Lo mismo si solo hay más/menos 2.5 sin 1X2.
   El comando guarda el pronóstico en `registro/pronosticos/<id>.json` y devuelve su `id`.

6. **Responde a Salva** con este formato:

   **Equipo A vs Equipo B** · competición · hora de Sonora · estadio (techado o no)

   | Mercado | Probabilidad | Cuota justa | Cuota mínima para apostar |
   | --- | --- | --- | --- |
   (ganador; doble oportunidad solo si ningún resultado llega a 55%; total de goles 2.5 y la línea más cercana al esperado;
   ambos anotan; hándicap asiático principal; goles por equipo; córners y tarjetas en la línea más común)

   **Justificación:** 3 a 6 viñetas con lo que movió el pronóstico (forma y fuerza, bajas, árbitro, clima, altitud, contexto),
   cada una con su fuente y cuánto ajustó.

   **Qué apostar:** las selecciones cuya cuota mínima es alcanzable, en orden de valor. Pide a Salva la cuota de Winpot de esas
   selecciones (o que confirme si Winpot paga la mínima o más).

7. **Cuando Salva da las cuotas de Winpot:**
   `python -m predicciones.cli evaluar --id <id> --winpot "goles:mas_2.5=-130" --winpot "tarjetas:mas_4.5=-133"`
   Recomienda solo lo que salga con `apostar: true`, con el monto calculado. Para la jornada, arma parlays con
   `python -m predicciones.cli parlays --fecha hoy`.
   Respeta el `disponible_hoy` de `python -m predicciones.cli bank` (en la prueba en papel ya descuenta lo
   "apostado" en papel hoy; `evaluar` y `parlays` también lo respetan entre varias selecciones).
   **Prueba en papel hasta el 26-nov-2026 (inclusive):** `evaluar` devuelve `apostar: false` siempre y marca con
   `apostar_papel: true` (y `monto_papel`) lo que habría pasado; lo guarda en `apuestas_papel` del pronóstico y
   GitHub Actions lo califica. `parlays` los lista como `parlays_papel`. Dile a Salva: "Prueba en papel hasta
   26-nov-2026: no apostar dinero", y muéstrale la selección y el monto que se habría apostado.
   `apostar` se niega en ese periodo; usa `--forzar` solo si Salva dice que ya apostó dinero real.

8. **Guarda el registro en GitHub** (Salva lo autorizó):
   `git add registro/ && git commit -m "Pronóstico: <partido>" && git pull --rebase origin main && git push origin HEAD:main`.
   Si el push a main es rechazado, crea una rama `registro/<fecha>` y abre un pull request.
   Solo `registro/` va directo a main; cambios de código van siempre por pull request.

## Cuando Salva confirma una apuesta o un movimiento

- Sencilla: `python -m predicciones.cli apostar --pata "<id>:goles:mas_2.5=-130" --monto 500`
- Parlay: una `--pata` por selección, con su cuota de Winpot.
- Retiro o depósito: `python -m predicciones.cli movimiento --tipo retiro --monto 2000`
- Liquidar a mano (apuesta marcada "Revisar a mano", o si Winpot pagó distinto):
  `python -m predicciones.cli liquidar --id <id_apuesta> --estado ganada|perdida|devuelta [--cuota-real +250]`.
  El estado aplica a la apuesta completa (en un parlay, al parlay entero); `--cuota-real` es la cuota total que pagó Winpot.
- Luego guarda el registro como en el paso 8.

GitHub Actions califica todo automáticamente cada 3 horas y actualiza el dashboard. Si faltan datos de un partido
(córners, tarjetas), lo vuelve a intentar en cada corrida durante 48 h; las patas sin datos quedan pendientes.

## Reglas de valor (revisadas el 26-oct-2026 tras el backtest)

- **Base: Pinnacle sin margen.** Ganador y doble oportunidad salen directo del 1X2 de Pinnacle sin margen. Para
  los demás mercados de goles (totales, ambos anotan, goles por equipo, hándicap, primer tiempo) el programa busca
  los goles esperados que reproducen ese 1X2 y el más/menos 2.5; sin línea de goles, el total sale del modelo.
  Encima van solo los ajustes por noticias que el mercado no ha cobrado (también mueven el ganador).
  El modelo propio queda como referencia y para avisar de desacuerdos.
- Solo se apuesta si Winpot paga más que ese precio justo, con estos mínimos de valor esperado:
- Sencilla en ganador, doble oportunidad, goles, ambos anotan, hándicap: valor esperado ≥ 3%.
- Sencilla en córners o tarjetas: valor esperado ≥ 5%. (Sin Pinnacle para estos mercados; el modelo se encoge
  hacia 50% porque el backtest mostró que se confiaba de más en los extremos.)
- Doble oportunidad: solo si ningún resultado del 1X2 llega a 55% y tiene valor.
- Parlay: 2 a 4 selecciones de partidos distintos, cada una con valor ≥ 2% y el parlay completo ≥ 5%.
  Ideal: +200 o más con 2 selecciones de confianza alta. Si una sencilla tiene claramente más valor, recomienda la sencilla.
- **Prueba en papel del 26-oct-2026 al 26-nov-2026:** nada de dinero real; se registran y califican las apuestas
  en papel. Al terminar, revisar sus resultados con Salva antes de apostar dinero.

## Resultados del backtest (26-oct-2026)

Temporadas 2022/23 a 2025/26, apostando contra la cuota de apertura de Pinnacle con la regla vieja
(50% modelo + 50% Pinnacle, valor ≥ 3%):

| Liga | ROI 1X2 |
| --- | --- |
| Premier | −15.7% (928 apuestas, acierto 24%) |
| LaLiga | −26.8% |
| Serie A | −21.2% |
| Bundesliga | −9.4% |
| Ligue 1 | −21.5% |

- Más/menos 2.5: entre −2% y −10%.
- Log-loss del 1X2 (menor es mejor), Premier: mezcla 50/50 0.972, modelo solo 0.984, Pinnacle de cierre 0.949.
  Cuando el modelo y Pinnacle discrepan, casi siempre acierta Pinnacle. Por eso la base ahora es Pinnacle.
- Con la regla nueva (base Pinnacle, sin ajustes) el backtest no encuentra apuestas contra la apertura de Pinnacle:
  es lo esperado. El valor tiene que venir de que Winpot pague más que Pinnacle o de noticias de último momento.
- Córners y tarjetas: el modelo se confiaba de más (predecía 0.83 y pasaba 0.61); ahora se encoge hacia 50%.

## Montos (porcentaje del bank actual)

- Sencilla: 5% como máximo (500 MXN con 10,000) con confianza alta y valor de al menos el doble del mínimo;
  3% con valor normal; 2% con confianza baja.
- Ningún monto recomendado pasa de lo que queda del límite del día (`disponible_hoy`).
- Parlay: 3% con confianza alta; 2% con confianza media o baja.
- Límite del día: 10% del bank; si ese día ya hay ganancia cobrada, 10% más la mitad de esa ganancia, hasta 20%.
- Pausa: si las apuestas del mes pierden 50% de la base del mes (bank al inicio del mes + depósitos − retiros del mes),
  no se recomiendan apuestas y se revisa el modelo. Los retiros y depósitos no cuentan como pérdida ni ganancia.
  En pausa, `evaluar` devuelve `apostar: false` y `parlays` no arma nada.

## Otros deportes

NBA, NFL, MLB y béisbol mexicano todavía no están construidos. Si Salva pide uno, dile que es la siguiente etapa
y no improvises un pronóstico con dinero.
