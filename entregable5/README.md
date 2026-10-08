# Entregable 5 · Agente de razonamiento cíclico con memoria persistente

Módulo 5 — *Razonamiento autónomo: agentes ReAct y LangGraph*.

A diferencia de los entregables anteriores (flujos fijos), acá no hay una ruta
`if/else` que decida qué hacer: el propio LLM elige, turno a turno, si necesita
una herramienta o si ya puede responder. Ese ciclo
**modelo → ¿herramienta? → herramienta → modelo → …** es el patrón **ReAct**
(*Reasoning + Acting*), implementado como un `StateGraph` de LangGraph con
memoria persistente en **SQLite** indexada por `thread_id`.

```python
import asyncio
from agente import abrir_agente, preguntar, extraer_texto

async def demo():
    async with abrir_agente() as agente:          # LLM del .env + checkpointer SQLite
        turno = await preguntar(agente, "¿Cuántos pedidos tuvo Ana García y cuál fue el total?", "mi-hilo")
        print(extraer_texto(turno[-1]))

asyncio.run(demo())
```

---

## 1. Cómo levantar el entorno

```bash
cd entregable5
uv venv --python 3.12
uv pip install -r requirements.txt

cp .env.example .env         # completá la API key de UN proveedor (gemini / openai / anthropic)

python main.py               # demo real: 4 escenarios + traza en evidencia/
python verificar.py          # criterios de aceptación (offline, sin API key)
python verificar.py --real   # además valida el camino real con el LLM del .env
```

Sin `uv`: `python -m venv .venv` y `.venv/Scripts/pip install -r requirements.txt`
(`.venv/bin/pip` en Linux/macOS). Requiere **Python 3.12**.

Las claves viven solo en `.env` (ignorado por git). `.env.example` es la plantilla.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LLM_PROVIDER` | no | `gemini` | `gemini`, `openai` o `anthropic` |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | **una** | — | Clave del proveedor elegido |
| `*_MODEL` | no | `gemini-flash-latest` / `gpt-4o-mini` / `claude-haiku-4-5-20251001` | Modelo de cada proveedor |
| `OPENAI_BASE_URL` | no | — | Endpoint compatible con OpenAI (ej. Groq) |
| `AGENT_RECURSION_LIMIT` | no | `10` | Techo de pasos por invocación |
| `AGENT_MAX_MESSAGES` | no | `30` | Mensajes de historial que ve el modelo |
| `CHECKPOINT_DB` | no | `checkpoints.sqlite` | Archivo SQLite de la memoria |

## 2. Diseño

```
entregable5/
├── config.py        # .env, fábrica del LLM (gemini/openai/anthropic), recursion_limit
├── herramientas.py  # "base de datos" simulada + 3 herramientas @tool asíncronas
├── agente.py        # EstadoAgente, StateGraph, AsyncSqliteSaver, preguntar()
├── main.py          # demo real de 4 escenarios; escribe evidencia/
├── verificar.py     # criterios de aceptación (LLM guionado offline; --real opcional)
└── evidencia/
    ├── traza_ejecucion.json   # traza ReAct de la ejecución real
    └── ejecucion.log          # la misma ejecución, legible
```

```
START ─► modelo ──(tools_condition)──► herramientas
           ▲  └──── sin tool_calls ──► END     │
           └────────────────────────────────────┘   herramientas → modelo (el ciclo)
