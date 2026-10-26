"""Cálculo del costo en USD por ejecución, a partir de los tokens.

Monitorear producción no es solo mirar latencia: hay que mirar el **costo**. El
dashboard (Phoenix) calcula el costo en dólares SOLO si conoce el precio del
modelo; con un modelo de Groq (p. ej. `openai/gpt-oss-20b`) muestra tokens pero
no USD. Este módulo cierra ese hueco calculando el costo nosotros mismos a partir
de los tokens, con una tabla de precios propia (editable) y override por entorno.

Flujo:
  1. El worker corre el grafo con un `UsageMetadataCallbackHandler` (de
     langchain-core), que suma los tokens de entrada/salida por modelo —incluidas
     las llamadas internas de los agentes ReAct, no solo el mensaje final.
  2. `resumen_de_costo(uso)` convierte esos tokens en USD usando `precio_de_modelo`.
  3. El costo se persiste en el `JobStore` y se expone en `GET /tasks/{id}`.

Precios en **USD por 1.000.000 de tokens** (entrada, salida). Son precios de
lista de referencia; se ajustan acá o, mejor, por entorno:

    COSTO_USD_INPUT_POR_1M / COSTO_USD_OUTPUT_POR_1M -> fuerzan el precio de
    CUALQUIER modelo (útil cuando el proveedor publica su propia tarifa).
"""

from __future__ import annotations

import os

# Precio de lista (USD por 1M de tokens): {prefijo_de_modelo: (entrada, salida)}.
# El match es por prefijo del nombre normalizado, tomando el prefijo MÁS LARGO que
# coincida (así "gpt-4o-mini" gana sobre "gpt-4o").
PRECIOS_USD_POR_1M: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    # Groq (OpenAI-compatible) — gpt-oss open weights
    "gpt-oss-20b": (0.10, 0.50),
    "gpt-oss-120b": (0.15, 0.75),
    # Anthropic
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku": (0.80, 4.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-sonnet": (3.00, 15.00),
    # Google Gemini
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-pro": (1.25, 5.00),
}

_UN_MILLON = 1_000_000


def _normalizar_modelo(nombre: str) -> str:
    """Minúsculas y sin el prefijo de proveedor (`openai/gpt-oss-20b` -> `gpt-oss-20b`)."""
    limpio = (nombre or "").strip().lower()
    return limpio.rsplit("/", 1)[-1] if "/" in limpio else limpio


def _precio_override() -> tuple[float, float] | None:
    """Precio forzado por entorno (USD por 1M), si están ambas variables."""
    inp = os.getenv("COSTO_USD_INPUT_POR_1M")
    out = os.getenv("COSTO_USD_OUTPUT_POR_1M")
    if inp is not None and out is not None:
        try:
            return float(inp), float(out)
        except ValueError:
            return None
    return None


def precio_de_modelo(nombre: str) -> tuple[tuple[float, float] | None, str]:
    """Devuelve ((entrada, salida) por 1M, fuente) para el modelo.

    `fuente` es "override_env", "tabla" o "desconocido". Si es desconocido, el
    precio es None y el costo quedará sin calcular (se reportan solo tokens).
    """
    override = _precio_override()
    if override is not None:
        return override, "override_env"

    normalizado = _normalizar_modelo(nombre)
    candidatos = [p for p in PRECIOS_USD_POR_1M if normalizado.startswith(p)]
    if candidatos:
        mejor = max(candidatos, key=len)  # prefijo más específico
        return PRECIOS_USD_POR_1M[mejor], "tabla"
    return None, "desconocido"


def costo_usd(nombre_modelo: str, input_tokens: int, output_tokens: int) -> float | None:
    """Costo en USD de una cantidad de tokens para un modelo. None si no hay precio."""
    precio, _ = precio_de_modelo(nombre_modelo)
    if precio is None:
        return None
    p_in, p_out = precio
    return (input_tokens * p_in + output_tokens * p_out) / _UN_MILLON


def fusionar(a: dict[str, dict], b: dict[str, dict]) -> dict[str, dict]:
    """Suma dos mapas {modelo: {input_tokens, output_tokens, total_tokens}}.

    Permite acumular el costo a través de la pausa HITL: cada `invoke`
    (ejecución y reanudación) aporta sus tokens al total del job.
    """
    salida: dict[str, dict] = {}
    for fuente in (a, b):
        for modelo, uso in (fuente or {}).items():
            acc = salida.setdefault(modelo, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
            acc["input_tokens"] += int(uso.get("input_tokens", 0) or 0)
            acc["output_tokens"] += int(uso.get("output_tokens", 0) or 0)
            acc["total_tokens"] += int(uso.get("total_tokens", 0) or 0)
    return salida


def resumen_de_costo(uso_por_modelo: dict[str, dict]) -> dict:
    """Convierte el uso de tokens por modelo en el resumen de costo del job.

    Estructura devuelta:
        {
          "input_tokens", "output_tokens", "total_tokens",
          "costo_usd": float | None,        # None solo si NINGÚN modelo tiene precio
          "moneda": "USD",
          "precio_fuente": "tabla"|"override_env"|"parcial"|"desconocido"|"sin_datos",
          "modelos": {modelo: {tokens... , "costo_usd", "precio_fuente"}},
        }
    """
    if not uso_por_modelo:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "costo_usd": 0.0,
            "moneda": "USD",
            "precio_fuente": "sin_datos",
            "modelos": {},
        }

    detalle: dict[str, dict] = {}
    tot_in = tot_out = tot_tok = 0
    costo_acumulado = 0.0
    hubo_precio = False
    hubo_sin_precio = False

    for modelo, uso in uso_por_modelo.items():
        inp = int(uso.get("input_tokens", 0) or 0)
        out = int(uso.get("output_tokens", 0) or 0)
        tot = int(uso.get("total_tokens", 0) or 0) or (inp + out)
        tot_in += inp
        tot_out += out
        tot_tok += tot

        precio, fuente = precio_de_modelo(modelo)
        if precio is None:
            hubo_sin_precio = True
            costo_modelo = None
        else:
            hubo_precio = True
            p_in, p_out = precio
            costo_modelo = round((inp * p_in + out * p_out) / _UN_MILLON, 6)
            costo_acumulado += costo_modelo

        detalle[modelo] = {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": tot,
            "costo_usd": costo_modelo,
            "precio_fuente": fuente,
        }

    if hubo_precio and hubo_sin_precio:
        fuente_global = "parcial"
    elif hubo_precio:
        fuente_global = "override_env" if _precio_override() is not None else "tabla"
    else:
        fuente_global = "desconocido"

    return {
        "input_tokens": tot_in,
        "output_tokens": tot_out,
        "total_tokens": tot_tok,
        "costo_usd": round(costo_acumulado, 6) if hubo_precio else None,
        "moneda": "USD",
        "precio_fuente": fuente_global,
        "modelos": detalle,
    }
