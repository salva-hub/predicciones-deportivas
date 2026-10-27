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
  No aplica a los guardados de rutina de `registro/` del paso 8.
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
  `registro/pronosticos/<id>.json` (si no tiene `apuestas_papel` ni aparece en apuestas o parlays en papel;
  revisa con `grep -rl <id> registro/ --exclude-dir=pronosticos`) para no contarlo dos veces. Si tiene apuestas, deja el viejo y no guardes otro.

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
   - Árbitro designado: prensa de la jornada (Récord y Mediotiempo para Liga MX, premierleague.com, etc.) o la
     página del partido en statshub.com (consulta manual, gratis; no automatizar).
   - Tarjetas por partido del árbitro en esa competición: playerstats.football, valuestats.com, o statshub.com
     (página del partido → rendimiento del equipo → estadística "árbitro" → filtro "Competiciones": deja solo la del
     partido). El total que StatsHub muestra junto al nombre del árbitro mezcla todas las competencias: no lo uses.
     Cuéntalas como Winpot (roja = 2): **amarillas por partido + 2 × rojas por partido**. Ese número va en `--arbitro-prom`.
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
   `--arbitro-prom` = amarillas por partido + 2 × rojas por partido del árbitro (regla de Winpot; el promedio de la
   liga con que se compara se calcula igual).
   `--confianza` es obligatoria (paso 4). En cancha neutral (Mundial, Copa América, finales en sede neutral)
   agrega `--neutral`: quita la ventaja de local.
   **Pago anticipado** (ver "Cómo liquida Winpot") va activado por defecto en todas las ligas. Si Salva avisa que
   Winpot no lo ofrece en ese partido, agrega `--sin-pago-anticipado` (se guarda `pago_anticipado: false`).
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
   ambos anotan; hándicap asiático principal; goles por equipo; córners y tarjetas en la línea más común; con alineación
   oficial, tiros a puerta de los 2 o 3 jugadores con más esperado de cada equipo, confianza baja)

   **Justificación:** 3 a 6 viñetas con lo que movió el pronóstico (forma y fuerza, bajas, árbitro, clima, altitud, contexto),
   cada una con su fuente y cuánto ajustó.

   **Qué apostar:** las selecciones cuya cuota mínima es alcanzable, en orden de valor. Pide a Salva la cuota de Winpot de esas
   selecciones (o que confirme si Winpot paga la mínima o más). **Pide también la cuota de Winpot de las 3 o 4 selecciones
   más probables del partido** (aunque no tengan valor): sirven para sencillas fuertes y para armar combinadas.

7. **Cuando Salva da las cuotas de Winpot:**
   `python -m predicciones.cli evaluar --id <id> --winpot "goles:mas_2.5=-130" --winpot "tarjetas:mas_4.5=-133"`
   Recomienda solo lo que salga con `apostar: true`, con el monto calculado. Cada fila trae su `categoria`
   (`valor`, `sencilla_fuerte` o vacía) y `sirve_para_combinada`. Para la jornada, arma parlays de valor y
   combinadas con `python -m predicciones.cli parlays --fecha hoy` (cada uno trae su `categoria`).
   Dile a Salva en qué categoría va cada apuesta. Si Salva apuesta, registra con `apostar ... --categoria <categoría>`.
   Respeta el `disponible_hoy` de `python -m predicciones.cli bank` (en la prueba en papel ya descuenta lo
   "apostado" en papel hoy; `evaluar` y `parlays` también lo respetan entre varias selecciones).
   **Prueba en papel hasta el 26-nov-2026 (inclusive):** `evaluar` devuelve `apostar: false` siempre y marca con
   `apostar_papel: true` (y `monto_papel`) lo que habría pasado; lo guarda en `apuestas_papel` del pronóstico y
   GitHub Actions lo califica. `parlays` los lista como `parlays_papel`. Dile a Salva: "Prueba en papel hasta
   26-nov-2026: no apostar dinero", y muéstrale la selección y el monto que se habría apostado.
   `apostar` se niega en ese periodo; usa `--forzar` solo si Salva dice que ya apostó dinero real.
   **Después del 26-nov-2026** solo la categoría `valor` sale con `apostar: true` (lista `CATEGORIAS_DINERO_REAL`
   en `bank.py`); las otras dicen "categoría pendiente de aprobación tras la prueba en papel" hasta que Salva las apruebe.

8. **Guarda el registro en GitHub** (Salva lo autorizó):
   `git add registro/ && git commit -m "Pronóstico: <partido>" && git pull --rebase origin main && git push origin HEAD:main`.
   Si el push a main es rechazado, crea una rama `registro/<fecha>` y abre un pull request.
   Solo `registro/` va directo a main; cambios de código van siempre por pull request.

