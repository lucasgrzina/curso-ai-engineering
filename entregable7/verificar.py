"""Chequeos deterministas del Entregable 7 (100% offline, sin Redis ni API keys).

Reutiliza la estrategia del Módulo 6 (dobles inyectados en `construir_grafo`) y
suma lo propio de esta entrega:

- Gate HITL: el grafo se PAUSA con `interrupt()` antes del analista y se REANUDA
  con `Command(resume=...)` (aprobado -> corre; rechazado -> cierra sin cálculo).
- JobStore sobre un Redis de mentira en memoria: máquina de estados PENDING ->
  RUNNING -> AWAITING_APPROVAL -> DONE / FAILED.
- Worker: una excepción en segundo plano deja el job en FAILED (no en loop).

Es la evidencia verificable por cualquiera que clone el repo: no necesita red,
ni servidor Redis, ni claves. Se corre con:  python verificar.py
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app import config
from app.agents.herramientas import buscar_en_politicas, calculadora, calcular, estadisticas
from app.graph import construir_grafo, enrutar
from app.hitl import enrutar_aprobacion
from app.jobs import Estado, Job, JobStore
from app.state import AgentState, estado_inicial
from app.supervisor import DecisionSupervisor, aplicar_guardas
from app.worker import ejecutar_tarea, reanudar_tarea

config.consola_utf8()
fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"  [{'OK ' if condicion else 'FALLA'}] {nombre}" + (f" - {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


# --------------------------------------------------------------------------
# Dobles deterministas (sin LLM)
# --------------------------------------------------------------------------

def nodo_falso(nombre: str, texto: str):
    def nodo(state: AgentState) -> dict:
        paso = state.get("pasos", 0) + 1
        return {
            "messages": [AIMessage(content=texto, name=nombre)],
            "contribuciones": [{"agente": nombre, "paso": paso, "instruccion": state["instruccion"], "aporte": texto}],
            "pasos": paso,
        }

    return nodo


def decisor_guionado(guion: list[DecisionSupervisor]):
    """Devuelve las decisiones en orden; la última se repite (simula un LLM terco)."""
    it = iter(guion)
    ultima = [guion[-1]]

    def decidir(state: AgentState) -> DecisionSupervisor:
        ultima[0] = next(it, ultima[0])
        return ultima[0]

    return decidir


def armar(guion: list[DecisionSupervisor], checkpointer=None):
    return construir_grafo(
        decisor_guionado(guion),
        nodo_falso("investigador", "14 dias (<5 años), 21 (5-10), 28 (>10). Fuente: politica_vacaciones.txt"),
        nodo_falso("analista", "5*14 + 5*21 + 2*28 = 231"),
        lambda prompt: "FINAL: " + ("CORTE" if "ATENCIÓN" in prompt else "OK"),
    ).compile(checkpointer=checkpointer)


def D(next_: str, instruccion: str = "x", razon: str = "r", requiere_calculo: bool = False) -> DecisionSupervisor:
    return DecisionSupervisor(next=next_, instruccion=instruccion, razon=razon, requiere_calculo=requiere_calculo)


# --------------------------------------------------------------------------
# Redis de mentira (en memoria) para probar el JobStore sin servidor
# --------------------------------------------------------------------------

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


def main() -> int:
    config_hilo = lambda tid: {"configurable": {"thread_id": tid}}  # noqa: E731

    seccion("1. Estado compartido (M6 + campo HITL)")
    ini = estado_inicial("hola")
    check("hereda MessagesState (campo messages)", "messages" in AgentState.__annotations__ or "messages" in ini)
    for campo in ("next_agent", "contribuciones", "pasos", "task_completed", "aprobacion_analista"):
        check(f"campo propio '{campo}'", campo in AgentState.__annotations__)

    seccion("2. Topología del grafo (gate HITL entre supervisor y analista)")
    app = armar([D("investigador"), D("analista"), D("FINISH")], checkpointer=MemorySaver())
    g = app.get_graph()
    nodos = set(g.nodes) - {"__start__", "__end__"}
    check("nodos: supervisor + 2 especialistas + aprobacion + síntesis",
          nodos == {"supervisor", "investigador", "aprobacion", "analista", "sintesis"}, str(sorted(nodos)))
    aristas = {(e.source, e.target) for e in g.edges}
    check("los especialistas vuelven SIEMPRE al supervisor",
          {("investigador", "supervisor"), ("analista", "supervisor")} <= aristas)
    check("el supervisor NO va directo al analista (pasa por el gate)", ("supervisor", "analista") not in aristas)
    check("el gate de aprobación habilita al analista", ("aprobacion", "analista") in aristas)
    check("los especialistas no se hablan entre sí",
          ("investigador", "analista") not in aristas and ("analista", "investigador") not in aristas)
    check("enrutar(analista) -> aprobacion", enrutar({"next_agent": "analista"}) == "aprobacion")
    check("enrutar(FINISH) -> sintesis", enrutar({"next_agent": "FINISH"}) == "sintesis")

    seccion("3. HITL: el grafo se PAUSA antes del analista")
    app = armar([D("analista", requiere_calculo=True), D("FINISH")], checkpointer=MemorySaver())
    cfg = config_hilo("t-pausa")
    r1 = app.invoke(estado_inicial("pregunta con cálculo"), config=cfg)
    check("invoke devuelve una interrupción (grafo pausado)", "__interrupt__" in r1, str(list(r1)))
    pausado = app.get_state(cfg)
    check("hay nodo pendiente mientras espera aprobación", bool(pausado.next))
    payload = r1["__interrupt__"][0].value if r1.get("__interrupt__") else {}
    check("la interrupción explica que requiere aprobación",
          isinstance(payload, dict) and payload.get("tipo") == "aprobacion_requerida", str(payload))

    seccion("4. HITL: reanudar APROBANDO corre el analista y llega a síntesis")
    from langgraph.types import Command

    final = app.invoke(Command(resume={"aprobado": True}), config=cfg)
    quienes = [c["agente"] for c in final["contribuciones"]]
    check("tras aprobar, el analista interviene", "analista" in quienes, str(quienes))
    check("la aprobación quedó registrada en el estado", final.get("aprobacion_analista") == "aprobada")
    check("la síntesis final se emite", final["messages"][-1].content.startswith("FINAL:"))

    seccion("5. HITL: reanudar RECHAZANDO cierra sin ejecutar el analista")
    app = armar([D("analista", requiere_calculo=True), D("FINISH")], checkpointer=MemorySaver())
    cfg = config_hilo("t-rechazo")
    app.invoke(estado_inicial("pregunta con cálculo"), config=cfg)
    final = app.invoke(Command(resume={"aprobado": False, "comentario": "no autorizado"}), config=cfg)
    quienes = [c["agente"] for c in final["contribuciones"]]
    check("tras rechazar, el analista NO interviene", "analista" not in quienes, str(quienes))
    check("motivo_fin = rechazada_por_humano", final.get("motivo_fin") == "rechazada_por_humano")
    check("la síntesis avisa del corte (prompt con ATENCIÓN)", final["messages"][-1].content == "FINAL: CORTE")
    check("enrutar_aprobacion sin aprobación -> sintesis", enrutar_aprobacion({"aprobacion_analista": ""}) == "sintesis")

    seccion("6. HITL: se aprueba UNA sola vez (un refinamiento no vuelve a pausar)")
    app = armar(
        [D("analista", requiere_calculo=True), D("analista", razon="refinar"), D("FINISH")],
        checkpointer=MemorySaver(),
    )
    cfg = config_hilo("t-unavez")
    app.invoke(estado_inicial("pregunta"), config=cfg)
    r = app.invoke(Command(resume={"aprobado": True}), config=cfg)
    check("tras una aprobación el grafo NO vuelve a pausar", "__interrupt__" not in r, str(list(r)))
    n_analista = sum(1 for c in r["contribuciones"] if c["agente"] == "analista")
    check("el analista pudo refinar sin segunda aprobación", n_analista >= 2, f"{n_analista} corridas")

    seccion("7. Anti 'Supervisor Infinito' (guardas del M6 intactas)")
    est = estado_inicial("p")
    destino, motivo = aplicar_guardas(est, D("FINISH"))
    check("no se puede cerrar sin contribuciones", destino == "investigador" and motivo == "")
    destino, _ = aplicar_guardas(
        {**est, "contribuciones": [{"agente": "investigador", "paso": 1, "instruccion": "", "aporte": "x"}]},
        D("FINISH", requiere_calculo=True),
    )
    check("no se cierra si falta cálculo y el analista no intervino", destino == "analista")
    original = config.MAX_PASOS
    config.MAX_PASOS = 3
    try:
        app = armar([D("investigador"), D("analista", requiere_calculo=True)] * 5, checkpointer=MemorySaver())
        # Auto-aprobamos cualquier pausa para poder llegar al tope de pasos.
        cfg = config_hilo("t-tope")
        salida = app.invoke(estado_inicial("pregunta"), cfg)
        while "__interrupt__" in salida:
            salida = app.invoke(Command(resume={"aprobado": True}), cfg)
        check("MAX_PASOS es un tope duro", salida["pasos"] <= 3 and salida["motivo_fin"] in ("limite_pasos", "sin_progreso"),
              f"pasos={salida['pasos']}, motivo={salida['motivo_fin']}")
    finally:
        config.MAX_PASOS = original

    seccion("8. JobStore sobre Redis (máquina de estados, con fake en memoria)")
    store = JobStore(FakeRedis(), ttl=3600)
    job = store.crear("job1", "¿cuántos días?")
    check("crear -> PENDING", store.obtener("job1").estado == Estado.PENDING)
    check("existe() detecta el job", store.existe("job1") and not store.existe("nope"))
    store.marcar_running("job1")
    check("marcar_running -> RUNNING", store.obtener("job1").estado == Estado.RUNNING)
    store.marcar_awaiting("job1", {"tipo": "aprobacion_requerida", "motivo": "x"})
    j = store.obtener("job1")
    check("marcar_awaiting -> AWAITING_APPROVAL + payload", j.estado == Estado.AWAITING_APPROVAL and j.aprobacion)
    store.marcar_done("job1", {"respuesta_final": "231 días"})
    j = store.obtener("job1")
    check("marcar_done -> DONE + resultado", j.estado == Estado.DONE and j.resultado["respuesta_final"] == "231 días")
    store.crear("job2", "otra")
    store.marcar_failed("job2", "BoomError: explotó")
    j = store.obtener("job2")
    check("marcar_failed -> FAILED + error", j.estado == Estado.FAILED and "Boom" in j.error)
    check("obtener() de un id inexistente levanta KeyError", _levanta(lambda: store.obtener("fantasma"), KeyError))

    seccion("9. Worker: captura de errores y pausa (sin tocar un LLM real)")
    store = JobStore(FakeRedis(), ttl=3600)
    store.crear("w-fail", "p")
    app_boom = SimpleNamespace(invoke=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falla del agente")))
    ejecutar_tarea(store, app_boom, "w-fail", "p")
    check("una excepción en background deja el job en FAILED", store.obtener("w-fail").estado == Estado.FAILED)

    store.crear("w-pause", "p")
    interrupt_obj = SimpleNamespace(value={"tipo": "aprobacion_requerida", "motivo": "x"})
    app_pausa = SimpleNamespace(invoke=lambda *a, **k: {"__interrupt__": [interrupt_obj]})
    ejecutar_tarea(store, app_pausa, "w-pause", "p")
    check("un interrupt en background deja el job en AWAITING_APPROVAL",
          store.obtener("w-pause").estado == Estado.AWAITING_APPROVAL)

    store.marcar_awaiting("w-pause", {"motivo": "x"})
    final_state = {"messages": [AIMessage(content="231 días", name="supervisor")],
                   "contribuciones": [{"agente": "analista", "paso": 1, "instruccion": "", "aporte": "231"}],
                   "motivo_fin": "suficiente"}
    app_done = SimpleNamespace(invoke=lambda *a, **k: final_state)
    reanudar_tarea(store, app_done, "w-pause", True)
    j = store.obtener("w-pause")
    check("reanudar y terminar deja el job en DONE con respuesta", j.estado == Estado.DONE and j.resultado["respuesta_final"] == "231 días")

    seccion("10. Herramientas (M6, incluida la seguridad de la calculadora)")
    check("buscar_en_politicas encuentra el dato (sin tildes)", "14 días" in buscar_en_politicas.invoke({"consulta": "dias de vacaciones antiguedad"}))
    check("buscar_en_politicas avisa si no hay resultados", "Sin resultados" in buscar_en_politicas.invoke({"consulta": "xyzzy"}))
    check("calculadora: 5*14+5*21+2*28 = 231", calcular("5*14 + 5*21 + 2*28") == 231)
    check("calculadora rechaza código arbitrario", "Error" in calculadora.invoke({"expresion": "__import__('os').system('echo hack')"}))
    check("calculadora rechaza exponentes gigantes", "Error" in calculadora.invoke({"expresion": "9**9**9"}))
    check("estadisticas", "suma=63" in estadisticas.invoke({"numeros": [14, 21, 28]}))

    print("\n" + ("TODOS LOS CHEQUEOS OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
    return 1 if fallos else 0


def _levanta(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
