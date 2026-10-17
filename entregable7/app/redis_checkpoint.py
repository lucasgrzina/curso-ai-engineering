"""Fábrica del checkpointer de LangGraph sobre Redis.

El `RedisSaver` persiste los checkpoints del grafo en Redis. Es lo que permite
que el grafo se pause en el `interrupt()` del HITL en un request y se reanude en
otro (`POST /tasks/{id}/approve`), usando `thread_id = job_id` como hilo.
"""

from __future__ import annotations

from langgraph.checkpoint.redis import RedisSaver

from app import config

_SAVER: RedisSaver | None = None


def crear_checkpointer_redis() -> RedisSaver:
    """Crea (una sola vez) el RedisSaver y ejecuta su `setup()` de índices."""
    global _SAVER
    if _SAVER is None:
        saver = RedisSaver(config.REDIS_URL)
        saver.setup()  # crea los índices de búsqueda que usa el saver
        _SAVER = saver
    return _SAVER
