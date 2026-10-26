"""Tests del grafo multi-agente + gate Human-in-the-loop (sin LLM real)."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import config
from app.graph import enrutar
from app.hitl import enrutar_aprobacion
from app.state import AgentState, estado_inicial
from app.supervisor import aplicar_guardas

from .conftest import D, armar_grafo


def cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# --- Estructura del estado -------------------------------------------------
def test_estado_hereda_messages_y_campos_propios():
    ini = estado_inicial("hola")
    assert "messages" in ini
    for campo in ("next_agent", "contribuciones", "pasos", "task_completed", "aprobacion_analista"):
        assert campo in AgentState.__annotations__


# --- Topología -------------------------------------------------------------
def test_topologia_con_gate_de_aprobacion():
    app = armar_grafo([D("investigador"), D("analista"), D("FINISH")])
    g = app.get_graph()
    nodos = set(g.nodes) - {"__start__", "__end__"}
    assert nodos == {"supervisor", "investigador", "aprobacion", "analista", "sintesis"}

    aristas = {(e.source, e.target) for e in g.edges}
    assert {("investigador", "supervisor"), ("analista", "supervisor")} <= aristas
    assert ("supervisor", "analista") not in aristas  # pasa por el gate
    assert ("aprobacion", "analista") in aristas
    assert ("investigador", "analista") not in aristas  # los especialistas no se hablan


def test_enrutar_desde_supervisor():
    assert enrutar({"next_agent": "analista"}) == "aprobacion"
    assert enrutar({"next_agent": "FINISH"}) == "sintesis"
    assert enrutar({"next_agent": "investigador"}) == "investigador"


# --- HITL: pausa -----------------------------------------------------------
def test_grafo_se_pausa_antes_del_analista():
    app = armar_grafo([D("analista", requiere_calculo=True), D("FINISH")])
    r1 = app.invoke(estado_inicial("pregunta con cálculo"), config=cfg("t-pausa"))
    assert "__interrupt__" in r1
    payload = r1["__interrupt__"][0].value
    assert payload["tipo"] == "aprobacion_requerida"
    assert payload["agente"] == "analista"


def test_reanudar_aprobando_corre_el_analista_y_sintetiza():
    c = cfg("t-aprueba")
    app = armar_grafo([D("analista", requiere_calculo=True), D("FINISH")])
    app.invoke(estado_inicial("pregunta con cálculo"), config=c)
    final = app.invoke(Command(resume={"aprobado": True}), config=c)
    agentes = [x["agente"] for x in final["contribuciones"]]
    assert "analista" in agentes
    assert final.get("aprobacion_analista") == "aprobada"
    assert final["messages"][-1].content.startswith("FINAL:")


def test_reanudar_rechazando_cierra_sin_analista():
    c = cfg("t-rechaza")
    app = armar_grafo([D("analista", requiere_calculo=True), D("FINISH")])
    app.invoke(estado_inicial("pregunta con cálculo"), config=c)
    final = app.invoke(Command(resume={"aprobado": False, "comentario": "no autorizado"}), config=c)
    agentes = [x["agente"] for x in final["contribuciones"]]
    assert "analista" not in agentes
    assert final.get("motivo_fin") == "rechazada_por_humano"
    assert final["messages"][-1].content == "FINAL: CORTE"  # la síntesis avisa del corte


def test_enrutar_aprobacion_sin_aprobar_va_a_sintesis():
    assert enrutar_aprobacion({"aprobacion_analista": ""}) == "sintesis"
    assert enrutar_aprobacion({"aprobacion_analista": "aprobada"}) == "analista"


def test_se_aprueba_una_sola_vez():
    c = cfg("t-unavez")
    app = armar_grafo([D("analista", requiere_calculo=True), D("analista", razon="refinar"), D("FINISH")])
    app.invoke(estado_inicial("pregunta"), config=c)
    r = app.invoke(Command(resume={"aprobado": True}), config=c)
    assert "__interrupt__" not in r  # no vuelve a pausar
    n_analista = sum(1 for x in r["contribuciones"] if x["agente"] == "analista")
    assert n_analista >= 2  # pudo refinar sin segunda aprobación


# --- Guardas anti "supervisor infinito" ------------------------------------
def test_guarda_no_cierra_sin_contribuciones():
    est = estado_inicial("p")
    destino, motivo = aplicar_guardas(est, D("FINISH"))
    assert destino == "investigador" and motivo == ""


def test_guarda_fuerza_analista_si_falta_calculo():
    est = estado_inicial("p")
    destino, _ = aplicar_guardas(
        {**est, "contribuciones": [{"agente": "investigador", "paso": 1, "instruccion": "", "aporte": "x"}]},
        D("FINISH", requiere_calculo=True),
    )
    assert destino == "analista"


def test_max_pasos_es_tope_duro(monkeypatch):
    monkeypatch.setattr(config, "MAX_PASOS", 3)
    c = cfg("t-tope")
    app = armar_grafo([D("investigador"), D("analista", requiere_calculo=True)] * 5)
    salida = app.invoke(estado_inicial("pregunta"), config=c)
    while "__interrupt__" in salida:  # auto-aprobamos para llegar al tope
        salida = app.invoke(Command(resume={"aprobado": True}), config=c)
    assert salida["pasos"] <= 3
    assert salida["motivo_fin"] in ("limite_pasos", "sin_progreso")
