"""Fábrica del checkpointer asíncrono de LangGraph sobre Redis.

El `AsyncRedisSaver` persiste los checkpoints del grafo en Redis y expone los
métodos `async` que necesita la ejecución con `app.ainvoke(...)`. Es lo que permite
que el grafo se pause en el `interrupt()` del HITL en un request y se reanude en
otro (`POST /tasks/{id}/approve`), usando `thread_id = job_id` como hilo. Se crea
una sola vez y su `asetup()` (índices de RediSearch) se espera en el `lifespan`.
"""

from __future__ import annotations

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from app import config

_SAVER: AsyncRedisSaver | None = None


async def crear_checkpointer_redis() -> AsyncRedisSaver:
    """Crea (una sola vez) el AsyncRedisSaver y ejecuta su `asetup()` de índices."""
    global _SAVER
    if _SAVER is None:
        saver = AsyncRedisSaver(config.REDIS_URL)
        await saver.asetup()  # crea los índices de búsqueda que usa el saver
        _SAVER = saver
    return _SAVER
