"""Configuración compartida del agente: variables de entorno y fábrica del LLM.

Es la única fuente de verdad para `agente.py`, `main.py` y `verificar.py`.
La fábrica de modelos reutiliza el patrón del Entregable 2 (OpenAI, Anthropic o
Gemini detrás de una misma función); ninguna clave vive en el código.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()


def consola_utf8() -> None:
    """Windows abre stdout en cp1252 y rompe los acentos; forzamos UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigure = getattr(flujo, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


# ---------------------------------------------------------------------------
# Parámetros del agente
# ---------------------------------------------------------------------------

# Techo de pasos del grafo por invocación. Cada vuelta modelo -> herramienta
# consume 2 pasos, así que 10 alcanza para ~4 llamadas a herramientas.
RECURSION_LIMIT = int(_env_str("AGENT_RECURSION_LIMIT") or "10")
# Cuántos mensajes del historial ve el modelo (el checkpoint guarda todos).
MAX_MENSAJES = int(_env_str("AGENT_MAX_MESSAGES") or "30")
CHECKPOINT_DB = _env_str("CHECKPOINT_DB") or "checkpoints.sqlite"

# ---------------------------------------------------------------------------
# Proveedores de LLM
# ---------------------------------------------------------------------------

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-flash-latest",
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


def proveedor_por_defecto() -> str:
    return (_env_str("LLM_PROVIDER") or "gemini").lower()


def get_model(provider: str | None = None) -> BaseChatModel:
    """Instancia el chat model del proveedor pedido, leyendo el `.env`."""
    provider = (provider or proveedor_por_defecto()).lower()
    if provider not in _API_KEY_VARS:
        raise ValueError(f"Proveedor no soportado: {provider}")

    api_key = _env_str(_API_KEY_VARS[provider])
    if not api_key and provider == "gemini":
        api_key = _env_str("GOOGLE_API_KEY")  # nombre alternativo habitual
    if not api_key:
        raise ValueError(
            f"Falta {_API_KEY_VARS[provider]} para usar {provider}. "
            "Copiá .env.example a .env y completalo."
        )

    model_name = _env_str(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]
    base_url = _env_str(_BASE_URL_VARS[provider])

    # temperature=0: un agente que decide qué herramienta llamar debe ser determinista.
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, temperature=0, api_key=api_key, base_url=base_url)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_name, temperature=0, api_key=api_key)

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, temperature=0, google_api_key=api_key)
