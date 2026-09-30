# Entregable 3 · Sistema de recuperación semántica local (RAG)

Módulo 2 — *Recuperación aumentada por generación con ChromaDB y LangChain/LCEL*.

Sistema **RAG End-to-End** sobre un corpus de políticas de una rentadora de
autos ficticia ("AutoRenta"): recibe una consulta, busca los fragmentos
relevantes en una base vectorial local (**ChromaDB**) y genera una respuesta
que usa **exclusivamente** esa información. Si el dato no está en los
documentos, el modelo responde que no lo tiene — no alucina.

```python
from rag_chain import get_rag_response

r = await get_rag_response(
    "¿Cuántos kilómetros diarios incluye la tarifa estándar de alquiler?"
)
print(r.respuesta)   # -> "200 km por día..."
print(r.fuentes)     # -> ['data/combustible_kilometraje.txt', ...]
```

**Embeddings 100% locales** (HuggingFace `all-MiniLM-L6-v2`): la recuperación
no necesita ninguna API key ni tiene costo. Solo la **generación** usa un LLM,
y el proveedor es configurable (`gemini` por defecto, o `openai` / `anthropic`)
reutilizando el patrón de fábrica de modelos del [Módulo 1](../entregable1) y el
[Módulo 2](../entregable2).

---

## 1. Puesta en marcha

```bash
cd entregable3
uv venv --python 3.12
uv pip install -r requirements.txt

cp .env.example .env         # completá GEMINI_API_KEY (o GOOGLE_API_KEY)

python ingesta.py            # 1) puebla ChromaDB (descarga el modelo la 1ª vez)
python main.py               # 2) corre las 2 pruebas (real + trampa)
python main.py --interactive # (opcional) chat interactivo sobre las políticas
python verificar.py          # criterios de aceptación (offline por defecto)
python verificar.py --real   # además corre el flujo RAG completo (requiere índice + key)
```

Sin `uv`, el equivalente es `python -m venv .venv` y
`.venv/Scripts/pip install -r requirements.txt` (`.venv/bin/pip` en Linux/macOS).

> La **primera** ejecución de `ingesta.py` descarga el modelo de embeddings
> (~90 MB) de HuggingFace. Las siguientes lo toman de la caché local.

## 2. Variables de entorno

Viven en `.env` (ignorado por git); el ejemplo completo está en `.env.example`.
Para los **embeddings no hace falta ninguna clave**; solo se necesita la del
proveedor de **generación** que uses.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LLM_PROVIDER` | no | `gemini` | Proveedor de generación: `gemini`, `openai` o `anthropic` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | sí, para Gemini | — | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (tier gratuito) |
| `GEMINI_MODEL` | no | `gemini-flash-latest` | Modelo de Gemini |
| `OPENAI_API_KEY` | sí, para OpenAI | — | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Modelo de OpenAI |
| `OPENAI_BASE_URL` | no | — | Endpoint alternativo compatible con OpenAI (ej. Groq) |
| `ANTHROPIC_API_KEY` | sí, para Anthropic | — | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `ANTHROPIC_MODEL` | no | `claude-haiku-4-5-20251001` | Modelo de Anthropic |
| `LLM_MAX_RETRIES` | no | `3` | Reintentos de la cadena LCEL ante un output mal formado |
| `CHROMA_PERSIST_DIR` | no | `./vectorstore` | Carpeta del índice persistido |
| `CHROMA_COLLECTION` | no | `autorenta_policies` | Nombre de la colección |
| `RAG_TOP_K` | no | `4` | Cantidad de fragmentos a recuperar (3-5) |

## 3. Diseño

```
entregable3/
├── data/            # dataset de ejemplo: 4 políticas de la rentadora AutoRenta (.txt)
├── schemas.py       # RespuestaLLM (lo que genera el LLM) + RAGResponse (objeto final)
├── ingesta.py       # carga /data -> chunking en tokens -> ChromaDB (anti-reindexado)
├── rag_chain.py     # embeddings, retriever, cadena LCEL y get_rag_response()
├── main.py          # 2 pruebas asíncronas + modo interactivo
└── verificar.py     # criterios de aceptación (offline por defecto)
```

### Módulo de ingesta (`ingesta.py`)

Lee todos los `.txt` de `/data`, los fragmenta y los persiste en ChromaDB.

- **Chunking en tokens, no en caracteres.** `RecursiveCharacterTextSplitter.from_tiktoken_encoder(chunk_size=500, chunk_overlap=70)`
  hace que "500" sean 500 *tokens* reales del tokenizer (cumple el mínimo de la
  consigna: 500 tokens / ≥50 de overlap). Cortar por caracteres daría una
  estimación poco confiable, porque los tokens por carácter varían según idioma
  y puntuación.
- **Chequeo anti-reindexado.** Si ya existe un índice en `./vectorstore`, se
  carga sin volver a indexar (ahorra tiempo y descarga). `python ingesta.py --force`
  lo rehace desde cero.

> Con documentos cortos como estos, cada archivo suele generar **un solo
> fragmento** (queda por debajo de 500 tokens): `chunk_size` es un techo, no un
> piso. El chunking se ve partiendo un documento largo en la verificación
> (sección 3 de `verificar.py`) o agregando más párrafos a cualquier `.txt`.

### Recuperación (`rag_chain.py`)

`get_vectorstore()` carga el índice con el **mismo** modelo de embeddings que la
ingesta, y `get_retriever(k=4)` expone una búsqueda de similitud con `top_k`
acotado a 3-5. Más fragmentos degradan la atención del modelo (*lost in the
middle*) y arriesgan el límite de tokens.

**Garantía estructural contra el error #1 de la consigna** (indexar y consultar
con embeddings distintos): tanto `ingesta.py` como `rag_chain.py` obtienen el
modelo de la *misma* función `get_embeddings()`, y ese modelo **no** es
configurable por entorno. No hay forma de que diverjan.

### Generación grounded (cadena LCEL)

```python
chain = (PROMPT | modelo | PydanticOutputParser(RespuestaLLM)).with_retry(...)
```

El `SYSTEM_PROMPT` actúa como **filtro de veracidad**: instruye al modelo a
responder solo con el `CONTEXTO` y, si el dato no está, a decir exactamente
*"No tengo acceso a esa información en los documentos disponibles."*. El
`PydanticOutputParser` valida la salida; `.with_retry()` reintenta con backoff
si el LLM devuelve algo mal formado.

### `get_rag_response` (función asíncrona)

Es la función pedida por la consigna, con sus cuatro pasos:

```python
async def get_rag_response(query, provider="gemini", k=4) -> RAGResponse:
    docs = await get_retriever(k).ainvoke(query)          # a. similitud en ChromaDB
    contexto = formatear_documentos(docs)                 # b. arma el prompt
    salida = await build_chain(provider).ainvoke({...})   # c. LLM asíncrono
    fuentes = sorted({d.metadata["source"] for d in docs})
    return RAGResponse(respuesta=salida.respuesta, fuentes=fuentes, ...)  # d. Pydantic
