"""Clasificación de excepciones y reintentos con backoff.

Los SDKs `openai` y `anthropic` se generan con la misma herramienta y exponen
jerarquías de excepción con nombres idénticos (`RateLimitError`,
`APIConnectionError`, `APIStatusError`, ...). Clasificar por nombre de clase,
recorriendo el MRO, evita importar ambos SDKs acá sólo para armar tuplas de
`except` y funciona igual si el usuario instala sólo uno de los dos.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .schemas import ErrorKind, LLMError, Provider, RETRYABLE_KINDS

T = TypeVar("T")

# Nombre de clase del SDK -> categoría propia.
_KIND_BY_CLASS: dict[str, ErrorKind] = {
    "AuthenticationError": ErrorKind.AUTH,
    "PermissionDeniedError": ErrorKind.AUTH,
    "RateLimitError": ErrorKind.RATE_LIMIT,
    "APITimeoutError": ErrorKind.TIMEOUT,
    "APIConnectionError": ErrorKind.CONNECTION,
    "BadRequestError": ErrorKind.BAD_REQUEST,
    "NotFoundError": ErrorKind.BAD_REQUEST,
    "UnprocessableEntityError": ErrorKind.BAD_REQUEST,
    "InternalServerError": ErrorKind.SERVER,
    "APIStatusError": ErrorKind.SERVER,
}


def _kind_from_status(status: int | None) -> ErrorKind | None:
    if status is None:
        return None
    if status == 429:
        return ErrorKind.RATE_LIMIT
    if status in (401, 403):
        return ErrorKind.AUTH
    if status >= 500:
        return ErrorKind.SERVER
    if status >= 400:
        return ErrorKind.BAD_REQUEST
    return None


def classify(exc: BaseException, provider: Provider, attempts: int = 1) -> LLMError:
    """Convierte cualquier excepción en un `LLMError` estructurado."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None

    kind: ErrorKind | None = None
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        kind = ErrorKind.TIMEOUT
    else:
        # El MRO permite que una subclase propia del SDK caiga en su categoría.
        for cls in type(exc).__mro__:
            if cls.__name__ in _KIND_BY_CLASS:
                kind = _KIND_BY_CLASS[cls.__name__]
                break

    # El status manda sobre `APIStatusError`, que es demasiado genérica.
    by_status = _kind_from_status(status)
    if by_status is not None and kind in (None, ErrorKind.SERVER):
        kind = by_status
    if kind is None:
        kind = ErrorKind.UNKNOWN

    message = str(exc).strip() or type(exc).__name__
    return LLMError(
        kind=kind,
        message=message,
        provider=provider,
        retryable=kind in RETRYABLE_KINDS,
        attempts=attempts,
        status_code=status,
    )


def retry_after_seconds(exc: BaseException) -> float | None:
    """Lee la cabecera `retry-after` cuando el proveedor la manda (429)."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        # También puede venir como fecha HTTP; no vale la pena parsearla.
        return None


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Backoff exponencial con jitter completo, para no sincronizar reintentos."""
    return random.uniform(0.0, min(cap, base * (2**attempt)))


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    provider: Provider,
    max_retries: int,
    on_retry: Callable[[int, LLMError, float], None] | None = None,
) -> tuple[T | None, LLMError | None, int]:
    """Ejecuta `operation` reintentando sólo los fallos recuperables.

    Devuelve `(resultado, error, intentos)`: exactamente uno de los dos
    primeros es `None`. Nunca relanza la excepción del SDK.
    """
    last_error: LLMError | None = None

    for attempt in range(max_retries + 1):
        try:
            return await operation(), None, attempt + 1
        except asyncio.CancelledError:
            # Cancelar no es un fallo del proveedor: debe propagarse siempre.
            raise
        except Exception as exc:  # noqa: BLE001 - la política es capturar todo
            last_error = classify(exc, provider, attempts=attempt + 1)
            if not last_error.retryable or attempt == max_retries:
                break
            delay = retry_after_seconds(exc) or backoff_delay(attempt)
            if on_retry is not None:
                on_retry(attempt + 1, last_error, delay)
            await asyncio.sleep(delay)

    assert last_error is not None
    return None, last_error, last_error.attempts
