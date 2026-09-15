# Entregable 1 · Cliente de LLM robusto y asíncrono

Módulo 1 — *La interfaz base: conexión y abstracción de LLMs*.

Cliente unificado en **Python 3.12** que expone OpenAI, Anthropic y Gemini
detrás de una misma interfaz asíncrona, con validación Pydantic en la
frontera, streaming token a token y errores capturados como dato en vez de
excepciones que rompan el proceso.

```python
from llm_client import AsyncLLMManager

async with AsyncLLMManager.from_env() as llm:          # proveedor desde .env
    respuesta = await llm.generate("¿Qué es la entropía?")
    print(respuesta.content if respuesta.ok else respuesta.error)

    async for chunk in llm.stream("¿Qué es la entropía?"):
        print(chunk.delta, end="", flush=True)
```

Cambiar de proveedor es cambiar `LLM_PROVIDER=openai` por `anthropic` o
`gemini`: no hay una sola línea de código de aplicación que dependa del SDK.

---

## 1. Puesta en marcha

```bash
cd entregable1
uv venv --python 3.12
uv pip install -r requirements.txt

cp .env.example .env     # completá al menos una API key

python verificar.py      # criterios de aceptación, sin gastar cuota
python main.py           # prueba real: modo normal + streaming
```

Sin `uv`, el equivalente es `python -m venv .venv` y
`.venv/Scripts/pip install -r requirements.txt` (`.venv/bin/pip` en Linux/macOS).

## 2. Variables de entorno

Todas viven en `.env` (ignorado por git); el ejemplo completo está en
`.env.example`. Alcanza con **una** de las tres claves para que `main.py` corra:
el script detecta qué proveedores tienen credenciales y prueba sólo esos.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LLM_PROVIDER` | no | `openai` | Proveedor: `openai`, `anthropic` o `gemini` |
| `OPENAI_API_KEY` | sí, para OpenAI | — | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Modelo de OpenAI |
| `OPENAI_BASE_URL` | no | — | Endpoint alternativo compatible con OpenAI (ver §6) |
| `ANTHROPIC_API_KEY` | sí, para Anthropic | — | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `ANTHROPIC_MODEL` | no | `claude-opus-5` | Modelo de Anthropic |
| `ANTHROPIC_EFFORT` | no | `low` | Profundidad del razonamiento: `low`…`max` |
| `GEMINI_API_KEY` | sí, para Gemini | — | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (tier gratuito) |
| `GEMINI_MODEL` | no | `gemini-3.6-flash` | Modelo de Gemini |
| `GEMINI_BASE_URL` | no | capa OpenAI de Google | Endpoint alternativo |
| `LLM_TEMPERATURE` | no | `0.7` | 0 a 2 (Anthropic acepta hasta 1) |
| `LLM_MAX_TOKENS` | no | `1024` | Techo de tokens de salida, > 0 |
| `LLM_TIMEOUT_S` | no | `30` | Timeout **por intento** |
| `LLM_MAX_RETRIES` | no | `2` | Reintentos ante 429 / 5xx / red |

## 3. Qué hace `main.py`

Para cada proveedor con clave cargada, pregunta *«¿Qué es la entropía?»*:

1. **Modo normal** — una respuesta completa, con latencia, tokens y motivo de
   corte.
2. **Modo streaming** — los fragmentos se imprimen a medida que llegan.

Y cierra con una **demostración de manejo de errores**: la misma pregunta con
una API key inválida a propósito. Las dos llamadas devuelven un error
estructurado, se imprime, y el programa termina normalmente en vez de tirar un
traceback. Ese es el criterio que la consigna llama *fuga de excepciones*.

`verificar.py` cubre los mismos criterios sin tocar la red: reemplaza los
proveedores por dobles que simulan respuestas y fallos, así se puede validar la
estructura antes de tener claves.

## 4. Diseño

```
entregable1/
├── main.py                  # script de prueba: normal + streaming + errores
├── verificar.py             # criterios de aceptación, sin llamadas reales
└── llm_client/
    ├── schemas.py           # Pydantic: ChatMessage, ModelConfig, ModelResponse…
    ├── errors.py            # clasificación de excepciones + backoff
    ├── base.py              # BaseLLMClient (ABC): generate() / stream()
    ├── manager.py           # AsyncLLMManager: elige proveedor por config
    └── providers/
        ├── openai_client.py     # AsyncOpenAI
        ├── anthropic_client.py  # AsyncAnthropic
        └── gemini_client.py     # Gemini vía la capa compatible con OpenAI
