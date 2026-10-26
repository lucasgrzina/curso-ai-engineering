"""Worker en segundo plano: corre el grafo y actualiza el estado en Redis.

Se invoca desde los endpoints vía `BackgroundTasks` de FastAPI, que **await**ea
estas corrutinas fuera del ciclo de la respuesta. El grafo se ejecuta con
`await app.ainvoke(...)`: la comunicación con el LLM, el RAG y los especialistas
es asíncrona de punta a punta. Los nodos que internamente aún son síncronos
(supervisor, síntesis) LangGraph los corre en su *executor*, de modo que **nada
bloquea el event loop** (el error #1 de la consigna). Cada job usa
`thread_id = job_id`, de modo que el checkpoint de Redis liga la ejecución con su
posible reanudación tras la aprobación humana.

Contrato de errores: CUALQUIER excepción del agente en segundo plano se captura
y el job pasa a FAILED en Redis. Así el cliente que hace polling nunca queda en
un loop infinito esperando un job que murió en silencio.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.types import Command

from app.agents._base import extraer_texto
from app.jobs import JobStore
from app.state import estado_inicial

logger = logging.getLogger("worker")


def _config_hilo(job_id: str, callbacks: list | None = None) -> dict:
    cfg: dict = {"configurable": {"thread_id": job_id}}
    if callbacks:
        cfg["callbacks"] = callbacks
    return cfg


def _resumen(resultado: dict) -> dict[str, Any]:
    """Extrae la vista de negocio del estado final del grafo."""
    mensajes = resultado.get("messages", [])
    respuesta = extraer_texto(mensajes[-1]) if mensajes else ""
    contribuciones = resultado.get("contribuciones", [])
    return {
        "respuesta_final": respuesta,
        "motivo_fin": resultado.get("motivo_fin", ""),
        "agentes_en_orden": [c["agente"] for c in contribuciones],
        "contribuciones": contribuciones,
    }


def _procesar(store: JobStore, job_id: str, resultado: Any) -> None:
    """Decide si el grafo se pausó (HITL) o terminó, y actualiza Redis."""
    interrupts = resultado.get("__interrupt__") if isinstance(resultado, dict) else None
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        store.marcar_awaiting(job_id, payload)
        logger.info("[%s] pausado en HITL: esperando aprobación humana.", job_id)
        return
    store.marcar_done(job_id, _resumen(resultado))
    logger.info("[%s] DONE.", job_id)


async def ejecutar_tarea(store: JobStore, app, job_id: str, pregunta: str) -> None:
    """Corre el grafo desde cero para `pregunta`. Maneja pausa HITL y errores."""
    try:
        store.marcar_running(job_id)
        uso = UsageMetadataCallbackHandler()  # acumula tokens de TODAS las llamadas al LLM
        resultado = await app.ainvoke(estado_inicial(pregunta), config=_config_hilo(job_id, [uso]))
        store.sumar_costo(job_id, uso.usage_metadata)
        _procesar(store, job_id, resultado)
    except Exception as e:  # noqa: BLE001 - el fallo en background debe verse como FAILED
        logger.exception("[%s] falló la ejecución.", job_id)
        store.marcar_failed(job_id, f"{type(e).__name__}: {e}")


async def reanudar_tarea(store: JobStore, app, job_id: str, aprobado: bool, comentario: str = "") -> None:
    """Reanuda un grafo pausado en el HITL con la decisión humana."""
    try:
        store.marcar_running(job_id)
        uso = UsageMetadataCallbackHandler()
        resultado = await app.ainvoke(
            Command(resume={"aprobado": aprobado, "comentario": comentario}),
            config=_config_hilo(job_id, [uso]),
        )
        store.sumar_costo(job_id, uso.usage_metadata)  # suma al costo ya acumulado antes de la pausa
        _procesar(store, job_id, resultado)
    except Exception as e:  # noqa: BLE001
        logger.exception("[%s] falló la reanudación.", job_id)
        store.marcar_failed(job_id, f"{type(e).__name__}: {e}")
