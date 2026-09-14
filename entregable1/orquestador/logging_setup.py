"""Logging con reloj relativo al arranque.

El criterio de aceptación pide *ver* que las tareas arrancan casi al mismo
tiempo. Un timestamp absoluto lo esconde; un offset `t+0.001s` lo hace obvio.
"""

from __future__ import annotations

import logging
import time

_T0 = time.perf_counter()


def elapsed() -> float:
    """Segundos transcurridos desde que se importó el módulo."""
    return time.perf_counter() - _T0


class RelativeTimeFormatter(logging.Formatter):
    """Antepone el offset relativo al inicio del proceso."""

    def format(self, record: logging.LogRecord) -> str:
        record.rel = f"t+{elapsed():07.3f}s"
        return super().format(record)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RelativeTimeFormatter(
            fmt="%(rel)s | %(levelname)-7s | %(name)-22s | %(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # httpx loguea cada request en INFO y ensucia la traza del ejercicio.
    logging.getLogger("httpx").setLevel(logging.WARNING)