```

- **Contrato de herramientas** (`herramientas.py`): `buscar_cliente_por_nombre`,
  `buscar_pedidos` y `buscar_ultimo_pedido`, con `@tool` y docstrings que dicen
  qué hacen, cuándo usarlas, qué reciben y cómo fallan. Hay dos tablas separadas
  a propósito: el usuario habla de nombres pero los pedidos se consultan por ID,
  así que el agente tiene que encadenar dos llamadas por su cuenta.
- **Estado** (`agente.py`): `EstadoAgente(MessagesState)`. El reducer
  `add_messages` *agrega* cada mensaje a la lista en lugar de reemplazarla.
- **Grafo**: nodo `modelo` (LLM con `bind_tools`) y nodo `herramientas`
  (`ToolNode`), unidos por la arista condicional `tools_condition` y la arista
  de retorno `herramientas → modelo`.
- **Persistencia**: `AsyncSqliteSaver` (la variante asíncrona de `SqliteSaver`,
  necesaria porque el grafo corre con `ainvoke`). Mismo `thread_id` ⇒ misma
  memoria; otro `thread_id` ⇒ conversación aislada.
- **Código**: Python 3.12, type hints en todo el código, `asyncio` de punta a
  punta (herramientas, nodos y checkpointer).

### Los tres errores comunes de la consigna, cubiertos

| Error | Cómo se evita |
|---|---|
| Descripciones vagas | Docstrings extensos y específicos; `verificar.py` exige ≥ 150 caracteres y que describan el error |
| Bucles infinitos | `recursion_limit=10` en cada invocación; si se alcanza, `preguntar()` devuelve un mensaje de corte en vez de explotar |
| Estado sucio | `recortar_historial()`: el modelo ve solo los últimos 30 mensajes (sin partir pares tool_call/ToolMessage); el checkpoint conserva todo |

## 3. Evidencia de ejecución real

`python main.py` corrió contra **Gemini** (`gemini-flash-latest`). La traza
completa está en [`evidencia/traza_ejecucion.json`](./evidencia/traza_ejecucion.json)
y [`evidencia/ejecucion.log`](./evidencia/ejecucion.log). Resumen:

**1 · Razonamiento multi-paso** (`thread_id=demo-multipaso`) — el modelo
encadena dos herramientas sin que nadie le indique el orden:

```
Usuario: ¿Cuántos pedidos tuvo Ana García y cuál fue el total?
-> El agente decide usar la herramienta: buscar_cliente_por_nombre({'nombre': 'Ana García'})
<- {"cliente": "Ana García", "cliente_id": 102}
-> El agente decide usar la herramienta: buscar_pedidos({'cliente_id': 102})
<- {"cliente_id": 102, "pedidos": 3, "total": 14500}
Respuesta: Ana García tuvo 3 pedidos, con un monto total de $14.500.
```

**2 · Memoria** (mismo `thread_id`, **conexión SQLite nueva**) — "¿Y el último?"
no dice de quién; el agente recuerda el `cliente_id=102` y no vuelve a buscarlo:

```
Usuario: ¿Y el último?
-> buscar_ultimo_pedido({'cliente_id': 102})
Respuesta: Su último pedido fue el P-1003, realizado el 20/05/2026, por un monto de $5.000.
```

**3 · Ciclo de retorno** — la herramienta devuelve un error y el agente pide
una aclaración en lugar de inventar un número:

```
Usuario: ¿Cuántos pedidos tuvo el cliente Roberto Sánchez?
<- {"error": "No se encontró ningún cliente llamado 'Roberto Sánchez'. ..."}
Respuesta: No encontré a ningún cliente registrado con el nombre "Roberto Sánchez".
           Por favor, confirmá si el nombre y apellido están completos y correctamente escritos.
```

**4 · Aislamiento** — la misma pregunta "¿Y el último?" en otro `thread_id`
no tiene contexto, 0 herramientas, y el agente pregunta de qué cliente se trata.

## 4. Criterios de aceptación

`python verificar.py` los comprueba sin red, con un LLM guionado que decide
mirando el historial (no por pregunta) sobre un SQLite real:

| Criterio | Cómo se demuestra |
|---|---|
| **Autonomía** | Sin rutas `if/else` en el grafo: el modelo emite los `tool_calls` y `tools_condition` solo lee si existen |
| **Ciclo de retorno** | Un `{"error": ...}` (o una excepción de la herramienta) vuelve al modelo como `ToolMessage`; el agente reintenta o pide aclaración |
| **Resiliencia de estado** | Con el mismo `thread_id` y una conexión SQLite nueva, el LLM recibe el historial previo; otro `thread_id` no lo ve |
| **Código limpio** | Python 3.12, type hints, `asyncio`; `recursion_limit` definido y probado con un modelo que nunca termina |
| **Multi-paso** | Una pregunta dispara ≥ 2 invocaciones de herramientas (también validado con el LLM real vía `--real`) |

## 5. Notas

- La memoria vive en `checkpoints.sqlite`, ignorado por git. `main.py` lo borra
  al arrancar para que la demo sea reproducible; sacá ese paso para conservar
  conversaciones entre ejecuciones.
- Para producción, el mismo grafo se compila con `AsyncPostgresSaver` cambiando
  solo el checkpointer; el resto del código no se toca.
- Este entregable no requiere Pinecone ni embeddings: el agente consulta una
  base simulada en memoria.
