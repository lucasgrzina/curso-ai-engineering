# Capturas del dashboard de observabilidad (Arize Phoenix)

Evidencia de la **Capa de Observabilidad** y de la **prueba de carga de 5
peticiones concurrentes**. Generadas corriendo `python prueba_carga.py --n 5` con
el stack levantado (API + Redis Stack + Phoenix) y leyendo el dashboard en
`http://localhost:6006`. Proyecto: **`intelligence-system`**. Corrida del 2026-10-26
con Groq (`openai/gpt-oss-20b`).

## Capturas incluidas

| Archivo | Qué muestra |
|---|---|
| `01-dashboard-intelligence-system.jpg` | Vista de proyectos: `intelligence-system` con **13 trazas**, **23.075 tokens** y **Latency P50 109,73 s**, actualizado al instante de la corrida. |
| `02-traza-lista-intelligence-system.jpg` | Lista de trazas del proyecto: una `LangGraph` por ejecución, con su input (las preguntas de vacaciones), latencia y tokens por traza. |
| `03-spans-por-nodo-con-rag.jpg` | Detalle de UNA traza (`OK`, 112,25 s): el **árbol de spans por nodo** — `supervisor → ChatOpenAI` (994 tokens) → `enrutar`, y `investigador → agent → call_model → ChatOpenAI`, con la rama **`tools → buscar_en_politicas → VectorStoreRetriever`**. Es la evidencia de que **el RAG real es la herramienta del investigador** y queda trazado como un span más. |

## Notas de la corrida

- **5 concurrentes → 2 DONE / 3 FAILED.** Los 3 fallos fueron `429 Too Many Requests`
  del **tier gratuito de Groq** bajo concurrencia (límite de tokens/minuto). El worker
  los pasó correctamente a `FAILED` en Redis (no quedan en loop); las trazas se
  registran igual para DONE y FAILED. Con una API key de tier pago, las 5 llegan a DONE.
- **Costo por ejecución en USD.** Phoenix no trae la tarifa de `gpt-oss-20b` (modelo de
  Groq), así que el costo en dólares lo calcula el propio sistema desde los tokens
  (`app/costos.py`) y se expone en `GET /tasks/{id}.costo`. Evidencia en
  `evidencia/costos_salida.txt` (p. ej. 6.968 tokens → **$0,001562 USD**).
- **Latencia.** El encabezado del proyecto muestra P50 y P99; la p95 medida del lado
  cliente está en `evidencia/prueba_carga_salida.txt`.
