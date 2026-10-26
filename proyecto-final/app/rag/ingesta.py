"""Ingesta de la base de conocimiento a ChromaDB (Módulos 3 y 4).

Lee los `.txt` de `data/`, los fragmenta en tokens y los persiste en el índice
vectorial que después consulta el agente investigador.

Uso:
    python -m app.rag.ingesta            # indexa si no hay índice; si ya existe, no reindexa
    python -m app.rag.ingesta --force    # borra el índice previo y reindexa desde cero

Puntos de diseño (heredados del M3/M4):

- **Chunking en tokens, no en caracteres.** `from_tiktoken_encoder(chunk_size=500)`
  hace que "500" sean 500 tokens reales del tokenizer del modelo, no una
  estimación por caracteres.
- **Mismo modelo de embeddings que la consulta.** Importa `get_embeddings` de
  `vectorstore`: una única fuente de verdad para indexar y consultar.
- **Anti-reindexado.** Si ya hay un índice persistido no se vuelve a indexar
  (ahorra tiempo y descarga), salvo `--force`.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.vectorstore import COLLECTION_NAME, PERSIST_DIR, get_embeddings

logger = logging.getLogger("ingesta")

# La carpeta de datos es relativa al raíz del proyecto (donde está `data/`).
DATA_DIR = str(Path(__file__).resolve().parents[2] / "data")
CHUNK_SIZE = 500   # tokens
CHUNK_OVERLAP = 70  # tokens


def _consola_utf8() -> None:
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def cargar_documentos(data_dir: str = DATA_DIR) -> list[Document]:
    if not Path(data_dir).is_dir():
        raise FileNotFoundError(f"No existe la carpeta de datos: '{data_dir}'")
    loader = DirectoryLoader(
        data_dir, glob="*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"}
    )
    docs = loader.load()
    if not docs:
        raise ValueError(f"No se encontraron archivos .txt en '{data_dir}'")
    logger.info("Documentos cargados: %d", len(docs))
    return docs


def fragmentar(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = splitter.split_documents(docs)
    logger.info("Fragmentos generados: %d", len(chunks))
    return chunks


def indice_existe() -> bool:
    p = Path(PERSIST_DIR)
    return p.is_dir() and any(p.iterdir())


def indexar(force: bool = False) -> int:
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
    ap = argparse.ArgumentParser(description="Ingesta de la base de conocimiento a ChromaDB")
    ap.add_argument("--force", action="store_true", help="Borra el índice previo y reindexa")
    args = ap.parse_args()

    try:
        total = indexar(force=args.force)
    except Exception as exc:  # noqa: BLE001
        logger.error("La ingesta falló: %s", exc)
        return 1

    print(f"\nÍndice RAG listo en '{PERSIST_DIR}' — {total} fragmento(s) en la colección.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
