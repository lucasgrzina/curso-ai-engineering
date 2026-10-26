"""Flujo Human-in-the-loop (HITL): pausa obligatoria antes de la acción crítica.

La acción "crítica" de este sistema es la ejecución del **analista**: es el
único especialista que corre una herramienta con efecto de cómputo (y, en un
sistema real, con costo de tokens asociado a cada cálculo). Antes de dejarlo
correr, el grafo se DETIENE en un `interrupt()` de LangGraph y espera una
aprobación externa (el endpoint `POST /tasks/{id}/approve`).

Mecánica:
- `interrupt(payload)` corta la ejecución y persiste el estado en el checkpoint
  de Redis. `app.invoke(...)` retorna con la marca `__interrupt__`.
- Para reanudar: `app.invoke(Command(resume={"aprobado": bool}), config)`. El
  valor de `resume` es exactamente lo que devuelve `interrupt()`.
- La aprobación se guarda en `aprobacion_analista` para no volver a pedirla si el
  Supervisor manda al analista otra vez (un refinamiento aprueba una sola vez).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langgraph.types import interrupt

from app.state import AgentState

MOTIVO_CRITICO = (
    "El analista ejecutará una herramienta de cálculo (acción crítica con costo). "
    "Se requiere aprobación humana antes de continuar."
)


def nodo_aprobacion(state: AgentState) -> dict:
    """Gate HITL: pausa con `interrupt()` salvo que ya exista aprobación previa."""
    if state.get("aprobacion_analista") == "aprobada":
        return {}  # ya autorizado en esta tarea: pasa de largo sin volver a preguntar

    respuesta = interrupt(
        {
            "tipo": "aprobacion_requerida",
            "agente": "analista",
            "instruccion": state.get("instruccion", ""),
            "motivo": MOTIVO_CRITICO,
        }
    )

    aprobado = bool(respuesta.get("aprobado")) if isinstance(respuesta, dict) else bool(respuesta)
    if aprobado:
        return {"aprobacion_analista": "aprobada"}
    comentario = respuesta.get("comentario", "") if isinstance(respuesta, dict) else ""
    return {
        "aprobacion_analista": "rechazada",
        "next_agent": "FINISH",
        "motivo_fin": "rechazada_por_humano",
        "razon": f"Un humano rechazó ejecutar el analista. {comentario}".strip(),
    }


def enrutar_aprobacion(state: AgentState) -> Literal["analista", "sintesis"]:
    """Tras el gate: al analista si fue aprobado; a síntesis (con aviso) si no."""
    return "analista" if state.get("aprobacion_analista") == "aprobada" else "sintesis"


# Firma reexportada para claridad en graph.py
NodoFn = Callable[[AgentState], dict]
