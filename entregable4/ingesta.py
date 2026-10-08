"""Pipeline de ingesta: /data -> chunks -> embeddings -> upsert por lotes a Pinecone.

Uso:
    python ingesta.py             # crea el índice si falta y sube los chunks
    python ingesta.py --reset     # vacía el namespace antes de subir

Puntos de diseño alineados con la consigna:

- **Metadata avanzada con el texto adentro.** Cada vector lleva `source`,
  `categoria`, `titulo`, `pagina`, `total_paginas` y `chunk_id`, además del texto
  original (`text`). Con una sola consulta a Pinecone se recupera vector + texto +
  fuente, sin ir a una base relacional aparte. Los nombres de metadata están
  fijos en `construir_metadata` para evitar el *schema drift*.
- **IDs determinísticos** (`<fuente>::<n>`): reingestar el mismo corpus
  *sobrescribe* los vectores en vez de duplicarlos (el upsert es idempotente).
- **Batch upsert de 100 vectores**, el punto dulce entre velocidad y estabilidad,
  en vez de subir vector a vector.
- **Chunking en tokens** (`from_tiktoken_encoder`), no en caracteres.
- **Namespace** propio para los documentos de Nimbus Queue.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    BATCH_SIZE,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DATA_DIR,
    INDEX_NAME,
    NAMESPACE,
    consola_utf8,
    get_embeddings,
)

logger = logging.getLogger("ingesta")

EXTENSIONES = (".md", ".txt")


def cargar_documentos(data_dir: str = DATA_DIR) -> list[Document]:
    """Carga los .md/.txt de /data. La carpeta contenedora es la categoría."""
    raiz = Path(data_dir)
    if not raiz.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de datos: '{data_dir}'")

    docs: list[Document] = []
    for ruta in sorted(raiz.rglob("*")):
        if ruta.suffix.lower() not in EXTENSIONES or not ruta.is_file():
            continue
        texto = ruta.read_text(encoding="utf-8").strip()
        if not texto:
            continue
        relativa = ruta.relative_to(raiz)
        titulo = next(
            (l.lstrip("# ").strip() for l in texto.splitlines() if l.startswith("#")),
            ruta.stem,
        )
        docs.append(
            Document(
                page_content=texto,
                metadata={
                    # Ruta relativa en formato posix: es el `documento_id` del Golden Set.
                    "source": relativa.as_posix(),
                    "categoria": relativa.parent.name or "general",
                    "titulo": titulo,
                },
            )
        )

    if not docs:
        raise ValueError(f"No se encontraron archivos {EXTENSIONES} en '{data_dir}'")
    logger.info("Documentos cargados: %d", len(docs))
    return docs


def construir_metadata(chunk: Document, pagina: int, total_paginas: int) -> dict:
    """Esquema ESTRICTO de metadata: mismos nombres y tipos en todos los vectores."""
    fuente = chunk.metadata["source"]
    return {
        "source": fuente,
        "categoria": chunk.metadata["categoria"],
        "titulo": chunk.metadata["titulo"],
        "pagina": pagina,
        "total_paginas": total_paginas,
        "chunk_id": f"{fuente}::{pagina}",
    }


def fragmentar(docs: list[Document]) -> list[Document]:
    """Parte cada documento en chunks de ~700 tokens y les asigna su metadata."""
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks: list[Document] = []
    for doc in docs:
        partes = splitter.split_documents([doc])
        for n, parte in enumerate(partes, start=1):
            parte.metadata = construir_metadata(parte, pagina=n, total_paginas=len(partes))
            chunks.append(parte)
    logger.info("Fragmentos generados: %d (a partir de %d documentos)", len(chunks), len(docs))
    return chunks


def preparar_chunks(data_dir: str = DATA_DIR) -> list[Document]:
    """Carga + fragmenta. Es la fuente de verdad que usan la ingesta Y el BM25."""
    return fragmentar(cargar_documentos(data_dir))


def lotes(items: list, tamano: int = BATCH_SIZE) -> Iterator[list]:
    """Divide una lista en lotes consecutivos de a lo sumo `tamano` elementos."""
    for i in range(0, len(items), tamano):
        yield items[i : i + tamano]


def ingestar(reset: bool = False) -> int:
    """Sube el corpus a Pinecone. Devuelve la cantidad de vectores en el namespace."""
    from langchain_pinecone import PineconeVectorStore

    from setup_index import asegurar_indice

    indice = asegurar_indice()

    if reset:
        stats = indice.describe_index_stats()
        if NAMESPACE in (stats.get("namespaces") or {}):
            logger.info("--reset: vaciando el namespace '%s'", NAMESPACE)
            indice.delete(delete_all=True, namespace=NAMESPACE)

    chunks = preparar_chunks()
    vectorstore = PineconeVectorStore(index=indice, embedding=get_embeddings(), namespace=NAMESPACE)

    subidos = 0
    for lote in lotes(chunks, BATCH_SIZE):
        # `add_documents` embebe el lote y guarda el texto en metadata["text"].
        vectorstore.add_documents(
            lote, ids=[c.metadata["chunk_id"] for c in lote], batch_size=BATCH_SIZE
        )
        subidos += len(lote)
        logger.info("Upsert: %d/%d vectores", subidos, len(chunks))

    # El conteo de Pinecone es eventualmente consistente: puede tardar unos segundos.
    stats = indice.describe_index_stats()
    return stats["namespaces"].get(NAMESPACE, {}).get("vector_count", subidos)


def main() -> int:
    consola_utf8()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for ruidoso in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description="Ingesta de documentos a Pinecone Serverless")
    ap.add_argument("--reset", action="store_true", help="Vacía el namespace antes de subir")
    args = ap.parse_args()

    try:
        total = ingestar(reset=args.reset)
    except Exception as exc:  # noqa: BLE001
        logger.error("La ingesta falló: %s", exc)
        return 1

    print(f"\nIngesta lista: {total} vector(es) en el namespace '{NAMESPACE}' del índice '{INDEX_NAME}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
