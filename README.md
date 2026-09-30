# Curso AI Engineering — Ejercicios y Entregables

Repositorio con el trabajo práctico del programa **AI Engineering** (CoderHouse).
Cada carpeta es autocontenida: dependencias, README propio y script de
verificación.

## Ejercicios

| # | Módulo | Tema | Estado |
|---|---|---|---|
| [1](./ejercicio1) | Módulo 1 · La interfaz base | Orquestador concurrente de modelos: `asyncio.gather`, `asyncio.timeout`, `asyncio.Semaphore` | ✅ Completo |

## Entregables

| # | Módulo | Tema | Estado |
|---|---|---|---|
| [1](./entregable1) | Módulo 1 · La interfaz base | Cliente de LLM robusto y asíncrono: OpenAI + Anthropic + Gemini tras una interfaz común, Pydantic, streaming | ✅ Completo |
| [2](./entregable2) | Módulo 2 · Encadenamiento lógico | Pipeline de procesamiento validado: LangChain / LCEL, salida estructurada con Pydantic, `.with_retry()` | ✅ Completo |
| [3](./entregable3) | Módulo 3 · Persistencia y vector DBs | Sistema de recuperación semántica local (RAG): ChromaDB, embeddings locales (HuggingFace), cadena LCEL con `PydanticOutputParser` y prueba anti-alucinación | ✅ Completo |
| 4 | Módulo 4 · Escalabilidad documental | RAG en la nube con Pinecone | ⏳ Pendiente |
| 5 | Módulo 5 · Razonamiento autónomo | Agente cíclico con memoria persistente (LangGraph) | ⏳ Pendiente |
| 6 | Módulo 6 · Sistemas multi-agente | Orquestador multi-agente especializado | ⏳ Pendiente |
| 7 | Módulo 7 · Producción y robustez | Observabilidad, costos y despliegue | ⏳ Pendiente |
| 8 | Módulo 8 · Capstone | Entrega final | ⏳ Pendiente |

## Ejercicio 1 — Orquestador Concurrente de Modelos

Consulta varios LLMs de forma concurrente con control de latencia y de flujo.
Llamadas reales a **Gemini** (`gemini-3.6-flash`) y **Groq**
(`openai/gpt-oss-20b`), ambos con tier gratuito, más un modelo local simulado
que excede el timeout a propósito para que el manejo de `TimeoutError` sea
observable en cada corrida.

```bash
cd ejercicio1
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # cargá tus API keys
python -m orquestador         # ambos escenarios
python verificar.py           # chequea los criterios de aceptación
```

Detalle completo en [`ejercicio1/README.md`](./ejercicio1/README.md).

## Entregable 1 — Cliente de LLM Robusto y Asíncrono

Cliente unificado que expone **OpenAI**, **Anthropic** y **Gemini** detrás de
la misma interfaz asíncrona: validación Pydantic en la frontera, streaming
token a token y errores capturados como dato en vez de excepciones que rompan
el proceso. Cambiar de proveedor es cambiar una variable de entorno.

```bash
cd entregable1
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # alcanza con una de las tres API keys
python verificar.py           # criterios de aceptación, sin gastar cuota
python main.py                # prueba real: modo normal + streaming
```

Detalle completo en [`entregable1/README.md`](./entregable1/README.md).

## Entregable 2 — Pipeline de Procesamiento Validado

Pipeline de extracción de entidades técnicas con **LangChain / LCEL**: un
texto libre (log de error, descripción de arquitectura) entra y sale un
objeto validado con Pydantic (tecnologías, nivel de criticidad, resumen
técnico). Cadena `prompt | model.with_structured_output(Schema)` con
`.with_retry()` como lógica de resiliencia ante JSON mal formado o
incompleto, intercambiable entre OpenAI, Anthropic y Gemini.

```bash
cd entregable2
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # alcanza con una de las tres API keys
python verificar.py           # criterios de aceptación, sin gastar cuota
python main.py                # prueba real: texto claro + prueba de estrés
```

Detalle completo en [`entregable2/README.md`](./entregable2/README.md).

## Entregable 3 — Sistema de Recuperación Semántica Local (RAG)

RAG End-to-End sobre un corpus de políticas de una rentadora de autos
("AutoRenta"): ingesta con
*chunking* en tokens y persistencia en **ChromaDB**, recuperación por similitud
con `top_k` acotado y generación *grounded* con una cadena **LCEL** que solo
responde con el contexto recuperado. Embeddings **locales** (HuggingFace
`all-MiniLM-L6-v2`, sin API key); generación intercambiable entre Gemini,
OpenAI y Anthropic. Incluye una "pregunta trampa" que verifica que el modelo no
alucina cuando el dato no está en los documentos.

```bash
cd entregable3
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # GEMINI_API_KEY (o GOOGLE_API_KEY); los embeddings son locales
python ingesta.py             # puebla ChromaDB (descarga el modelo la 1ª vez)
python main.py                # 2 pruebas: con respuesta + trampa
python verificar.py           # criterios de aceptación (offline por defecto)
```

Detalle completo en [`entregable3/README.md`](./entregable3/README.md).

## Convenciones del repositorio

* **Python 3.12** en todas las carpetas (cada una fija su versión en
  `.python-version` y `pyproject.toml`).
* Las API keys van siempre en un `.env` local, ignorado por git. Cada carpeta
  incluye su `.env.example` con los campos vacíos y los links para obtener las
  claves gratuitas.
* El material de lectura del curso no se versiona: es contenido propietario de
  CoderHouse.
