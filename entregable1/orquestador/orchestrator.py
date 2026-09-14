"""Orquestación concurrente: gather + timeout + semáforo.

Dos escenarios:

* `fan_out_with_timeout`  -> punto 3 y 4 del enunciado (gather + timeout total).
* `throttled_burst`       -> punto 5 (10 disparos, 2 concurrentes).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .config import Settings
from .logging_setup import elapsed
from .providers import PROVIDERS, CallContext, ModelResult, ProviderCall

log = logging.getLogger("orquestador.core")


# --------------------------------------------------------------------------
# Escenario A: fan-out de los 3 modelos con timeout total
# --------------------------------------------------------------------------
async def fan_out_with_timeout(prompt: str, ctx: CallContext) -> list[ModelResult]:
    """Dispara las tres llamadas a la vez bajo un único timeout global.

    `asyncio.timeout` cancela las tareas pendientes al vencer, así que después
    de capturar la excepción inspeccionamos cada tarea por separado: las que
    alcanzaron a terminar conservan su resultado y las canceladas se reportan
    como timeout. El programa continúa en ambos casos.
    """
    timeout_s = ctx.settings.total_timeout_s

    # Nota: creamos Tasks explícitas (no corrutinas sueltas) para poder leer su
    # estado individual después de que el timeout las cancele.
    tasks: dict[str, asyncio.Task[ModelResult]] = {
        name: asyncio.create_task(call(prompt, ctx), name=f"call:{name}")
        for name, call in PROVIDERS
    }
    log.info("Disparadas %d llamadas en paralelo (timeout total=%.1fs)", len(tasks), timeout_s)

    timed_out = False
    try:
        async with asyncio.timeout(timeout_s):
            await asyncio.gather(*tasks.values(), return_exceptions=True)
    except TimeoutError:
        timed_out = True
        log.error(
            "⏱ TimeoutError: la ejecución superó %.1fs. Se cancelan las llamadas "
            "pendientes y el programa CONTINÚA con resultados parciales.",
            timeout_s,
        )

    if timed_out:
        # `gather` ya volvió; aseguramos que las cancelaciones se propaguen.
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    return [_harvest(name, task, timeout_s) for name, task in tasks.items()]


def _harvest(name: str, task: asyncio.Task[ModelResult], timeout_s: float) -> ModelResult:
    """Convierte el estado final de una Task en un ModelResult uniforme."""
    if task.cancelled():
        return ModelResult(name, "-", False, error=f"timeout > {timeout_s:.1f}s")
    exc = task.exception()
    if exc is not None:
        return ModelResult(name, "-", False, error=f"{type(exc).__name__}: {exc}")
    return task.result()


# --------------------------------------------------------------------------
# Escenario B: 10 disparos, 2 concurrentes
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ConcurrencyTracker:
    """Registra cuántas corrutinas estuvieron dentro de la sección crítica."""

    active: int = 0
    peak: int = 0
    timeline: list[str] = field(default_factory=list)

    def enter(self, label: str) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.timeline.append(f"t+{elapsed():07.3f}s IN  {label} (activos={self.active})")

    def exit(self, label: str) -> None:
        self.active -= 1
        self.timeline.append(f"t+{elapsed():07.3f}s OUT {label} (activos={self.active})")


async def _throttled_call(
    index: int,
    name: str,
    call: ProviderCall,
    prompt: str,
    ctx: CallContext,
    tracker: ConcurrencyTracker,
) -> ModelResult:
    """Envuelve una llamada con el semáforo y un timeout individual."""
    assert ctx.semaphore is not None, "throttled_burst requiere un semáforo"
    label = f"#{index:02d} {name}"

    log.info("… %s encolada, esperando turno del semáforo", label)
    async with ctx.semaphore:
        tracker.enter(label)
        try:
            # Timeout por llamada: acá el objetivo es que un lento no frene al
            # lote entero, a diferencia del timeout global del escenario A.
            async with asyncio.timeout(ctx.settings.total_timeout_s):
                return await call(prompt, ctx)
        except TimeoutError:
            log.warning("⏱ %s superó el timeout individual", label)
            return ModelResult(
                name, "-", False,
                error=f"timeout > {ctx.settings.total_timeout_s:.1f}s",
            )
        finally:
            tracker.exit(label)


async def throttled_burst(
    prompt: str, ctx: CallContext
) -> tuple[list[ModelResult], ConcurrencyTracker]:
    """Encola `burst_size` llamadas pero deja correr sólo `semaphore_limit`."""
    total = ctx.settings.burst_size
    limit = ctx.settings.semaphore_limit
    ctx.semaphore = asyncio.Semaphore(limit)
    tracker = ConcurrencyTracker()

    # Rotamos entre los 3 proveedores para que el lote sea heterogéneo.
    tasks = [
        _throttled_call(i, *PROVIDERS[i % len(PROVIDERS)], prompt, ctx, tracker)
        for i in range(1, total + 1)
    ]
    log.info("Encoladas %d llamadas con semáforo de %d cupos", total, limit)

    # return_exceptions=True: un fallo puntual no tumba el lote completo.
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[ModelResult] = []
    for i, item in enumerate(raw, start=1):
        if isinstance(item, BaseException):
            results.append(
                ModelResult(f"#{i:02d}", "-", False, error=f"{type(item).__name__}: {item}")
            )
        else:
            results.append(item)
    return results, tracker


__all__ = [
    "CallContext",
    "ConcurrencyTracker",
    "Settings",
    "fan_out_with_timeout",
    "throttled_burst",
]
