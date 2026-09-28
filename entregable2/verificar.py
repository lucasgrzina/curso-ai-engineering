"""Chequeo de los criterios de aceptación.

Por defecto no llama a ninguna API real: `get_model()` y `build_chain()` se
ejercitan con claves falsas (langchain sólo las necesita para instanciar el
cliente, no para construir la cadena) y las pruebas de resiliencia usan un
modelo doble que simula un JSON incompleto antes de recuperarse.

Además, si hay una API key disponible para el proveedor configurado
(`LLM_PROVIDER`), la sección 4 ejercita el camino real de producción
—`build_chain()` → `with_structured_output()` → `.with_retry()`— con una
llamada real. Si no hay clave, esa sección se saltea (no cuenta como falla).

    python verificar.py            # sólo checks offline (sin gastar cuota)
    python verificar.py --real     # además fuerza el camino real (requiere API key)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from pydantic import ValidationError

import chain
from schemas import EntidadesTecnicas, NivelCriticidad


def _consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_consola_utf8()

# Los fallos simulados son parte de la prueba: no deben ensuciar la salida.
logging.getLogger("pipeline_extraccion").setLevel(logging.CRITICAL)

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "OK " if condicion else "FALLA"
    sufijo = f" - {detalle}" if detalle else ""
    print(f"  [{marca}] {nombre}{sufijo}")
    if not condicion:
        fallos.append(nombre)


def seccion(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


# ---------------------------------------------------------------------------
# 1. Esquema Pydantic
# ---------------------------------------------------------------------------


def verificar_schema() -> None:
    seccion("1. Contrato de salida (schemas.py)")

    valido = EntidadesTecnicas(
        tecnologias=["FastAPI", "Redis"],
        nivel_de_criticidad=NivelCriticidad.ALTA,
        resumen_tecnico="Cuello de botella de conexiones en producción.",
    )
    check("una instancia válida se acepta", valido.nivel_de_criticidad == "alta")

    try:
        EntidadesTecnicas(tecnologias=[], nivel_de_criticidad="alta", resumen_tecnico="x" * 20)
        check("lista de tecnologías vacía rechazada", False)
    except ValidationError:
        check("lista de tecnologías vacía rechazada", True)

    try:
        EntidadesTecnicas(tecnologias=["  ", ""], nivel_de_criticidad="alta", resumen_tecnico="x" * 20)
        check("lista de tecnologías sólo con blancos rechazada", False)
    except ValidationError:
        check("lista de tecnologías sólo con blancos rechazada", True)

    try:
        EntidadesTecnicas(tecnologias=["Redis"], nivel_de_criticidad="critica", resumen_tecnico="x" * 20)
        check("nivel_de_criticidad fuera del enum rechazado", False)
    except ValidationError:
        check("nivel_de_criticidad fuera del enum rechazado", True)

    try:
        EntidadesTecnicas(tecnologias=["Redis"], nivel_de_criticidad="alta", resumen_tecnico="corto")
        check("resumen_tecnico demasiado corto rechazado", False)
    except ValidationError:
        check("resumen_tecnico demasiado corto rechazado", True)

    con_duplicados = EntidadesTecnicas(
        tecnologias=["Redis", " Redis ", "Redis", "PostgreSQL"],
        nivel_de_criticidad="media",
        resumen_tecnico="x" * 20,
    )
    check(
        "duplicados exactos se eliminan preservando el orden",
        con_duplicados.tecnologias == ["Redis", "PostgreSQL"],
        f"quedó {con_duplicados.tecnologias}",
    )


# ---------------------------------------------------------------------------
# 2. Fábrica de modelos y cadena LCEL
# ---------------------------------------------------------------------------


def verificar_fabrica_y_cadena() -> None:
    seccion("2. Fábrica de modelos y cadena LCEL (chain.py)")

    try:
        chain.get_model("cohere")
        check("proveedor no soportado rechazado", False)
    except ValueError:
        check("proveedor no soportado rechazado", True)

    entorno_previo = dict(__import__("os").environ)
    try:
        __import__("os").environ.pop("ANTHROPIC_API_KEY", None)
        chain.get_model("anthropic")
        check("falta de API key detectada", False)
    except ValueError:
        check("falta de API key detectada", True)
    finally:
        __import__("os").environ.clear()
        __import__("os").environ.update(entorno_previo)

    cadena = chain.build_chain("gemini")
    check("build_chain() devuelve un Runnable", isinstance(cadena, Runnable))
    check(
        "la cadena queda envuelta en reintento (.with_retry)",
        type(cadena).__name__ == "RunnableRetry",
    )
    check(
        "el prompt declara {texto} y {formato} como variables",
        set(chain.PROMPT.input_variables) == {"texto", "formato"},
    )


# ---------------------------------------------------------------------------
# 3. Resiliencia: reintento ante una respuesta incompleta
# ---------------------------------------------------------------------------


async def verificar_resiliencia() -> None:
    seccion("3. Resiliencia ante JSON incompleto (.with_retry, offline)")

    # Nota: este check valida el MECANISMO de reintento sin gastar cuota. Usa un
    # PydanticOutputParser como sustituto porque with_structured_output() necesita
    # un modelo con tool calling real (el modelo doble no lo implementa). El camino
    # de producción real (with_structured_output + .with_retry) se cubre en la
    # sección 4 cuando hay una API key disponible.
    incompleto = AIMessage(content='{"tecnologias": ["Redis"]}')  # sin los otros campos
    completo = AIMessage(
        content=(
            '{"tecnologias": ["Redis", "PostgreSQL"], "nivel_de_criticidad": "alta", '
            '"resumen_tecnico": "Cuello de botella de conexiones en produccion."}'
        )
    )

    modelo_falso = GenericFakeChatModel(messages=iter([incompleto, completo]))
    cadena_resiliente = (chain.PROMPT | modelo_falso | _parser()).with_retry(
        stop_after_attempt=3, wait_exponential_jitter=False
    )

    resultado = await cadena_resiliente.ainvoke(
        {"texto": "texto de prueba", "formato": chain.FORMATO_INSTRUCCIONES}
    )
    check(
        "se recupera tras un JSON incompleto y valida el segundo intento",
        isinstance(resultado, EntidadesTecnicas) and resultado.nivel_de_criticidad == "alta",
    )

    solo_incompleto = GenericFakeChatModel(messages=iter([incompleto, incompleto, incompleto]))
    cadena_sin_suerte = (chain.PROMPT | solo_incompleto | _parser()).with_retry(
        stop_after_attempt=3, wait_exponential_jitter=False
    )
    try:
        await cadena_sin_suerte.ainvoke({"texto": "texto de prueba", "formato": chain.FORMATO_INSTRUCCIONES})
        check("agota los reintentos y propaga el error si nunca valida", False)
    except (ValidationError, Exception):
        check("agota los reintentos y propaga el error si nunca valida", True)


def _parser() -> Any:
    from langchain_core.output_parsers import PydanticOutputParser

    return PydanticOutputParser(pydantic_object=EntidadesTecnicas)


# Señales de que el fallo es del entorno (quota, key vencida, proveedor saturado)
# y no de la lógica del pipeline. En ese caso no tiene sentido marcar un criterio
# como incumplido en la corrida offline: el código está bien, falta cuota/clave.
_ERRORES_DE_ENTORNO = (
    "429",
    "503",
    "resource_exhausted",
    "ratelimit",
    "rate limit",
    "quota",
    "unavailable",
    "overloaded",
    "authentication",
    "invalid api key",
    "expired_api_key",
    "permission",
    "401",
)


def _es_error_de_entorno(exc: Exception) -> bool:
    firma = f"{type(exc).__name__} {exc}".lower()
    return any(marca in firma for marca in _ERRORES_DE_ENTORNO)


# ---------------------------------------------------------------------------
# 4. Camino real de producción: with_structured_output + .with_retry
# ---------------------------------------------------------------------------


async def verificar_camino_real(forzar: bool = False) -> None:
    """Ejercita la cadena real contra el proveedor configurado, si hay API key.

    A diferencia de la sección 3 (que valida el mecanismo de reintento con un
    parser sustituto), acá corre `chain.build_chain()`/`chain.process_text()`,
    que usan `with_structured_output()` sobre un modelo real. Es la única forma
    de comprobar que ese camino —el de producción— realmente devuelve un
    `EntidadesTecnicas` válido de punta a punta.
    """
    seccion("4. Camino real de producción (with_structured_output, requiere API key)")

    provider = (os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider not in chain._API_KEY_VARS:
        provider = "openai"

    tiene_key = bool(chain._env_str(chain._API_KEY_VARS[provider]))
    if not tiene_key:
        falta_key = f"falta {chain._API_KEY_VARS[provider]} para el proveedor '{provider}'"
        if forzar:
            check(f"camino real ejercitado ({provider})", False, f"{falta_key} (forzado con --real)")
        else:
            print(f"  [SKIP ] camino real ({provider}) - {falta_key}: se saltea, no cuenta como falla.")
        return

    # Mantenemos el logger silenciado (como en la sección 3): el resultado del
    # camino real ya se reporta abajo con check()/SKIP, sin ruido de stack traces.
    texto = (
        "Nuestra API en FastAPI devuelve timeouts intermitentes: el pool de conexiones "
        "a PostgreSQL se agota y el caché en Redis se satura en picos de tráfico."
    )
    try:
        resultado = await chain.process_text(texto, provider=provider)
    except Exception as exc:  # noqa: BLE001 - clasificamos entorno vs. lógica
        detalle = f"lanzó {type(exc).__name__}: {exc}"
        # Un problema de entorno (quota agotada, key vencida, proveedor saturado)
        # no invalida la lógica del pipeline: sólo es falla si se forzó con --real.
        if _es_error_de_entorno(exc) and not forzar:
            print(
                f"  [SKIP ] camino real ({provider}) - problema de entorno, no de código: {detalle}"
            )
        else:
            check(f"camino real ejercitado ({provider})", False, detalle)
        return

    check(
        f"with_structured_output devuelve un EntidadesTecnicas válido ({provider})",
        isinstance(resultado, EntidadesTecnicas)
        and len(resultado.tecnologias) > 0
        and isinstance(resultado.nivel_de_criticidad, NivelCriticidad),
        f"tecnologías={resultado.tecnologias}" if isinstance(resultado, EntidadesTecnicas) else "",
    )


async def main() -> int:
    forzar_real = "--real" in sys.argv[1:]
    print("Verificación del Entregable 2 - Pipeline de procesamiento validado")
    verificar_schema()
    verificar_fabrica_y_cadena()
    await verificar_resiliencia()
    await verificar_camino_real(forzar=forzar_real)

    print(f"\n{'=' * 72}")
    if fallos:
        print(f"FALLARON {len(fallos)} criterios: {', '.join(fallos)}")
        return 1
    print("Todos los criterios se cumplen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
