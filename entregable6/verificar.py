"""Chequeo de los criterios de aceptación del Entregable 6 (100% offline).

Sin red ni API keys: el LLM del Supervisor y los especialistas se reemplazan por
dobles deterministas inyectados en `construir_grafo`. Valida lo que NO depende
del modelo: estructura del estado, reducer, topología, ruteo, validación,
límites anti bucle y herramientas (incluida la seguridad de la calculadora).

    python verificar.py
"""

from __future__ import annotations

import sys

from langchain_core.messages import AIMessage

import config
from agents.herramientas import buscar_en_politicas, calculadora, calcular, estadisticas
from graph import construir_grafo
from state import AgentState, estado_inicial
from supervisor import DecisionSupervisor, aplicar_guardas

config.consola_utf8()
fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"  [{'OK ' if condicion else 'FALLA'}] {nombre}" + (f" - {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


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


def armar(guion: list[DecisionSupervisor]):
    return construir_grafo(
        decisor_guionado(guion),
        nodo_falso("investigador", "14 dias (<5 años), 21 (5-10), 28 (>10). Fuente: politica_vacaciones.txt"),
        nodo_falso("analista", "5*14 + 5*21 + 2*28 = 231"),
        lambda prompt: "FINAL: " + ("CORTE" if "ATENCIÓN" in prompt else "OK"),
    ).compile()


def D(next_: str, instruccion: str = "x", razon: str = "r") -> DecisionSupervisor:
    return DecisionSupervisor(next=next_, instruccion=instruccion, razon=razon)


def main() -> int:
    seccion("1. Estado compartido")
    ini = estado_inicial("hola")
    check("hereda MessagesState (campo messages)", "messages" in AgentState.__annotations__ or "messages" in ini)
    for campo in ("next_agent", "contribuciones", "pasos", "task_completed"):
        check(f"campo propio '{campo}'", campo in AgentState.__annotations__)

    seccion("2. Topología del grafo")
    app = armar([D("investigador"), D("analista"), D("FINISH")])
    g = app.get_graph()
    nodos = set(g.nodes) - {"__start__", "__end__"}
    check("nodos: supervisor + 2 especialistas + síntesis", nodos == {"supervisor", "investigador", "analista", "sintesis"}, str(sorted(nodos)))
    aristas = {(e.source, e.target) for e in g.edges}
    check("los especialistas vuelven SIEMPRE al supervisor", {("investigador", "supervisor"), ("analista", "supervisor")} <= aristas)
    check("los especialistas no se hablan entre sí", ("investigador", "analista") not in aristas and ("analista", "investigador") not in aristas)
    check("diagrama Mermaid generable", "supervisor" in app.get_graph().draw_mermaid())

    seccion("3. Flujo de delegación: investigador -> analista -> síntesis")
    final = app.invoke(estado_inicial("pregunta"))
    quienes = [c["agente"] for c in final["contribuciones"]]
    check("orden de intervención", quienes == ["investigador", "analista"], str(quienes))
    check("el reducer acumula contribuciones sin pisarlas", len(final["contribuciones"]) == 2)
    check("cada contribución es trazable (agente, paso, aporte)", all({"agente", "paso", "aporte", "instruccion"} <= set(c) for c in final["contribuciones"]))
    check("síntesis final emitida por el supervisor", final["messages"][-1].name == "supervisor" and final["messages"][-1].content == "FINAL: OK")
    check("motivo_fin = suficiente", final["motivo_fin"] == "suficiente" and final["task_completed"])

    seccion("4. Validación / refinamiento")
    app = armar([D("investigador"), D("analista"), D("analista", razon="R3 falla: refinar"), D("FINISH")])
    final = app.invoke(estado_inicial("pregunta"))
    quienes = [c["agente"] for c in final["contribuciones"]]
    check("el supervisor puede pedir que un especialista refine", quienes == ["investigador", "analista", "analista"], str(quienes))

    seccion("5. Anti 'Supervisor Infinito'")
    app = armar([D("investigador")])  # LLM terco: pide investigador para siempre
    final = app.invoke(estado_inicial("pregunta"))
    n = len(final["contribuciones"])
    check("corta el reintento de un mismo agente", n == 1 + config.MAX_REFINAMIENTOS, f"{n} invocaciones")
    check("motivo_fin = sin_progreso y la síntesis avisa del corte", final["motivo_fin"] == "sin_progreso" and final["messages"][-1].content == "FINAL: CORTE")

    original = config.MAX_PASOS
    config.MAX_PASOS = 3
    try:
        app = armar([D("investigador"), D("analista")] * 5)
        final = app.invoke(estado_inicial("pregunta"), {"recursion_limit": 50})
        check("MAX_PASOS es un tope duro", final["pasos"] <= 3 and final["motivo_fin"] in ("limite_pasos", "sin_progreso"), f"pasos={final['pasos']}, motivo={final['motivo_fin']}")
    finally:
        config.MAX_PASOS = original

    est = estado_inicial("p")
    destino, motivo = aplicar_guardas(est, D("FINISH"))
    check("no se puede cerrar sin contribuciones", destino == "investigador" and motivo == "")

    destino, motivo = aplicar_guardas(
        {**est, "contribuciones": [{"agente": "investigador", "paso": 1, "instruccion": "", "aporte": "x"}]},
        DecisionSupervisor(next="FINISH", requiere_calculo=True, razon="r"),
    )
    check("no se puede cerrar si hace falta cálculo y el analista no intervino", destino == "analista")

    seccion("6. Herramientas")
    check("buscar_en_politicas encuentra el dato (sin tildes)", "14 días" in buscar_en_politicas.invoke({"consulta": "dias de vacaciones antiguedad"}))
    check("buscar_en_politicas avisa si no hay resultados", "Sin resultados" in buscar_en_politicas.invoke({"consulta": "xyzzy"}))
    check("calculadora: 5*14+5*21+2*28 = 231", calcular("5*14 + 5*21 + 2*28") == 231)
    check("calculadora rechaza código arbitrario", "Error" in calculadora.invoke({"expresion": "__import__('os').system('echo hack')"}))
    check("calculadora rechaza exponentes gigantes", "Error" in calculadora.invoke({"expresion": "9**9**9"}))
    check("estadisticas", "suma=63.0" in estadisticas.invoke({"numeros": [14, 21, 28]}) or "suma=63" in estadisticas.invoke({"numeros": [14, 21, 28]}))

    print("\n" + ("TODOS LOS CHEQUEOS OK" if not fallos else f"FALLARON {len(fallos)}: {fallos}"))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
