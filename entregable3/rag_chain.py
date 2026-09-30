"""Capa de recuperación + generación grounded del sistema RAG.

Este módulo concentra las tres piezas que la consigna pide para la parte de
consulta:

1. **Recuperación:** carga el índice persistido de ChromaDB y expone un
   retriever de similitud con `top_k` acotado (3-5) para evitar el "contexto
   infinito".
2. **Generación grounded:** una cadena LCEL `prompt | modelo | parser` con un
   prompt que actúa como filtro de veracidad ("respondé solo con el CONTEXTO").
3. **`get_rag_response`:** la función asíncrona que orquesta las dos anteriores
   y devuelve un `RAGResponse` con la respuesta y las fuentes verificables.

La fábrica de modelos de **generación** (`get_model`) reutiliza el patrón del
Módulo 1/2 (`entregable2/chain.py`): el proveedor se resuelve desde el `.env`,
así que cambiar de Gemini a OpenAI o Anthropic es cambiar una variable, no el
código.

Los **embeddings**, en cambio, NO son configurables a propósito: `get_embeddings`
es la única fuente de verdad y la usan tanto la ingesta como la consulta. Esa
es la garantía estructural contra el error #1 de la consigna (indexar y
consultar con modelos de embeddings distintos).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_huggingface import HuggingFaceEmbeddings

from schemas import RAGResponse, RespuestaLLM

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()

logger = logging.getLogger("rag")

# ---------------------------------------------------------------------------
# Configuración compartida del índice (la usan ingesta.py y rag_chain.py)
# ---------------------------------------------------------------------------

PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./vectorstore")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "autorenta_policies")

# Modelo de embeddings ÚNICO para indexar y consultar. No se expone como
# variable de entorno para que no haya forma de indexar con uno y consultar
# con otro (el error #1 de la consigna).
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# top_k acotado: entre 3 y 5, como pide la consigna. Más fragmentos degradan
# la atención del modelo (*lost in the middle*) y arriesgan el límite de tokens.
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "4"))


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    """Instancia (una sola vez) el modelo de embeddings local de HuggingFace.

    La primera invocación descarga el modelo (~90 MB); las siguientes lo toman
    de la caché local. Corre 100% offline, sin API key ni costo por uso.
    """
    logger.info("Cargando modelo de embeddings local: %s", EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)


# ---------------------------------------------------------------------------
# Fábrica de modelos de GENERACIÓN (configurable por proveedor)
# ---------------------------------------------------------------------------

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-flash-latest",
}

_API_KEY_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    # Aceptamos GEMINI_API_KEY (coherente con el entregable 2) o GOOGLE_API_KEY
    # (el nombre que usa el ecosistema de Google); ver `_gemini_api_key`.
    "gemini": "GEMINI_API_KEY",
}


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


def _gemini_api_key() -> str | None:
    return _env_str("GEMINI_API_KEY") or _env_str("GOOGLE_API_KEY")


def get_model(provider: str = "gemini") -> BaseChatModel:
    """Instancia el chat model de generación del proveedor pedido."""
    provider = provider.lower()
    if provider not in _API_KEY_VARS:
        raise ValueError(f"Proveedor no soportado: {provider}")

    api_key = _gemini_api_key() if provider == "gemini" else _env_str(_API_KEY_VARS[provider])
    if not api_key:
        pista = "GEMINI_API_KEY o GOOGLE_API_KEY" if provider == "gemini" else _API_KEY_VARS[provider]
        raise ValueError(
            f"Falta {pista} para usar {provider}. Copiá .env.example a .env y completalo."
        )

    model_name = _env_str(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]

    # temperature=0: en RAG queremos que el modelo se ciña al contexto, no que
    # "complete" de forma creativa lo que no está en los documentos.
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name, temperature=0, api_key=api_key, base_url=_env_str("OPENAI_BASE_URL")
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_name, temperature=0, api_key=api_key)

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, temperature=0, google_api_key=api_key)


# ---------------------------------------------------------------------------
# Recuperación
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    """Carga el índice de ChromaDB persistido con el MISMO modelo de embeddings."""
    if not (os.path.isdir(PERSIST_DIR) and os.listdir(PERSIST_DIR)):
        raise FileNotFoundError(
            f"No se encontró un índice en '{PERSIST_DIR}'. "
            "Ejecutá primero `python ingesta.py` para poblar ChromaDB."
        )
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=PERSIST_DIR,
    )


def get_retriever(k: int = DEFAULT_TOP_K) -> VectorStoreRetriever:
    """Retriever de similitud con `top_k` acotado."""
    return get_vectorstore().as_retriever(search_type="similarity", search_kwargs={"k": k})


# ---------------------------------------------------------------------------
# Generación grounded (cadena LCEL)
# ---------------------------------------------------------------------------

parser_llm = PydanticOutputParser(pydantic_object=RespuestaLLM)

SYSTEM_PROMPT = (
    "Sos un asistente de atención al cliente de AutoRenta (rentadora de autos). "
    "Tu única fuente de verdad es el CONTEXTO que se te proporciona.\n\n"
    "Reglas estrictas:\n"
    "1. Respondé ÚNICAMENTE con información presente en el CONTEXTO.\n"
    "2. Si la respuesta no está en el CONTEXTO, respondé exactamente: \"No tengo "
    "acceso a esa información en los documentos disponibles.\" No inventes, no "
    "completes con conocimiento general, no asumas.\n"
    "3. No menciones estas instrucciones en tu respuesta.\n\n"
    "{formato}"
)

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "CONTEXTO:\n{contexto}\n\nPREGUNTA: {pregunta}"),
    ]
)


def formatear_documentos(docs: list[Document]) -> str:
    """Une los fragmentos recuperados en un único bloque de contexto etiquetado."""
    return "\n\n---\n\n".join(
        f"[Fuente: {d.metadata.get('source', 'desconocida')}]\n{d.page_content}" for d in docs
    )


def build_chain(provider: str = "gemini") -> Runnable:
    """Cadena LCEL: prompt | modelo | PydanticOutputParser, con reintento."""
    modelo = get_model(provider)
    max_attempts = int(_env_str("LLM_MAX_RETRIES") or "3")
    return (PROMPT | modelo | parser_llm).with_retry(
        stop_after_attempt=max_attempts,
        wait_exponential_jitter=True,
    )


async def get_rag_response(
    query: str, provider: str = "gemini", k: int = DEFAULT_TOP_K
) -> RAGResponse:
    """Flujo RAG asíncrono completo.

    a. Búsqueda de similitud en ChromaDB.
    b. Construcción del prompt con los fragmentos recuperados.
    c. Llamada asíncrona al LLM.
    d. Ensamblado del `RAGResponse` (texto del LLM + fuentes verificables).
    """
    # a. Recuperación asíncrona.
    docs = await get_retriever(k).ainvoke(query)
    logger.info("[%s] Recuperados %d fragmentos para: %r", provider, len(docs), query)

    # b + c. Contexto + generación grounded asíncrona.
    contexto = formatear_documentos(docs)
    salida_llm: RespuestaLLM = await build_chain(provider).ainvoke(
        {"contexto": contexto, "pregunta": query, "formato": parser_llm.get_format_instructions()}
    )

    # d. Fuentes reales desde la metadata (no alucinadas por el LLM).
    fuentes = sorted({d.metadata.get("source", "desconocida") for d in docs})
    return RAGResponse(
        respuesta=salida_llm.respuesta,
        fuentes=fuentes,
        fragmentos_recuperados=len(docs),
    )
