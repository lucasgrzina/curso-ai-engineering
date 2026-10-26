"""Piezas comunes a los nodos especialistas (idéntico al Módulo 6)."""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

from app.state import Agente, AgentState


def extraer_texto(mensaje: BaseMessage) -> str:
    """Normaliza `content` (Gemini a veces devuelve una lista de bloques)."""
    contenido = mensaje.content
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in contenido)
    return str(contenido)


def crear_nodo_especialista(
    nombre: Agente, llm: BaseChatModel, herramientas: list, prompt: str
) -> Callable[[AgentState], dict]:
    """Envuelve un agente ReAct en un nodo del grafo con CONTEXTO ACOTADO.

    El especialista no recibe el historial: solo la `instruccion` puntual del
    Supervisor. Evita la "contaminación de contexto" y mantiene barato el prompt.
    Su salida vuelve como una `Contribucion` trazable, no solo como un mensaje.
    """
    agente = create_react_agent(llm, herramientas, prompt=prompt)

    async def nodo(state: AgentState) -> dict:
        instruccion = state["instruccion"] or extraer_texto(state["messages"][0])
        # ainvoke: el especialista (y su herramienta RAG async) corren con await,
        # sin bloquear el event loop.
        resultado = await agente.ainvoke({"messages": [HumanMessage(content=instruccion)]})
        respuesta = extraer_texto(resultado["messages"][-1]).strip() or "(sin respuesta)"
        paso = state.get("pasos", 0) + 1
        return {
            "messages": [AIMessage(content=respuesta, name=nombre)],
            "contribuciones": [{"agente": nombre, "paso": paso, "instruccion": instruccion, "aporte": respuesta}],
            "pasos": paso,
        }

    return nodo
