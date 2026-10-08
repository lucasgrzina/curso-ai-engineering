"""Agente ReAct: StateGraph cíclico con memoria persistente en SQLite.

A diferencia de los flujos fijos de los entregables anteriores, acá el propio
LLM decide en cada turno si necesita una herramienta o si ya puede responder:

    START -> modelo --(¿tool_calls?)--> herramientas --+
               ^           |no                          |
               |           v                            |
               +-------- END <--------------------------+  (herramientas -> modelo)

El ciclo `herramientas -> modelo` es el que hace el razonamiento multi-paso:
el modelo puede volver a pasar por las herramientas tantas veces como necesite,
con `recursion_limit` como techo anti bucle infinito.

La persistencia usa `AsyncSqliteSaver` (la variante asíncrona de `SqliteSaver`):
cada paso del grafo se guarda en un archivo SQLite indexado por `thread_id`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

import config
from herramientas import HERRAMIENTAS

SYSTEM_PROMPT = (
    "Sos un asistente de soporte que responde preguntas sobre clientes y sus pedidos. "
    "Solo conocés lo que devuelven tus herramientas: nunca inventes IDs, cantidades ni "
    "montos. Si necesitás un dato, llamá a la herramienta que lo provee (podés llamar a "
    "más de una, una después de otra, usando el resultado de la anterior). Si una "
    "herramienta devuelve un error o un dato incompleto, no adivines: reintentá con "
    "datos corregidos si podés, o pedile al usuario la aclaración que falta. Reutilizá "
    "lo que ya se habló en la conversación (por ejemplo el cliente_id) antes de volver "
    "a consultar. Respondé en español, breve, con los montos en formato $14.500."
)


class EstadoAgente(MessagesState):
    """Estado del grafo: hereda `messages` con el reducer `add_messages`.

    El estado es inmutable en el sentido de LangGraph: los nodos no lo
    modifican, devuelven un diccionario con las actualizaciones y el reducer
    las *agrega* a la lista (no la reemplaza).
    """


def recortar_historial(mensajes: list[AnyMessage], maximo: int = config.MAX_MENSAJES) -> list[AnyMessage]:
    """Evita el "estado sucio": el modelo ve solo los últimos `maximo` mensajes.

    El checkpoint conserva TODO el historial; solo se acota lo que viaja al LLM.
    `start_on="human"` garantiza que la ventana no empiece en medio de un par
    tool_call/ToolMessage (los proveedores rechazan un ToolMessage huérfano).
    """
    return trim_messages(
        mensajes,
        strategy="last",
        token_counter=len,  # cuenta mensajes, no tokens
        max_tokens=maximo,
        start_on="human",
        allow_partial=False,
    )


def construir_grafo(llm: BaseChatModel) -> StateGraph:
    """Arma (sin compilar) el StateGraph con el LLM ya vinculado a las herramientas."""
    llm_con_herramientas = llm.bind_tools(HERRAMIENTAS)

    async def nodo_modelo(state: EstadoAgente) -> dict[str, list[BaseMessage]]:
        """Le pasa el historial (recortado) al LLM; su respuesta trae tool_calls o es la final."""
        entrada = [SystemMessage(content=SYSTEM_PROMPT), *recortar_historial(state["messages"])]
        respuesta = await llm_con_herramientas.ainvoke(entrada)
        return {"messages": [respuesta]}

    grafo = StateGraph(EstadoAgente)
    grafo.add_node("modelo", nodo_modelo)
    # handle_tool_errors=True: una excepción dentro de una herramienta vuelve al
    # modelo como ToolMessage de error en vez de romper el programa.
    grafo.add_node("herramientas", ToolNode(HERRAMIENTAS, handle_tool_errors=True))

    grafo.add_edge(START, "modelo")
    # Arista condicional: si el último mensaje trae tool_calls -> "tools", si no -> END.
    grafo.add_conditional_edges("modelo", tools_condition, {"tools": "herramientas", END: END})
    grafo.add_edge("herramientas", "modelo")  # el ciclo ReAct
    return grafo


@asynccontextmanager
async def abrir_agente(
    llm: BaseChatModel | None = None, db_path: str = config.CHECKPOINT_DB
) -> AsyncIterator[CompiledStateGraph]:
    """Compila el grafo con un checkpointer SQLite abierto mientras dure el `async with`."""
    llm = llm or config.get_model()
    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        yield construir_grafo(llm).compile(checkpointer=checkpointer)


def config_hilo(thread_id: str, recursion_limit: int = config.RECURSION_LIMIT) -> RunnableConfig:
    """Config de invocación: el `thread_id` elige la memoria; `recursion_limit` el techo de pasos."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}


def extraer_texto(mensaje: BaseMessage) -> str:
    """Normaliza `content` (Gemini a veces devuelve una lista de bloques)."""
    contenido = mensaje.content
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in contenido)
    return str(contenido)


async def preguntar(agente: CompiledStateGraph, pregunta: str, thread_id: str) -> list[BaseMessage]:
    """Corre un turno y devuelve SOLO los mensajes de ese turno (pregunta incluida).

    Si el agente supera `recursion_limit` no explota: devuelve un mensaje final
    explicando el corte, para que el llamador siempre tenga algo que mostrar.
    """
    cfg = config_hilo(thread_id)
    try:
        resultado: dict[str, Any] = await agente.ainvoke({"messages": [HumanMessage(content=pregunta)]}, config=cfg)
        mensajes: list[BaseMessage] = resultado["messages"]
    except GraphRecursionError:
        estado = await agente.aget_state(cfg)
        mensajes = [
            *estado.values.get("messages", []),
            AIMessage(content=f"[corte] Se alcanzó el límite de {cfg['recursion_limit']} pasos sin llegar a una respuesta."),
        ]
    inicio = max(i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage))
    return mensajes[inicio:]


def contar_llamadas(mensajes: list[BaseMessage]) -> list[str]:
    """Nombres de las herramientas invocadas (en orden) dentro de una lista de mensajes."""
    return [m.name for m in mensajes if isinstance(m, ToolMessage) and m.name]
