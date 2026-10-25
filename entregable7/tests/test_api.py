"""Tests de los endpoints FastAPI (app/main.py) con dobles inyectados.

No se levanta Redis ni se usa un LLM: se sobreescriben las dependencias `get_store`
(con un `JobStore` sobre `FakeRedis`) y `get_graph` (con el grafo real compilado
con dobles deterministas). El `TestClient` se usa SIN context manager a propósito,
para no disparar el `lifespan` (que conectaría a Redis y Phoenix).

Las `BackgroundTasks` de FastAPI corren de forma síncrona tras enviar la respuesta,
así que al volver de `client.post` el job ya fue procesado por el grafo de prueba.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.jobs import Estado, JobStore

from .conftest import D, FakeRedis, armar_grafo


@pytest.fixture
def store() -> JobStore:
    return JobStore(FakeRedis(), ttl=3600)


def hacer_client(store: JobStore, grafo) -> TestClient:
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_graph] = lambda: grafo
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _limpiar_overrides():
    yield
    main.app.dependency_overrides.clear()


# --- Camino feliz sin HITL -------------------------------------------------
def test_crear_tarea_devuelve_202_y_termina_en_done(store):
    grafo = armar_grafo([D("investigador"), D("FINISH")])  # no requiere cálculo -> no pausa
    client = hacer_client(store, grafo)

    r = client.post("/tasks", json={"pregunta": "¿cuántos días de vacaciones?"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert r.json()["estado"] == "PENDING"

    estado = client.get(f"/tasks/{job_id}").json()
    assert estado["estado"] == "DONE"
    assert estado["resultado"]["respuesta_final"].startswith("FINAL:")


# --- Camino con Human-in-the-loop -----------------------------------------
def test_flujo_hitl_pausa_aprueba_y_termina(store):
    grafo = armar_grafo([D("analista", requiere_calculo=True), D("FINISH")])
    client = hacer_client(store, grafo)

    job_id = client.post("/tasks", json={"pregunta": "calculá el total"}).json()["job_id"]

    estado = client.get(f"/tasks/{job_id}").json()
    assert estado["estado"] == "AWAITING_APPROVAL"
    assert estado["aprobacion"]["tipo"] == "aprobacion_requerida"

    r = client.post(f"/tasks/{job_id}/approve", json={"aprobado": True})
    assert r.status_code == 200
    assert r.json()["estado"] == "RUNNING"

    final = client.get(f"/tasks/{job_id}").json()
    assert final["estado"] == "DONE"


def test_rechazo_hitl_cierra_sin_calcular(store):
    grafo = armar_grafo([D("analista", requiere_calculo=True), D("FINISH")])
    client = hacer_client(store, grafo)
    job_id = client.post("/tasks", json={"pregunta": "calculá el total"}).json()["job_id"]
    client.post(f"/tasks/{job_id}/approve", json={"aprobado": False, "comentario": "no autorizado"})

    final = client.get(f"/tasks/{job_id}").json()
    assert final["estado"] == "DONE"
    agentes = [c["agente"] for c in final["resultado"]["contribuciones"]]
    assert "analista" not in agentes


# --- Errores y validaciones ------------------------------------------------
def test_get_de_job_inexistente_da_404(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    assert client.get("/tasks/noexiste").status_code == 404


def test_approve_de_job_inexistente_da_404(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    r = client.post("/tasks/noexiste/approve", json={"aprobado": True})
    assert r.status_code == 404


def test_approve_sobre_job_terminal_da_409(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    store.crear("ya-done", "p")
    store.marcar_done("ya-done", {"respuesta_final": "ok"})
    r = client.post("/tasks/ya-done/approve", json={"aprobado": True})
    assert r.status_code == 409


def test_approve_sobre_job_que_no_espera_aprobacion_da_409(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    store.crear("corriendo", "p")
    store.marcar_running("corriendo")
    r = client.post("/tasks/corriendo/approve", json={"aprobado": True})
    assert r.status_code == 409


def test_crear_tarea_valida_pregunta_minima(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    r = client.post("/tasks", json={"pregunta": "ab"})  # min_length=3
    assert r.status_code == 422


def test_health_responde_aunque_no_haya_redis(store):
    client = hacer_client(store, armar_grafo([D("FINISH")]))
    r = client.get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert "redis" in cuerpo and "observabilidad" in cuerpo
