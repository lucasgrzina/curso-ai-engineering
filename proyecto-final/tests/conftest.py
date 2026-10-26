"""Dobles y fixtures compartidos por la suite.

Toda la suite corre **100% offline**: sin Redis, sin red y sin API keys. Se apoya
en los mismos puntos de inyección que usa la app en producción:

- `JobStore` recibe el cliente Redis por parámetro -> le pasamos un `FakeRedis`
  en memoria.
- `construir_grafo(...)` recibe las piezas (decisor, especialistas, síntesis) ->
  las reemplazamos por dobles deterministas (sin LLM).
- Los endpoints piden `store` y `grafo` vía `Depends` -> los sustituimos con
  `app.dependency_overrides`.
"""

from __future__ import annotations

import os

# La observabilidad jamás debe inicializarse en los tests.
os.environ.setdefault("OBSERVABILITY", "none")

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.graph import construir_grafo
from app.state import AgentState
from app.supervisor import DecisionSupervisor


# ---------------------------------------------------------------------------
# Redis de mentira (en memoria): implementa solo lo que usa el JobStore.
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self) -> None:
        self.h: dict[str, dict] = {}
        self.ttl: dict[str, int] = {}

    def hset(self, key, mapping=None):  # noqa: ANN001
        self.h.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    def expire(self, key, ttl):  # noqa: ANN001
        self.ttl[key] = ttl
        return True

    def exists(self, key):  # noqa: ANN001
        return 1 if key in self.h else 0

    def hgetall(self, key):  # noqa: ANN001
        return dict(self.h.get(key, {}))

    def ping(self):
        return True


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


# ---------------------------------------------------------------------------
# Dobles deterministas del grafo (mismos que usa la verificación offline).
# ---------------------------------------------------------------------------
def nodo_falso(nombre: str, texto: str):
    """Un especialista de mentira: deja una contribución fija, sin tocar un LLM."""

    def nodo(state: AgentState) -> dict:
        paso = state.get("pasos", 0) + 1
        return {
            "messages": [AIMessage(content=texto, name=nombre)],
            "contribuciones": [
                {"agente": nombre, "paso": paso, "instruccion": state["instruccion"], "aporte": texto}
            ],
            "pasos": paso,
        }

    return nodo


def decisor_guionado(guion: list[DecisionSupervisor]):
    """Devuelve las decisiones en orden; repite la última (simula un LLM terco)."""
    it = iter(guion)
    ultima = [guion[-1]]

    def decidir(state: AgentState) -> DecisionSupervisor:
        ultima[0] = next(it, ultima[0])
        return ultima[0]

    return decidir


def D(next_: str, instruccion: str = "x", razon: str = "r", requiere_calculo: bool = False) -> DecisionSupervisor:
    return DecisionSupervisor(next=next_, instruccion=instruccion, razon=razon, requiere_calculo=requiere_calculo)


def armar_grafo(guion: list[DecisionSupervisor], checkpointer=None):
    """Compila el grafo real con dobles en cada nodo. `checkpointer` default: MemorySaver."""
    return construir_grafo(
        decisor_guionado(guion),
        nodo_falso("investigador", "14 dias (<5 años), 21 (5-10), 28 (>10). Fuente: politica_vacaciones.txt"),
        nodo_falso("analista", "5*14 + 5*21 + 2*28 = 231"),
        lambda prompt: "FINAL: " + ("CORTE" if "ATENCIÓN" in prompt else "OK"),
    ).compile(checkpointer=checkpointer or MemorySaver())
