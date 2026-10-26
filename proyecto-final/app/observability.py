"""Capa de observabilidad: trazas de cada ejecución del grafo.

Soporta dos backends, elegidos por la variable `OBSERVABILITY`:

- `phoenix`  (por defecto): inicializa Arize Phoenix vía OpenInference. El
  `LangChainInstrumentor` decora automáticamente TODA llamada de LangChain /
  LangGraph (nodos, LLMs, tool calls), con lo que cada nodo del grafo aparece
  como un span con sus tokens y latencia. Phoenix calcula el costo por ejecución
  a partir de los tokens de entrada/salida.
- `langsmith`: no instrumenta nada acá; LangChain emite las trazas solo con las
  variables de entorno `LANGSMITH_*` / `LANGCHAIN_TRACING_V2` puestas.
- `none`: desactiva la observabilidad (útil para tests y CI).

`init_observability()` es idempotente: se puede llamar varias veces sin duplicar
instrumentación.
"""

from __future__ import annotations

import logging

from app import config

logger = logging.getLogger("observabilidad")

_INICIALIZADO = False


def init_observability() -> str:
    """Inicializa el backend configurado. Devuelve el nombre del backend activo."""
    global _INICIALIZADO
    if _INICIALIZADO:
        return config.OBSERVABILITY

    backend = config.OBSERVABILITY
    try:
        if backend == "phoenix":
            _init_phoenix()
        elif backend == "langsmith":
            _init_langsmith()
        else:
            logger.info("Observabilidad desactivada (OBSERVABILITY=%s).", backend)
    except Exception as e:  # noqa: BLE001 - la observabilidad nunca debe tumbar la API
        logger.warning("No se pudo inicializar la observabilidad (%s): %s", backend, e)

    _INICIALIZADO = True
    return backend


def _init_phoenix() -> None:
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import register

    endpoint = config.PHOENIX_ENDPOINT.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint = f"{endpoint}/v1/traces"

    tracer_provider = register(
        project_name=config.PHOENIX_PROJECT,
        endpoint=endpoint,
        set_global_tracer_provider=False,  # evita pisar otro provider si ya existe
    )
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider, skip_dep_check=True)
    logger.info("Phoenix activo -> proyecto '%s' endpoint %s", config.PHOENIX_PROJECT, endpoint)


def _init_langsmith() -> None:
    import os

    # LangChain emite a LangSmith con estas variables; solo confirmamos que estén.
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        logger.warning("OBSERVABILITY=langsmith pero falta LANGSMITH_API_KEY; no habrá trazas.")
    logger.info("LangSmith activo (trazas automáticas de LangChain).")
