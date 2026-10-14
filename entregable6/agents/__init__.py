"""Agentes especialistas: cada uno con un rol y herramientas acotadas."""

from agents.analyst_agent import crear_nodo_analista
from agents.research_agent import crear_nodo_investigador

__all__ = ["crear_nodo_analista", "crear_nodo_investigador"]