```

**Las fuentes las arma el código, no el LLM.** El modelo solo genera el texto
(`RespuestaLLM`); las referencias se derivan de la metadata real de los
fragmentos recuperados. Si le pidiéramos las fuentes al LLM, alucinaría
referencias inexistentes — este es un punto de diseño, no un olvido.

## 4. Las dos pruebas (`main.py`)

`main.py` ejecuta dos consultas fijas que cubren los dos escenarios que pide la
consigna:

**Prueba 1 — pregunta con respuesta en los documentos**

> ¿Cuántos kilómetros diarios incluye la tarifa estándar de alquiler?

```
RESPUESTA: La tarifa estándar de alquiler incluye 200 kilómetros por día sin cargo adicional.
FUENTES: ['data/combustible_kilometraje.txt', 'data/devolucion_cargos.txt',
          'data/requisitos_alquiler.txt', 'data/seguros_coberturas.txt']
Fragmentos usados: 4
```

**Prueba 2 — "pregunta trampa" (el dato NO está en el corpus)**

> ¿AutoRenta ofrece un programa de puntos de fidelidad o millas para clientes frecuentes?

```
RESPUESTA: No tengo acceso a esa información en los documentos disponibles.
FUENTES: ['data/combustible_kilometraje.txt', 'data/devolucion_cargos.txt',
          'data/requisitos_alquiler.txt', 'data/seguros_coberturas.txt']
Fragmentos usados: 4
```

La segunda prueba es la clave: los programas de fidelidad son muy creíbles en
una rentadora, así que es una tentación real para que el modelo invente — y
verifica que el sistema **no alucina** cuando la información no está en el corpus.

### Más preguntas para el modo interactivo

`python main.py --interactive` abre un chat; estas preguntas **sí** tienen
respuesta en el dataset (una por documento):

| Pregunta | Documento con la respuesta |
|---|---|
| ¿Cuál es la edad mínima para alquilar un vehículo estándar? | `requisitos_alquiler.txt` (21 años) |
| ¿Cómo funciona la política de combustible? | `combustible_kilometraje.txt` (full-to-full) |
| ¿Qué daños no cubre la CDW? | `seguros_coberturas.txt` (techo, bajos, neumáticos, cristales…) |
| ¿Hasta cuándo puedo cancelar sin costo? | `devolucion_cargos.txt` (48 horas antes) |

Y estas son "trampa" — no están en los documentos, deben devolver *"No tengo
acceso a esa información…"*: *¿Tienen sillas para bebés?*, *¿Puedo pagar con
criptomonedas?*, *¿Cuál es el teléfono de la sucursal del aeropuerto?*.

## 5. Errores comunes que el entregable evita

| Error de la consigna | Cómo se evita acá |
|---|---|
| "Contexto infinito" | `top_k=4` (entre 3 y 5), configurable con `RAG_TOP_K` |
| Embeddings no coincidentes | Un único `get_embeddings()` compartido por ingesta y consulta; no es configurable |
| Falta de persistencia | Chequeo anti-reindexado: `ingesta.py` no reindexa si ya existe el índice |
| Alucinación de fuentes | Las referencias salen de la metadata real, no del LLM |
| Claves en el repo | `.env` (ignorado por git) + `.env.example` como plantilla |

## 6. Estado de la verificación

`python verificar.py` cubre, sin gastar cuota ni descargar el modelo de
embeddings, los contratos Pydantic, la fábrica de modelos (rechaza proveedores
no soportados y detecta API keys faltantes), el prompt (variables y filtro de
veracidad), el armado de contexto etiquetado, y el chunking (parte un documento
largo en varios fragmentos respetando el mínimo de tokens). La sección 4
(`--real`) corre `get_rag_response` de punta a punta contra el índice y el
proveedor configurado cuando hay índice y API key; si falta alguno —o el
proveedor responde con quota/rate limit—, se saltea (`SKIP`) sin marcar falla.
