"""Configuración compartida del módulo de recuperación escalable.

Es la única fuente de verdad para `setup_index.py`, `ingesta.py`, `rag_system.py`
y `evaluate.py`: el modelo de embeddings, su dimensión, el nombre del índice y
el namespace viven acá y nada más. Así es estructuralmente imposible indexar con
un modelo y consultar con otro, o crear el índice con una dimensión distinta de
la que producen los vectores (el "mismatch de dimensiones" de la consigna).

Los embeddings son locales (HuggingFace) por defecto, como en el Entregable 3,
y opcionalmente de OpenAI (`EMBEDDINGS_PROVIDER=openai`). La única credencial
obligatoria es `PINECONE_API_KEY`.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

from dotenv import load_dotenv

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()


def consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


# ---------------------------------------------------------------------------
# Modelo de embeddings (ÚNICO para indexar y consultar; no es configurable)
# ---------------------------------------------------------------------------

# Proveedor: "huggingface" (local, sin API key; por defecto) u "openai"
# (requiere OPENAI_API_KEY). Se elige UNA vez por entorno vía EMBEDDINGS_PROVIDER.
EMBEDDINGS_PROVIDER = (_env_str("EMBEDDINGS_PROVIDER") or "huggingface").lower()

_MODELOS = {
    # proveedor: (modelo, dimensión)
    "huggingface": ("sentence-transformers/all-MiniLM-L6-v2", 384),
    "openai": ("text-embedding-3-small", 1536),
}
if EMBEDDINGS_PROVIDER not in _MODELOS:
    raise ValueError(f"EMBEDDINGS_PROVIDER inválido: '{EMBEDDINGS_PROVIDER}'. Usá {sorted(_MODELOS)}.")

EMBEDDING_MODEL, EMBEDDING_DIM = _MODELOS[EMBEDDINGS_PROVIDER]
# El índice de Pinecone debe tener EXACTAMENTE esta dimensión; `setup_index.py`
# lo valida y falla con un mensaje claro si no coincide.
METRIC = "cosine"
# Clave de metadata donde Pinecone guarda el texto del chunk.
TEXT_KEY = "content"

# ---------------------------------------------------------------------------
# Índice Serverless y namespace
# ---------------------------------------------------------------------------

INDEX_NAME = _env_str("INDEX_NAME") or "nimbus-rag-hibrido"
NAMESPACE = _env_str("PINECONE_NAMESPACE") or "nimbus-docs"
CLOUD = _env_str("PINECONE_CLOUD") or "aws"
REGION = _env_str("PINECONE_REGION") or "us-east-1"

# ---------------------------------------------------------------------------
# Corpus, chunking y recuperación
# ---------------------------------------------------------------------------

DATA_DIR = "data"
CHUNK_SIZE = 700  # tokens (rango 500-800 de la consigna)
CHUNK_OVERLAP = 100  # tokens
BATCH_SIZE = 100  # vectores por upsert (punto dulce de 100-200)
TOP_K = int(_env_str("RAG_TOP_K") or "5")
# Peso de cada recuperador en el EnsembleRetriever: [BM25, vectorial].
ENSEMBLE_WEIGHTS = (0.5, 0.5)


def get_api_key() -> str:
    """Devuelve la `PINECONE_API_KEY` o falla con un mensaje accionable."""
    key = _env_str("PINECONE_API_KEY")
    if not key:
        raise ValueError(
            "Falta PINECONE_API_KEY. Copiá .env.example a .env y completalo "
            "(gratis en https://app.pinecone.io)."
        )
    return key


@lru_cache(maxsize=1)
def get_embeddings():
    """Instancia (una sola vez) el modelo de embeddings del proveedor configurado.

    HuggingFace: la primera invocación descarga el modelo (~90 MB); las
    siguientes lo toman de la caché local. OpenAI: exige `OPENAI_API_KEY`.
    """
    if EMBEDDINGS_PROVIDER == "openai":
        if not _env_str("OPENAI_API_KEY"):
            raise ValueError("EMBEDDINGS_PROVIDER=openai requiere OPENAI_API_KEY en el .env.")
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=EMBEDDING_MODEL)

    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def get_pinecone():
    """Cliente del SDK nativo de Pinecone."""
    from pinecone import Pinecone

    return Pinecone(api_key=get_api_key())
