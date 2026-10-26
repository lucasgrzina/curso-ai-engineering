"""Capa de recuperación RAG (Módulos 3 y 4) sobre ChromaDB.

Esta es la base de conocimiento del sistema: el RAG del M3/M4 se integra aquí y
se expone como la **herramienta del agente investigador** (ver
`app/agents/herramientas.py`). El investigador ya no "busca en un diccionario":
consulta un índice vectorial real.

Decisiones de diseño (heredadas del M3/M4):

- **Embeddings locales y únicos.** `sentence-transformers/all-MiniLM-L6-v2` corre
  100% offline (sin API key ni costo) y es la ÚNICA fuente de verdad: la ingesta
  y la consulta usan el mismo modelo, lo que evita el error clásico de indexar con
  un modelo y consultar con otro.
- **Sin hard-coding.** El directorio del índice, la colección, el modelo y el
  `top_k` se leen de variables de entorno (nunca IDs ni rutas escritas en el
  código), con defaults razonables.
- **`top_k` acotado (3-5).** Demasiados fragmentos degradan la atención del modelo
  (*lost in the middle*) y arriesgan el límite de contexto.
- **Recuperación asíncrona.** `buscar(...)` usa `retriever.ainvoke(...)`: la tool
  del investigador es `async` de punta a punta, sin bloquear el event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStoreRetriever

logger = logging.getLogger("rag")

# --- Configuración por entorno (sin hard-coding) ---------------------------
PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./vectorstore")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "techcorp_politicas")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "4"))


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """Modelo de embeddings local (una sola instancia por proceso).

    La primera invocación descarga el modelo (~90 MB) y lo cachea; las siguientes
    lo toman de la caché local. Import perezoso para no pagar el costo salvo que
    realmente se use el RAG (los tests offline inyectan su propio retriever).
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    logger.info("Cargando modelo de embeddings local: %s", EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    """Carga el índice persistido de ChromaDB con el MISMO modelo de embeddings."""
    if not (os.path.isdir(PERSIST_DIR) and os.listdir(PERSIST_DIR)):
        raise FileNotFoundError(
            f"No se encontró un índice RAG en '{PERSIST_DIR}'. "
            "Ejecutá primero `python -m app.rag.ingesta` para poblar ChromaDB "
            "(o usá docker compose, que lo hace en el build)."
        )
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=PERSIST_DIR,
    )


def get_retriever(k: int | None = None) -> VectorStoreRetriever:
    """Retriever de similitud con `top_k` acotado."""
    return get_vectorstore().as_retriever(
        search_type="similarity", search_kwargs={"k": k or DEFAULT_TOP_K}
    )


def formatear(docs: list[Document]) -> str:
    """Une los fragmentos recuperados en un bloque de contexto etiquetado con su fuente."""
    if not docs:
        return (
            "Sin resultados: ninguna política coincide con la consulta. "
            "Reformulá con otras palabras clave."
        )
    bloques = []
    for d in docs:
        fuente = os.path.basename(d.metadata.get("source", "desconocida"))
        bloques.append(f"[Fuente: {fuente}] {d.page_content.strip()}")
    return "\n\n".join(bloques)


async def buscar(consulta: str, k: int | None = None, retriever: VectorStoreRetriever | None = None) -> str:
    """Recuperación asíncrona: devuelve los fragmentos relevantes con su fuente.

    `retriever` es inyectable para los tests (que usan un índice en memoria con
    embeddings falsos, sin descargar modelos ni tocar la red).

    Cuando no se inyecta retriever, la resolución del índice (que la primera vez
    carga el modelo de embeddings) y la búsqueda se corren en un hilo con
    `asyncio.to_thread`, para no bloquear el event loop ni siquiera en el primer
    uso.
    """
    if retriever is not None:
        docs = await retriever.ainvoke(consulta)
    else:
        docs = await asyncio.to_thread(lambda: get_retriever(k).invoke(consulta))
    logger.info("[RAG] Recuperados %d fragmento(s) para: %r", len(docs), consulta)
    return formatear(docs)
