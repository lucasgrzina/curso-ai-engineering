"""Capa RAG (Módulos 3 y 4): recuperación vectorial sobre ChromaDB.

Expone la base de conocimiento del sistema, que el agente investigador consulta
como su herramienta (`app/agents/herramientas.py`).
"""

from app.rag.vectorstore import buscar, get_retriever

__all__ = ["buscar", "get_retriever"]
