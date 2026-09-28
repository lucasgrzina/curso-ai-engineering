"""Cadena LCEL para el pipeline de extracción de entidades técnicas.

La fábrica de modelos reutiliza el patrón del Módulo 1
(`entregable1/llm_client/manager.py`): una función central resuelve el
proveedor a partir de variables de entorno, así que cambiar de OpenAI a
Anthropic o Gemini es cambiar el `.env`, no el código.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from schemas import EntidadesTecnicas

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()

logger = logging.getLogger("pipeline_extraccion")

# Modelos por defecto de cada proveedor, si el .env no dice otra cosa.
# Son IDs reales y vigentes para que el pipeline corra de entrada; se pueden
# ajustar desde el `.env` (variables *_MODEL) según el modelo que tengas habilitado.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-3.8-flash",
}

_API_KEY_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_BASE_URL_VARS: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
}

# Prompt modular: el texto de entrada y las instrucciones de formato llegan
# como variables, nunca como f-strings hardcodeadas dentro de la cadena.
PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Sos un analista técnico. Extraé información estructurada del texto que te "
            "pasa el usuario. Identificá las tecnologías, frameworks o herramientas "
            "mencionadas, evaluá el nivel de criticidad del problema o arquitectura "
            "descripta, y generá un resumen técnico breve.\n\n"
            "Instrucciones de formato:\n{formato}",
        ),
        ("human", "{texto}"),
    ]
)

FORMATO_INSTRUCCIONES = (
    "Devolvé únicamente el objeto estructurado solicitado: una lista de tecnologías no "
    "vacía (sin duplicados), el nivel de criticidad ('baja', 'media' o 'alta') y un "
    "resumen técnico de al menos 10 caracteres con 1-2 oraciones."
)


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


def get_model(provider: str = "openai") -> BaseChatModel:
    """Instancia el chat model del proveedor pedido, leyendo el `.env`."""
    provider = provider.lower()
    if provider not in _API_KEY_VARS:
        raise ValueError(f"Proveedor no soportado: {provider}")

    api_key = _env_str(_API_KEY_VARS[provider])
    if not api_key:
        raise ValueError(
            f"Falta {_API_KEY_VARS[provider]} para usar {provider}. "
            "Copiá .env.example a .env y completalo."
        )

    model_name = _env_str(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]
    base_url = _env_str(_BASE_URL_VARS[provider])

    # temperature=0: para extracción estructurada queremos determinismo, no creatividad.
    if provider == "openai":
        return ChatOpenAI(model=model_name, temperature=0, api_key=api_key, base_url=base_url)
    if provider == "anthropic":
        return ChatAnthropic(model=model_name, temperature=0, api_key=api_key)
    return ChatGoogleGenerativeAI(model=model_name, temperature=0, google_api_key=api_key)


def build_chain(provider: str = "openai") -> Runnable:
    """Cadena LCEL: prompt | modelo con salida forzada, con reintento automático."""
    model = get_model(provider)
    structured_model = model.with_structured_output(EntidadesTecnicas)

    max_attempts = int(_env_str("LLM_MAX_RETRIES") or "3")
    return (PROMPT | structured_model).with_retry(
        stop_after_attempt=max_attempts,
        wait_exponential_jitter=True,
    )


async def process_text(text: str, provider: str = "openai") -> EntidadesTecnicas:
    """Ejecuta la cadena de forma asíncrona y valida el resultado con Pydantic."""
    chain = build_chain(provider)
    logger.info("[%s] Procesando texto (%d caracteres)...", provider, len(text))

    try:
        resultado = await chain.ainvoke({"texto": text, "formato": FORMATO_INSTRUCCIONES})
    except Exception as exc:
        logger.error("[%s] Falló tras los reintentos configurados: %s", provider, exc)
        raise

    logger.info("[%s] Extracción validada: %s", provider, resultado.model_dump())
    return resultado
