"""Agente de Investigación: consulta la fuente de conocimiento y NO calcula."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from agents._base import crear_nodo_especialista
from agents.herramientas import HERRAMIENTAS_INVESTIGADOR

PROMPT = (
    "Sos el agente de INVESTIGACIÓN de un equipo. Tu única función es buscar datos "
    "concretos en las políticas internas de TechCorp con tu herramienta. NO hagas cálculos "
    "ni conclusiones numéricas. Citá siempre la fuente. Si la búsqueda no trae lo pedido, "
    "reintentá con otras palabras clave; si sigue sin aparecer, decilo explícitamente en "
    "lugar de inventar. Respondé breve, solo con los datos hallados."
)


def crear_nodo_investigador(llm: BaseChatModel):
    return crear_nodo_especialista("investigador", llm, HERRAMIENTAS_INVESTIGADOR, PROMPT)
