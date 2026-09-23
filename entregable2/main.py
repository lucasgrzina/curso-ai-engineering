"""Mini-script de prueba asíncrono del pipeline de extracción de entidades técnicas.

Uso:
    python main.py                # usa el proveedor de LLM_PROVIDER (o "openai")
    python main.py anthropic      # fuerza un proveedor puntual
    python main.py gemini
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from chain import process_text

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TEXTO_CLARO = (
    "Nuestra API en FastAPI está devolviendo timeouts intermitentes. El caché en Redis "
    "parece saturarse en picos de tráfico y las conexiones a PostgreSQL se agotan porque "
    "el pool está mal dimensionado. Esto está afectando a usuarios en producción."
)

TEXTO_AMBIGUO = "El sistema anda medio raro últimamente, no sé bien qué está pasando."


async def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else os.getenv("LLM_PROVIDER", "openai")

    print(f"--- Texto claro ({provider}) ---")
    try:
        resultado = await process_text(TEXTO_CLARO, provider=provider)
        print(resultado.model_dump_json(indent=2))
    except Exception as exc:
        print(f"Falló tras los reintentos: {exc}")
        return 1

    print(f"\n--- Prueba de estrés: texto ambiguo ({provider}) ---")
    try:
        resultado = await process_text(TEXTO_AMBIGUO, provider=provider)
        print(resultado.model_dump_json(indent=2))
    except Exception as exc:
        print(f"El pipeline no pudo extraer un objeto válido: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
