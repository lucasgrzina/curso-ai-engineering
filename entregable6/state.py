"""Estado compartido del orquestador.

Todos los nodos (Supervisor, especialistas, síntesis) leen y escriben este
esquema. Lo importante para no perder contexto es `contribuciones`: un registro
estructurado de QUIÉN aportó QUÉ y en qué paso, separado del historial de
`messages`. Así el Supervisor valida sobre datos y no re-lee toda la charla.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import MessagesState

Agente = Literal["investigador", "analista"]
Destino = Literal["investigador", "analista", "FINISH"]


class Contribucion(TypedDict):
    """Un aporte de un especialista, con trazabilidad."""

    agente: Agente
    paso: int  # número de invocación a especialistas en que se produjo
    instruccion: str  # lo que el Supervisor le pidió (y SOLO eso vio el especialista)
    aporte: str  # lo que devolvió


class AgentState(MessagesState):
    """Hereda `messages` (reducer `add_messages`) y suma los campos de orquestación.

    `contribuciones` usa `operator.add` como reducer: cada nodo devuelve SOLO su
    aporte y LangGraph lo concatena, en vez de pisar la lista. Es lo que permite
    que dos agentes escriban sin perder el trabajo del otro.
    """

    next_agent: Destino  # decisión del Supervisor: a quién le toca (o FINISH)
    instruccion: str  # tarea acotada para ese especialista
    razon: str  # justificación de la decisión (auditable en la traza)
    contribuciones: Annotated[list[Contribucion], operator.add]
    pasos: int  # invocaciones a especialistas hechas (contador anti bucle)
    task_completed: bool
    motivo_fin: str  # "suficiente" | "limite_pasos" | "sin_progreso"


def estado_inicial(pregunta: str) -> AgentState:
    return {
        "messages": [HumanMessage(content=pregunta)],
        "next_agent": "investigador",
        "instruccion": "",
        "razon": "",
        "contribuciones": [],
        "pasos": 0,
        "task_completed": False,
        "motivo_fin": "",
    }
