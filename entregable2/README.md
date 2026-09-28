# Entregable 2 · Pipeline de procesamiento validado

Módulo 2 — *Encadenamiento lógico: LangChain y LCEL*.

Pipeline de **Extracción de Entidades Técnicas**: recibe un párrafo de texto
libre (un log de error, una descripción de arquitectura) y devuelve un
objeto validado con Pydantic — tecnologías mencionadas, nivel de
criticidad y un resumen técnico — usando una cadena LCEL con salida
estructurada forzada y reintento automático.

```python
from chain import process_text

resultado = await process_text(
    "Nuestra API en FastAPI está devolviendo timeouts intermitentes. "
    "El caché en Redis se satura y las conexiones a PostgreSQL se agotan.",
    provider="gemini",
)
print(resultado.model_dump_json(indent=2))
```

Cambiar de proveedor es cambiar `LLM_PROVIDER=openai` por `anthropic` o
`gemini` en el `.env`: la fábrica de modelos (`chain.get_model`) reutiliza el
mismo patrón del [Módulo 1](../entregable1) — una función central que resuelve
el proveedor a partir de variables de entorno.

---

## 1. Puesta en marcha

```bash
cd entregable2
uv venv --python 3.12
uv pip install -r requirements.txt

cp .env.example .env     # completá al menos una API key

python verificar.py         # criterios de aceptación (offline por defecto)
python verificar.py --real  # además ejercita el camino real (requiere API key)
python main.py              # prueba real: texto claro + prueba de estrés
python main.py anthropic    # fuerza un proveedor puntual
```

Sin `uv`, el equivalente es `python -m venv .venv` y
`.venv/Scripts/pip install -r requirements.txt` (`.venv/bin/pip` en Linux/macOS).

## 2. Variables de entorno

Todas viven en `.env` (ignorado por git); el ejemplo completo está en
`.env.example`. Alcanza con **una** de las tres claves.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LLM_PROVIDER` | no | `openai` | Proveedor: `openai`, `anthropic` o `gemini` |
| `OPENAI_API_KEY` | sí, para OpenAI | — | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Modelo de OpenAI |
| `OPENAI_BASE_URL` | no | — | Endpoint alternativo compatible con OpenAI (ej. Groq) |
| `ANTHROPIC_API_KEY` | sí, para Anthropic | — | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `ANTHROPIC_MODEL` | no | `claude-haiku-4-5-20251001` | Modelo de Anthropic (ajustá al que tengas habilitado) |
| `GEMINI_API_KEY` | sí, para Gemini | — | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (tier gratuito) |
| `GEMINI_MODEL` | no | `gemini-3.8-flash` | Modelo de Gemini (ajustá al disponible en tu key) |
| `LLM_MAX_RETRIES` | no | `3` | Intentos de `.with_retry()` ante un JSON mal formado o incompleto |

## 3. Diseño

```
entregable2/
├── schemas.py     # EntidadesTecnicas (Pydantic) + enum NivelCriticidad
├── chain.py        # get_model() · PROMPT · build_chain() · process_text()
├── main.py          # mini-script de prueba asíncrono
└── verificar.py      # criterios de aceptación, sin llamadas reales
```

**`schemas.py` es el contrato.** `EntidadesTecnicas` exige `tecnologias` no
vacía (y limpia duplicados/espacios con un `field_validator`),
`nivel_de_criticidad` restringido al enum `baja | media | alta` — nunca un
string libre que el modelo podría inventar como `"high"` — y `resumen_tecnico`
con un mínimo de 10 caracteres.

**El prompt es modular.** `ChatPromptTemplate` declara `{texto}` y `{formato}`
como variables de entrada; no hay f-strings de Python hardcodeadas dentro de
la cadena, así LangChain gestiona la sustitución y el prompt se puede testear
o versionar aparte del texto de entrada.

**La cadena LCEL:**

```python
chain = (prompt | model.with_structured_output(EntidadesTecnicas)).with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)
```

`model.with_structured_output(EntidadesTecnicas)` fuerza al modelo a devolver
directamente una instancia validada (no texto crudo que haya que parsear a
mano). `.with_retry()` es la lógica de resiliencia pedida por la consigna: si
el LLM devuelve un JSON mal formado o incompleto (por ejemplo, cortado por
`finish_reason="length"`), la validación de Pydantic falla dentro de
`with_structured_output`, LangChain relanza esa falla como excepción y
`.with_retry()` reintenta la llamada completa con backoff exponencial y
*jitter*, antes de dejarla pasar a la capa de arriba.

**`process_text()` es la función asíncrona pedida por la consigna:**

```python
async def process_text(text: str, provider: str = "openai") -> EntidadesTecnicas:
    ...
    resultado = await chain.ainvoke({"texto": text, "formato": FORMATO_INSTRUCCIONES})
    ...
