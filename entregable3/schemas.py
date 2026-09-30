"""Contratos de salida del sistema RAG.

Separamos dos modelos a propósito:

- `RespuestaLLM` es lo único que genera el LLM: solo el texto de la respuesta.
- `RAGResponse` es el objeto final que devuelve `get_rag_response`, y combina
  ese texto con las **referencias reales** (los archivos de origen de los
  fragmentos recuperados) que arma nuestro propio código a partir de la
  metadata de ChromaDB.

Por qué no le pedimos las fuentes al LLM: si el modelo tiene que "recordar" de
qué documento salió cada dato, alucina referencias que no existen. Las fuentes
son un hecho verificable de la capa de recuperación, no una opinión del modelo.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RespuestaLLM(BaseModel):
    """Lo que el LLM debe generar, parseado directamente de su salida."""

    respuesta: str = Field(
        ...,
        min_length=1,
        description=(
            "Respuesta a la pregunta del usuario, basada EXCLUSIVAMENTE en el CONTEXTO. "
            "Si la información no está en el contexto, decir explícitamente que no se "
            "cuenta con esa información."
        ),
    )


class RAGResponse(BaseModel):
    """Objeto final de `get_rag_response`: texto del LLM + metadata verificable."""

    respuesta: str = Field(..., description="Texto generado por el LLM")
    fuentes: list[str] = Field(
        default_factory=list,
        description="Archivos de origen de los fragmentos usados como contexto",
    )
    fragmentos_recuperados: int = Field(
        ..., ge=0, description="Cantidad de fragmentos que devolvió el retriever"
    )
