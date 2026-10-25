# Entregable 7 · API de producción y monitoreo activo

Módulo 7 — *API de producción*.

Expone el **orquestador multi-agente del Módulo 6** (Supervisor + Investigador +
Analista + Síntesis) detrás de una **API REST FastAPI asíncrona**, con cuatro
capacidades nuevas de producción:

| Capacidad | Dónde vive |
|---|---|
| **Endpoints asíncronos** (encola y devuelve `job_id`, no bloquea el event loop) | `app/main.py` + `app/worker.py` |
| **Estado en Redis** (jobs + *checkpoints* de LangGraph con `RedisSaver`) | `app/jobs.py` + `app/redis_checkpoint.py` |
| **Observabilidad activa** (trazas por nodo a Arize Phoenix vía OpenInference) | `app/observability.py` |
| **Costo por ejecución en USD** (calculado desde los tokens, con tabla de precios y override por entorno) | `app/costos.py` |
| **Human-in-the-loop** (pausa obligatoria para aprobar la acción crítica) | `app/hitl.py` + `app/graph.py` |
| **Suite de tests con pytest** (endpoints, máquina de estados, HITL y costos) + CI | `tests/` + `.github/workflows/ci.yml` |

---

## 1. Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/tasks` | Encola la consulta y **devuelve `job_id` de inmediato** (HTTP 202). No bloquea. |
| `GET` | `/tasks/{job_id}` | Estado del job (solo lee Redis): `PENDING · RUNNING · AWAITING_APPROVAL · DONE · FAILED`. Incluye `costo` (tokens + USD acumulado). |
| `POST` | `/tasks/{job_id}/approve` | Resuelve la pausa HITL: `{"aprobado": true}` reanuda, `{"aprobado": false}` cierra sin calcular. |
| `GET` | `/health` | Salud + estado de Redis + backend de observabilidad. |
| `GET` | `/docs` | Swagger UI (interactivo). |

**Por qué no bloquea el event loop:** los endpoints solo tocan Redis (operación
rápida) y delegan el trabajo pesado —invocar el grafo y el LLM— a
`BackgroundTasks`, que FastAPI corre en un *threadpool*. El event loop nunca
espera a un LLM (ver "Errores comunes a evitar" de la consigna).

### Ciclo de vida de un job

```
POST /tasks ─► PENDING ─► RUNNING ─┬─► DONE
                                   ├─► FAILED               (excepción capturada)
                                   └─► AWAITING_APPROVAL ─► (POST /approve) ─► RUNNING ─► DONE
```

## 2. El grafo multi-agente + gate HITL

```mermaid
graph TD;
    __start__([start]):::first
    supervisor(supervisor)
    investigador(investigador)
    aprobacion[["aprobacion (HITL · interrupt)"]]
    analista(analista)
    sintesis(sintesis)
    __end__([end]):::last
    __start__ --> supervisor;
    supervisor -.-> investigador;
    supervisor -.-> aprobacion;
    supervisor -.-> sintesis;
    aprobacion -.->|aprobado| analista;
    aprobacion -.->|rechazado| sintesis;
    investigador --> supervisor;
    analista --> supervisor;
    sintesis --> __end__;
    classDef first fill-opacity:0
    classDef last fill:#bfb6fc
```

La topología *hub-and-spoke* del M6 se mantiene intacta. La **única diferencia**
es el nodo `aprobacion`: el Supervisor ya no manda al analista directamente, sino
que pasa por el gate HITL.

**¿Cuál es la acción "crítica"?** La ejecución del **analista**: es el único
especialista que corre una herramienta de cálculo (en un sistema real, con costo
de tokens por cada llamada). Antes de dejarlo correr, el grafo se detiene con
`interrupt()` de LangGraph y espera aprobación humana. La aprobación se registra
en el estado (`aprobacion_analista`), así un refinamiento posterior del analista
no vuelve a pausar (se aprueba una sola vez por tarea).

## 3. Cómo levantar todo

### Opción A — Docker Compose (recomendada, todo en un comando)

```bash
cd entregable7
cp .env.example .env          # completá la API key de UN proveedor
docker compose up --build
```

Levanta tres servicios: **Redis Stack** (`:6379`, con RediSearch que necesita el
`RedisSaver`), **Arize Phoenix** (`:6006`) y la **API** (`:8000`).

