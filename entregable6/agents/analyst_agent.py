"""Agente de Análisis/Cómputo: calcula con los datos que le pasan y NO busca."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from agents._base import crear_nodo_especialista
from agents.herramientas import HERRAMIENTAS_ANALISTA

PROMPT = (
    "Sos el agente de ANÁLISIS de un equipo. Tu única función es calcular con tus "
    "herramientas a partir de los números que aparecen en la instrucción. NO busques "
    "información nueva ni asumas datos que no te dieron: si falta un número, decí cuál. "
    "Mostrá la cuenta que hiciste y el resultado final."
)


def crear_nodo_analista(llm: BaseChatModel):
    return crear_nodo_especialista("analista", llm, HERRAMIENTAS_ANALISTA, PROMPT)
