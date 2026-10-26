"""Estado compartido del orquestador.

Igual al Módulo 6, con un campo nuevo para el flujo Human-in-the-loop:
`aprobacion_analista`, que registra si un humano ya autorizó (o rechazó) la
ejecución de la herramienta "crítica" (el cálculo del analista). Se persiste en
el checkpoint de Redis, así la decisión sobrevive a la pausa del grafo.
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
    """Hereda `messages` (reducer `add_messages`) y suma los campos de orquestación."""

    next_agent: Destino  # decisión del Supervisor: a quién le toca (o FINISH)
    instruccion: str  # tarea acotada para ese especialista
    razon: str  # justificación de la decisión (auditable en la traza)
    contribuciones: Annotated[list[Contribucion], operator.add]
    pasos: int  # invocaciones a especialistas hechas (contador anti bucle)
    task_completed: bool
    motivo_fin: str  # "suficiente" | "limite_pasos" | "sin_progreso" | "rechazada_por_humano"
    aprobacion_analista: str  # "" | "aprobada" | "rechazada" (HITL)


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
        "aprobacion_analista": "",
    }
