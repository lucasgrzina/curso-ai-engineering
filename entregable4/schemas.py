"""Contratos de datos del módulo de recuperación.

`Fragmento` es lo que devuelve `RAGSystem.buscar`: el texto recuperado más la
metadata que se guardó en Pinecone al ingestar (fuente, página, categoría).
`CasoGolden` valida cada par del benchmark de `evaluate.py`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Fragmento(BaseModel):
    """Un chunk recuperado, con la metadata que viaja junto al vector."""

    contenido: str = Field(..., min_length=1, description="Texto original del chunk")
    fuente: str = Field(..., description="Documento de origen (ruta relativa a /data)")
    pagina: int = Field(..., ge=1, description="Posición del chunk dentro del documento (1-based)")
    categoria: str = Field(..., description="Categoría del documento (carpeta de /data)")


class CasoGolden(BaseModel):
    """Un par del Golden Set: pregunta + documento donde está la respuesta."""

    pregunta: str = Field(..., min_length=1)
    documento_id_esperado: str = Field(..., min_length=1)
