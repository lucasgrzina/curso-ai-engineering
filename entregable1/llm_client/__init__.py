"""Cliente de LLM unificado, asíncrono y validado - Entregable 1 (Módulo 1)."""

from .base import BaseLLMClient
from .manager import AsyncLLMManager, available_providers
from .schemas import (
    ChatMessage,
    ErrorKind,
    LLMError,
    ModelConfig,
    ModelResponse,
    Provider,
    Role,
    StreamChunk,
    Usage,
)

__all__ = [
    "AsyncLLMManager",
    "BaseLLMClient",
    "ChatMessage",
    "ErrorKind",
    "LLMError",
    "ModelConfig",
    "ModelResponse",
    "Provider",
    "Role",
    "StreamChunk",
    "Usage",
    "available_providers",
]
