# Curso AI Engineering — Entregables

Repositorio con los entregables del programa **AI Engineering** (CoderHouse).
Cada módulo vive en su propia carpeta, autocontenida: dependencias, README
propio y script de verificación.

## Entregables

| # | Módulo | Tema | Estado |
|---|---|---|---|
| [1](./entregable1) | Módulo 1 · La interfaz base | Orquestador concurrente de modelos: `asyncio.gather`, `asyncio.timeout`, `asyncio.Semaphore` | ✅ Completo |
| 2 | Módulo 2 · Encadenamiento lógico | Pipeline de procesamiento validado (LangChain / LCEL) | ⏳ Pendiente |
| 3 | Módulo 3 · Persistencia y vector DBs | Sistema de recuperación semántica local (RAG) | ⏳ Pendiente |
| 4 | Módulo 4 · Escalabilidad documental | RAG en la nube con Pinecone | ⏳ Pendiente |
| 5 | Módulo 5 · Razonamiento autónomo | Agente cíclico con memoria persistente (LangGraph) | ⏳ Pendiente |
| 6 | Módulo 6 · Sistemas multi-agente | Orquestador multi-agente especializado | ⏳ Pendiente |
| 7 | Módulo 7 · Producción y robustez | Observabilidad, costos y despliegue | ⏳ Pendiente |
| 8 | Módulo 8 · Capstone | Entrega final | ⏳ Pendiente |

## Entregable 1 — Orquestador Concurrente de Modelos

Consulta varios LLMs de forma concurrente con control de latencia y de flujo.
Llamadas reales a **Gemini** (`gemini-3.6-flash`) y **Groq**
(`openai/gpt-oss-20b`), ambos con tier gratuito, más un modelo local simulado
que excede el timeout a propósito para que el manejo de `TimeoutError` sea
observable en cada corrida.

```bash
cd entregable1
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env          # cargá tus API keys
python -m orquestador         # ambos escenarios
python verificar.py           # chequea los criterios de aceptación
```

Detalle completo en [`entregable1/README.md`](./entregable1/README.md).

## Convenciones del repositorio

* **Python 3.12** en todos los entregables (cada carpeta fija su versión en
  `.python-version` y `pyproject.toml`).
* Las API keys van siempre en un `.env` local, ignorado por git. Cada entregable
  incluye su `.env.example` con los campos vacíos y los links para obtener las
  claves gratuitas.
* El material de lectura del curso no se versiona: es contenido propietario de
  CoderHouse.
