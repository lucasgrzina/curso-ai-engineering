"""Esquemas Pydantic del cliente unificado.

Toda la frontera del sistema (entrada del usuario, configuración del modelo,
salida de los proveedores) pasa por estos modelos. La razón es evitar el
problema clásico de arrastrar diccionarios anidados crudos entre capas: con
Pydantic, un `temperature=3.5` falla al construir la configuración y no cinco
llamadas más abajo, dentro del SDK.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------
# Enumeraciones
# --------------------------------------------------------------------------


class Provider(StrEnum):
    """Proveedores soportados por el manager."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Role(StrEnum):
    """Roles válidos de un mensaje de chat."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ErrorKind(StrEnum):
    """Clasificación de fallos, común a ambos SDKs.

    Se usa para decidir si un error se reintenta o se devuelve al usuario.
    """

    AUTH = "auth"              # API key inválida o sin permisos
    RATE_LIMIT = "rate_limit"  # 429: cuota o límite de tasa
    TIMEOUT = "timeout"        # la request superó timeout_s
    CONNECTION = "connection"  # DNS, TLS, red caída
    BAD_REQUEST = "bad_request"  # 400/404: parámetro o modelo inválido
    SERVER = "server"          # 5xx del proveedor
    UNKNOWN = "unknown"


# Sólo estos se reintentan: reintentar un 400 o una key inválida es quemar
# cuota para obtener exactamente el mismo error.
RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.CONNECTION, ErrorKind.SERVER}
)


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """Un turno de conversación, independiente del proveedor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = Field(min_length=1, description="Texto del turno, no vacío.")

    def as_dict(self) -> dict[str, str]:
        """Forma cruda `{"role": ..., "content": ...}` que esperan los SDKs."""
        return {"role": self.role.value, "content": self.content}


class ModelConfig(BaseModel):
    """Parámetros de generación validados.

    `temperature` admite 0..2 (rango de la consigna, que es el de OpenAI).
    Anthropic acepta sólo 0..1, así que el validador de abajo rechaza el
    exceso en vez de dejar que el SDK devuelva un 400 en runtime.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    provider: Provider
    model: str = Field(min_length=1)

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, gt=0, le=32_000)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)

    # Presupuesto de tiempo por intento y política de reintentos propia.
    timeout_s: float = Field(default=30.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)

    # Sólo Anthropic: controla la profundidad del razonamiento adaptativo.
    effort: str | None = Field(default=None, pattern=r"^(low|medium|high|xhigh|max)$")

    @model_validator(mode="after")
    def _check_provider_limits(self) -> "ModelConfig":
        if self.provider is Provider.ANTHROPIC and self.temperature > 1.0:
            raise ValueError(
                "Anthropic acepta temperature en 0..1; "
                f"se recibió {self.temperature}."
            )
        if self.provider is Provider.OPENAI and self.effort is not None:
            raise ValueError("`effort` es un parámetro exclusivo de Anthropic.")
        return self


# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------


class Usage(BaseModel):
    """Tokens consumidos, normalizados entre proveedores."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMError(BaseModel):
    """Fallo ya capturado y clasificado.

    Nunca se propaga como excepción al llamador: viaja dentro de
    `ModelResponse.error` o de un `StreamChunk.error`.
    """

    model_config = ConfigDict(frozen=True)

    kind: ErrorKind
    message: str
    provider: Provider
    retryable: bool = False
    attempts: int = Field(default=1, ge=1, description="Intentos realizados.")
    status_code: int | None = None

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message} (intentos={self.attempts})"


class ModelResponse(BaseModel):
    """Resultado de una generación no incremental.

    `ok` es la única bandera que hay que mirar: si es `False`, `error` explica
    qué pasó y `content` queda vacío.
    """

    model_config = ConfigDict(frozen=True)

    provider: Provider
    model: str
    content: str = ""
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    finish_reason: str | None = None
    attempts: int = 1
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def from_error(cls, error: LLMError, model: str, latency_s: float) -> "ModelResponse":
        return cls(
            provider=error.provider,
            model=model,
            latency_s=latency_s,
            attempts=error.attempts,
            error=error,
        )


class StreamChunk(BaseModel):
    """Fragmento emitido por el generador asíncrono.

    El último fragmento siempre lleva `done=True`; si algo falló, además trae
    `error`. Así el consumidor nunca ve una excepción cruzar el `async for`.
    """

    model_config = ConfigDict(frozen=True)

    delta: str = ""
    index: int = 0
    done: bool = False
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