## Cuando Salva confirma una apuesta o un movimiento

- Sencilla: `python -m predicciones.cli apostar --pata "<id>:goles:mas_2.5=-130" --monto 500 --categoria valor`
  (`--categoria`: valor, sencilla_fuerte o combinada; cuenta para el máximo por día).
- Parlay: una `--pata` por selección, con su cuota de Winpot.
- Retiro o depósito: `python -m predicciones.cli movimiento --tipo retiro --monto 2000`
- Liquidar a mano (apuesta marcada "Revisar a mano", o si Winpot pagó distinto):
  `python -m predicciones.cli liquidar --id <id_apuesta> --estado ganada|perdida|devuelta [--cuota-real +250]`.
  El estado aplica a la apuesta completa (en un parlay, al parlay entero); `--cuota-real` es la cuota total que pagó Winpot.
- Luego guarda el registro como en el paso 8.

GitHub Actions califica todo automáticamente cada 30 minutos (empieza 2.5 h después del inicio del partido) y actualiza el dashboard. Si faltan datos de un partido
(córners, tarjetas), lo vuelve a intentar en cada corrida durante 48 h; las patas sin datos quedan pendientes.
El dashboard filtra por deporte, liga y periodo (día, semana, mes o año, en hora de Sonora; por defecto el mes actual)
y muestra cada parlay con sus patas, cuotas y estados. Cada pronóstico guarda `deporte` (hoy siempre `futbol`;
los registros viejos sin ese campo cuentan como fútbol).
En "Pronósticos" muestra la **calibración** (sin filtros): por mercado, selecciones calificadas, probabilidad media,
acierto real y Brier; y por rango de probabilidad (0-10% … 90-100%). Usa las mismas reglas de `calificar`
(pago anticipado incluido); devueltas y sin datos no cuentan.

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
- Tiros al arco de jugador (`tiros_jugador`): solo con valor ≥ 10% (también como pata de parlay), categoría `valor`,
  confianza **siempre baja** (monto 2%; nunca sencilla fuerte ni combinada). El sistema los calcula solo:
  - `pronostico` lee la **alineación oficial de ESPN** (`rosters` del resumen del partido, sale ~1 hora antes). Sin
    alineación no los calcula y lo anota ("ESPN aún no publica la alineación oficial"): rehaz el pronóstico definitivo.
  - Para cada equipo baja de ESPN (gratis, con caché) sus partidos de la temporada actual y la anterior (hasta 50) y
    toma los tiros a puerta y minutos de cada jugador. Esperado = tiros a puerta por 90 (encogidos hacia el promedio de
    su posición en el equipo; con 450 min propios pesa la mitad) × minutos esperados (su promedio como titular, tope 85)
    × rival (tiros a puerta que concede vs la liga, encogido) × sede. Poisson, encogida 10% hacia la tasa base de la línea.
  - Muestra hasta 6 titulares de campo por equipo (delanteros y medios primero) con más de 0.5, 1.5 y 2.5
    (las líneas con menos de 5% no salen). El pronóstico guarda también `tiros_jugador` (esperado, minutos) para la justificación.
  - Cuota de Winpot en `evaluar`: `--winpot "tiros_jugador:<Nombre Jugador>:mas_0.5=+150"` (el nombre como sale en el
    pronóstico; acepta sin acentos ni mayúsculas, o solo el apellido si no se repite). En `apostar`:
    `--pata "<id>:tiros_jugador:<Nombre Jugador>:mas_0.5=+150"`.
  - Límites: fichajes sin minutos con el equipo usan el promedio de su posición; no hay datos por jugador para
    selecciones nacionales ni (casi siempre) Concacaf; no sabe de penales asignados ni de cambios de rol.
  - Prueba histórica (LaLiga y Premier 2025/26, 15,020 titulares de campo, `python -m predicciones.backtest_tiros
    --liga laliga --liga premier --temporada 2025`): más de 0.5 log-loss 0.511 y Brier 0.168 (0.198 sin modelo);
    calibrado de 7% a 83%; sin sobredispersión (1.02).
- **Prueba en papel del 26-oct-2026 al 26-nov-2026:** nada de dinero real; se registran y califican las apuestas
  en papel. Al terminar, revisar sus resultados con Salva antes de apostar dinero.

### Cómo liquida Winpot (reglas Altenar v1.29, PDF público en winpot-cdn.mx)

- Todo en tiempo reglamentario (90 min + reposición); la prórroga y los penales no cuentan salvo que el mercado lo diga.
  El programa ya califica los goles con el marcador de 90 min. Pero en un partido con prórroga, ESPN incluye en
  tarjetas y córners los de la prórroga y `calificar` no puede separarlos: esas patas se liquidan a mano con `liquidar`.
