"""Chequeo de los criterios de aceptación del Entregable 5.

Por defecto corre 100% offline: sin red ni API keys, con un LLM guionado que
reacciona a lo que ve en el historial como lo haría un modelo real. Valida el
contrato de herramientas, la forma del grafo, la autonomía (el modelo decide
las llamadas, no hay if/else), el ciclo de retorno ante errores, la memoria por
`thread_id` sobre SQLite, el `recursion_limit` y el recorte del historial.

`--real` además corre una consulta multi-paso con el LLM del `.env` y valida la
traza guardada en `evidencia/`.

    python verificar.py            # solo checks offline
    python verificar.py --real     # además llama al LLM real
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import MessagesState

import agente
import config
import herramientas

config.consola_utf8()

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "OK " if condicion else "FALLA"
    sufijo = f" - {detalle}" if detalle else ""
    print(f"  [{marca}] {nombre}{sufijo}")
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


class LLMGuionado(BaseChatModel):
    """LLM de mentira: `responder` mira el historial y decide qué contestar.

    Hace de "cerebro" del agente sin red. `bind_tools` es un no-op porque el
    guion ya sabe qué herramientas existen; `vistos` guarda cada entrada que
    recibió, para auditar qué memoria le llegó.
    """

    responder: Callable[[list[BaseMessage]], AIMessage]
    vistos: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "guionado"

    def bind_tools(self, tools: Any, **kwargs: Any) -> LLMGuionado:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.vistos.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responder(messages))])


_ids = iter(range(1, 10_000))


def llamar(herramienta: str, /, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": herramienta, "args": args, "id": f"call_{next(_ids)}"}])


def razonador(mensajes: list[BaseMessage]) -> AIMessage:
    """Guion ReAct: decide mirando el último mensaje, sin rutas fijas por pregunta."""
    ultimo = mensajes[-1]
    if isinstance(ultimo, HumanMessage):
        texto = str(ultimo.content).lower()
        if "ana garcía" in texto:
            return llamar("buscar_cliente_por_nombre", nombre="Ana García")
        if "roberto" in texto:
            return llamar("buscar_cliente_por_nombre", nombre="Roberto Sánchez")
        if "último" in texto:
            ids = [json.loads(str(m.content)).get("cliente_id") for m in mensajes if isinstance(m, ToolMessage) and "cliente_id" in str(m.content)]
            if ids:
                return llamar("buscar_ultimo_pedido", cliente_id=ids[-1])
            return AIMessage(content="¿De qué cliente querés saber el último pedido?")
    if isinstance(ultimo, ToolMessage):
        datos = json.loads(str(ultimo.content))
        if "error" in datos:
            return AIMessage(content=f"No pude resolverlo: {datos['error']} ¿Podés confirmar el nombre completo?")
        if ultimo.name == "buscar_cliente_por_nombre":
            return llamar("buscar_pedidos", cliente_id=datos["cliente_id"])
        if ultimo.name == "buscar_pedidos":
            return AIMessage(content=f"Tuvo {datos['pedidos']} pedidos por un total de ${datos['total']}.")
        if ultimo.name == "buscar_ultimo_pedido":
            return AIMessage(content=f"Su último pedido fue {datos['ultimo_pedido']['id']}.")
    return AIMessage(content="No entendí la consulta.")


def nuevo_llm(responder: Callable[[list[BaseMessage]], AIMessage] = razonador) -> LLMGuionado:
    return LLMGuionado(responder=responder, vistos=[])


# ---------------------------------------------------------------------------
# 1. Entorno y contrato de herramientas
# ---------------------------------------------------------------------------


def verificar_herramientas() -> None:
    seccion("1. Entorno y contrato de herramientas (herramientas.py)")

    check("Python 3.12+", sys.version_info >= (3, 12), sys.version.split()[0])
    check("hay al menos una herramienta propia", len(herramientas.HERRAMIENTAS) >= 1, f"{len(herramientas.HERRAMIENTAS)} herramientas")
    check("todas las herramientas son asíncronas (coroutine)", all(t.coroutine is not None for t in herramientas.HERRAMIENTAS))
    check(
        "cada herramienta tiene un docstring descriptivo (>= 150 caracteres)",
        all(len(t.description) >= 150 for t in herramientas.HERRAMIENTAS),
        ", ".join(f"{t.name}={len(t.description)}" for t in herramientas.HERRAMIENTAS),
    )
    check(
        "los docstrings dicen cómo fallan (mencionan 'error')",
        all("error" in t.description.lower() for t in herramientas.HERRAMIENTAS),
    )


async def verificar_herramientas_async() -> None:
    ok = json.loads(await herramientas.buscar_cliente_por_nombre.ainvoke({"nombre": "  MARIA   lopez "}))
    check("la búsqueda de cliente ignora acentos, mayúsculas y espacios", ok.get("cliente_id") == 310, str(ok))

    malo = json.loads(await herramientas.buscar_cliente_por_nombre.ainvoke({"nombre": "Roberto Sánchez"}))
    check("un cliente inexistente devuelve {'error': ...}, no una excepción", "error" in malo and "cliente_id" not in malo)

    pedidos = json.loads(await herramientas.buscar_pedidos.ainvoke({"cliente_id": 102}))
    check("cliente 102: 3 pedidos por $14.500 (el ejemplo de la consigna)", (pedidos["pedidos"], pedidos["total"]) == (3, 14500), str(pedidos))
    check("un cliente_id inexistente devuelve error", "error" in json.loads(await herramientas.buscar_pedidos.ainvoke({"cliente_id": 999})))

    ultimo = json.loads(await herramientas.buscar_ultimo_pedido.ainvoke({"cliente_id": 102}))
    check("el último pedido es el de fecha más reciente", ultimo["ultimo_pedido"]["id"] == "P-1003", str(ultimo))


# ---------------------------------------------------------------------------
# 2. Forma del grafo
# ---------------------------------------------------------------------------


def verificar_grafo() -> None:
    seccion("2. StateGraph (agente.py)")

    # MessagesState es un TypedDict: no admite issubclass, se mira la herencia declarada.
    check(
        "el estado hereda de MessagesState",
        MessagesState in getattr(agente.EstadoAgente, "__orig_bases__", ()) and "messages" in agente.EstadoAgente.__annotations__,
    )
    grafo = agente.construir_grafo(nuevo_llm()).compile()
    nodos = set(grafo.get_graph().nodes) - {"__start__", "__end__"}
    check("nodo de modelo + nodo de herramientas", nodos == {"modelo", "herramientas"}, str(sorted(nodos)))

    aristas = {(e.source, e.target, e.conditional) for e in grafo.get_graph().edges}
    check("arista condicional modelo -> herramientas (tools_condition)", ("modelo", "herramientas", True) in aristas)
    check("arista condicional modelo -> END", ("modelo", "__end__", True) in aristas)
    check("el ciclo: herramientas -> modelo", ("herramientas", "modelo", False) in aristas)
    check("el recursion_limit por defecto es un techo finito (<= 25)", 0 < config.RECURSION_LIMIT <= 25, f"{config.RECURSION_LIMIT}")


# ---------------------------------------------------------------------------
# 3. Comportamiento del agente (con LLM guionado)
# ---------------------------------------------------------------------------


async def verificar_agente() -> None:
    seccion("3. Autonomía, ciclo de retorno y memoria (LLM guionado, SQLite real)")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = str(Path(tmp) / "cp.sqlite")

        llm = nuevo_llm()
        async with agente.abrir_agente(llm, db) as ag:
            turno = await agente.preguntar(ag, "¿Cuántos pedidos tuvo Ana García y cuál fue el total?", "t1")
        llamadas = agente.contar_llamadas(turno)
        check(
            "multi-paso: nombre -> cliente_id -> pedidos (herramienta invocada >= 2 veces)",
            llamadas == ["buscar_cliente_por_nombre", "buscar_pedidos"],
            str(llamadas),
        )
        final = agente.extraer_texto(turno[-1])
        check("la respuesta final sale de los datos de las herramientas", "14500" in final and "3 pedidos" in final, final)
        check("el turno devuelto empieza en la pregunta del usuario", isinstance(turno[0], HumanMessage))

        # Memoria: conexión SQLite NUEVA, mismo thread_id.
        llm2 = nuevo_llm()
        async with agente.abrir_agente(llm2, db) as ag:
            turno2 = await agente.preguntar(ag, "¿Y el último?", "t1")
        check("memoria: '¿Y el último?' reutiliza el cliente_id del turno anterior", agente.contar_llamadas(turno2) == ["buscar_ultimo_pedido"], str(agente.contar_llamadas(turno2)))
        check("memoria: la respuesta usa los datos del cliente recordado", "P-1003" in agente.extraer_texto(turno2[-1]))
        entrada = llm2.vistos[0]
        check(
            "memoria: el LLM recibió el historial del turno 1 desde SQLite (con una conexión nueva)",
            any("Ana García" in str(m.content) for m in entrada if isinstance(m, HumanMessage)),
            f"{len(entrada)} mensajes de entrada",
        )

        # Aislamiento entre hilos.
        llm3 = nuevo_llm()
        async with agente.abrir_agente(llm3, db) as ag:
            turno3 = await agente.preguntar(ag, "¿Y el último?", "otro-hilo")
        check("otro thread_id no ve la memoria del primero", agente.contar_llamadas(turno3) == [] and "¿De qué cliente" in agente.extraer_texto(turno3[-1]))

        # Ciclo de retorno.
        async with agente.abrir_agente(nuevo_llm(), db) as ag:
            turno4 = await agente.preguntar(ag, "¿Cuántos pedidos tuvo el cliente Roberto Sánchez?", "t-error")
        tool_msg = next(m for m in turno4 if isinstance(m, ToolMessage))
        check("ciclo de retorno: el error de la herramienta vuelve al modelo como mensaje", "error" in str(tool_msg.content))
        check("ciclo de retorno: el agente pide aclaración en vez de inventar datos", "confirmar" in agente.extraer_texto(turno4[-1]) and "$" not in agente.extraer_texto(turno4[-1]))

        # Una excepción dentro de la herramienta tampoco rompe el ciclo.
        def tras_excepcion(mensajes: list[BaseMessage]) -> AIMessage:
            if isinstance(mensajes[-1], ToolMessage):
                return AIMessage(content=f"La herramienta falló, reintento más tarde. ({str(mensajes[-1].content)[:40]})")
            return llamar("buscar_pedidos", cliente_id="no-es-un-numero")

        async with agente.abrir_agente(nuevo_llm(tras_excepcion), db) as ag:
            turno5 = await agente.preguntar(ag, "pedidos del cliente", "t-excepcion")
        check(
            "argumentos inválidos: la excepción vuelve como ToolMessage (handle_tool_errors)",
            "reintento" in agente.extraer_texto(turno5[-1]),
            agente.extraer_texto(turno5[-1])[:80],
        )

        # recursion_limit.
        def insistente(mensajes: list[BaseMessage]) -> AIMessage:
            return llamar("buscar_pedidos", cliente_id=102)

        async with agente.abrir_agente(nuevo_llm(insistente), db) as ag:
            turno6 = await agente.preguntar(ag, "pedidos del cliente 102", "t-bucle")
        check(
            "un modelo que no termina nunca se corta en recursion_limit (sin excepción hacia afuera)",
            "[corte]" in agente.extraer_texto(turno6[-1]),
            agente.extraer_texto(turno6[-1])[:70],
        )
        check(
            "el corte ocurre dentro del techo de pasos",
            len(agente.contar_llamadas(turno6)) <= config.RECURSION_LIMIT // 2,
            f"{len(agente.contar_llamadas(turno6))} llamadas con limit={config.RECURSION_LIMIT}",
        )


# ---------------------------------------------------------------------------
# 4. Estado sucio
# ---------------------------------------------------------------------------


def verificar_recorte() -> None:
    seccion("4. Recorte del historial (anti estado sucio)")

    historial: list[BaseMessage] = []
    for i in range(40):
        historial.append(HumanMessage(content=f"pregunta {i}"))
        llamada = llamar("buscar_pedidos", cliente_id=102)
        historial.append(llamada)
        historial.append(ToolMessage(content="{}", name="buscar_pedidos", tool_call_id=llamada.tool_calls[0]["id"]))
        historial.append(AIMessage(content=f"respuesta {i}"))

    recortado = agente.recortar_historial(historial, maximo=30)
    check("el historial enviado al modelo respeta el máximo", len(recortado) <= 30, f"{len(historial)} -> {len(recortado)} mensajes")
    check("la ventana arranca en un mensaje del usuario (sin ToolMessage huérfano)", isinstance(recortado[0], HumanMessage))
    check("conserva lo más reciente", recortado[-1].content == "respuesta 39")
    check("un historial corto no se toca", agente.recortar_historial(historial[:4], maximo=30) == historial[:4])
    check("el SystemMessage no forma parte del estado persistido", not any(isinstance(m, SystemMessage) for m in historial))


# ---------------------------------------------------------------------------
# 5. Camino real
# ---------------------------------------------------------------------------


async def verificar_camino_real(forzar: bool = False) -> None:
    seccion("5. LLM real (requiere la API key del proveedor)")

    try:
        llm = config.get_model()
    except ValueError as exc:
        if forzar:
            check("LLM real", False, f"{exc} (forzado con --real)")
        else:
            print(f"  [SKIP ] LLM real - {exc} Se saltea, no cuenta como falla.")
        return

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        async with agente.abrir_agente(llm, str(Path(tmp) / "real.sqlite")) as ag:
            turno = await agente.preguntar(ag, "¿Cuántos pedidos tuvo Ana García y cuál fue el total?", "real-1")
    llamadas = agente.contar_llamadas(turno)
    texto = agente.extraer_texto(turno[-1])
    check("real: el modelo invocó herramientas >= 2 veces por sí mismo", len(llamadas) >= 2, str(llamadas))
    check("real: la respuesta contiene los datos correctos", "14.500" in texto or "14500" in texto, texto[:90])

    traza = Path("evidencia/traza_ejecucion.json")
    if traza.exists():
        datos = json.loads(traza.read_text(encoding="utf-8"))
        check("evidencia: la traza guardada muestra el ciclo multi-paso", len(datos["turno_1_multi_paso"]["herramientas_invocadas"]) >= 2)
    else:
        check("evidencia: existe evidencia/traza_ejecucion.json (corré `python main.py`)", False)


async def main() -> int:
    forzar = "--real" in sys.argv[1:]
    print("Verificación del Entregable 5 - Agente ReAct con memoria persistente")
    verificar_herramientas()
    await verificar_herramientas_async()
    verificar_grafo()
    await verificar_agente()
    verificar_recorte()
    await verificar_camino_real(forzar=forzar)

    print(f"\n{'=' * 72}")
    if fallos:
        print(f"FALLARON {len(fallos)} criterios: {', '.join(fallos)}")
        return 1
    print("Todos los criterios se cumplen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
