# Capturas del dashboard de observabilidad (Arize Phoenix)

Evidencia de la **Capa de Observabilidad** (punto 3) y de la **Prueba de carga**
(punto 5). Generadas corriendo `python prueba_carga.py --n 5` con el stack
levantado (API + Redis Stack + Phoenix) y leyendo el dashboard en
`http://localhost:6006`.

## Capturas incluidas

| Archivo | Qué muestra |
|---|---|
| `01-trazas-lista-proyecto.jpg` | Vista del proyecto `entregable7-orquestador`: **26 trazas**, **59.714 tokens**, Latency **P50 29,71 s** / **P99 176,17 s**, y la tabla de trazas (una `LangGraph` por ejecución, con su latencia y tokens). |
| `02-traza-detalle-spans-por-nodo.jpg` | Detalle de UNA traza: el árbol de **spans por nodo** del grafo (`supervisor → ChatOpenAI` con sus tokens → `enrutar` → `investigador → agent → call_model`…) y la latencia de cada uno. Es la evidencia de que cada nodo se instrumenta. |
| `03-spans-por-nodo.jpg` | Pestaña **Spans**: cada nodo como span individual (`supervisor`, `enrutar`, `sintesis`, `ChatOpenAI`/LLM…), con la decisión del Supervisor visible en el output. Útil para leer dónde se concentra la latencia y qué nodo consume más tokens. |

## Sobre la latencia p95 y el costo por ejecución

- **Latencia:** Phoenix muestra en el encabezado del proyecto los percentiles
  **P50 y P99** (la p95 queda entre ambos). La **p95 medida del lado cliente** en
  esta misma corrida está en `evidencia/prueba_carga_salida.txt`.
- **Costo por ejecución:** Phoenix (igual que LangSmith) calcula el costo en USD a
  partir de los tokens **solo si conoce el precio del modelo**. Esta corrida usó
  un modelo de **Groq** (`openai/gpt-oss-20b`), que no está en la tabla de precios
  por defecto, así que el dashboard muestra los **tokens** pero no el costo en
  dólares. Para ver el costo en USD: corré la prueba con un modelo de OpenAI o
  Anthropic (`LLM_PROVIDER=openai`/`anthropic` con su API key), o cargá el precio
  del modelo en *Settings → Models* de Phoenix. Los tokens por ejecución (base del
  cálculo) ya están en las capturas.
