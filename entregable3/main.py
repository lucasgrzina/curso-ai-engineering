"""Script de prueba del sistema RAG.

Uso:
    python main.py                      # 2 pruebas: una con respuesta y una "trampa"
    python main.py --provider openai    # fuerza el proveedor de generación
    python main.py --interactive        # además abre un chat interactivo

Requisito previo: haber poblado el índice con `python ingesta.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from rag_chain import get_rag_response
from schemas import RAGResponse

load_dotenv()


def _consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_consola_utf8()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Pregunta con respuesta en los documentos.
PREGUNTA_REAL = "¿Cuántos kilómetros diarios incluye la tarifa estándar de alquiler?"
# Pregunta "trampa": el dato no está en ningún documento -> el modelo no debe alucinar.
PREGUNTA_TRAMPA = "¿AutoRenta ofrece un programa de puntos de fidelidad o millas para clientes frecuentes?"


def _mostrar(titulo: str, r: RAGResponse) -> None:
    print(f"\n--- {titulo} ---")
    print("RESPUESTA:", r.respuesta)
    print("FUENTES:", r.fuentes)
    print("Fragmentos usados:", r.fragmentos_recuperados)


async def _interactivo(provider: str) -> None:
    print("\n💬 Modo interactivo — preguntá sobre las políticas de AutoRenta ('salir' para terminar)\n")
    while True:
        try:
            pregunta = input("🧑 Vos: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Listo, terminamos.")
            break
        if pregunta.lower() in ("salir", "exit", "quit", ""):
            print("👋 Listo, terminamos.")
            break
        resultado = await get_rag_response(pregunta, provider=provider)
        print("\n🤖", resultado.respuesta)
        if resultado.fuentes:
            print(f"📎 Fuentes: {', '.join(resultado.fuentes)}")
        print(f"🔢 Fragmentos: {resultado.fragmentos_recuperados}")
        print("-" * 80)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba del sistema RAG")
    ap.add_argument(
        "--provider",
        default=os.getenv("LLM_PROVIDER", "gemini"),
        help="Proveedor de generación: gemini | openai | anthropic",
    )
    ap.add_argument("--interactive", action="store_true", help="Abre un chat interactivo tras las pruebas")
    args = ap.parse_args()
    provider = args.provider.lower()

    print(f"Sistema RAG — proveedor de generación: {provider}")
    try:
        _mostrar("Prueba 1 · pregunta con respuesta en los documentos", await get_rag_response(PREGUNTA_REAL, provider=provider))
        _mostrar("Prueba 2 · pregunta trampa (no está en los documentos)", await get_rag_response(PREGUNTA_TRAMPA, provider=provider))
    except FileNotFoundError as exc:
        print(f"\n{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nFalló la consulta RAG: {exc}")
        return 1

    if args.interactive:
        await _interactivo(provider)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