### Opción B — Local (venv) + Redis y Phoenix en contenedores

```bash
cd entregable7
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env          # completá la API key de UN proveedor

# Redis STACK (no el redis común: hace falta RediSearch) y Phoenix:
docker run -d --name e7-redis   -p 6379:6379 redis/redis-stack:7.4.0-v0
docker run -d --name e7-phoenix -p 6006:6006 arizephoenix/phoenix:version-7.9.0

uvicorn app.main:app --reload --port 8000
```

Sin `uv`: `python -m venv .venv` y `.venv/Scripts/pip install -r requirements.txt`.
Requiere **Python 3.12**. Las claves viven solo en `.env` (ignorado por git).

| Variable | Default | Para qué sirve |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini`, `openai` (o compatible, ej. Groq) o `anthropic` |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis **Stack** (jobs + checkpoints) |
| `OBSERVABILITY` | `phoenix` | `phoenix`, `langsmith` o `none` |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006` | Colector OTLP de Phoenix |
| `LLM_MAX_RETRIES` | `6` | Reintentos con backoff ante 429 del proveedor |
| `ORQ_MAX_PASOS` | `8` | Tope duro anti "supervisor infinito" |

## 4. Probar la API

### Flujo completo con `curl`

```bash
# 1) Encolar una tarea que requiere cálculo (disparará el HITL)
curl -X POST localhost:8000/tasks -H "Content-Type: application/json" \
  -d '{"pregunta":"Un empleado con 12 años de antigüedad: confirmá los días de vacaciones por tramo y calculá el total."}'
# -> {"job_id":"abc123...","estado":"PENDING"}

# 2) Consultar estado (polling). En algún momento devuelve AWAITING_APPROVAL
curl localhost:8000/tasks/abc123
# -> {"estado":"AWAITING_APPROVAL","aprobacion":{"tipo":"aprobacion_requerida","motivo":"..."}}

# 3) Aprobar la acción crítica -> el grafo reanuda y termina
curl -X POST localhost:8000/tasks/abc123/approve -H "Content-Type: application/json" \
  -d '{"aprobado":true}'

# 4) Consultar hasta DONE
curl localhost:8000/tasks/abc123
# -> {"estado":"DONE","resultado":{"respuesta_final":"... 231 días ...","agentes_en_orden":["investigador","analista"]}}
```

### Las 5 peticiones concurrentes (prueba de carga)

```bash
python prueba_carga.py --n 5        # lanza 5 en paralelo y auto-aprueba las pausas HITL
python prueba_carga.py --n 5 --no-approve   # las deja pausadas a propósito
```

Mientras corre, abrí **Phoenix en http://localhost:6006** y sacá las capturas
(trazas por nodo, costo por ejecución y latencia p95). Van en `screenshots/`
(ver `screenshots/README.md` para la lista exacta).

> **Nota sobre los tiers gratuitos.** 5 ejecuciones multi-agente concurrentes
> hacen muchas llamadas al LLM y **superan el límite de requests/minuto de los
> tiers gratuitos** (Groq: 8000 tokens/min; Gemini free: 5 req/min y 20 req/día).
> El `LLM_MAX_RETRIES` con backoff absorbe parte de esos 429; lo que no entra se
> marca `FAILED` (ver §6). Para una corrida con las 5 en `DONE`, usá una API key
> de tier pago o bajá la concurrencia. Las trazas se registran en Phoenix igual,
> tanto para los jobs `DONE` como para los `FAILED`.

## 5. Observabilidad

`app/observability.py` inicializa el backend según `OBSERVABILITY`:

- **`phoenix`** (default): `phoenix.otel.register(...)` + `LangChainInstrumentor`
  de OpenInference. Instrumenta **automáticamente** toda llamada de LangChain /
  LangGraph, así cada nodo del grafo (supervisor, investigador, analista,
  síntesis) y cada llamada al LLM aparecen como *spans* con sus tokens y
  latencia. Phoenix calcula el **costo por ejecución** a partir de los tokens.
- **`langsmith`**: no instrumenta nada; LangChain emite las trazas con las
  variables `LANGSMITH_API_KEY` / `LANGCHAIN_TRACING_V2`.
