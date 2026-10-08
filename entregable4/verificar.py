"""Chequeo de los criterios de aceptación del Entregable 4.

Por defecto corre 100% offline: no usa la red, no necesita la API key de
Pinecone ni descarga el modelo de embeddings. Valida el chunking y la metadata,
el batching, el BM25 sobre códigos de error, la fusión del `EnsembleRetriever`
(con un recuperador vectorial simulado), las métricas y el Golden Set.

La sección "camino real" (`--real`) ejercita Pinecone de punta a punta si hay
`PINECONE_API_KEY`; si falta, se saltea (SKIP) sin contar como falla.

    python verificar.py            # sólo checks offline
    python verificar.py --real     # además valida el índice real y corre evaluate
"""

from __future__ import annotations

import os
import sys

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ValidationError

import config
import evaluate
import ingesta
import rag_system
from schemas import CasoGolden, Fragmento

config.consola_utf8()

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "OK " if condicion else "FALLA"
    sufijo = f" - {detalle}" if detalle else ""
    print(f"  [{marca}] {nombre}{sufijo}")
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


class VectorialSimulado(BaseRetriever):
    """Reemplaza a Pinecone: devuelve siempre los mismos documentos, en orden."""

    docs: list[Document]

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return self.docs


# ---------------------------------------------------------------------------
# 1. Configuración y contratos
# ---------------------------------------------------------------------------


def verificar_config() -> None:
    seccion("1. Configuración y contratos (config.py, schemas.py)")

    check("la dimensión del índice coincide con el modelo (all-MiniLM-L6-v2 = 384)", config.EMBEDDING_DIM == 384)
    check("chunk_size dentro del rango 500-800 tokens", 500 <= config.CHUNK_SIZE <= 800, f"chunk_size={config.CHUNK_SIZE}")
    check("batch de upsert dentro del rango 100-200", 100 <= config.BATCH_SIZE <= 200, f"batch={config.BATCH_SIZE}")
    check("se usa un namespace explícito", bool(config.NAMESPACE))
    check("top-k por defecto es 5", config.TOP_K == 5)

    previo = os.environ.pop("PINECONE_API_KEY", None)
    try:
        config.get_api_key()
        check("falta de PINECONE_API_KEY detectada", False)
    except ValueError:
        check("falta de PINECONE_API_KEY detectada", True)
    finally:
        if previo is not None:
            os.environ["PINECONE_API_KEY"] = previo

    try:
        CasoGolden(pregunta="", documento_id_esperado="x.md")
        check("CasoGolden con pregunta vacía rechazado", False)
    except ValidationError:
        check("CasoGolden con pregunta vacía rechazado", True)
    try:
        Fragmento(contenido="x", fuente="a.md", pagina=0, categoria="c")
        check("Fragmento con página 0 rechazado", False)
    except ValidationError:
        check("Fragmento con página 0 rechazado", True)


# ---------------------------------------------------------------------------
# 2. Ingesta: carga, chunking, metadata y batching
# ---------------------------------------------------------------------------


def verificar_ingesta() -> list[Document]:
    seccion("2. Pipeline de ingesta (ingesta.py)")

    docs = ingesta.cargar_documentos()
    check("se cargan los documentos de /data", len(docs) >= 5, f"{len(docs)} documentos")
    check(
        "la categoría sale de la carpeta contenedora",
        {d.metadata["categoria"] for d in docs} >= {"autenticacion", "despliegue", "operaciones"},
    )

    chunks = ingesta.fragmentar(docs)
    check("el chunking genera más de un fragmento por documento en promedio", len(chunks) > len(docs), f"{len(chunks)} chunks")

    claves = {frozenset(c.metadata) for c in chunks}
    check(
        "todos los chunks comparten el mismo esquema de metadata (sin schema drift)",
        len(claves) == 1 and set(next(iter(claves))) == {"source", "categoria", "titulo", "pagina", "total_paginas", "chunk_id"},
    )
    ids = [c.metadata["chunk_id"] for c in chunks]
    check("los IDs son únicos (y determinísticos: <fuente>::<n>)", len(set(ids)) == len(ids))
    check(
        "el texto original queda disponible como page_content (se guarda en metadata['text'])",
        all(c.page_content.strip() for c in chunks),
    )

    tamanos = [len(lote) for lote in ingesta.lotes(list(range(250)), 100)]
    check("el batch upsert parte 250 vectores en lotes 100/100/50", tamanos == [100, 100, 50], f"{tamanos}")
    return chunks


# ---------------------------------------------------------------------------
# 3. Recuperación léxica e híbrida
# ---------------------------------------------------------------------------


def verificar_recuperacion(chunks: list[Document]) -> None:
    seccion("3. Recuperador híbrido (rag_system.py)")

    check(
        "el tokenizador deja un código de error como UN solo token",
        rag_system.tokenizar("Ver `ERR_TLS_4021`.") == ["ver", "err_tls_4021"],
    )
    check("el tokenizador ignora acentos y mayúsculas", rag_system.tokenizar("Autenticación") == ["autenticacion"])

    bm25 = rag_system.construir_bm25(chunks, k=5)
    top = bm25.invoke("ERR_TLS_4021")
    check(
        "BM25 encuentra por término exacto el documento del código de error",
        bool(top) and top[0].metadata["source"] == "autenticacion/errores_tls.md",
        f"top-1={top[0].metadata['source'] if top else None}",
    )

    # Un vectorial simulado que "se equivoca": pone primero un chunk de otro documento.
    ajeno = next(c for c in chunks if c.metadata["source"] == "despliegue/instalacion_docker.md")
    rag = rag_system.RAGSystem(k=5, chunks=chunks, retriever_vectorial=VectorialSimulado(docs=[ajeno, top[0]]))
    fusion = rag.buscar("ERR_TLS_4021")
    check("RAGSystem devuelve a lo sumo k=5 fragmentos", 0 < len(fusion) <= 5, f"{len(fusion)}")
    check(
        "RRF premia al documento presente en ambos rankings",
        fusion[0].fuente == "autenticacion/errores_tls.md",
        f"top-1={fusion[0].fuente}",
    )
    check("la fusión no duplica fragmentos", len({f.contenido for f in fusion}) == len(fusion))
    check("los resultados son Fragmento con metadata completa", all(isinstance(f, Fragmento) and f.pagina >= 1 for f in fusion))
    check("el modo 'bm25' aísla al recuperador léxico", rag.buscar("ERR_TLS_4021", modo="bm25")[0].fuente == "autenticacion/errores_tls.md")


