"""Grafo del orquestador: Supervisor (hub) + especialistas + síntesis.

    START -> supervisor --(Literal)--> investigador --+
                 ^   |                  analista    --+--> supervisor  (vuelve a validar)
                 |   +--> sintesis -> END
                 +--------------------------------------+

Topología jerárquica tipo "hub and spoke": los especialistas NUNCA se hablan
entre sí; todo pasa por el Supervisor, que es el único que decide el flujo y el
único punto donde se valida.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

import config
from agents import crear_nodo_analista, crear_nodo_investigador
from agents._base import extraer_texto
from state import AgentState
from supervisor import Decidir, crear_decisor_llm, crear_nodo_supervisor

NodoFn = Callable[[AgentState], dict]


def enrutar(state: AgentState) -> Literal["investigador", "analista", "sintesis"]:
    """Arista condicional: traduce la decisión del Supervisor a un nodo del grafo."""
    if state["next_agent"] == "FINISH":
        return "sintesis"
    return state["next_agent"]


def crear_nodo_sintesis(sintetizar: Callable[[str], str]) -> NodoFn:
    """Redacta la respuesta final a partir de las contribuciones (fase de síntesis)."""

    def nodo(state: AgentState) -> dict:
        pregunta = extraer_texto(state["messages"][0])
        aportes = "\n".join(f"- [{c['agente']}] {c['aporte']}" for c in state.get("contribuciones", []))
        aviso = ""
        if state.get("motivo_fin") in ("limite_pasos", "sin_progreso"):
            aviso = (
                "\nATENCIÓN: el equipo se cortó por límite de control sin cumplir la rúbrica; "
                "aclará qué parte quedó sin confirmar."
            )
        prompt = (
            f"Pregunta original: {pregunta}\n\nAportes del equipo:\n{aportes}\n{aviso}\n"
            "Redactá una respuesta final clara y breve en español usando SOLO estos aportes. No hagas cálculos nuevos."
        )
        return {"messages": [AIMessage(content=sintetizar(prompt), name="supervisor")]}

    return nodo


def construir_grafo(decidir: Decidir, investigador: NodoFn, analista: NodoFn, sintetizar: Callable[[str], str]) -> StateGraph:
    """Arma el grafo con piezas inyectables (permite probarlo offline con dobles)."""
    grafo = StateGraph(AgentState)
    grafo.add_node("supervisor", crear_nodo_supervisor(decidir))
    grafo.add_node("investigador", investigador)
    grafo.add_node("analista", analista)
    grafo.add_node("sintesis", crear_nodo_sintesis(sintetizar))

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges(
        "supervisor", enrutar, {"investigador": "investigador", "analista": "analista", "sintesis": "sintesis"}
    )
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_edge("sintesis", END)
    return grafo


def crear_app(llm: BaseChatModel | None = None) -> CompiledStateGraph:
    """Orquestador real: todos los nodos usan el LLM configurado en el `.env`."""
    llm = llm or config.get_model()
    return construir_grafo(
        decidir=crear_decisor_llm(llm),
        investigador=crear_nodo_investigador(llm),
        analista=crear_nodo_analista(llm),
        sintetizar=lambda prompt: extraer_texto(llm.invoke(prompt)),
    ).compile()
