"""Script de prueba del cliente unificado.

Corre, para cada proveedor con API key cargada:

1. generación normal (respuesta completa),
2. generación en streaming (token a token),

y cierra con una demostración de manejo de errores: una key inválida a
propósito, que debe devolver un error estructurado en vez de romper el
programa.

    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from llm_client import (
    AsyncLLMManager,
    ModelConfig,
    ModelResponse,
    Provider,
    available_providers,
)

PREGUNTA = "¿Qué es la entropía?"
SISTEMA = "Sos un divulgador científico. Respondé en dos o tres oraciones, en español."


def _consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_consola_utf8()

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-8s | %(name)s | %(message)s",
)


def titulo(texto: str) -> None:
    print(f"\n{'=' * 72}\n {texto}\n{'=' * 72}")


def resumen(response: ModelResponse) -> None:
    """Imprime el resultado sin asumir que salió bien."""
    if not response.ok:
        assert response.error is not None
        print(f"  ERROR CONTROLADO -> {response.error}")
        return
    print(f"  {response.content.strip()}")
    print(
        f"  [modelo={response.model} | {response.latency_s:.2f}s | "
        f"tokens in/out={response.usage.input_tokens}/{response.usage.output_tokens} | "
        f"intentos={response.attempts} | fin={response.finish_reason}]"
    )


async def demo_proveedor(provider: Provider) -> None:
    titulo(f"Proveedor: {provider}")

    async with AsyncLLMManager.from_env(provider) as llm:
        print(f"{llm!r}\n")

        print("-- 1. Modo normal ------------------------------------------")
        respuesta = await llm.generate(PREGUNTA, system=SISTEMA)
        resumen(respuesta)

        print("\n-- 2. Modo streaming ---------------------------------------")
        print("  ", end="", flush=True)
        recibidos = 0
        async for chunk in llm.stream(PREGUNTA, system=SISTEMA):
            if not chunk.ok:
                print(f"\n  ERROR CONTROLADO -> {chunk.error}")
                break
            if chunk.done:
                print(f"\n  [fin del stream: {recibidos} fragmentos]")
                break
            print(chunk.delta, end="", flush=True)
            recibidos += 1


async def demo_error_controlado(provider: Provider) -> None:
    """Una key inválida no debe tirar abajo el programa."""
    titulo("Manejo de errores: API key inválida")

    # El fallo se imprime más abajo como resultado; el log lo duplicaría.
    logger_cliente = logging.getLogger("llm_client")
    nivel_previo = logger_cliente.level
    logger_cliente.setLevel(logging.CRITICAL)

    config = ModelConfig(
        provider=provider,
        model=AsyncLLMManager.from_env(provider).config.model,
        max_retries=0,  # no tiene sentido reintentar una key inválida
        timeout_s=10.0,
    )
    async with AsyncLLMManager(config, api_key="sk-clave-invalida-a-proposito") as llm:
        resumen(await llm.generate(PREGUNTA))

        print("  streaming con la misma key:")
        async for chunk in llm.stream(PREGUNTA):
            if not chunk.ok:
                print(f"  ERROR CONTROLADO -> {chunk.error}")
            if chunk.done:
                break

    print("\n  El programa sigue vivo: el error se devolvió, no se propagó.")


async def main() -> int:
    proveedores = available_providers()
    if not proveedores:
        print(
            "No hay ninguna API key configurada.\n"
            "Copiá .env.example a .env y completá al menos una de "
            "OPENAI_API_KEY, ANTHROPIC_API_KEY o GEMINI_API_KEY.",
            file=sys.stderr,
        )
        return 1

    # Las demos son secuenciales a propósito: el streaming se lee mejor así.
    for provider in proveedores:
        await demo_proveedor(provider)

    await demo_error_controlado(proveedores[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
