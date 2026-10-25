"""Tests de la máquina de estados del JobStore (app/jobs.py) sobre Redis falso."""

from __future__ import annotations

import pytest

from app.jobs import Estado, JobStore


@pytest.fixture
def store(fake_redis) -> JobStore:
    return JobStore(fake_redis, ttl=3600)


def test_crear_arranca_en_pending(store):
    store.crear("j1", "¿cuántos días?")
    job = store.obtener("j1")
    assert job.estado == Estado.PENDING
    assert job.pregunta == "¿cuántos días?"


def test_existe_detecta_el_job(store):
    store.crear("j1", "p")
    assert store.existe("j1")
    assert not store.existe("fantasma")


def test_transiciones_running_awaiting_done(store):
    store.crear("j1", "p")
    store.marcar_running("j1")
    assert store.obtener("j1").estado == Estado.RUNNING

    store.marcar_awaiting("j1", {"tipo": "aprobacion_requerida", "motivo": "x"})
    job = store.obtener("j1")
    assert job.estado == Estado.AWAITING_APPROVAL
    assert job.aprobacion == {"tipo": "aprobacion_requerida", "motivo": "x"}

    store.marcar_done("j1", {"respuesta_final": "231 días"})
    job = store.obtener("j1")
    assert job.estado == Estado.DONE
    assert job.resultado["respuesta_final"] == "231 días"
    assert job.aprobacion is None  # se limpia al cerrar


def test_marcar_failed_guarda_el_error(store):
    store.crear("j2", "otra")
    store.marcar_failed("j2", "BoomError: explotó")
    job = store.obtener("j2")
    assert job.estado == Estado.FAILED
    assert "Boom" in job.error


def test_obtener_inexistente_levanta_keyerror(store):
    with pytest.raises(KeyError):
        store.obtener("fantasma")


def test_ttl_se_aplica_en_cada_escritura(store, fake_redis):
    store.crear("j1", "p")
    assert fake_redis.ttl["job:j1"] == 3600


def test_sumar_costo_acumula_a_traves_de_varias_corridas(store):
    """Simula el patrón del worker: dos invokes (ejecución + reanudación HITL)."""
    store.crear("j1", "p")
    store.sumar_costo("j1", {"gpt-4o-mini": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}})
    store.sumar_costo("j1", {"gpt-4o-mini": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})

    costo = store.obtener("j1").costo
    assert costo["input_tokens"] == 110
    assert costo["output_tokens"] == 45
    assert costo["total_tokens"] == 155
    esperado = (110 * 0.15 + 45 * 0.60) / 1_000_000
    assert costo["costo_usd"] == pytest.approx(round(esperado, 6))


def test_sumar_costo_vacio_no_crea_costo(store):
    """Sin llamadas al LLM (dobles de test) el job no arrastra un costo espurio."""
    store.crear("j1", "p")
    store.sumar_costo("j1", {})
    assert store.obtener("j1").costo is None


def test_costo_sobrevive_al_marcar_done(store):
    store.crear("j1", "p")
    store.sumar_costo("j1", {"gpt-4o-mini": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}})
    store.marcar_done("j1", {"respuesta_final": "ok"})
    job = store.obtener("j1")
    assert job.estado == Estado.DONE
    assert job.costo["total_tokens"] == 140  # el costo no se pierde al cerrar
