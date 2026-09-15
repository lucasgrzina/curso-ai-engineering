"""Punto de entrada. Un único `asyncio.run()`, como manda Python 3.12."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from dataclasses import replace

from .config import Settings
from .logging_setup import elapsed, setup_logging
from .orchestrator import (
    CallContext,
    ConcurrencyTracker,
    fan_out_with_timeout,
    throttled_burst,
)
from .providers import ModelResult

log = logging.getLogger("orquestador.main")

DEFAULT_PROMPT = "Explicá qué es la entropía en dos oraciones breves."


def _banner(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def _print_results(results: list[ModelResult]) -> None:
    print(f"\n  {'PROVEEDOR':<14} {'ESTADO':<8} {'LATENCIA':>9}  DETALLE")
    print(f"  {'-' * 74}")
    for r in results:
        estado = "OK" if r.ok else "FALLÓ"
        marca = " (sim)" if r.simulated else ""
        print(f"  {r.provider:<14} {estado:<8} {r.latency_s:>8.2f}s  {r.preview()}{marca}")


def _print_tracker(tracker: ConcurrencyTracker, limit: int) -> None:
    print("\n  Traza del semáforo (entradas/salidas de la sección crítica):")
    for line in tracker.timeline:
        print(f"    {line}")
    veredicto = "✓ CUMPLE" if tracker.peak <= limit else "✗ VIOLADO"
    print(f"\n  Concurrencia máxima observada: {tracker.peak} (límite {limit}) → {veredicto}")


async def run_demo(prompt: str, settings: Settings, scenario: str) -> int:
    """Ejecuta los escenarios pedidos. Devuelve 0: un timeout es un caso
    contemplado del ejercicio, no un fallo del programa."""
    timeout = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
    # Un único AsyncClient reutiliza conexiones entre llamadas concurrentes.
    async with httpx.AsyncClient(timeout=timeout) as http:
        ctx = CallContext(settings=settings, http=http)

        if scenario in ("all", "fanout"):
            _banner("ESCENARIO A · asyncio.gather + asyncio.timeout(%.1fs)" % settings.total_timeout_s)
            results = await fan_out_with_timeout(prompt, ctx)
            _print_results(results)

        if scenario in ("all", "burst"):
            _banner(
                "ESCENARIO B · %d llamadas, asyncio.Semaphore(%d)"
                % (settings.burst_size, settings.semaphore_limit)
            )
            results, tracker = await throttled_burst(prompt, ctx)
            _print_results(results)
            _print_tracker(tracker, settings.semaphore_limit)

        print(f"\n  Tiempo total del proceso: {elapsed():.2f}s")
        print("  El programa terminó de forma controlada pese a los timeouts.\n")
        return 0


def cli() -> int:
    parser = argparse.ArgumentParser(
        prog="orquestador",
        description="Orquestador concurrente de modelos (asyncio, Python 3.12).",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt a enviar a los modelos.")
    parser.add_argument(
        "--scenario", choices=("all", "fanout", "burst"), default="all",
        help="Qué escenario ejecutar (por defecto: ambos).",
    )
    parser.add_argument("--timeout", type=float, help="Sobrescribe el timeout en segundos.")
    parser.add_argument("--verbose", action="store_true", help="Logging en nivel DEBUG.")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    settings = Settings.from_env()
    if args.timeout is not None:
        settings = replace(settings, total_timeout_s=args.timeout)

    log.info("Configuración → %s", settings.describe())
    if not settings.gemini_api_key and not settings.groq_api_key:
        log.warning(
            "Sin API keys: los proveedores corren en modo simulado. "
            "Copiá .env.example a .env y cargá tus claves para llamadas reales."
        )

    try:
        return asyncio.run(run_demo(args.prompt, settings, args.scenario))
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario.")
        return 130


if __name__ == "__main__":
    sys.exit(cli())