- **Tarjetas:** amarilla = 1, roja directa = 2, doble amarilla = 3 en total (máximo 3 por jugador). No cuentan las
  mostradas después del silbatazo final ni a suplentes en la banca, sustituidos o cuerpo técnico. El sistema cuenta
  igual (historial, modelo, árbitro y calificación). ESPN no trae la primera amarilla del expulsado por doble amarilla:
  el programa la suma leyendo los eventos del partido; en historiales viejos esa expulsión cuenta 2.
- **Córners:** solo los que se cobran (concedido y no cobrado no cuenta).
- **Pago anticipado de fútbol:** una apuesta prepartido al ganador (1X2) **local o visitante** se paga completa como
  ganada si ese equipo va **2 goles arriba en cualquier momento**, aunque al final empate o pierda. Aplica a
  sencillas, parlays (múltiples), apuestas de sistema y bet builder. **No** aplica al empate, a apuestas en vivo ni
  con Cash Out. Salva confirmó (26-oct-2026) que está en prácticamente todas las ligas: **activado por defecto**;
  si en un partido no está, él avisa y se usa `--sin-pago-anticipado`.
  - Probabilidad: ganador "1" (o "2") = P(gana) del 1X2 de Pinnacle sin margen (con ajustes) + P(va 2 arriba y no
    gana). El extra sale de la matriz de goles esperados: dado el marcador final, el orden de los goles es al azar
    (cálculo exacto, sin simulación; `pago_anticipado.py`). Por eso "1", "X" y "2" ya no suman 100%. Cada selección
    guarda también `prob_sin_pago_anticipado` y las notas dicen cuánto sumó. **Solo** el ganador 1 y 2: el empate,
    la doble oportunidad, el hándicap 0 (empate no apuesta) y el primer tiempo no cambian; la regla de doble
    oportunidad (55%) mira el 1X2 real. `evaluar` y `parlays` usan la probabilidad nueva sin hacer nada más.
  - Validación (26-oct-2026, ESPN + Pinnacle de cierre, 5 ligas grandes, 2023/24 y 2024/25, 3,295 partidos): un
    equipo fue 2 arriba y no ganó en 138 casos (4.2% de los partidos); el modelo daba 101 ± 10 (por liga de 1.07 a
    1.66 veces). El orden al azar sí cuadra (147 contra 132 dado el marcador final); lo que falta es que Poisson da
    pocos marcadores altos parejos. Se multiplica el extra por **1.35** (`CALIBRACION`). Ejemplo: goles esperados
    1.52 – 0.83, local 52.5% → 53.8% (cuota justa −111 → −117); visitante 19.7% → 21.2%.
  - Calificación: `calificar` lee de ESPN la secuencia de goles del tiempo reglamentario (keyEvents; autogoles al
    equipo beneficiado; prórroga y penales no cuentan) y guarda `ventaja_max_local` / `ventaja_max_visitante` en el
    resultado. Una pata de ganador 1/2 que perdería se marca ganada si su equipo llegó a +2. Si la secuencia no se
    puede leer (o no cuadra con el marcador de 90 min) queda sin datos, se reintenta 48 h y luego "Revisar a mano"
    (`liquidar`). Vale igual para sencillas y parlays en papel y apuestas reales. Los pronósticos guardados antes
    de esta regla (sin el campo `pago_anticipado`) se califican **sin** pago anticipado. El backtest no lo usa
    (Pinnacle no lo tiene).
- **Hándicap asiático y totales de cuarto (±0.25, ±0.75):** media apuesta en cada línea vecina (medio ganado / medio
  perdido). El programa no ofrece esas líneas; si Salva apuesta una, se liquida a mano.
- **Tiros a puerta de jugador en `calificar`:** se califican solos con `rosters` de ESPN (tiros a puerta del jugador),
  buscando al jugador por su id de ESPN (guardado en el pronóstico) y, si no, por nombre.
  Jugador que no participó o no está en el acta: devuelta. ESPN cuenta el partido completo: con prórroga quedan sin
  datos y se liquidan a mano; ESPN no distingue tiros bloqueados sobre la línea ni el mercado "jugador y su sustituto".
- **Tiros a puerta (equipo y jugador):** cuentan goles y tiros atajados, y los bloqueados por el último defensor
  sobre la línea; un tiro al poste no cuenta, salvo en el mercado "jugador y su sustituto (incl. madera)", que además
  suma al sustituto directo e incluye prórroga. Jugador que no participa: apuesta anulada (devuelta); si el mercado
  dice "alineación inicial" y no es titular, también se anula. Si entra de cambio, cuenta lo que haga.

