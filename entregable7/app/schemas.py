"""Modelos Pydantic de entrada/salida de la API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CrearTarea(BaseModel):
    pregunta: str = Field(..., min_length=3, description="Consulta para el orquestador multi-agente")


class TareaCreada(BaseModel):
    job_id: str
    estado: str
    detalle: str = "Tarea encolada. Consultá GET /tasks/{job_id} para el estado."


class EstadoTarea(BaseModel):
    job_id: str
    estado: str
    pregunta: str
    resultado: dict[str, Any] | None = None
    error: str | None = None
    aprobacion: dict[str, Any] | None = None


class Aprobacion(BaseModel):
    aprobado: bool = Field(..., description="True para autorizar la acción crítica; False para rechazarla")
    comentario: str = Field(default="", description="Nota opcional del revisor humano")


class RespuestaAprobacion(BaseModel):
    job_id: str
    estado: str
    detalle: str
