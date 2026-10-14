"""Supervisor: router inteligente, validador y controlador de flujo.

Decide en cada vuelta QUIÉN interviene (investigador / analista) o si es hora
de cerrar (FINISH). Tres defensas contra el "Supervisor Infinito":

  1. `MAX_PASOS`: tope duro de invocaciones a especialistas.
  2. `MAX_REFINAMIENTOS`: cuántas veces se puede pedir a un mismo agente que rehaga.
  3. Rúbrica de suficiencia en el prompt: criterio explícito para decir FINISH.

Las tres primeras reglas viven en CÓDIGO (`aplicar_guardas`), no solo en el
prompt: el LLM decide, pero no puede saltearse los topes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

import config
from agents._base import extraer_texto
from state import AgentState, Destino

RUBRICA = """RÚBRICA DE VALIDACIÓN (aplicala antes de decidir FINISH):
 R1. Si la pregunta necesita datos de la empresa, el investigador aportó datos concretos CON fuente.
 R2. Si la pregunta necesita cálculo, el analista aportó un resultado numérico con la cuenta a la vista.
 R3. Coherencia: los números que usó el analista coinciden con los que halló el investigador.
 R4. El resultado responde exactamente lo que se preguntó (no una parte).
R2 solo se cumple si en las contribuciones HAY una entrada [analista]. Nunca digas FINISH "porque el analista aportará": si falta, mandalo vos.
Si TODAS aplicables se cumplen -> FINISH.
Si alguna falla -> mandá al agente responsable con una instrucción de REFINAMIENTO que diga qué corregir."""

PROMPT_SUPERVISOR = """Sos el SUPERVISOR de un equipo con dos especialistas:
- investigador: busca datos concretos en las políticas internas de TechCorp (no calcula).
- analista: calcula con números que le des (no busca información).

Flujo habitual: primero investigador (si faltan datos), luego analista (si hay que calcular),
y FINISH cuando la rúbrica se cumple.

{rubrica}

IMPORTANTE sobre `instruccion`: el especialista NO ve la conversación, solo esa instrucción.
Debe ser autocontenida: si es para el analista, copiá en ella los números y la cuenta a hacer.

Pasos usados: {pasos}/{max_pasos}.

Contribuciones hasta ahora:
{contribuciones}"""


class DecisionSupervisor(BaseModel):
    """Salida estructurada del Supervisor."""

    next: Literal["investigador", "analista", "FINISH"] = Field(
        description="Próximo agente a invocar, o FINISH si la rúbrica ya se cumple"
    )
    instruccion: str = Field(
        default="",
        description="Tarea autocontenida para el especialista elegido (vacía si FINISH)",
    )
    requiere_calculo: bool = Field(
        default=False,
        description="True si responder la pregunta exige un cálculo numérico (sumas, totales, promedios, etc.)",
    )
    razon: str = Field(description="Justificación breve, mencionando qué punto de la rúbrica se cumple o falla")


Decidir = Callable[[AgentState], DecisionSupervisor]


def _texto_contribuciones(state: AgentState) -> str:
    contribuciones = state.get("contribuciones", [])
    if not contribuciones:
        return "(ninguna todavía)"
    return "\n".join(f"- paso {c['paso']} [{c['agente']}]: {c['aporte'][:400]}" for c in contribuciones)


def crear_decisor_llm(llm: BaseChatModel) -> Decidir:
    """Decisor real: el LLM con salida estructurada (`DecisionSupervisor`)."""
    llm_estructurado = llm.with_structured_output(DecisionSupervisor)

    def decidir(state: AgentState) -> DecisionSupervisor:
        sistema = PROMPT_SUPERVISOR.format(
            rubrica=RUBRICA,
            pasos=state.get("pasos", 0),
            max_pasos=config.MAX_PASOS,
            contribuciones=_texto_contribuciones(state),
        )
        pregunta = extraer_texto(state["messages"][0])
        return llm_estructurado.invoke(
            [{"role": "system", "content": sistema}, {"role": "user", "content": f"Pregunta del usuario: {pregunta}"}]
        )

    return decidir


def aplicar_guardas(state: AgentState, decision: DecisionSupervisor) -> tuple[Destino, str]:
    """Reglas deterministas que acotan al LLM. Devuelve (destino, motivo_fin)."""
    pasos = state.get("pasos", 0)
    previas = state.get("contribuciones", [])

    if pasos >= config.MAX_PASOS:
        return "FINISH", "limite_pasos"
    if decision.next == "FINISH":
        if not previas:  # cerrar sin que nadie haya trabajado no es "suficiente"
            return "investigador", ""
        hay_analista = any(c["agente"] == "analista" for c in previas)
        if decision.requiere_calculo and not hay_analista:  # R2: el cálculo no lo hace la síntesis
            return "analista", ""
        return "FINISH", "suficiente"
    # Refinamiento acotado: 1 primera vez + MAX_REFINAMIENTOS reintentos por agente.
    veces = sum(1 for c in previas if c["agente"] == decision.next)
    if veces >= 1 + config.MAX_REFINAMIENTOS:
        return "FINISH", "sin_progreso"
    return decision.next, ""


def crear_nodo_supervisor(decidir: Decidir) -> Callable[[AgentState], dict]:
    def nodo(state: AgentState) -> dict:
        # Con el tope alcanzado ni siquiera se consulta al LLM.
        if state.get("pasos", 0) >= config.MAX_PASOS:
            return {"next_agent": "FINISH", "task_completed": True, "motivo_fin": "limite_pasos",
                    "razon": f"Se alcanzó MAX_PASOS={config.MAX_PASOS}."}
        decision = decidir(state)
        destino, motivo = aplicar_guardas(state, decision)
        razon = decision.razon
        if destino != decision.next:
            razon += f" [guarda: se forzó {destino} ({motivo or 'sin contribuciones previas'})]"
        return {
            "next_agent": destino,
            "instruccion": decision.instruccion if destino != "FINISH" else "",
            "razon": razon,
            "task_completed": destino == "FINISH",
            "motivo_fin": motivo,
        }

    return nodo
