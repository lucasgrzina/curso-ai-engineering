"""Tests del worker en segundo plano (app/worker.py): errores, pausa, costo."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.jobs import Estado, JobStore
from app.worker import ejecutar_tarea, reanudar_tarea


@pytest.fixture
def store(fake_redis) -> JobStore:
    return JobStore(fake_redis, ttl=3600)


def test_excepcion_en_background_deja_el_job_en_failed(store):
    store.crear("w-fail", "p")
    app_boom = SimpleNamespace(invoke=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla del agente")))
    ejecutar_tarea(store, app_boom, "w-fail", "p")
    job = store.obtener("w-fail")
    assert job.estado == Estado.FAILED
    assert "falla del agente" in job.error


def test_interrupt_en_background_deja_el_job_en_awaiting(store):
    store.crear("w-pause", "p")
    interrupt_obj = SimpleNamespace(value={"tipo": "aprobacion_requerida", "motivo": "x"})
    app_pausa = SimpleNamespace(invoke=lambda *a, **k: {"__interrupt__": [interrupt_obj]})
    ejecutar_tarea(store, app_pausa, "w-pause", "p")
    assert store.obtener("w-pause").estado == Estado.AWAITING_APPROVAL


def test_reanudar_y_terminar_deja_el_job_en_done(store):
    store.crear("w-done", "p")
    store.marcar_awaiting("w-done", {"motivo": "x"})
    estado_final = {
        "messages": [AIMessage(content="231 días", name="supervisor")],
        "contribuciones": [{"agente": "analista", "paso": 1, "instruccion": "", "aporte": "231"}],
        "motivo_fin": "suficiente",
    }
    app_done = SimpleNamespace(invoke=lambda *a, **k: estado_final)
    reanudar_tarea(store, app_done, "w-done", aprobado=True)
    job = store.obtener("w-done")
    assert job.estado == Estado.DONE
    assert job.resultado["respuesta_final"] == "231 días"


def test_worker_registra_el_costo_desde_el_callback(store):
    """El worker debe recoger los tokens del `UsageMetadataCallbackHandler` y
    persistir el costo en USD. El doble de app dispara el callback como lo haría
    una llamada real al LLM."""
    store.crear("w-costo", "p")

    def invoke_que_consume_tokens(_entrada, config=None, **_kw):
        # Simula el reporte de tokens que haría un ChatModel real durante la corrida.
        callbacks = (config or {}).get("callbacks") or []
        msg = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
            response_metadata={"model_name": "gpt-4o-mini"},
        )
        res = LLMResult(generations=[[ChatGeneration(message=msg)]], llm_output={"model_name": "gpt-4o-mini"})
        for cb in callbacks:
            cb.on_llm_end(res)
        return {
            "messages": [AIMessage(content="listo", name="supervisor")],
            "contribuciones": [{"agente": "analista", "paso": 1, "instruccion": "", "aporte": "x"}],
            "motivo_fin": "suficiente",
        }

    app = SimpleNamespace(invoke=invoke_que_consume_tokens)
    ejecutar_tarea(store, app, "w-costo", "p")

    job = store.obtener("w-costo")
    assert job.estado == Estado.DONE
    assert job.costo is not None
    assert job.costo["total_tokens"] == 1500
    esperado = (1000 * 0.15 + 500 * 0.60) / 1_000_000
    assert job.costo["costo_usd"] == pytest.approx(round(esperado, 6))
