# Ejercicio 1 · Orquestador Concurrente de Modelos

Módulo 1 — *La interfaz base: conexión y abstracción de LLMs*.
Implementación en **Python 3.12** de un orquestador que consulta varios modelos
de lenguaje de forma concurrente, con control de latencia (`asyncio.timeout`) y
control de flujo (`asyncio.Semaphore`).

Los proveedores **Gemini** (`gemini-3.6-flash`) y **Groq** (`openai/gpt-oss-20b`)
se consultan de verdad por HTTP asíncrono; ambos tienen tier gratuito. El tercer
proveedor, `local_llama`, queda simulado con una latencia de 3 s que excede el
timeout a propósito, para que el manejo de `TimeoutError` sea observable en cada
ejecución.

> Sin API keys el proyecto **igual corre**: los tres proveedores caen a modo
> simulado con `await asyncio.sleep()`.

---

## 1. Requisitos

* Python 3.12 (el proyecto fija `requires-python = ">=3.12,<3.13"`)
* Conexión a internet sólo si querés llamadas reales

## 2. Instalación

### Opción A — con [uv](https://docs.astral.sh/uv/) (recomendada)

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
```

### Opción B — con venv estándar

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\activate        # Windows PowerShell
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
```

## 3. Configuración

```bash
cp .env.example .env    # en PowerShell: Copy-Item .env.example .env
```

Completá las claves en `.env`:

| Variable | Dónde se obtiene | Costo |
|---|---|---|
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | gratis (tier free) |
| `GROQ_API_KEY` | <https://console.groq.com/keys> | gratis (tier free) |

Modelos por defecto: `GEMINI_MODEL=gemini-3.6-flash` y
`GROQ_MODEL=openai/gpt-oss-20b`. Ambos proveedores dan de baja modelos con
frecuencia; si aparece un `HTTP 404 - modelo inexistente o dado de baja`,
consultá el catálogo vigente de tu cuenta:

```bash
curl -H "x-goog-api-key: $GEMINI_API_KEY" https://generativelanguage.googleapis.com/v1beta/models
curl -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models
```

Parámetros ajustables en el mismo archivo: `TOTAL_TIMEOUT_S` (2.0),
`SEMAPHORE_LIMIT` (2), `BURST_SIZE` (10), `LOCAL_LLAMA_LATENCY_S` (3.0).

`.env` está en `.gitignore`; las claves nunca se imprimen en logs ni en los
mensajes de error (`Settings.describe()` sólo informa `real` / `simulado`).

## 4. Ejecución

```bash
python -m orquestador                   # ambos escenarios
python -m orquestador --scenario fanout # sólo gather + timeout
python -m orquestador --scenario burst  # sólo semáforo
python -m orquestador --prompt "¿Qué es la entropía?" --timeout 4
python verificar.py                     # chequea los criterios de aceptación
```

---

## 5. Cómo se resuelve cada punto de la consigna

| # | Consigna | Dónde | Implementación |
|---|---|---|---|
| 1 | Entorno con Python 3.12+ | `pyproject.toml`, `.python-version` | `requires-python = ">=3.12,<3.13"` |
| 2 | Tres corrutinas que simulan modelos | `orquestador/providers.py` | `gemini_call`, `groq_call`, `local_llama_call`; latencias distintas vía `await asyncio.sleep()` |
| 3 | Disparo simultáneo con `asyncio.gather` | `orchestrator.py::fan_out_with_timeout` | `asyncio.create_task` + `asyncio.gather` |
| 4 | `asyncio.timeout` de 2 s sobre la ejecución total | `orchestrator.py::fan_out_with_timeout` | `async with asyncio.timeout(2.0)` y captura de `TimeoutError` |
| 5 | `asyncio.Semaphore`: 10 disparos, 2 concurrentes | `orchestrator.py::throttled_burst` | `asyncio.Semaphore(2)` + `ConcurrencyTracker` que registra el pico real |

### Detalle del punto 4

La consigna pide un timeout sobre la **ejecución total**, no por llamada.
`asyncio.timeout` cancela las tareas pendientes al vencer, así que las llamadas
se crean como `Task` explícitas: después de capturar la excepción, cada tarea se
inspecciona por separado (`_harvest`) y se distingue entre *terminó a tiempo*,
*falló* y *fue cancelada por timeout*. Resultado: se conservan los resultados
parciales y **el programa sigue**.

En el escenario B el timeout es **por llamada**, porque ahí el objetivo es que
una llamada lenta no arrastre al lote entero — complementado con
`gather(..., return_exceptions=True)`.

---

