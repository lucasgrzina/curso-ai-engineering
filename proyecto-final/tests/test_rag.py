"""Tests de la capa RAG (app/rag/vectorstore.py) — 100% offline.

No se descarga ningún modelo ni se toca la red: se usa un índice ChromaDB en
memoria con un embedding FALSO determinista (bolsa de palabras sobre un
vocabulario fijo), suficiente para que la búsqueda por similitud devuelva el
fragmento correcto. Así se prueba el contrato de la tool RAG del investigador
sin dependencias externas.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from app.rag import vectorstore
from app.rag.vectorstore import buscar, formatear

VOCAB = ["vacaciones", "antiguedad", "dias", "teletrabajo", "remoto", "seguridad", "contrasena", "onboarding"]


class FakeEmbeddings(Embeddings):
    """Embedding determinista por frecuencia de términos (sin red, sin modelo)."""

    def _vec(self, texto: str) -> list[float]:
        t = texto.lower()
        v = [float(t.count(w)) for w in VOCAB]
        norma = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norma for x in v]

    def embed_documents(self, textos: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in textos]

    def embed_query(self, texto: str) -> list[float]:
        return self._vec(texto)


@pytest.fixture
def retriever_en_memoria():
    vs = Chroma.from_texts(
        texts=[
            "Politica de vacaciones: menos de 5 anios 14 dias, 5 a 10 anios 21 dias, mas de 10 anios 28 dias segun antiguedad.",
            "Politica de teletrabajo: 3 dias remoto y 2 presenciales por semana.",
            "Politica de seguridad: la contrasena se renueva cada 90 dias y 2FA obligatorio.",
            "Proceso de onboarding: dura 4 semanas con induccion general.",
        ],
        embedding=FakeEmbeddings(),
        metadatas=[
            {"source": "politica_vacaciones.txt"},
            {"source": "politica_teletrabajo.txt"},
            {"source": "politica_seguridad_informatica.txt"},
            {"source": "onboarding_nuevos_empleados.txt"},
        ],
        collection_name="test_politicas",
    )
    return vs.as_retriever(search_type="similarity", search_kwargs={"k": 2})


def test_formatear_sin_docs_avisa():
    assert "Sin resultados" in formatear([])


def test_buscar_recupera_el_fragmento_relevante_con_fuente(retriever_en_memoria):
    salida = asyncio.run(buscar("dias de vacaciones por antiguedad", retriever=retriever_en_memoria))
    assert "14 dias" in salida
    assert "[Fuente: politica_vacaciones.txt]" in salida


def test_la_tool_del_investigador_es_async_y_usa_el_rag(monkeypatch, retriever_en_memoria):
    """`buscar_en_politicas` delega en la capa RAG real (retriever inyectado)."""
    from app.agents.herramientas import buscar_en_politicas

    monkeypatch.setattr(vectorstore, "get_retriever", lambda k=None: retriever_en_memoria)
    salida = asyncio.run(buscar_en_politicas.ainvoke({"consulta": "teletrabajo dias remoto"}))
    assert "[Fuente: politica_teletrabajo.txt]" in salida
