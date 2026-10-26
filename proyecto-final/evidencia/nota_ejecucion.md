# Nota de ejecución y evidencia

Fecha de la corrida: 2026-10-26.

## Qué está verificado en este repo (reproducible por cualquiera)

| Evidencia | Archivo | Estado |
|---|---|---|
| Suite de tests (pytest, 100% offline) | `pytest_salida.txt` | **50 passed** |
| Chequeos deterministas como script autónomo | `verificar_salida.txt` | **TODOS OK** |
| Recuperación RAG real (ChromaDB + `all-MiniLM-L6-v2`) | `rag_salida.txt` | **OK** — recupera el fragmento correcto por consulta, con su fuente |
| Topología del grafo compilado (exportada del grafo real) | `grafo.mmd` | supervisor + investigador + gate HITL + analista + síntesis |
| **5 peticiones concurrentes + latencias** | `prueba_carga_salida.txt` | **2/5 DONE**, 3 FAILED por 429 de Groq (ver abajo) |
| **Costo por ejecución en USD** | `costos_salida.txt` | 6.968 tok → **$0,001562**; 6.475 tok → **$0,001237** |
| Trazas por nodo y costo en el dashboard | `../screenshots/*.jpg` | proyecto `intelligence-system`: 13 trazas, span `buscar_en_politicas → VectorStore` |

Cómo reproducir lo anterior (offline, sin API keys ni red):

```bash
pip install -r requirements-dev.txt
python -m app.rag.ingesta      # construye el índice ChromaDB desde data/
pytest                         # 50 passed
python verificar.py            # TODOS LOS CHEQUEOS OK
```

## Flujo end-to-end con LLM real (RAG + agentes + HITL + trazas)

Durante la validación en vivo se confirmó, contra el `AsyncRedisSaver` y Phoenix
reales, que:

- La API encola (`202` + `job_id`) y no bloquea; el worker corre el grafo con
  `await app.ainvoke(...)`.
- El **investigador invoca la tool RAG** y recupera fragmentos reales del índice
  vectorial. Extracto del log del servidor durante la corrida:

  ```
  rag INFO Cargando modelo de embeddings local: sentence-transformers/all-MiniLM-L6-v2
  rag INFO [RAG] Recuperados 4 fragmento(s) para: 'escala días de vacaciones antigüedad TechCorp'
  ```

- El grafo llega al **gate HITL** (`AWAITING_APPROVAL`) y reanuda tras `POST /approve`.

### Las 5 pruebas concurrentes — corrida real con Groq

`python prueba_carga.py --n 5` contra el stack real (API + Redis Stack + Phoenix),
proveedor Groq (`openai/gpt-oss-20b`):

```
Concurrencia: 5 | Terminadas DONE: 2/5 | Tiempo total: 134.4s
Latencia extremo a extremo (cliente): min=110.6s  mediana=110.6s  p95=134.4s  max=134.4s
```

- **2/5 DONE**: ejecución completa investigador (RAG) → analista, con pausa HITL
  auto-aprobada, respuesta final y **costo en USD** (ver `costos_salida.txt`).
- **3/5 FAILED**: `429 Too Many Requests` del **tier gratuito de Groq** bajo
  concurrencia (límite de tokens/minuto). El worker los pasó a `FAILED` en Redis —el
  manejo de errores previsto—, no quedan en loop. Con una key de tier pago las 5
  llegan a DONE. Las trazas se registran igual (DONE y FAILED).

Las **13 trazas** quedaron en Phoenix (proyecto `intelligence-system`); las capturas
en `../screenshots/` muestran el árbol de spans por nodo, incluida la rama
`investigador → tools → buscar_en_politicas → VectorStoreRetriever` (el RAG real como
herramienta del investigador).

El cálculo de **costo en USD** también está cubierto por `tests/test_costos.py` y
`tests/test_worker.py` (conversión tokens→USD y acumulación a través de la pausa HITL).
