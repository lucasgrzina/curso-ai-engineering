"""Módulo de ingesta: lee /data, fragmenta y persiste en ChromaDB.

Uso:
    python ingesta.py            # indexa si no hay índice; si ya existe, no reindexar
    python ingesta.py --force    # borra el índice previo y reindexa desde cero

Puntos de diseño alineados con la consigna:

- **Chunking en tokens, no en caracteres.** El límite de contexto del LLM se
  mide en tokens; `from_tiktoken_encoder(chunk_size=500, ...)` hace que "500"
  sean 500 tokens reales del tokenizer, no una estimación por caracteres.
- **Mismo modelo de embeddings que la consulta.** Importamos `get_embeddings`
  de `rag_chain`: una única fuente de verdad para indexar y consultar.
- **Chequeo anti-reindexado.** Si ya hay un índice persistido, no se vuelve a
  indexar (ahorra tiempo y descarga), salvo que se pase `--force`.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path


def _consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_chain import COLLECTION_NAME, PERSIST_DIR, get_embeddings

logger = logging.getLogger("ingesta")

DATA_DIR = "data"
CHUNK_SIZE = 500  # tokens (mínimo pedido por la consigna)
CHUNK_OVERLAP = 70  # tokens (≥ 50, el mínimo pedido)


def cargar_documentos(data_dir: str = DATA_DIR) -> list[Document]:
    """Carga todos los .txt de la carpeta de datos."""
    if not Path(data_dir).is_dir():
        raise FileNotFoundError(f"No existe la carpeta de datos: '{data_dir}'")
    loader = DirectoryLoader(
        data_dir,
        glob="*.txt",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    docs = loader.load()
    if not docs:
        raise ValueError(f"No se encontraron archivos .txt en '{data_dir}'")
    logger.info("Documentos cargados: %d", len(docs))
    return docs


def fragmentar(docs: list[Document]) -> list[Document]:
    """Fragmenta respetando el mínimo de la consigna (500 tokens / 70 de overlap)."""
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(docs)
    logger.info("Fragmentos generados: %d", len(chunks))
    return chunks


def indice_existe() -> bool:
    p = Path(PERSIST_DIR)
    return p.is_dir() and any(p.iterdir())


def indexar(force: bool = False) -> int:
    """Puebla ChromaDB. Devuelve la cantidad de documentos en la colección."""
    if force and Path(PERSIST_DIR).exists():
        logger.info("--force: borrando índice previo en '%s'", PERSIST_DIR)
        shutil.rmtree(PERSIST_DIR)

    embeddings = get_embeddings()

    if indice_existe():
        logger.info("Índice existente detectado — se carga sin reindexar (usá --force para rehacerlo)")
        vs = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=PERSIST_DIR,
        )
    else:
        logger.info("No hay índice previo — indexando por primera vez")
        chunks = fragmentar(cargar_documentos())
        vs = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=COLLECTION_NAME,
            persist_directory=PERSIST_DIR,
        )

    total = vs._collection.count()
    logger.info("Documentos en la colección '%s': %d", COLLECTION_NAME, total)
    return total


def main() -> int:
    _consola_utf8()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Ingesta de documentos a ChromaDB")
    ap.add_argument("--force", action="store_true", help="Borra el índice previo y reindexa")
    args = ap.parse_args()

    try:
        total = indexar(force=args.force)
    except Exception as exc:  # noqa: BLE001
        logger.error("La ingesta falló: %s", exc)
        return 1

    print(f"\nÍndice listo en '{PERSIST_DIR}' — {total} documento(s) en la colección.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
