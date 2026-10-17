"""API FastAPI: expone el orquestador multi-agente del Módulo 6 de forma asíncrona.

Endpoints:
  POST /tasks                -> encola la tarea y devuelve job_id (NO bloquea)
  GET  /tasks/{id}           -> estado del job (lectura de Redis, no bloquea)
  POST /tasks/{id}/approve   -> aprueba/rechaza la pausa HITL y reanuda el grafo
  GET  /health               -> salud + backend de observabilidad

Diseño anti-bloqueo: los endpoints solo tocan Redis (rápido) y delegan el trabajo
pesado (invocar el grafo / LLM) a `BackgroundTasks`, que FastAPI corre en un
threadpool. El event loop nunca espera a un LLM.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import redis as redis_lib
from fastapi import BackgroundTasks, FastAPI, HTTPException

from app import config
from app.graph import get_app
from app.jobs import TERMINALES, Estado, JobStore
from app.observability import init_observability
from app.schemas import (
    Aprobacion,
    CrearTarea,
    EstadoTarea,
    RespuestaAprobacion,
    TareaCreada,
)
from app.worker import ejecutar_tarea, reanudar_tarea

config.consola_utf8()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("api")

# Cliente Redis para el JobStore (estado de negocio). El checkpointer del grafo
# usa su propia conexión dentro de RedisSaver.
_redis = redis_lib.from_url(config.REDIS_URL)
store = JobStore(_redis, ttl=config.JOB_TTL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Al arrancar: inicializar trazas y compilar el grafo (crea índices de Redis).
    backend = init_observability()
    logger.info("Observabilidad: %s", backend)
    get_app()  # fuerza el setup del RedisSaver una sola vez
    logger.info("Grafo compilado y checkpointer Redis listo (%s).", config.REDIS_URL)
    yield


app = FastAPI(
    title="Orquestador multi-agente · API de producción (Entregable 7)",
    version="1.0.0",
    description="API asíncrona sobre el sistema multi-agente del M6, con Redis, observabilidad y HITL.",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    try:
        _redis.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"status": "ok", "redis": redis_ok, "observabilidad": config.OBSERVABILITY}


@app.post("/tasks", response_model=TareaCreada, status_code=202)
async def crear_tarea(body: CrearTarea, background: BackgroundTasks) -> TareaCreada:
    """Encola la tarea y devuelve el job_id de inmediato (patrón async)."""
    job_id = uuid.uuid4().hex
    store.crear(job_id, body.pregunta)
    background.add_task(ejecutar_tarea, store, get_app(), job_id, body.pregunta)
    return TareaCreada(job_id=job_id, estado=Estado.PENDING.value)


@app.get("/tasks/{job_id}", response_model=EstadoTarea)
async def estado_tarea(job_id: str) -> EstadoTarea:
    """Devuelve el estado actual del job (solo lee Redis; no bloquea)."""
    try:
        job = store.obtener(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No existe el job {job_id}")
    return EstadoTarea(
        job_id=job.id,
        estado=job.estado.value,
        pregunta=job.pregunta,
        resultado=job.resultado,
        error=job.error,
        aprobacion=job.aprobacion,
    )


@app.post("/tasks/{job_id}/approve", response_model=RespuestaAprobacion)
async def aprobar_tarea(job_id: str, body: Aprobacion, background: BackgroundTasks) -> RespuestaAprobacion:
    """Resuelve la pausa HITL: reanuda el grafo (aprobado) o lo cierra (rechazado)."""
    try:
        job = store.obtener(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No existe el job {job_id}")

    if job.estado in TERMINALES:
        raise HTTPException(status_code=409, detail=f"El job ya está {job.estado.value}; no admite aprobación.")
    if job.estado != Estado.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"El job está {job.estado.value}, no esperando aprobación.",
        )

    background.add_task(reanudar_tarea, store, get_app(), job_id, body.aprobado, body.comentario)
    decision = "aprobada" if body.aprobado else "rechazada"
    return RespuestaAprobacion(
        job_id=job_id,
        estado=Estado.RUNNING.value,
        detalle=f"Aprobación {decision} recibida; reanudando. Consultá GET /tasks/{job_id}.",
    )
