"""Carga y validación de la configuración del orquestador.

Las API keys nunca se imprimen: `Settings.describe()` sólo informa si están
presentes o ausentes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Busca el .env desde el cwd hacia arriba; si no existe, no falla.
load_dotenv()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un número, se recibió {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser un entero, se recibió {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuración inmutable del proceso."""

    gemini_api_key: str | None = field(default=None, repr=False)
    groq_api_key: str | None = field(default=None, repr=False)

    gemini_model: str = "gemini-3.6-flash"
    groq_model: str = "openai/gpt-oss-20b"

    # Ejercicio: timeout total de 2 segundos sobre la ejecución completa.
    total_timeout_s: float = 2.0
    # Ejercicio: 10 llamadas disparadas, sólo 2 concurrentes.
    semaphore_limit: int = 2
    burst_size: int = 10

    # Latencia simulada del modelo "local" (supera el timeout a propósito).
    local_llama_latency_s: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            total_timeout_s=_env_float("TOTAL_TIMEOUT_S", 2.0),
            semaphore_limit=_env_int("SEMAPHORE_LIMIT", 2),
            burst_size=_env_int("BURST_SIZE", 10),
            local_llama_latency_s=_env_float("LOCAL_LLAMA_LATENCY_S", 3.0),
        )

    def describe(self) -> str:
        gemini = "real" if self.gemini_api_key else "simulado (falta GEMINI_API_KEY)"
        groq = "real" if self.groq_api_key else "simulado (falta GROQ_API_KEY)"
        return (
            f"gemini={gemini} | groq={groq} | local_llama=simulado | "
            f"timeout_total={self.total_timeout_s}s | semaforo={self.semaphore_limit} | "
            f"burst={self.burst_size}"
        )
