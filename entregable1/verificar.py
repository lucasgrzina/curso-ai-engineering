"""Chequeo de los criterios de aceptación, sin gastar cuota.

No llama a ninguna API: los proveedores se reemplazan por dobles que simulan
respuestas y fallos. Sirve para verificar que la estructura cumple la consigna
antes de cargar las claves.

    python verificar.py
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from collections.abc import AsyncIterator, Sequence

from pydantic import ValidationError

from llm_client import (
    AsyncLLMManager,
    BaseLLMClient,
    ChatMessage,
    ErrorKind,
    ModelConfig,
    ModelResponse,
    Provider,
    Role,
    Usage,
)
from llm_client.providers import AnthropicClient, OpenAIClient


def _consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_consola_utf8()

# Los fallos simulados son parte de la prueba: no deben ensuciar la salida.
logging.getLogger("llm_client").setLevel(logging.CRITICAL)

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "OK " if condicion else "FALLA"
    sufijo = f" - {detalle}" if detalle else ""
    print(f"  [{marca}] {nombre}{sufijo}")
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


class ErrorFalso(Exception):
    """Imita un error del SDK: la clasificación mira el nombre de la clase."""

    def __init__(self, nombre: str, status: int | None = None):
        super().__init__(f"fallo simulado ({nombre})")
        type(self).__name__ = nombre
        self.status_code = status


class ClienteFalso(BaseLLMClient):
    """Falla las primeras `fallos_previos` llamadas y después responde."""

    def __init__(
        self,
        config: ModelConfig,
        *,
        fallos_previos: int = 0,
        error: Exception | None = None,
    ):
        super().__init__(config, api_key="clave-de-prueba")
        self.fallos_previos = fallos_previos
        self.error = error or ErrorFalso("RateLimitError", 429)
        self.llamadas = 0

    async def _raw_generate(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.llamadas += 1
        if self.llamadas <= self.fallos_previos:
            raise self.error
        return ModelResponse(
            provider=self.config.provider,
            model=self.config.model,
            content="respuesta simulada",
            usage=Usage(input_tokens=7, output_tokens=3),
        )

    async def _raw_stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.llamadas += 1
        if self.llamadas <= self.fallos_previos:
            raise self.error
        for palabra in ("la ", "entropía ", "mide "):
            await asyncio.sleep(0)
            yield palabra

    async def aclose(self) -> None:
        return None


CONFIG = ModelConfig(provider=Provider.OPENAI, model="modelo-de-prueba", max_retries=2)
MENSAJES = [ChatMessage(role=Role.USER, content="hola")]


# ---------------------------------------------------------------------------
# Criterios
# ---------------------------------------------------------------------------


def verificar_schemas() -> None:
    seccion("1. Validación con Pydantic")

    ok = ModelConfig(provider=Provider.OPENAI, model="gpt-4o-mini", temperature=1.5)
    check("temperature dentro de 0..2 se acepta", ok.temperature == 1.5)

    for valor in (-0.1, 2.1):
        try:
            ModelConfig(provider=Provider.OPENAI, model="m", temperature=valor)
            check(f"temperature={valor} rechazada", False)
        except ValidationError:
            check(f"temperature={valor} rechazada", True)

    try:
        ModelConfig(provider=Provider.OPENAI, model="m", max_tokens=0)
        check("max_tokens=0 rechazado", False)
    except ValidationError:
        check("max_tokens=0 rechazado", True)

    try:
        ModelConfig(provider=Provider.ANTHROPIC, model="claude-opus-5", temperature=1.8)
        check("temperature>1 rechazada para Anthropic", False)
    except ValidationError:
        check("temperature>1 rechazada para Anthropic", True)

    try:
        ChatMessage(role=Role.USER, content="")
        check("mensaje vacío rechazado", False)
    except ValidationError:
        check("mensaje vacío rechazado", True)

    try:
        ChatMessage(role="pirata", content="hola")  # type: ignore[arg-type]
        check("rol desconocido rechazado", False)
    except ValidationError:
        check("rol desconocido rechazado", True)


def verificar_estructura() -> None:
    seccion("2. Interfaz común e intercambiabilidad")

    check(
        "OpenAIClient y AnthropicClient heredan de BaseLLMClient",
        issubclass(OpenAIClient, BaseLLMClient)
        and issubclass(AnthropicClient, BaseLLMClient),
    )
    check("BaseLLMClient es abstracta", inspect.isabstract(BaseLLMClient))
    check(
        "generate() es corutina en la interfaz base",
        inspect.iscoroutinefunction(BaseLLMClient.generate),
    )
    check(
        "stream() es generador asíncrono",
        inspect.isasyncgenfunction(BaseLLMClient.stream)
        and inspect.isasyncgenfunction(AsyncLLMManager.stream),
    )
    check(
        "el manager conoce ambos proveedores",
        {AsyncLLMManager._client_class(p) for p in Provider}
        == {OpenAIClient, AnthropicClient},
    )


async def verificar_runtime() -> None:
    seccion("3. Asincronía, streaming y reintentos")

    cliente = ClienteFalso(CONFIG)
    respuesta = await cliente.generate(MENSAJES)
    check("generate() devuelve ModelResponse.ok", respuesta.ok and respuesta.content != "")
    check("mide la latencia", respuesta.latency_s >= 0.0)

    fragmentos = [c async for c in cliente.stream(MENSAJES)]
    check("stream() emite fragmentos", len([c for c in fragmentos if c.delta]) == 3)
    check("el último fragmento marca done", fragmentos[-1].done and fragmentos[-1].ok)

    # Reintento: falla dos veces con 429 y a la tercera responde.
    reintenta = ClienteFalso(CONFIG, fallos_previos=2)
    respuesta = await reintenta.generate(MENSAJES)
    check("reintenta un 429 y se recupera", respuesta.ok and respuesta.attempts == 3)

    # Tres llamadas concurrentes no deben serializarse: si el cliente
    # bloqueara el event loop, el total sería la suma y no el máximo.
    lento = ClienteFalso(CONFIG)

    async def tarea() -> None:
        await asyncio.sleep(0.2)
        await lento.generate(MENSAJES)

    reloj = asyncio.get_running_loop().time
    inicio = reloj()
    await asyncio.gather(tarea(), tarea(), tarea())
    transcurrido = reloj() - inicio
    check(
        "3 llamadas concurrentes no se serializan",
        transcurrido < 0.5,
        f"{transcurrido:.2f}s (serializado serían ~0.6s)",
    )


async def verificar_errores() -> None:
    seccion("4. Errores controlados, sin excepciones que se escapen")

    casos = [
        ("AuthenticationError", 401, ErrorKind.AUTH, False),
        ("RateLimitError", 429, ErrorKind.RATE_LIMIT, True),
        ("APIConnectionError", None, ErrorKind.CONNECTION, True),
        ("APITimeoutError", None, ErrorKind.TIMEOUT, True),
        ("BadRequestError", 400, ErrorKind.BAD_REQUEST, False),
        ("InternalServerError", 503, ErrorKind.SERVER, True),
    ]
    sin_reintentos = CONFIG.model_copy(update={"max_retries": 0})

    for nombre, status, esperado, reintentable in casos:
        cliente = ClienteFalso(
            sin_reintentos, fallos_previos=99, error=ErrorFalso(nombre, status)
        )
        respuesta = await cliente.generate(MENSAJES)
        assert respuesta.error is not None
        check(
            f"{nombre} -> {esperado}",
            respuesta.error.kind is esperado
            and respuesta.error.retryable is reintentable,
            f"clasificado como {respuesta.error.kind}",
        )

    # El mismo fallo en streaming llega como chunk, no como excepción.
    cliente = ClienteFalso(
        sin_reintentos, fallos_previos=99, error=ErrorFalso("AuthenticationError", 401)
    )
    fragmentos = [c async for c in cliente.stream(MENSAJES)]
    check(
        "un fallo en streaming vuelve como chunk con error",
        len(fragmentos) == 1
        and fragmentos[0].done
        and fragmentos[0].error is not None,
    )

    # Un error no reintentable no debe consumir reintentos.
    cliente = ClienteFalso(
        CONFIG, fallos_previos=99, error=ErrorFalso("AuthenticationError", 401)
    )
    await cliente.generate(MENSAJES)
    check(
        "no reintenta una API key inválida",
        cliente.llamadas == 1,
        f"llamadas={cliente.llamadas}",
    )


async def main() -> int:
    print("Verificación del Entregable 1 - Cliente de LLM async (sin llamadas reales)")
    verificar_schemas()
    verificar_estructura()
    await verificar_runtime()
    await verificar_errores()

    print(f"\n{'=' * 72}")
    if fallos:
        print(f"FALLARON {len(fallos)} criterios: {', '.join(fallos)}")
        return 1
    print("Todos los criterios se cumplen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