- **`none`**: desactiva la observabilidad (tests/CI).

### Costo por ejecución en USD (no solo latencia)

Phoenix calcula el costo en dólares **solo si conoce el precio del modelo**: con un
modelo de Groq (`openai/gpt-oss-20b`) mostraba los **tokens** pero no el USD. Para
cerrar ese hueco, `app/costos.py` **calcula el costo nosotros mismos a partir de
los tokens**:

1. El worker corre el grafo con un `UsageMetadataCallbackHandler` (de
   langchain-core) que suma los tokens de **todas** las llamadas al LLM por modelo
   —incluidas las internas de los agentes ReAct, no solo el mensaje final—.
2. `resumen_de_costo(...)` convierte esos tokens en USD con una **tabla de precios**
   (`PRECIOS_USD_POR_1M`, editable) que matchea por el prefijo más específico del
   nombre del modelo.
3. El costo se **acumula a través de la pausa HITL** (ejecución + reanudación) y se
   expone en `GET /tasks/{id}` bajo `costo`:

```json
"costo": {
  "input_tokens": 41113, "output_tokens": 18601, "total_tokens": 59714,
  "costo_usd": 0.013412, "moneda": "USD", "precio_fuente": "tabla",
  "modelos": { "openai/gpt-oss-20b": { "...": "...", "costo_usd": 0.013412 } }
}
```

Si el proveedor publica su propia tarifa (o querés forzar un precio), se setea por
entorno sin tocar código:

| Variable | Para qué sirve |
|---|---|
| `COSTO_USD_INPUT_POR_1M` | Precio de entrada (USD por 1M de tokens) para **cualquier** modelo |
| `COSTO_USD_OUTPUT_POR_1M` | Precio de salida (USD por 1M de tokens) para **cualquier** modelo |

Si un modelo no está en la tabla ni hay override, se reportan los tokens y
`costo_usd` queda en `null` (nunca rompe). La evidencia del cálculo sobre la
corrida real está en **`evidencia/costos_salida.txt`**.

## 6. Manejo de errores (job → FAILED)

El worker envuelve **toda** la ejecución en `try/except` (`app/worker.py`):
cualquier excepción del agente en segundo plano (incluido un 429 que agotó los
reintentos) se captura y el job pasa a `FAILED` en Redis con el mensaje de error.
Así el cliente que hace *polling* nunca queda en un loop infinito esperando un
job que murió en silencio (error común señalado en la consigna).

## 7. Tests automatizados (pytest) + CI

La validación principal es una **suite de pytest** (`tests/`), integrada a un flujo
de **CI estándar** (`.github/workflows/ci.yml`): corre en cada push/PR. Como todo
lo demás acá, es **100% offline** —sin Redis, sin red y sin API keys—, apoyándose
en los mismos puntos de inyección que usa la app:

- `JobStore` recibe el cliente Redis por parámetro → un `FakeRedis` en memoria.
- `construir_grafo(...)` recibe sus piezas → dobles deterministas (sin LLM).
- Los endpoints piden `store` y `grafo` vía `Depends` → se sustituyen con
  `app.dependency_overrides`; el `TestClient` corre los endpoints reales (incluidas
  las `BackgroundTasks`) sin levantar el `lifespan`.

```bash
uv pip install -r requirements-dev.txt   # trae pytest
pytest                                    # 49 tests
pytest -v                                 # con nombre de cada caso
```

| Archivo de test | Qué cubre |
|---|---|
| `tests/test_api.py` | Endpoints: `POST /tasks` (202), flujo HITL completo por HTTP, `404`/`409`/`422`, `/health`. |
| `tests/test_jobs.py` | **Máquina de estados** del `JobStore` y acumulación del costo. |
| `tests/test_worker.py` | Worker: excepción → `FAILED`, interrupt → `AWAITING`, reanudar → `DONE`, registro del costo desde el callback. |
| `tests/test_hitl_graph.py` | Topología del grafo, gate `aprobacion`, pausa/aprobar/rechazar, "una sola aprobación", guardas y `MAX_PASOS`. |
| `tests/test_costos.py` | Tabla de precios, normalización de modelos, override por entorno, costo en USD. |
| `tests/test_herramientas.py` | Herramientas del M6, incluida la seguridad de la calculadora. |

