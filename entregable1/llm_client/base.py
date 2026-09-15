"""Interfaz común a todos los proveedores.

`BaseLLMClient` concentra lo que no depende del SDK: medición de latencia,
reintentos y captura de errores. Cada proveedor sólo implementa los dos
métodos `_raw_*`, que pueden lanzar excepciones libremente: la clase base las
convierte en `LLMError`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from .errors import backoff_delay, classify, retry_after_seconds, with_retries
from .schemas import ChatMessage, LLMError, ModelConfig, ModelResponse, StreamChunk

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """Cliente asíncrono de un proveedor concreto."""

    def __init__(self, config: ModelConfig, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                f"Falta la API key de {config.provider}. "
                "Completá el .env a partir de .env.example."
            )
        self.config = config
        self._api_key = api_key
        self._base_url = base_url

    # -- Contrato que implementa cada proveedor ----------------------------

    @abstractmethod
    async def _raw_generate(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        """Una llamada, sin reintentos ni captura de errores."""

    @abstractmethod
    def _raw_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        """Generador asíncrono de fragmentos de texto."""

    @abstractmethod
    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente."""

    # -- API pública -------------------------------------------------------

    async def generate(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        """Genera una respuesta completa.

        Nunca lanza: los fallos vuelven en `ModelResponse.error`.
        """
        started = time.perf_counter()

        def _log_retry(attempt: int, error: LLMError, delay: float) -> None:
            logger.warning(
                "%s: intento %d falló (%s); reintento en %.2fs",
                self.config.provider, attempt, error.kind, delay,
            )

        response, error, attempts = await with_retries(
            lambda: self._raw_generate(messages),
            provider=self.config.provider,
            max_retries=self.config.max_retries,
            on_retry=_log_retry,
        )
        elapsed = time.perf_counter() - started

        if error is not None:
            logger.error("%s: %s", self.config.provider, error)
            return ModelResponse.from_error(error, self.config.model, elapsed)

        assert response is not None
        return response.model_copy(update={"latency_s": elapsed, "attempts": attempts})

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamChunk]:
        """Emite fragmentos a medida que llegan.

        Política de reintentos: sólo antes del primer token. Una vez que el
        consumidor recibió texto, reintentar duplicaría la respuesta, así que
        el fallo se emite como un chunk final con `error`.
        """
        index = 0

        for attempt in range(self.config.max_retries + 1):
            emitted = 0
            try:
                async for delta in self._raw_stream(messages):
                    if not delta:
                        continue
                    emitted += 1
                    yield StreamChunk(delta=delta, index=index)
                    index += 1
            except Exception as exc:  # noqa: BLE001 - se devuelve, no se propaga
                error = classify(exc, self.config.provider, attempts=attempt + 1)
                retry_ok = (
                    error.retryable
                    and emitted == 0
                    and attempt < self.config.max_retries
                )
                if not retry_ok:
                    logger.error("%s (streaming): %s", self.config.provider, error)
                    yield StreamChunk(index=index, done=True, error=error)
                    return
                delay = retry_after_seconds(exc) or backoff_delay(attempt)
                logger.warning(
                    "%s (streaming): intento %d falló (%s); reintento en %.2fs",
                    self.config.provider, attempt + 1, error.kind, delay,
                )
                await asyncio.sleep(delay)
                continue

            yield StreamChunk(index=index, done=True)
            return

    # -- Manejo de recursos ------------------------------------------------

    async def __aenter__(self) -> "BaseLLMClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
