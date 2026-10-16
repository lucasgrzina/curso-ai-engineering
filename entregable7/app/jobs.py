"""JobStore: estado de cada tarea persistido en Redis (independiente del grafo).

Mientras el `RedisSaver` guarda el checkpoint interno de LangGraph, este store
guarda la vista "de negocio" del job que consulta el cliente por polling:

    PENDING -> RUNNING -> (AWAITING_APPROVAL -> RUNNING)* -> DONE | FAILED

Cada job es un hash de Redis en la clave `job:{id}`. El estado se serializa a
JSON en campos de texto. El store recibe el cliente de Redis por inyección, así
los tests pueden pasar un doble en memoria sin levantar un servidor.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Estado(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


# Estados terminales: ya no cambian y no admiten /approve.
TERMINALES = {Estado.DONE, Estado.FAILED}


@dataclass
class Job:
    id: str
    estado: Estado
    pregunta: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resultado: dict[str, Any] | None = None
    error: str | None = None
    aprobacion: dict[str, Any] | None = None  # payload del interrupt (motivo, instrucción)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "estado": self.estado.value,
            "pregunta": self.pregunta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resultado": self.resultado,
            "error": self.error,
            "aprobacion": self.aprobacion,
        }


class JobStore:
    """Capa fina sobre un cliente Redis (hash por job)."""

    def __init__(self, redis_client, ttl: int):
        self._r = redis_client
        self._ttl = ttl

    @staticmethod
    def _key(job_id: str) -> str:
        return f"job:{job_id}"

    def _guardar(self, job: Job) -> None:
        job.updated_at = time.time()
        campos = {
            "id": job.id,
            "estado": job.estado.value,
            "pregunta": job.pregunta,
            "created_at": str(job.created_at),
            "updated_at": str(job.updated_at),
            "resultado": json.dumps(job.resultado, ensure_ascii=False) if job.resultado is not None else "",
            "error": job.error or "",
            "aprobacion": json.dumps(job.aprobacion, ensure_ascii=False) if job.aprobacion is not None else "",
        }
        key = self._key(job.id)
        self._r.hset(key, mapping=campos)
        self._r.expire(key, self._ttl)

    # -- escritura / transiciones de estado -------------------------------

    def crear(self, job_id: str, pregunta: str) -> Job:
        job = Job(id=job_id, estado=Estado.PENDING, pregunta=pregunta)
        self._guardar(job)
        return job

    def marcar_running(self, job_id: str) -> None:
        job = self.obtener(job_id)
        job.estado = Estado.RUNNING
        job.aprobacion = None
        self._guardar(job)

    def marcar_awaiting(self, job_id: str, aprobacion: dict[str, Any]) -> None:
        job = self.obtener(job_id)
        job.estado = Estado.AWAITING_APPROVAL
        job.aprobacion = aprobacion
        self._guardar(job)

    def marcar_done(self, job_id: str, resultado: dict[str, Any]) -> None:
        job = self.obtener(job_id)
        job.estado = Estado.DONE
        job.resultado = resultado
        job.aprobacion = None
        self._guardar(job)

    def marcar_failed(self, job_id: str, error: str) -> None:
        job = self.obtener(job_id)
        job.estado = Estado.FAILED
        job.error = error
        self._guardar(job)

    # -- lectura ----------------------------------------------------------

    def existe(self, job_id: str) -> bool:
        return bool(self._r.exists(self._key(job_id)))

    def obtener(self, job_id: str) -> Job:
        datos = self._r.hgetall(self._key(job_id))
        if not datos:
            raise KeyError(job_id)
        datos = _decodificar(datos)
        return Job(
            id=datos["id"],
            estado=Estado(datos["estado"]),
            pregunta=datos["pregunta"],
            created_at=float(datos["created_at"]),
            updated_at=float(datos["updated_at"]),
            resultado=json.loads(datos["resultado"]) if datos.get("resultado") else None,
            error=datos.get("error") or None,
            aprobacion=json.loads(datos["aprobacion"]) if datos.get("aprobacion") else None,
        )


def _decodificar(datos: dict) -> dict:
    """Redis puede devolver bytes; normalizamos a str para no romper los `float()`."""
    salida = {}
    for k, v in datos.items():
        k = k.decode() if isinstance(k, bytes) else k
        v = v.decode() if isinstance(v, bytes) else v
        salida[k] = v
    return salida
