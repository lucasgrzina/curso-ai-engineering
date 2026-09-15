"""Cliente asíncrono de Anthropic sobre `AsyncAnthropic`.

Dos diferencias de forma con OpenAI que esta clase absorbe:

1. El prompt de sistema no es un mensaje más: va en el parámetro `system`.
2. Los modelos actuales (Opus 5, Sonnet 5, Opus 4.7/4.8, Fable 5) eliminaron
   `temperature` y `top_p`: mandarlos devuelve un 400. La config los sigue
   validando porque la consigna lo pide, pero acá se omiten cuando el modelo
   destino no los acepta.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic import AsyncAnthropic

from ..base import BaseLLMClient
from ..schemas import ChatMessage, ModelConfig, ModelResponse, Provider, Role, Usage

# Modelos que rechazan parámetros de sampling (`temperature` / `top_p`).
_NO_SAMPLING = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
)

# Modelos que aceptan `output_config.effort` para regular el razonamiento.
_SUPPORTS_EFFORT = _NO_SAMPLING + ("claude-opus-4-6", "claude-sonnet-4-6")


def _matches(model: str, prefixes: tuple[str, ...]) -> bool:
    return model.startswith(prefixes)


class AnthropicClient(BaseLLMClient):
    """Implementación para la Messages API de Anthropic."""

    def __init__(self, config: ModelConfig, api_key: str, base_url: str | None = None):
        super().__init__(config, api_key, base_url)
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=config.timeout_s,
            max_retries=0,  # el backoff lo aplica BaseLLMClient
        )

    @staticmethod
    def _split_system(
        messages: Sequence[ChatMessage],
    ) -> tuple[str | None, list[dict[str, str]]]:
        """Separa el prompt de sistema del historial de turnos."""
        system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
        turns = [m.as_dict() for m in messages if m.role is not Role.SYSTEM]
        return ("\n\n".join(system_parts) or None), turns

    def _payload(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        system, turns = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,  # obligatorio en esta API
            "messages": turns,
        }
        if system is not None:
            payload["system"] = system
        if not _matches(self.config.model, _NO_SAMPLING):
            # El SDK 1.x ya no expone `temperature` / `top_p` en la firma
            # tipada de `messages.create()` (pasarlos como kwarg es un
            # TypeError), pero la API los sigue aceptando en los modelos
            # viejos. `extra_body` los inyecta en el cuerpo del request.
            sampling: dict[str, Any] = {"temperature": self.config.temperature}
            if self.config.top_p is not None:
                sampling["top_p"] = self.config.top_p
            payload["extra_body"] = sampling
        if self.config.effort and _matches(self.config.model, _SUPPORTS_EFFORT):
            payload["output_config"] = {"effort": self.config.effort}
        return payload

    async def _raw_generate(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        message = await self._client.messages.create(**self._payload(messages))

        # `content` es una lista de bloques (texto, thinking, tool_use...);
        # sólo nos interesan los de texto.
        text = "".join(b.text for b in message.content if b.type == "text")
        return ModelResponse(
            provider=Provider.ANTHROPIC,
            model=message.model or self.config.model,
            content=text,
            finish_reason=message.stop_reason,
            usage=Usage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
        )

    async def _raw_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        # `messages.stream()` es el helper del SDK: `text_stream` ya filtra los
        # eventos y entrega sólo los deltas de texto.
        async with self._client.messages.stream(**self._payload(messages)) as stream:
            async for text in stream.text_stream:
                yield text

    async def aclose(self) -> None:
        await self._client.close()
