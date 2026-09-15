"""`AsyncLLMManager`: elige el proveedor a partir de la configuración.

Es el único punto que el código de aplicación necesita conocer. Cambiar de
OpenAI a Anthropic es cambiar una variable de entorno, no una línea de código.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence

from dotenv import load_dotenv

from .base import BaseLLMClient
from .schemas import ChatMessage, ModelConfig, ModelResponse, Provider, Role, StreamChunk

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()

# Modelos por defecto de cada proveedor, si el .env no dice otra cosa.
DEFAULT_MODELS: dict[Provider, str] = {
    Provider.OPENAI: "gpt-4o-mini",
    Provider.ANTHROPIC: "claude-opus-5",
}

_API_KEY_VARS: dict[Provider, str] = {
    Provider.OPENAI: "OPENAI_API_KEY",
    Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
}

_BASE_URL_VARS: dict[Provider, str] = {
    Provider.OPENAI: "OPENAI_BASE_URL",
    Provider.ANTHROPIC: "ANTHROPIC_BASE_URL",
}


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un número, se recibió {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero, se recibió {raw!r}") from exc


def available_providers() -> list[Provider]:
    """Proveedores con API key cargada en el entorno."""
    return [p for p, var in _API_KEY_VARS.items() if _env_str(var)]


class AsyncLLMManager:
    """Fachada asíncrona sobre un proveedor intercambiable."""

    # Se importan dentro del método para que faltar un SDK sólo rompa al
    # usar ese proveedor, no al importar el paquete.
    @staticmethod
    def _client_class(provider: Provider) -> type[BaseLLMClient]:
        if provider is Provider.OPENAI:
            from .providers.openai_client import OpenAIClient

            return OpenAIClient
        from .providers.anthropic_client import AnthropicClient

        return AnthropicClient

    def __init__(
        self,
        config: ModelConfig,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.config = config
        key = api_key or _env_str(_API_KEY_VARS[config.provider])
        if not key:
            raise ValueError(
                f"Falta {_API_KEY_VARS[config.provider]} para usar "
                f"{config.provider}. Copiá .env.example a .env y completalo."
            )
        url = base_url or _env_str(_BASE_URL_VARS[config.provider])
        self._client = self._client_class(config.provider)(config, key, url)

    # -- Construcción ------------------------------------------------------

    @classmethod
    def from_env(cls, provider: str | Provider | None = None, **overrides: object) -> "AsyncLLMManager":
        """Arma el manager leyendo el `.env`.

        `provider` gana sobre la variable `LLM_PROVIDER`; los `overrides` se
        aplican sobre `ModelConfig` y quedan validados por Pydantic igual.
        """
        raw_provider = provider or _env_str("LLM_PROVIDER") or Provider.OPENAI
        resolved = Provider(str(raw_provider).lower())

        model = _env_str(f"{resolved.upper()}_MODEL") or DEFAULT_MODELS[resolved]
        effort = _env_str("ANTHROPIC_EFFORT") if resolved is Provider.ANTHROPIC else None

        config = ModelConfig(
            provider=resolved,
            model=model,
            temperature=_env_float("LLM_TEMPERATURE", 0.7),
            max_tokens=_env_int("LLM_MAX_TOKENS", 1024),
            timeout_s=_env_float("LLM_TIMEOUT_S", 30.0),
            max_retries=_env_int("LLM_MAX_RETRIES", 2),
            effort=effort,
            **overrides,  # type: ignore[arg-type]
        )
        return cls(config)

    # -- Uso ---------------------------------------------------------------

    @staticmethod
    def _normalize(
        messages: Sequence[ChatMessage] | str, system: str | None = None
    ) -> list[ChatMessage]:
        """Acepta un prompt suelto o una lista ya tipada."""
        if isinstance(messages, str):
            turns = [ChatMessage(role=Role.USER, content=messages)]
        else:
            turns = list(messages)
        if system:
            turns.insert(0, ChatMessage(role=Role.SYSTEM, content=system))
        return turns

    async def generate(
        self, messages: Sequence[ChatMessage] | str, system: str | None = None
    ) -> ModelResponse:
        """Respuesta completa. Nunca lanza: mirá `response.ok`."""
        return await self._client.generate(self._normalize(messages, system))

    async def stream(
        self, messages: Sequence[ChatMessage] | str, system: str | None = None
    ) -> AsyncIterator[StreamChunk]:
        """Generador asíncrono de fragmentos. El último trae `done=True`."""
        async for chunk in self._client.stream(self._normalize(messages, system)):
            yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncLLMManager":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return (
            f"AsyncLLMManager(provider={self.config.provider}, "
            f"model={self.config.model!r})"
        )