# ---------------------------------------------------------------------------
# 4. Evaluación
# ---------------------------------------------------------------------------


def verificar_evaluacion(chunks: list[Document]) -> None:
    seccion("4. Evaluación (evaluate.py)")

    check("Recall@5: 1.0 si el documento esperado está en el top-5", evaluate.recall_at_k("a", ["b", "a", "c"], 5) == 1.0)
    check("Recall@2: 0.0 si el documento esperado queda fuera del top-2", evaluate.recall_at_k("a", ["b", "c", "a"], 2) == 0.0)
    check(
        "Precision@5: fracción de los 5 puestos que son del documento esperado",
        evaluate.precision_at_k("a", ["a", "b", "a", "c", "d"], 5) == 0.4,
    )
    check("Precision@k con k=0 no divide por cero", evaluate.precision_at_k("a", ["a"], 0) == 0.0)

    golden = evaluate.cargar_golden_set()
    check("el Golden Set tiene 5 preguntas", len(golden) == 5, f"{len(golden)}")
    fuentes = {c.metadata["source"] for c in chunks}
    check(
        "todos los documentos esperados existen en el corpus",
        all(g.documento_id_esperado in fuentes for g in golden),
    )

    # Con un vectorial vacío, la evaluación corre sólo con BM25 y no necesita red.
    rag = rag_system.RAGSystem(k=5, chunks=chunks, retriever_vectorial=VectorialSimulado(docs=[]))
    reporte = evaluate.evaluar(rag, golden, k=5, modo="bm25")
    check("evaluar() devuelve promedios en [0, 1]", 0.0 <= reporte["recall@5"] <= 1.0 and 0.0 <= reporte["precision@5"] <= 1.0)
    check("BM25 solo ya ubica el documento correcto en el top-5 de todo el Golden Set", reporte["recall@5"] == 1.0, f"recall@5={reporte['recall@5']:.0%}")


# ---------------------------------------------------------------------------
# 5. Camino real: Pinecone de punta a punta
# ---------------------------------------------------------------------------


def verificar_camino_real(forzar: bool = False) -> None:
    seccion("5. Pinecone real (requiere PINECONE_API_KEY e ingesta previa)")

    if not os.getenv("PINECONE_API_KEY"):
        if forzar:
            check("Pinecone real", False, "falta PINECONE_API_KEY (forzado con --real)")
        else:
            print("  [SKIP ] Pinecone real - falta PINECONE_API_KEY: se saltea, no cuenta como falla.")
        return

    pc = config.get_pinecone()
    if not pc.has_index(config.INDEX_NAME):
        detalle = f"no existe el índice '{config.INDEX_NAME}' (corré `python ingesta.py`)"
        if forzar:
            check("el índice existe", False, detalle)
        else:
            print(f"  [SKIP ] Pinecone real - {detalle}: se saltea, no cuenta como falla.")
        return

    info = pc.describe_index(config.INDEX_NAME)
    check("el índice es Serverless", "serverless" in str(info.spec).lower(), str(info.spec))
    check("la dimensión del índice coincide con el modelo", info.dimension == config.EMBEDDING_DIM, f"dim={info.dimension}")

    esperados = len(ingesta.preparar_chunks())
    cuenta = pc.Index(config.INDEX_NAME).describe_index_stats()["namespaces"].get(config.NAMESPACE, {}).get("vector_count", 0)
    check("el namespace contiene un vector por chunk", cuenta == esperados, f"{cuenta} vectores / {esperados} chunks")

    rag = rag_system.RAGSystem()
    golden = evaluate.cargar_golden_set()
    reporte = evaluate.evaluar(rag, golden, k=5)
    check("el híbrido sobre Pinecone tiene Recall@5 de 100% en el Golden Set", reporte["recall@5"] == 1.0, f"recall@5={reporte['recall@5']:.0%}")

    hit = rag.buscar("¿Qué significa ERR_TLS_4021?")[0]
    check("el texto y la metadata vuelven desde Pinecone (sin otra base)", bool(hit.contenido) and hit.pagina >= 1, f"{hit.fuente} pág {hit.pagina}")


def main() -> int:
    forzar = "--real" in sys.argv[1:]
    print("Verificación del Entregable 4 - RAG escalable con Pinecone")
    verificar_config()
    chunks = verificar_ingesta()
    verificar_recuperacion(chunks)
    verificar_evaluacion(chunks)
    verificar_camino_real(forzar=forzar)

    print(f"\n{'=' * 72}")
    if fallos:
        print(f"FALLARON {len(fallos)} criterios: {', '.join(fallos)}")
        return 1
    print("Todos los criterios se cumplen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
