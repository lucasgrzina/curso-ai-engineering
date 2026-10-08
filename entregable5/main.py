"""Demo real del agente (llama al LLM configurado en el `.env`).

Corre 4 escenarios y deja la traza ReAct en `evidencia/`:

  1. Razonamiento multi-paso: nombre -> cliente_id -> pedidos (>= 2 herramientas).
  2. Memoria: "¿Y el último?" con el MISMO thread_id, sobre una conexión SQLite
     NUEVA (prueba que la memoria está en el disco, no en la RAM del proceso).
  3. Ciclo de retorno: cliente inexistente -> el agente pide aclaración.
  4. Aislamiento: la misma pregunta ambigua en OTRO thread_id no tiene contexto.

    python main.py                 # corre todo y escribe evidencia/
    python main.py --proveedor openai
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

import config
from agente import abrir_agente, contar_llamadas, extraer_texto, preguntar

config.consola_utf8()

EVIDENCIA = Path("evidencia")
logger = logging.getLogger("agente")


def serializar_traza(mensajes: list[BaseMessage]) -> list[dict]:
    """Mensajes de LangGraph -> JSON simple (la traza ReAct que pide la consigna)."""
    traza: list[dict] = []
    for m in mensajes:
        paso: dict = {"tipo": type(m).__name__, "contenido": extraer_texto(m)}
        if isinstance(m, AIMessage) and m.tool_calls:
            paso["tool_calls"] = [{"nombre": tc["name"], "argumentos": tc["args"]} for tc in m.tool_calls]
        if isinstance(m, ToolMessage):
            paso["herramienta"] = m.name
        traza.append(paso)
    return traza


def narrar(mensajes: list[BaseMessage]) -> None:
    """Imprime y loguea el ciclo ReAct paso a paso."""
    for m in mensajes:
        if isinstance(m, HumanMessage):
            logger.info("Usuario: %s", extraer_texto(m))
        elif isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                logger.info("-> El agente decide usar la herramienta: %s(%s)", tc["name"], tc["args"])
        elif isinstance(m, ToolMessage):
            logger.info("<- La herramienta %s devuelve: %s", m.name, extraer_texto(m))
        elif isinstance(m, AIMessage):
            logger.info("Respuesta: %s", extraer_texto(m))


def configurar_logging() -> None:
    EVIDENCIA.mkdir(exist_ok=True)
    formato = logging.Formatter("%(message)s")
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    archivo = logging.FileHandler(EVIDENCIA / "ejecucion.log", mode="w", encoding="utf-8")
    archivo.setFormatter(formato)
    logger.setLevel(logging.INFO)
    logger.handlers = [consola, archivo]


async def turno(titulo: str, pregunta: str, thread_id: str, llm, db_path: str) -> dict:
    """Un turno sobre una conexión SQLite propia (se abre y se cierra en cada turno)."""
    logger.info("\n%s\n%s [thread_id=%s]\n%s", "=" * 78, titulo, thread_id, "=" * 78)
    async with abrir_agente(llm, db_path) as agente:
        mensajes = await preguntar(agente, pregunta, thread_id)
    narrar(mensajes)
    herramientas = contar_llamadas(mensajes)
    logger.info("   (herramientas invocadas en este turno: %d %s)", len(herramientas), herramientas)
    return {
        "thread_id": thread_id,
        "pregunta": pregunta,
        "herramientas_invocadas": herramientas,
        "traza": serializar_traza(mensajes),
    }


async def main(proveedor: str | None) -> int:
    configurar_logging()
    db_path = config.CHECKPOINT_DB
    for sufijo in ("", "-shm", "-wal"):  # demo reproducible: arranca sin memoria previa
        Path(db_path + sufijo).unlink(missing_ok=True)

    llm = config.get_model(proveedor)
    logger.info("Proveedor: %s | recursion_limit=%d | checkpoints: %s", proveedor or config.proveedor_por_defecto(), config.RECURSION_LIMIT, db_path)

    t1 = await turno(
        "1) Razonamiento multi-paso",
        "¿Cuántos pedidos tuvo Ana García y cuál fue el total?",
        "demo-multipaso",
        llm,
        db_path,
    )
    t2 = await turno(
        "2) Memoria entre turnos (mismo thread_id, conexión SQLite nueva)",
        "¿Y el último?",
        "demo-multipaso",
        llm,
        db_path,
    )
    t3 = await turno(
        "3) Ciclo de retorno: la herramienta falla -> el agente pide aclaración",
        "¿Cuántos pedidos tuvo el cliente Roberto Sánchez?",
        "demo-error",
        llm,
        db_path,
    )
    t4 = await turno(
        "4) Aislamiento: la misma pregunta ambigua en OTRO thread_id",
        "¿Y el último?",
        "demo-otro-hilo",
        llm,
        db_path,
    )

    traza = {
        "proveedor": proveedor or config.proveedor_por_defecto(),
        "recursion_limit": config.RECURSION_LIMIT,
        "turno_1_multi_paso": t1,
        "turno_2_memoria": t2,
        "turno_3_error_y_aclaracion": t3,
        "turno_4_aislamiento_de_hilos": t4,
    }
    destino = EVIDENCIA / "traza_ejecucion.json"
    destino.write_text(json.dumps(traza, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("\nTraza guardada en %s y %s", destino, EVIDENCIA / "ejecucion.log")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proveedor", choices=["openai", "anthropic", "gemini"], help="por defecto, LLM_PROVIDER del .env")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.proveedor)))