## 6. Criterios de aceptación — evidencia

### ✅ El código no usa `time.sleep` (bloqueante)

Toda espera pasa por `await asyncio.sleep()` (`providers.py::_simulate_latency`)
y toda I/O de red por `httpx.AsyncClient`. `verificar.py` lo comprueba con un
escaneo de los módulos.

### ✅ Las tareas inician casi al mismo tiempo

El logger usa un reloj **relativo al arranque** (`logging_setup.py`), de modo que
la simultaneidad se ve directamente en la traza:

```
t+000.087s | INFO | orquestador.core      | Disparadas 3 llamadas en paralelo (timeout total=2.0s)
t+000.087s | INFO | orquestador.providers | ▶ gemini        inicia llamada (real, modelo=gemini-3.6-flash)
t+000.122s | INFO | orquestador.providers | ▶ groq          inicia llamada (real, modelo=openai/gpt-oss-20b)
t+000.123s | INFO | orquestador.providers | ▶ local_llama   inicia llamada (simulado, 3.0s, modelo=llama-3.2-3b-local)
```

Las tres arrancan en el mismo milisegundo; `verificar.py` mide la dispersión y
exige que sea menor a 50 ms.

### ✅ Se maneja correctamente `TimeoutError`

Corrida real contra ambas APIs:

```
t+002.099s | ERROR | orquestador.core | ⏱ TimeoutError: la ejecución superó 2.0s.
                                        Se cancelan las llamadas pendientes y el
                                        programa CONTINÚA con resultados parciales.

  PROVEEDOR      ESTADO    LATENCIA  DETALLE
  --------------------------------------------------------------------------
  gemini         OK           1.33s  La entropía es una medida física del grado de desorden…
  groq           OK           0.40s  La entropía mide el grado de desorden o incertidumbre…
  local_llama    FALLÓ        0.00s  ERROR: timeout > 2.0s

  Tiempo total del proceso: 2.10s
  El programa terminó de forma controlada pese a los timeouts.
```

El proceso termina con código de salida **0**: un timeout es un caso contemplado
del diseño, no un crash.

### ✅ Extra: el semáforo se verifica, no se asume

`ConcurrencyTracker` registra cada entrada y salida de la sección crítica y
reporta el pico real de concurrencia:

```
    t+000.067s IN  #01 groq (activos=1)
    t+000.102s IN  #02 local_llama (activos=2)
    t+000.622s OUT #01 groq (activos=1)
    t+000.622s IN  #03 gemini (activos=2)
    t+002.078s OUT #03 gemini (activos=1)
    t+002.078s IN  #04 groq (activos=2)
    ...
  Concurrencia máxima observada: 2 (límite 2) → ✓ CUMPLE
```

Nunca hay tres `IN` sin un `OUT` intercalado: el semáforo hace de portero real,
no de decoración.

Salida de `verificar.py`:

```
  [PASS] 1. No se usa time.sleep (bloqueante)
  [PASS] 2. Las tareas inician casi simultáneamente
         dispersión entre arranques: 0.0 ms (umbral 50 ms)
  [PASS] 3. TimeoutError manejado + semáforo activo
         timeouts capturados=True, pico de concurrencia=2/2, resultados devueltos=10/10
  3/3 criterios cumplidos
```

---

## 7. Estructura

```
ejercicio1/
├── orquestador/
│   ├── __init__.py
│   ├── __main__.py         # CLI: único punto de entrada con asyncio.run()
│   ├── config.py           # Settings desde .env, sin filtrar secretos
│   ├── logging_setup.py    # logger con reloj relativo al arranque
│   ├── providers.py        # gemini_call / groq_call / local_llama_call
│   └── orchestrator.py     # gather + timeout + semáforo
├── verificar.py            # chequeo de los criterios de aceptación
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 8. Anti-patrones evitados (Módulo 1, sección 5)

* **Bloqueo del event loop**: nada de `time.sleep` ni de clientes HTTP síncronos
  dentro de corrutinas.
* **Fuga de excepciones**: cada proveedor captura sus errores y devuelve un
  `ModelResult(ok=False)` estructurado. `CancelledError` se re-lanza a propósito,
  porque tragarla rompería el mecanismo de `asyncio.timeout`.
* **Fan-out sin control**: nunca se disparan N llamadas libres contra una API con
  rate limit; el semáforo acota la concurrencia (`429: Too Many Requests`).
* **Fuga de secretos**: las API keys no aparecen en logs ni en los mensajes de
  error HTTP, que se recortan y normalizan en `_http_error` (401/403/404/429/503
  tienen mensajes propios).