```

**`schemas.py` primero.** Toda la frontera del sistema pasa por modelos
Pydantic, así un `temperature=3.5` falla al construir la configuración y no
cinco capas más abajo dentro del SDK. `ModelConfig` valida el rango 0–2 que
pide la consigna y además estrecha a 0–1 cuando el proveedor es Anthropic,
que no acepta más.

**La base concentra lo que no depende del SDK.** `BaseLLMClient` mide latencia,
aplica reintentos y captura errores; cada proveedor sólo implementa
`_raw_generate` y `_raw_stream`, que pueden lanzar excepciones con total
libertad. Agregar un proveedor nuevo es escribir esos dos métodos — y si el
proveedor ya habla el protocolo de OpenAI, ni siquiera eso (ver §6).

**Los errores son datos, no excepciones.** `generate()` devuelve siempre un
`ModelResponse`: si `ok` es `False`, `error` trae un `LLMError` con categoría
(`auth`, `rate_limit`, `timeout`, `connection`, `bad_request`, `server`),
mensaje, status HTTP e intentos realizados. En streaming, el fallo llega como
un `StreamChunk` final con `done=True` y `error` cargado, de modo que nunca
cruza una excepción por el `async for`.

**Sólo se reintenta lo que tiene sentido reintentar.** 429, 5xx, timeouts y
errores de red van con backoff exponencial y *jitter*; si el proveedor manda
la cabecera `retry-after`, se respeta. Una API key inválida o un 400 se
devuelven de inmediato: reintentarlos es quemar cuota para obtener el mismo
error. En streaming los reintentos se aplican **sólo antes del primer token**,
porque una vez que el consumidor recibió texto, reintentar duplicaría la
respuesta.

Los reintentos del SDK van en `max_retries=0` a propósito: la política es una
sola, la de `BaseLLMClient`, y no dos backoffs superpuestos sobre el mismo fallo.

## 5. Diferencias entre proveedores que el cliente absorbe

* **El prompt de sistema.** En OpenAI es un mensaje más; en Anthropic va en el
  parámetro `system`. `AnthropicClient` separa los turnos `system` del
  historial antes de armar el request, así el código de aplicación usa la misma
  lista de `ChatMessage` para los dos.

* **`temperature` en los modelos Claude actuales.** Opus 5, Sonnet 5, Opus
  4.7/4.8 y Fable 5 eliminaron los parámetros de *sampling*: mandarlos devuelve
  un **400**. Además, el SDK `anthropic` 1.x ya ni los expone en la firma de
  `messages.create()` (pasarlos como kwarg es un `TypeError`). La config los
  sigue validando porque la consigna lo pide, pero el cliente los omite en los
  modelos que no los aceptan y los inyecta vía `extra_body` en los que sí
  (Haiku 4.5, Sonnet 4.6, …).

* **Gemini contesta 400 donde los otros contestan 401.** Una clave inválida
  vuelve como `400 INVALID_ARGUMENT` con el texto *«Please pass a valid API
  key»*. Tal cual, el usuario leería `bad_request` y buscaría el problema en
  los parámetros; el clasificador detecta ese caso y lo reetiqueta como `auth`.

En la misma línea, `OpenAIClient` reintenta con `max_completion_tokens` cuando
el modelo destino rechaza `max_tokens`, que es lo que pasa con las familias
o-\* y GPT-5.

## 6. Gemini y otros endpoints compatibles con OpenAI

Google expone los modelos Gemini en un endpoint que habla el mismo protocolo
que la API de Chat Completions de OpenAI. Por eso `GeminiClient` **hereda de
`OpenAIClient`** y sólo cambia la URL base: no agrega ninguna dependencia y
son cuatro líneas de configuración en vez de una implementación paralela. Es
la prueba de que la abstracción paga.

El precio de esa decisión, que conviene tener presente: las funciones propias
de Gemini sin equivalente en el protocolo de OpenAI (`thinking_config`, las
`safety_settings` granulares) no quedan expuestas. Para eso haría falta el SDK
`google-genai` e implementar `_raw_generate` / `_raw_stream` directamente —
que es exactamente lo que la clase base deja abierto.

El mismo mecanismo sirve para cualquier otra API compatible. `OPENAI_BASE_URL`
apunta el `AsyncOpenAI` a donde haga falta, sin tocar código:

```bash
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=openai/gpt-oss-20b
OPENAI_API_KEY=<clave de Groq>
```

## 7. Errores comunes que el entregable evita

| Error de la consigna | Cómo se evita acá |
|---|---|
| Bloqueo del event loop | Sólo se usan `AsyncOpenAI` / `AsyncAnthropic` y `await`. `verificar.py` lo comprueba: tres llamadas concurrentes tardan ~0.2 s, no ~0.6 s |
| Fuga de excepciones | `generate()` y `stream()` no lanzan nunca; el fallo viaja en `ModelResponse.error` o en un `StreamChunk.error` |
| Diccionarios anidados crudos | Todo entra y sale como modelo Pydantic |
| Reintentar lo irreintentable | `RETRYABLE_KINDS` deja afuera `auth` y `bad_request` |
| Doble backoff | `max_retries=0` en ambos SDKs; la política de reintentos es una sola |

## 8. Estado de la verificación

`python verificar.py` cubre 28 criterios (validación, herencia, asincronía,
streaming, reintentos y clasificación de errores) y pasa completo.

La ruta real de red está probada con claves inválidas a propósito contra los
tres proveedores: OpenAI y Anthropic devuelven un 401 auténtico y Gemini un
400, y el cliente clasifica los tres como `auth` en modo normal y en
streaming, con el proceso terminando en código 0. Una corrida exitosa de punta
a punta requiere claves válidas.

Versiones con las que se probó: `anthropic` 1.5.0, `openai` 3.14.0,
`pydantic` 2.13.5, CPython 3.12.14.
