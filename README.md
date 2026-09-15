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
| [1](./entregable1) | Módulo 1 · La interfaz base | Cliente de LLM robusto y asíncrono: OpenAI + Anthropic tras una interfaz común, Pydantic, streaming | ✅ Completo |
| 2 | Módulo 2 · Encadenamiento lógico | Pipeline de procesamiento validado (LangChain / LCEL) | ⏳ Pendiente |
| 3 | Módulo 3 · Persistencia y vector DBs | Sistema de recuperación semántica local (RAG) | ⏳ Pendiente |
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

Cliente unificado que expone **OpenAI** y **Anthropic** detrás de la misma
interfaz asíncrona: validación Pydantic en la frontera, streaming token a
token y errores capturados como dato en vez de excepciones que rompan el
proceso. Cambiar de proveedor es cambiar una variable de entorno.

```bash
cd entregable1
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # alcanza con una de las dos API keys
python verificar.py           # criterios de aceptación, sin gastar cuota
python main.py                # prueba real: modo normal + streaming
```

Detalle completo en [`entregable1/README.md`](./entregable1/README.md).

## Convenciones del repositorio

* **Python 3.12** en todas las carpetas (cada una fija su versión en
  `.python-version` y `pyproject.toml`).
* Las API keys van siempre en un `.env` local, ignorado por git. Cada carpeta
  incluye su `.env.example` con los campos vacíos y los links para obtener las
  claves gratuitas.
* El material de lectura del curso no se versiona: es contenido propietario de
  CoderHouse.
