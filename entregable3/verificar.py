"""Chequeo de los criterios de aceptación del Entregable 3.

Por defecto corre 100% offline y sin gastar cuota: valida los contratos
Pydantic, la fábrica de modelos, el prompt, el armado de contexto y el chunking
(con claves falsas donde hace falta, porque langchain sólo las usa para
instanciar el cliente, no para construir la cadena).

La sección "camino real" (`--real`) ejercita el flujo RAG completo contra el
índice persistido y el proveedor configurado, si hay índice y API key. Si falta
alguno, se saltea (SKIP) sin contar como falla.

    python verificar.py            # sólo checks offline
    python verificar.py --real     # además corre get_rag_response de punta a punta
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from langchain_core.documents import Document
from langchain_core.runnables import Runnable
from pydantic import ValidationError

import ingesta
import rag_chain
from schemas import RAGResponse, RespuestaLLM


def _consola_utf8() -> None:
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_consola_utf8()
logging.getLogger("rag").setLevel(logging.CRITICAL)
logging.getLogger("ingesta").setLevel(logging.CRITICAL)

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
# 1. Contratos Pydantic
# ---------------------------------------------------------------------------


def verificar_schemas() -> None:
    seccion("1. Contratos de salida (schemas.py)")

    ok = RAGResponse(respuesta="200 km por día", fuentes=["data/combustible_kilometraje.txt"], fragmentos_recuperados=4)
    check("RAGResponse válido se acepta", ok.fragmentos_recuperados == 4)

    try:
        RespuestaLLM(respuesta="")
        check("RespuestaLLM vacía rechazada", False)
    except ValidationError:
        check("RespuestaLLM vacía rechazada", True)

    try:
        RAGResponse(respuesta="x", fuentes=[], fragmentos_recuperados=-1)
        check("fragmentos_recuperados negativo rechazado", False)
    except ValidationError:
        check("fragmentos_recuperados negativo rechazado", True)


# ---------------------------------------------------------------------------
# 2. Fábrica de modelos, prompt y contexto
# ---------------------------------------------------------------------------


def verificar_generacion() -> None:
    seccion("2. Fábrica de modelos, prompt y contexto (rag_chain.py)")

    try:
        rag_chain.get_model("cohere")
        check("proveedor no soportado rechazado", False)
    except ValueError:
        check("proveedor no soportado rechazado", True)

    entorno_previo = dict(os.environ)
    try:
        for var in ("ANTHROPIC_API_KEY",):
            os.environ.pop(var, None)
        rag_chain.get_model("anthropic")
        check("falta de API key detectada", False)
    except ValueError:
        check("falta de API key detectada", True)
    finally:
        os.environ.clear()
        os.environ.update(entorno_previo)

    check(
        "el prompt declara {contexto}, {pregunta} y {formato}",
        set(rag_chain.PROMPT.input_variables) == {"contexto", "pregunta", "formato"},
    )
    check(
        "el system prompt instruye a no inventar (filtro de veracidad)",
        "No tengo acceso a esa información" in rag_chain.SYSTEM_PROMPT,
    )

    docs = [
        Document(page_content="200 km por día", metadata={"source": "data/combustible_kilometraje.txt"}),
        Document(page_content="edad mínima 21 años", metadata={"source": "data/requisitos_alquiler.txt"}),
    ]
    contexto = rag_chain.formatear_documentos(docs)
    check(
        "formatear_documentos etiqueta cada fragmento con su fuente",
        "[Fuente: data/combustible_kilometraje.txt]" in contexto
        and "[Fuente: data/requisitos_alquiler.txt]" in contexto,
    )

    # build_chain necesita una key para instanciar el cliente; usamos una falsa
    # (no se llama a la API, sólo se construye la cadena).
    entorno_previo = dict(os.environ)
    try:
        os.environ["GEMINI_API_KEY"] = "clave-falsa-para-construir"
        cadena = rag_chain.build_chain("gemini")
        check("build_chain() devuelve un Runnable", isinstance(cadena, Runnable))
        check(
            "la cadena queda envuelta en reintento (.with_retry)",
            type(cadena).__name__ == "RunnableRetry",
        )
    finally:
        os.environ.clear()
        os.environ.update(entorno_previo)


# ---------------------------------------------------------------------------
# 3. Chunking (ingesta.py)
# ---------------------------------------------------------------------------


def verificar_chunking() -> None:
    seccion("3. Chunking estratégico (ingesta.py)")

    check(
        "chunk_size cumple el mínimo de 500 tokens",
        ingesta.CHUNK_SIZE >= 500,
        f"chunk_size={ingesta.CHUNK_SIZE}",
    )
    check(
        "chunk_overlap cumple el mínimo de 50 tokens",
        ingesta.CHUNK_OVERLAP >= 50,
        f"overlap={ingesta.CHUNK_OVERLAP}",
    )

    parrafo = (
        "AutoRenta regula el alquiler de sus vehículos mediante políticas estrictas. "
        "Cada sección detalla condiciones concretas para los clientes. "
    )
    texto_largo = parrafo * 40  # fuerza más de un fragmento
    docs = [Document(page_content=texto_largo, metadata={"source": "sintetico.txt"})]
    chunks = ingesta.fragmentar(docs)
    check("un documento largo se parte en más de un fragmento", len(chunks) > 1, f"{len(chunks)} chunks")
    check("cada chunk conserva la metadata de origen", all(c.metadata.get("source") == "sintetico.txt" for c in chunks))


# ---------------------------------------------------------------------------
# 4. Camino real: flujo RAG de punta a punta
# ---------------------------------------------------------------------------

_ERRORES_DE_ENTORNO = (
    "429", "503", "resource_exhausted", "ratelimit", "rate limit", "quota",
    "unavailable", "overloaded", "authentication", "invalid api key",
    "expired_api_key", "permission", "401",
)


def _es_error_de_entorno(exc: Exception) -> bool:
    firma = f"{type(exc).__name__} {exc}".lower()
    return any(m in firma for m in _ERRORES_DE_ENTORNO)


async def verificar_camino_real(forzar: bool = False) -> None:
    seccion("4. Flujo RAG real (requiere índice + API key)")

    provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
    if provider not in rag_chain._API_KEY_VARS:
        provider = "gemini"

    tiene_key = bool(
        rag_chain._gemini_api_key() if provider == "gemini"
        else rag_chain._env_str(rag_chain._API_KEY_VARS[provider])
    )
    tiene_indice = ingesta.indice_existe()

    if not (tiene_key and tiene_indice):
        motivo = []
        if not tiene_indice:
            motivo.append("falta el índice (corré `python ingesta.py`)")
        if not tiene_key:
            motivo.append(f"falta API key para '{provider}'")
        detalle = "; ".join(motivo)
        if forzar:
            check(f"flujo RAG real ({provider})", False, f"{detalle} (forzado con --real)")
        else:
            print(f"  [SKIP ] flujo RAG real ({provider}) - {detalle}: se saltea, no cuenta como falla.")
        return

    from main import PREGUNTA_REAL, PREGUNTA_TRAMPA

    try:
        real = await rag_chain.get_rag_response(PREGUNTA_REAL, provider=provider)
        trampa = await rag_chain.get_rag_response(PREGUNTA_TRAMPA, provider=provider)
    except Exception as exc:  # noqa: BLE001
        detalle = f"lanzó {type(exc).__name__}: {exc}"
        if _es_error_de_entorno(exc) and not forzar:
            print(f"  [SKIP ] flujo RAG real ({provider}) - problema de entorno, no de código: {detalle}")
        else:
            check(f"flujo RAG real ({provider})", False, detalle)
        return

    check("la pregunta real recupera fragmentos y responde", real.fragmentos_recuperados > 0 and len(real.respuesta) > 0)
    check("la pregunta real cita los '200' km diarios", "200" in real.respuesta, f"respuesta={real.respuesta!r}")
    check(
        "la pregunta trampa NO alucina (declara que no tiene la info)",
        "no tengo acceso" in trampa.respuesta.lower(),
        f"respuesta={trampa.respuesta!r}",
    )


async def main() -> int:
    forzar = "--real" in sys.argv[1:]
    print("Verificación del Entregable 3 - Sistema RAG local")
    verificar_schemas()
    verificar_generacion()
    verificar_chunking()
    await verificar_camino_real(forzar=forzar)

    print(f"\n{'=' * 72}")
    if fallos:
        print(f"FALLARON {len(fallos)} criterios: {', '.join(fallos)}")
        return 1
    print("Todos los criterios se cumplen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
