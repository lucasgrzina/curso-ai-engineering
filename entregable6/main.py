"""Demo real del orquestador (llama al LLM configurado en el `.env`).

Corre 2 escenarios y deja la evidencia en `evidencia/`:

  1. Delegación completa: investigador (dato) -> analista (cálculo) -> síntesis.
  2. Solo un dominio: pregunta de pura búsqueda; el Supervisor no debe llamar
     al analista (prueba que el ruteo es dinámico y no una secuencia fija).

Además exporta el diagrama del grafo (`evidencia/grafo.mmd`).

    python main.py
    python main.py --proveedor openai
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import config
from agents._base import extraer_texto
from graph import crear_app
from state import estado_inicial

config.consola_utf8()

EVIDENCIA = Path("evidencia")
logger = logging.getLogger("orquestador")

ESCENARIOS = [
    (
        "Escenario 1: delegación investigador -> analista -> síntesis",
        "Un empleado cumple 12 años en TechCorp. Sus primeros 5 años tuvo derecho a 14 días de "
        "vacaciones por año, los siguientes 5 años a 21 por año, y los últimos 2 a 28 por año. "
        "Confirmá esos valores en la política y calculá cuántos días de vacaciones acumuló en total.",
    ),
    (
        "Escenario 2: un solo dominio (el Supervisor no debe llamar al analista)",
        "¿Cada cuántos días hay que renovar las contraseñas en TechCorp y es obligatorio el 2FA?",
    ),
]


def configurar_logging() -> None:
    EVIDENCIA.mkdir(exist_ok=True)
    formato = logging.Formatter("%(message)s")
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    archivo = logging.FileHandler(EVIDENCIA / "ejecucion.log", mode="w", encoding="utf-8")
    archivo.setFormatter(formato)
    logger.setLevel(logging.INFO)
    logger.handlers = [consola, archivo]


def correr(app, titulo: str, pregunta: str) -> dict:
    """Ejecuta una consulta narrando cada nodo (modo `updates`) y devuelve la traza."""
    logger.info("\n%s\n%s\n%s", "=" * 78, titulo, "=" * 78)
    logger.info("Pregunta: %s\n", pregunta)
    traza: list[dict] = []
    final: dict = {}
    for evento in app.stream(estado_inicial(pregunta), stream_mode="updates"):
        for nodo, cambio in evento.items():
            paso: dict = {"nodo": nodo}
            if nodo == "supervisor":
                paso.update(decision=cambio["next_agent"], instruccion=cambio.get("instruccion", ""), razon=cambio["razon"],
                            motivo_fin=cambio.get("motivo_fin", ""))
                logger.info("[supervisor] -> %s | %s", cambio["next_agent"], cambio["razon"])
                if cambio.get("instruccion"):
                    logger.info("             instrucción: %s", cambio["instruccion"])
            else:
                aporte = extraer_texto(cambio["messages"][-1])
                paso["aporte"] = aporte
                logger.info("[%s] %s", nodo, aporte)
                if nodo == "sintesis":
                    final = {"respuesta_final": aporte}
            traza.append(paso)
    contribuyentes = [p["nodo"] for p in traza if p["nodo"] in ("investigador", "analista")]
    logger.info("\nAgentes que intervinieron (en orden): %s", contribuyentes)
    return {"titulo": titulo, "pregunta": pregunta, "agentes_en_orden": contribuyentes, "traza": traza, **final}


def main(proveedor: str | None) -> int:
    configurar_logging()
    try:
        app = crear_app(config.get_model(proveedor))
    except ValueError as e:
        logger.error("No se pudo crear el LLM: %s", e)
        return 1

    (EVIDENCIA / "grafo.mmd").write_text(app.get_graph().draw_mermaid(), encoding="utf-8")
    resultados = [correr(app, titulo, pregunta) for titulo, pregunta in ESCENARIOS]
    (EVIDENCIA / "traza_ejecucion.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("\nEvidencia escrita en %s/ (ejecucion.log, traza_ejecucion.json, grafo.mmd)", EVIDENCIA)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proveedor", choices=sorted(config.DEFAULT_MODELS), default=None)
    sys.exit(main(parser.parse_args().proveedor))
