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

## Preparación de cada sesión

```bash
git pull --ff-only origin main
pip install -q -r requirements.txt
export PYTHONPATH=src
```

Las claves (`THE_ODDS_API_KEY`, etc.) vienen de las variables del entorno; nunca las escribas en archivos.

## Flujo cuando Salva pide un pronóstico

Ejemplos de pedido: "dame el pronóstico de Real Madrid vs Barcelona de hoy", "pronósticos de la jornada de Liga MX de hoy".

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

3. **Convierte lo investigado en ajustes** (`--ajuste objetivo:factor:motivo`). Valores de partida:

   | Situación | Ajuste |
   | --- | --- |
   | Baja del delantero estrella o máximo goleador | goles del equipo ×0.90 a ×0.95 |
   | Baja de 2 o más titulares ofensivos | goles del equipo ×0.88 a ×0.93 |
   | Baja del portero titular | goles del rival ×1.05 a ×1.08 |
   | Baja de 1 o 2 defensas titulares | goles del rival ×1.03 a ×1.06 |
   | Menos de 3 días de descanso o rotación anunciada | goles del equipo ×0.97 |
   | Local a 2,000 m o más contra visitante de menos de 1,000 m (Toluca, Pachuca, CDMX, Puebla, La Paz, Quito, Bogotá) | goles_local ×1.05 y goles_visitante ×0.95 |
   | Clásico o rivalidad fuerte | tarjetas ×1.10 |
   | Partido decisivo (final, eliminación, descenso) | tarjetas ×1.05 |
   | Equipo sin nada en juego | goles del equipo ×0.95 |

   Objetivos válidos: `goles_local`, `goles_visitante`, `corners`, `tarjetas`.
   El clima (lluvia fuerte, viento, calor) se ajusta solo; el factor del árbitro se calcula con `--arbitro-prom` y `--arbitro-partidos`.

4. **Elige la confianza de los datos** (`--confianza`): `alta` si la alineación está confirmada o es muy probable y se conoce al árbitro;
   `media` si falta uno de los dos; `baja` si faltan varios datos. El programa baja un nivel más en Liga MX, MLS,
   Brasil, Argentina, Libertadores y Concacaf (menos datos), en equipos sin historial y cuando el modelo y Pinnacle discrepan mucho.

5. **Calcula y guarda el pronóstico:**
   ```bash
   python -m predicciones.cli pronostico --liga liga_mx --local "Toluca" --visitante "America" --fecha hoy \
     --ajuste "goles_visitante:0.93:Baja de su delantero titular (Sportsgambler)" \
     --arbitro "Nombre" --arbitro-prom 5.4 --arbitro-partidos 30 --confianza alta
   ```
   `--confianza` es obligatoria (paso 4). En cancha neutral (Mundial, Copa América, finales en sede neutral)
   agrega `--neutral`: quita la ventaja de local.
   Si no hay clave de The Odds API, puedes pasar cuotas de referencia leídas en la web con `--pinnacle "1=-150,X=+280,2=+400"`.
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
   Respeta el `disponible_hoy` de `python -m predicciones.cli bank`.

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

## Reglas de valor (aprobadas el 26-oct-2026)

- Sencilla en ganador, doble oportunidad, goles, ambos anotan, hándicap: valor esperado ≥ 3%.
- Sencilla en córners o tarjetas: valor esperado ≥ 5%.
- Doble oportunidad: solo si ningún resultado del 1X2 llega a 55% y tiene valor.
- Parlay: 2 a 4 selecciones de partidos distintos, cada una con valor ≥ 2% y el parlay completo ≥ 5%.
  Ideal: +200 o más con 2 selecciones de confianza alta. Si una sencilla tiene claramente más valor, recomienda la sencilla.
- La probabilidad final mezcla el modelo con Pinnacle sin margen (50/50).

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
