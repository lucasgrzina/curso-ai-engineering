"""Setup de infraestructura: crea el índice Serverless de Pinecone si no existe.

Uso:
    python setup_index.py

Es idempotente: si el índice ya existe, no lo vuelve a crear; solo valida que su
dimensión y métrica coincidan con las del modelo de embeddings. Si no coinciden
falla con un mensaje claro en vez de dejar que el error aparezca recién en el
primer upsert (el "mismatch de dimensiones" de la consigna).
"""

from __future__ import annotations

import logging
import sys
import time

from config import (
    CLOUD,
    EMBEDDING_DIM,
    INDEX_NAME,
    METRIC,
    REGION,
    consola_utf8,
    get_pinecone,
)

logger = logging.getLogger("setup_index")

ESPERA_MAXIMA_S = 120


def _esta_listo(pc, nombre: str) -> bool:
    return bool(pc.describe_index(nombre).status["ready"])


def asegurar_indice(nombre: str = INDEX_NAME):
    """Crea el índice si falta, valida su dimensión y devuelve el handle `Index`."""
    from pinecone import ServerlessSpec

    pc = get_pinecone()

    if pc.has_index(nombre):
        logger.info("El índice '%s' ya existe — no se vuelve a crear", nombre)
    else:
        logger.info(
            "Creando índice Serverless '%s' (dimensión %d, métrica %s, %s/%s)...",
            nombre, EMBEDDING_DIM, METRIC, CLOUD, REGION,
        )
        pc.create_index(
            name=nombre,
            dimension=EMBEDDING_DIM,
            metric=METRIC,
            spec=ServerlessSpec(cloud=CLOUD, region=REGION),
        )
        inicio = time.monotonic()
        while not _esta_listo(pc, nombre):
            if time.monotonic() - inicio > ESPERA_MAXIMA_S:
                raise TimeoutError(f"El índice '{nombre}' no estuvo listo en {ESPERA_MAXIMA_S}s")
            time.sleep(2)

    info = pc.describe_index(nombre)
    if info.dimension != EMBEDDING_DIM:
        raise ValueError(
            f"El índice '{nombre}' tiene dimensión {info.dimension} pero el modelo de "
            f"embeddings produce {EMBEDDING_DIM}. Usá otro INDEX_NAME o borrá el índice."
        )
    if info.metric != METRIC:
        raise ValueError(f"El índice '{nombre}' usa la métrica '{info.metric}', se esperaba '{METRIC}'.")

    return pc.Index(nombre)


def main() -> int:
    consola_utf8()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for ruidoso in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    try:
        indice = asegurar_indice()
    except Exception as exc:  # noqa: BLE001
        logger.error("El setup falló: %s", exc)
        return 1

    print(f"\nÍndice '{INDEX_NAME}' listo:")
    print(indice.describe_index_stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
