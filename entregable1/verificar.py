"""Chequeo automático de los tres criterios de aceptación del enunciado.

Uso:  python verificar.py
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys

import httpx

from dataclasses import replace

from orquestador.config import Settings
from orquestador.logging_setup import setup_logging
from orquestador.orchestrator import CallContext, fan_out_with_timeout, throttled_burst

SRC = pathlib.Path(__file__).parent / "orquestador"


def criterio_1_sin_time_sleep() -> tuple[bool, str]:
    """El código no debe usar time.sleep (bloqueante)."""
    patron = re.compile(r"\btime\.sleep\s*\(")
    culpables = [
        f.name for f in SRC.glob("*.py") if patron.search(f.read_text(encoding="utf-8"))
    ]
    if culpables:
        return False, f"time.sleep encontrado en: {', '.join(culpables)}"
    return True, "ningún módulo usa time.sleep; sólo await asyncio.sleep"


async def criterio_2_inicio_simultaneo() -> tuple[bool, str]:
    """Las tareas deben iniciar casi al mismo tiempo."""
    # Forzamos modo simulado: así los tres arranques son observables sin
    # depender de la red ni de que existan API keys.
    settings = replace(Settings.from_env(), gemini_api_key=None, groq_api_key=None)
    marcas: list[float] = []

    loop = asyncio.get_running_loop()
    inicio = loop.time()

    async with httpx.AsyncClient() as http:
        ctx = CallContext(settings=settings, http=http)
        original = asyncio.sleep

        async def espia(delay, *a, **kw):
            marcas.append(loop.time() - inicio)
            return await original(delay, *a, **kw)

        asyncio.sleep = espia  # type: ignore[assignment]
        try:
            await fan_out_with_timeout("ping", ctx)
        finally:
            asyncio.sleep = original  # type: ignore[assignment]

    if len(marcas) < 3:
        return False, f"se esperaban 3 arranques, se observaron {len(marcas)}"
    dispersion = max(marcas) - min(marcas)
    ok = dispersion < 0.05
    return ok, f"dispersión entre arranques: {dispersion * 1000:.1f} ms (umbral 50 ms)"


async def criterio_3_timeout_y_semaforo() -> tuple[bool, str]:
    """Se maneja TimeoutError y el semáforo limita la concurrencia."""
    settings = replace(Settings.from_env(), gemini_api_key=None, groq_api_key=None)
    async with httpx.AsyncClient() as http:
        ctx = CallContext(settings=settings, http=http)
        resultados, tracker = await throttled_burst("ping", ctx)

    hubo_timeout = any(r.error and "timeout" in r.error for r in resultados)
    respeto_limite = tracker.peak <= settings.semaphore_limit
    completo = len(resultados) == settings.burst_size

    detalle = (
        f"timeouts capturados={hubo_timeout}, pico de concurrencia={tracker.peak}"
        f"/{settings.semaphore_limit}, resultados devueltos={len(resultados)}"
        f"/{settings.burst_size}"
    )
    return (hubo_timeout and respeto_limite and completo), detalle


async def main() -> int:
    setup_logging(level=40)  # sólo ERROR: la salida del verificador debe ser limpia
    print("\nVerificación de criterios de aceptación\n" + "-" * 62)

    resultados = [
        ("1. No se usa time.sleep (bloqueante)", *criterio_1_sin_time_sleep()),
        ("2. Las tareas inician casi simultáneamente", *await criterio_2_inicio_simultaneo()),
        ("3. TimeoutError manejado + semáforo activo", *await criterio_3_timeout_y_semaforo()),
    ]

    for nombre, ok, detalle in resultados:
        print(f"  [{'PASS' if ok else 'FAIL'}] {nombre}\n         {detalle}")

    fallos = sum(1 for _, ok, _ in resultados if not ok)
    print("-" * 62)
    print(f"  {len(resultados) - fallos}/{len(resultados)} criterios cumplidos\n")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
