"""Recuperador híbrido: BM25 (léxico) + Pinecone (semántico) con EnsembleRetriever.

La búsqueda semántica entiende parafraseos ("¿cuánto dura la credencial?" ≈
"vencimiento del token") pero es débil con identificadores exactos como
`ERR_TLS_4021`; BM25 es lo opuesto. `EnsembleRetriever` fusiona ambos rankings
con **Reciprocal Rank Fusion (RRF)**: combina por *posición*, no por puntaje, así
que no hace falta normalizar escalas incomparables (un BM25 de 20 vs. un coseno
de 0.8). Sumar los scores a mano es el error #1 de la recuperación híbrida.

`RAGSystem.buscar(consulta)` devuelve el top-5 de la fusión.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from config import ENSEMBLE_WEIGHTS, INDEX_NAME, NAMESPACE, TOP_K, get_embeddings, get_pinecone
from schemas import Fragmento

Modo = Literal["hibrido", "vectorial", "bm25"]

_TOKEN = re.compile(r"\w+")


def tokenizar(texto: str) -> list[str]:
    """Preprocesado para BM25: minúsculas, sin acentos y sin puntuación.

    El `split()` por defecto de `BM25Retriever` deja pegados los signos
    ("`ERR_TLS_4021`." != "ERR_TLS_4021"), lo que rompería justo el caso de uso
    donde BM25 más aporta. `\\w+` conserva los guiones bajos, así que un código
    de error sigue siendo UN solo token.
    """
    sin_acentos = unicodedata.normalize("NFKD", texto.lower())
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return _TOKEN.findall(sin_acentos)


def construir_bm25(chunks: list[Document], k: int = TOP_K) -> BaseRetriever:
    """Retriever léxico en memoria sobre los chunks (misma fuente que la ingesta)."""
    from langchain_community.retrievers import BM25Retriever

    return BM25Retriever.from_documents(chunks, preprocess_func=tokenizar, k=k)


def construir_hibrido(
    retriever_bm25: BaseRetriever,
    retriever_vectorial: BaseRetriever,
    pesos: tuple[float, float] = ENSEMBLE_WEIGHTS,
) -> BaseRetriever:
    """Fusiona ambos recuperadores con RRF. El orden de `pesos` sigue al de los retrievers."""
    from langchain_classic.retrievers import EnsembleRetriever

    return EnsembleRetriever(retrievers=[retriever_bm25, retriever_vectorial], weights=list(pesos))


def a_fragmento(doc: Document) -> Fragmento:
    """Convierte un Document de LangChain en el contrato `Fragmento`."""
    meta = doc.metadata
    return Fragmento(
        contenido=doc.page_content,
        fuente=meta.get("source", "desconocida"),
        pagina=int(meta.get("pagina", 1)),  # Pinecone devuelve los números como float
        categoria=meta.get("categoria", "desconocida"),
    )


class RAGSystem:
    """Encapsula el `EnsembleRetriever` y expone una API simple: consulta -> top-k."""

    def __init__(
        self,
        k: int = TOP_K,
        pesos: tuple[float, float] = ENSEMBLE_WEIGHTS,
        chunks: list[Document] | None = None,
        retriever_vectorial: BaseRetriever | None = None,
    ) -> None:
        """
        `chunks` y `retriever_vectorial` son inyectables para poder probar la
        fusión sin red; por defecto se arman desde /data y desde Pinecone.
        """
        self.k = k

        if chunks is None:
            from ingesta import preparar_chunks

            chunks = preparar_chunks()
        self.retriever_bm25 = construir_bm25(chunks, k=k)

        if retriever_vectorial is None:
            retriever_vectorial = self._vectorial_pinecone(k)
        self.retriever_vectorial = retriever_vectorial

        self.retriever_hibrido = construir_hibrido(self.retriever_bm25, self.retriever_vectorial, pesos)

    @staticmethod
    def _vectorial_pinecone(k: int) -> BaseRetriever:
        from langchain_pinecone import PineconeVectorStore

        pc = get_pinecone()
        if not pc.has_index(INDEX_NAME):
            raise FileNotFoundError(
                f"No existe el índice '{INDEX_NAME}'. Ejecutá primero `python ingesta.py`."
            )
        indice = pc.Index(INDEX_NAME)
        vectorstore = PineconeVectorStore(index=indice, embedding=get_embeddings(), namespace=NAMESPACE)
        return vectorstore.as_retriever(search_kwargs={"k": k})

    def buscar(self, consulta: str, modo: Modo = "hibrido") -> list[Fragmento]:
        """Devuelve los top-k fragmentos. `modo` permite aislar cada recuperador para comparar."""
        retriever = {
            "hibrido": self.retriever_hibrido,
            "vectorial": self.retriever_vectorial,
            "bm25": self.retriever_bm25,
        }[modo]
        return [a_fragmento(d) for d in retriever.invoke(consulta)[: self.k]]