### Tres categorías de apuesta (aprobadas por Salva el 26-oct-2026)

Las tres se registran en papel por separado y **el 26-nov-2026 se comparan con Salva** (dashboard: "Prueba en papel
por categoría": apuestas, acierto, ganancia, ROI y peor racha). "Precio justo" = la cuota justa de la probabilidad final.
"Dentro de tolerancia" = Winpot paga la cuota justa o hasta 4% menos (en decimal: Winpot ≥ justa × 0.96).

1. **Valor** (`valor`): las reglas de arriba (sencillas y parlays con valor).
2. **Sencilla fuerte** (`sencilla_fuerte`): cuota negativa en Winpot de **−200 o mejor** (−150 sí, −250 no;
   `CUOTA_MIN_FUERTE` en `valor.py`), probabilidad ≥ 60%, confianza alta o media (nunca baja) y dentro de tolerancia.
   Se apuesta para **ganar** un objetivo (ver Montos). Máximo 1 por día. Con momio peor que −200 no va como
   sencilla fuerte (`motivo`: "momio peor que −200…"), pero puede ir como valor si cumple sus reglas o como pata
   de combinada (`sirve_para_combinada`).
3. **Combinada** (`combinada`): 2 o 3 patas de partidos distintos, cada una con probabilidad ≥ 55%, confianza alta o
   media y dentro de tolerancia (o con valor); cuota total de Winpot entre +200 y +350. Se prefieren las de 2 patas y
   luego la de mayor probabilidad; ninguna pata se repite entre las combinadas del día. Máximo 2 por día.

Una sencilla que cumple sencilla fuerte cuenta como **sencilla fuerte aunque tenga valor** (se apuesta para ganar el
objetivo); queda marcada `con_valor: true` para analizarla aparte. Valor cubre el resto (cuotas positivas, probabilidad
menor a 60%, confianza baja). Si ya se usó la sencilla fuerte del día y la selección tiene valor, pasa a valor.
Con dinero real, mientras sencilla fuerte no esté en `CATEGORIAS_DINERO_REAL`, una selección con valor cuenta como valor.
Una apuesta cuyo monto bajó por el límite del día queda marcada `recortada: true` (el resumen cuenta cuántas por categoría).
Un parlay idéntico a uno de valor no se repite como combinada. Los máximos cuentan por día de Sonora en que se evalúa.

**Aparte: Elección de Salva** (`eleccion_salva`): apuestas que Salva elige por su cuenta (en papel o reales,
`apostar ... --categoria eleccion_salva`). No es una categoría del sistema: va en su propia fila del dashboard,
no ocupa el máximo por día de sencilla fuerte ni de combinada, y su monto **sí** cuenta para el límite del día.
`parlays` nunca la reemplaza al rearmar los parlays en papel del día.

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
- Parlay de valor: 3% con confianza alta; 2% con confianza media o baja.
- Sencilla fuerte: lo necesario para **ganar** 5% del bank con confianza alta (con 10,000: ganar 500; a −120 se ponen 600)
  o 2.5% con confianza media (ganar 250). Se redondea hacia arriba a 10 MXN. Como el momio mínimo es −200, el riesgo
  máximo es el doble del objetivo (con 10,000 y confianza alta: 1,000 a −200).
- Combinada: 5% del bank si todas las patas son de confianza alta; 3% si alguna es media.
- Límite del día: 10% del bank; si ese día ya hay ganancia cobrada, 10% más la mitad de esa ganancia, hasta 20%.
  Cubre las tres categorías juntas (reales o en papel). Si los montos pasan del disponible, se **recortan en orden**:
  en `evaluar` sencillas fuertes y de valor juntas, primero la de mayor valor esperado; en `parlays` primero los de valor y luego las combinadas. Cada uno
  toma lo que queda; cuando ya no queda nada, esa apuesta no se hace.
- Pausa: si las apuestas del mes pierden 50% de la base del mes (bank al inicio del mes + depósitos − retiros del mes),
  no se recomiendan apuestas y se revisa el modelo. Los retiros y depósitos no cuentan como pérdida ni ganancia.
  En pausa, `evaluar` devuelve `apostar: false` y `parlays` no arma nada, en las tres categorías.
- `python -m predicciones.cli bank` muestra cuántas de cada categoría van hoy (`usadas_hoy`) y los montos de cada una.

## Otros deportes

NBA, NFL, MLB y béisbol mexicano todavía no están construidos. Si Salva pide uno, dile que es la siguiente etapa
y no improvises un pronóstico con dinero.