La salida completa está en **`evidencia/pytest_salida.txt`** (49 en `PASSED`).

### Complemento: `verificar.py` (un solo comando, sin instalar pytest)

`verificar.py` mantiene los mismos chequeos deterministas como script autónomo
(útil como evidencia reproducible por cualquiera que clone el repo, sin dependencias
de desarrollo). Su salida está en **`evidencia/verificar_salida.txt`**.

```bash
python verificar.py
```

## 8. Estructura

```
entregable7/
├── app/
│   ├── main.py             # FastAPI: POST /tasks, GET /tasks/{id}, POST /approve, /health
│   ├── graph.py            # grafo del M6 + gate HITL + compile con checkpointer
│   ├── worker.py           # corre el grafo en background y actualiza Redis (incl. FAILED)
│   ├── observability.py    # init de Phoenix / LangSmith (OpenInference)
│   ├── costos.py           # costo en USD por ejecución, calculado desde los tokens
│   ├── hitl.py             # nodo de aprobación humana (interrupt + ruteo)
│   ├── jobs.py             # JobStore sobre Redis (máquina de estados + costo del job)
│   ├── redis_checkpoint.py # fábrica del RedisSaver (checkpoints de LangGraph)
│   ├── schemas.py          # modelos Pydantic de la API
│   ├── config.py           # entorno, fábrica de LLM, Redis, observabilidad
│   ├── state.py            # AgentState (M6) + campo aprobacion_analista (HITL)
│   ├── supervisor.py       # Supervisor del M6 (decisión estructurada + guardas)
│   └── agents/             # investigador, analista, herramientas (M6)
├── tests/                  # suite de pytest (offline): api, jobs, worker, hitl, costos
│   ├── conftest.py         # FakeRedis + dobles del grafo + fixtures
│   ├── test_api.py         # endpoints vía TestClient con dependency_overrides
│   ├── test_jobs.py        # máquina de estados + costo
│   ├── test_worker.py      # background: FAILED / AWAITING / DONE + costo
│   ├── test_hitl_graph.py  # topología + flujo HITL + guardas
│   ├── test_costos.py      # cálculo de costo en USD
│   └── test_herramientas.py
├── prueba_carga.py         # 5 peticiones concurrentes + auto-aprobación HITL
├── verificar.py            # chequeos deterministas como script autónomo (offline)
├── pytest.ini              # config de pytest (pythonpath, testpaths)
├── requirements.txt
├── requirements-dev.txt    # requirements.txt + pytest
├── Dockerfile
├── docker-compose.yml      # app + redis-stack + phoenix
├── .env.example
├── evidencia/              # salidas de pytest, verificar.py, costo USD y prueba de carga
└── screenshots/            # capturas del dashboard (observabilidad)

# CI del repo: ../.github/workflows/ci.yml  (corre pytest + verificar.py en cada push/PR)
```

## 9. Checklist de la rúbrica

- [x] Endpoint asíncrono que encola y devuelve `job_id` sin bloquear el event loop → `POST /tasks` + `BackgroundTasks`.
- [x] Estado del job en Redis, incluido el paso a `FAILED` ante excepción → `app/jobs.py` + `app/worker.py`.
- [x] Checkpoints de LangGraph en Redis (`RedisSaver`) → `app/redis_checkpoint.py`.
- [x] Trazas por nodo en el dashboard (Phoenix) → `app/observability.py` + `screenshots/`.
- [x] Costo por ejecución y latencia p95 de las 5 concurrentes → `prueba_carga.py` + `screenshots/`.
- [x] **Costo por ejecución en USD** calculado desde los tokens → `app/costos.py` + `GET /tasks/{id}.costo` + `evidencia/costos_salida.txt`.
- [x] Nodo Human-in-the-loop que pausa hasta aprobación externa → `app/hitl.py` + `POST /approve`.
- [x] README con pasos para Redis + API + las 5 concurrentes, y `requirements.txt`.
- [x] **Tests automatizados (pytest) + CI** → `tests/` + `.github/workflows/ci.yml` + `evidencia/pytest_salida.txt`.
- [x] **Mejora:** salida de `verificar.py` incluida en `evidencia/`.
```
