"""Grafo del orquestador (Módulo 6) + gate HITL + checkpointer de Redis.

    START -> supervisor --(Literal)--> investigador --------+
                 ^   |                  aprobacion(HITL) -.-> analista --+--> supervisor
                 |   |                        |                          |
                 |   +--> sintesis -> END     +--(rechazo)--> sintesis   |
                 +-----------------------------------------------------+

Diferencias con el M6:
- Antes de correr el **analista** (acción crítica/costosa) se pasa por el nodo
  `aprobacion`, que puede pausar el grafo esperando aprobación humana.
- Al compilar en producción se le pasa un `RedisSaver` como checkpointer: así el
  estado del grafo (incluida la pausa del HITL) se persiste en Redis y la
  ejecución puede reanudarse desde otro request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app import config
from app.agents import crear_nodo_analista, crear_nodo_investigador
from app.agents._base import extraer_texto
from app.hitl import enrutar_aprobacion, nodo_aprobacion
from app.state import AgentState
from app.supervisor import Decidir, crear_decisor_llm, crear_nodo_supervisor

NodoFn = Callable[[AgentState], dict]


def enrutar(state: AgentState) -> Literal["investigador", "aprobacion", "sintesis"]:
    """Arista condicional desde el Supervisor.

    El analista no se invoca directamente: se pasa por el gate `aprobacion` (HITL).
    """
    destino = state["next_agent"]
    if destino == "FINISH":
        return "sintesis"
    if destino == "analista":
        return "aprobacion"
    return "investigador"


def crear_nodo_sintesis(sintetizar: Callable[[str], str]) -> NodoFn:
    """Redacta la respuesta final a partir de las contribuciones (fase de síntesis)."""

    def nodo(state: AgentState) -> dict:
        pregunta = extraer_texto(state["messages"][0])
        aportes = "\n".join(f"- [{c['agente']}] {c['aporte']}" for c in state.get("contribuciones", []))
        aviso = ""
        motivo = state.get("motivo_fin")
        if motivo in ("limite_pasos", "sin_progreso"):
            aviso = (
                "\nATENCIÓN: el equipo se cortó por límite de control sin cumplir la rúbrica; "
                "aclará qué parte quedó sin confirmar."
            )
        elif motivo == "rechazada_por_humano":
            aviso = (
                "\nATENCIÓN: un humano rechazó la ejecución del analista; NO entregues un "
                "resultado numérico calculado por vos. Aclará que el cálculo quedó sin aprobar."
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
    grafo.add_node("aprobacion", nodo_aprobacion)
    grafo.add_node("analista", analista)
    grafo.add_node("sintesis", crear_nodo_sintesis(sintetizar))

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges(
        "supervisor",
        enrutar,
        {"investigador": "investigador", "aprobacion": "aprobacion", "sintesis": "sintesis"},
    )
    grafo.add_conditional_edges("aprobacion", enrutar_aprobacion, {"analista": "analista", "sintesis": "sintesis"})
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_edge("sintesis", END)
    return grafo


def crear_app(llm: BaseChatModel | None = None, checkpointer=None) -> CompiledStateGraph:
    """Orquestador real: todos los nodos usan el LLM configurado en el `.env`.

    `checkpointer` es obligatorio para el HITL (para poder pausar y reanudar). En
    producción se le inyecta un `RedisSaver`; en los tests, un `MemorySaver`.
    """
    llm = llm or config.get_model()
    grafo = construir_grafo(
        decidir=crear_decisor_llm(llm),
        investigador=crear_nodo_investigador(llm),
        analista=crear_nodo_analista(llm),
        sintetizar=lambda prompt: extraer_texto(llm.invoke(prompt)),
    )
    return grafo.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Singleton de producción: grafo compilado con RedisSaver, creado una sola vez.
# ---------------------------------------------------------------------------

_APP: CompiledStateGraph | None = None


def get_app() -> CompiledStateGraph:
    """Devuelve el grafo compilado con checkpointer Redis (lazy singleton)."""
    global _APP
    if _APP is None:
        from app.redis_checkpoint import crear_checkpointer_redis

        _APP = crear_app(checkpointer=crear_checkpointer_redis())
    return _APP
