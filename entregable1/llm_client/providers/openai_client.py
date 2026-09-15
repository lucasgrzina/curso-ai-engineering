"""Cliente asíncrono de OpenAI sobre `AsyncOpenAI`.

Se usa siempre la variante asíncrona del SDK: la versión síncrona dentro de
una corutina bloquearía el event loop entero mientras el modelo responde, que
es justamente lo que el ejercicio pide evitar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from openai import AsyncOpenAI

from ..base import BaseLLMClient
from ..schemas import ChatMessage, ModelConfig, ModelResponse, Provider, Usage


class OpenAIClient(BaseLLMClient):
    """Implementación para la API de Chat Completions de OpenAI.

    Acepta `base_url` para apuntar a cualquier endpoint compatible con la API
    de OpenAI (Groq, Together, Ollama, un gateway propio) sin cambiar código.
    """

    def __init__(self, config: ModelConfig, api_key: str, base_url: str | None = None):
        super().__init__(config, api_key, base_url)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.timeout_s,
            # Los reintentos los maneja `BaseLLMClient`; dejar los del SDK
            # activos aplicaría dos backoffs superpuestos sobre el mismo fallo.
            max_retries=0,
        )

    def _payload(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.top_p is not None:
            payload["top_p"] = self.config.top_p
        return payload

    async def _create(self, payload: dict[str, Any]) -> Any:
        """Llama a la API, adaptándose a los modelos de razonamiento.

        Las familias o-* y GPT-5 rechazan `max_tokens` y exigen
        `max_completion_tokens`. En vez de pedirle al usuario que sepa cuál
        toca, se reintenta una vez con el nombre nuevo.
        """
        try:
            return await self._client.chat.completions.create(**payload)
        except Exception as exc:
            if "max_completion_tokens" in str(exc) and "max_tokens" in payload:
                payload = payload | {
                    "max_completion_tokens": payload.pop("max_tokens")
                }
                return await self._client.chat.completions.create(**payload)
            raise

    async def _raw_generate(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        completion = await self._create(self._payload(messages))

        choice = completion.choices[0]
        usage = completion.usage
        return ModelResponse(
            provider=Provider.OPENAI,
            model=completion.model or self.config.model,
            content=choice.message.content or "",
            finish_reason=choice.finish_reason,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )

    async def _raw_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        stream = await self._create(self._payload(messages) | {"stream": True})
        async for event in stream:
            if not event.choices:
                continue  # chunk de usage o keepalive: no trae texto
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def aclose(self) -> None:
        await self._client.close()