```

Loguea el inicio del procesamiento, el resultado validado y, si se agotan los
reintentos, el error final antes de relanzarlo — nunca lo traga en silencio.

## 4. Prueba de estrés

`main.py` corre, además del texto claro de ejemplo, un texto ambiguo sin
detalles técnicos (`"El sistema anda medio raro últimamente, no sé bien qué
está pasando."`). Corrida real contra Gemini (`gemini-3.8-flash`):

```json
{
  "tecnologias": ["Sistema general"],
  "nivel_de_criticidad": "baja",
  "resumen_tecnico": "Se reporta un comportamiento anómalo indeterminado en la plataforma. Se recomienda realizar un diagnóstico preventivo y revisión de logs para identificar la causa raíz."
}
```

* El modelo **no alucina tecnologías inexistentes**: ante la falta de nombres
  concretos, devuelve un valor genérico (`"Sistema general"`) en vez de
  inventar un stack específico. El `field_validator` de todas formas
  rechazaría una lista vacía, así que el modelo no tiene margen para omitir
  el campo.
* `nivel_de_criticidad` cayó en `"baja"` ante la falta de señales de
  gravedad — el texto no menciona producción, usuarios afectados ni ningún
  indicio de urgencia.
* Cuando el modelo devuelve un campo faltante o un valor fuera del enum,
  `with_structured_output` lo detecta antes de llegar a la aplicación (la
  validación de Pydantic falla) y `.with_retry()` reintenta la llamada
  completa; si los reintentos se agotan, `process_text()` propaga la
  excepción en lugar de devolver un objeto a medio validar. En la misma
  corrida se observó esta resiliencia en la capa de red: la API de Gemini
  devolvió dos `503 Service Unavailable` por saturación antes de responder
  `200 OK`, y el pipeline se recuperó sin intervención.

## 5. Errores comunes que el entregable evita

| Error de la consigna | Cómo se evita acá |
|---|---|
| Ignorar el `finish_reason` | `with_structured_output` valida contra `EntidadesTecnicas` antes de devolver nada; una respuesta cortada por límite de tokens no pasa la validación de Pydantic y dispara el reintento en vez de devolver un objeto incompleto |
| Hardcoding de prompts | `ChatPromptTemplate` con `{texto}`/`{formato}` como variables; cero f-strings dentro de la cadena |
| `nivel_de_criticidad` como string libre | Enum `NivelCriticidad(str, Enum)`: sólo `baja`, `media` o `alta` |
| Reintentar sin backoff | `.with_retry(wait_exponential_jitter=True)` en vez de un `for` manual |

## 6. Ejemplo de salida

```json
{
  "tecnologias": [
    "FastAPI",
    "Redis",
    "PostgreSQL"
  ],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "API con caché en Redis y persistencia en PostgreSQL; cuello de botella en conexiones concurrentes."
}
```

## 7. Estado de la verificación

`python verificar.py` cubre los criterios sin gastar cuota por defecto:
validación Pydantic (vacíos, duplicados, enum, longitud mínima), la fábrica de
modelos (`get_model` rechaza proveedores no soportados y detecta API keys
faltantes), que `build_chain()` devuelve un `Runnable` envuelto en
`RunnableRetry`, y la resiliencia ante un JSON incompleto usando un
`GenericFakeChatModel` de `langchain_core` como doble de prueba (se
recupera al segundo intento; propaga el error si nunca valida). Ese doble usa
un `PydanticOutputParser` para poder simular la salida sin red; el **camino de
producción real** (`with_structured_output` + `.with_retry`) se valida en la
sección 4, que corre `chain.process_text()` contra el proveedor configurado
cuando hay una API key disponible (`python verificar.py --real` la fuerza). Si
falta la key —o el proveedor responde con quota agotada / rate limit—, la
sección 4 se saltea (`SKIP`) sin marcar el criterio como incumplido: es un
problema de entorno, no de la lógica del pipeline.

La ruta real de red se probó punta a punta con `python main.py gemini`
(`gemini-3.8-flash`, tier gratuito): el texto claro devolvió un objeto con
`nivel_de_criticidad="alta"` y las tres tecnologías mencionadas (FastAPI,
Redis, PostgreSQL), y la prueba de estrés con el texto ambiguo se resolvió en
`"baja"` sin lanzar una excepción — ver §4 para las salidas completas. En esa
misma corrida la API de Gemini devolvió dos `503` por saturación, que se
resolvieron solos antes de la respuesta final.

Versiones con las que se probó: `langchain-core` 1.6.4, `langchain-openai`
1.6.4, `langchain-anthropic` 1.7.3, `langchain-google-genai` 4.4.0,
`pydantic` 2.13.5, CPython 3.12.14.
