"""Esquema Pydantic: el contrato de salida del pipeline.

`nivel_de_criticidad` es un Enum (no un string libre) para que el modelo no
pueda inventar valores como "high" o "ALTA". La lista de tecnologías no
puede quedar vacía ni con duplicados.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class NivelCriticidad(str, Enum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class EntidadesTecnicas(BaseModel):
    tecnologias: list[str] = Field(
        ...,
        min_length=1,
        description="Lista de tecnologías, frameworks o herramientas mencionadas en el texto",
    )
    nivel_de_criticidad: NivelCriticidad = Field(
        ..., description="Gravedad del problema o relevancia de la arquitectura descripta"
    )
    resumen_tecnico: str = Field(
        ..., min_length=10, description="Resumen técnico de 1-2 oraciones sobre el contenido del texto"
    )

    @field_validator("tecnologias")
    @classmethod
    def sin_duplicados_ni_vacios(cls, v: list[str]) -> list[str]:
        limpio = [t.strip() for t in v if t.strip()]
        if not limpio:
            raise ValueError("La lista de tecnologías no puede quedar vacía tras limpiar")
        return list(dict.fromkeys(limpio))
