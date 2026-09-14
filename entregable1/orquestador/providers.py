"""Las tres corrutinas que simulan/ejecutan llamadas a modelos distintos.

Cada una respeta el mismo contrato: `async def (prompt, ctx) -> ModelResult`,
nunca bloquea el event loop y nunca lanza hacia afuera salvo `CancelledError`
(que debe propagarse para que el timeout del orquestador funcione).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Settings
from .logging_setup import elapsed

log = logging.getLogger("orquestador.providers")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


@dataclass(slots=True)
class ModelResult:
    """Resultado normalizado, independiente del proveedor."""

    provider: str
    model: str
    ok: bool
    text: str = ""
    latency_s: float = 0.0
    error: str | None = None
    simulated: bool = False

    def preview(self, width: int = 90) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        flat = " ".join(self.text.split())
        return flat[:width] + ("…" if len(flat) > width else "")


@dataclass(slots=True)
class CallContext:
    """Dependencias compartidas por las corrutinas."""

    settings: Settings
    http: httpx.AsyncClient
    semaphore: asyncio.Semaphore | None = None


class ProviderCall(Protocol):
    async def __call__(self, prompt: str, ctx: CallContext) -> ModelResult: ...


async def _simulate_latency(seconds: float) -> None:
    """Espera no bloqueante. Jamás `time.sleep`: congelaría el event loop."""
    await asyncio.sleep(seconds)


def _log_start(provider: str, model: str, mode: str) -> float:
    log.info("▶ %-13s inicia llamada (%s, modelo=%s)", provider, mode, model)
    return elapsed()


# --------------------------------------------------------------------------
# 1. Gemini (Google AI Studio - tier gratuito)
# --------------------------------------------------------------------------
async def gemini_call(prompt: str, ctx: CallContext) -> ModelResult:
    model = ctx.settings.gemini_model
    key = ctx.settings.gemini_api_key

    if not key:
        started = _log_start("gemini", model, "simulado")
        await _simulate_latency(random.uniform(0.4, 1.2))
        return ModelResult(
            provider="gemini",
            model=model,
            ok=True,
            text="[simulado] Respuesta de Gemini sin API key.",
            latency_s=elapsed() - started,
            simulated=True,
        )

    started = _log_start("gemini", model, "real")
    try:
        response = await ctx.http.post(
            GEMINI_URL.format(model=model),
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 256,
                    # Sin esto, 2.5-flash gasta el presupuesto "pensando" y
                    # puede devolver un candidate sin `parts`.
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return ModelResult("gemini", model, True, text, elapsed() - started)
    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        return _http_error("gemini", model, exc, started)
    except Exception as exc:  # red caída, JSON inesperado, etc.
        return ModelResult(
            "gemini", model, False, latency_s=elapsed() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------
# 2. Groq (Llama 3.3 70B - tier gratuito, API compatible con OpenAI)
# --------------------------------------------------------------------------
async def groq_call(prompt: str, ctx: CallContext) -> ModelResult:
    model = ctx.settings.groq_model
    key = ctx.settings.groq_api_key

    if not key:
        started = _log_start("groq", model, "simulado")
        await _simulate_latency(random.uniform(0.3, 1.0))
        return ModelResult(
            provider="groq",
            model=model,
            ok=True,
            text="[simulado] Respuesta de Groq sin API key.",
            latency_s=elapsed() - started,
            simulated=True,
        )

    started = _log_start("groq", model, "real")
    try:
        response = await ctx.http.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 256,
            },
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return ModelResult("groq", model, True, text, elapsed() - started)
    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        return _http_error("groq", model, exc, started)
    except Exception as exc:
        return ModelResult(
            "groq", model, False, latency_s=elapsed() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


# --------------------------------------------------------------------------
# 3. Llama local (siempre simulado: excede el timeout a propósito)
# --------------------------------------------------------------------------
async def local_llama_call(prompt: str, ctx: CallContext) -> ModelResult:
    model = "llama-3.2-3b-local"
    latency = ctx.settings.local_llama_latency_s
    started = _log_start("local_llama", model, f"simulado, {latency:.1f}s")
    await _simulate_latency(latency)
    return ModelResult(
        provider="local_llama",
        model=model,
        ok=True,
        text="[simulado] Inferencia local terminada.",
        latency_s=elapsed() - started,
        simulated=True,
    )


def _http_error(
    provider: str, model: str, exc: httpx.HTTPStatusError, started: float
) -> ModelResult:
    """Traduce un status HTTP a un error legible, sin filtrar la API key."""
    status = exc.response.status_code
    hint = {
        400: "request inválido (¿modelo inexistente?)",
        401: "API key inválida",
        403: "API key sin permisos / API deshabilitada en el proyecto",
        404: "modelo inexistente o dado de baja",
        429: "rate limit / cuota gratuita agotada",
        503: "proveedor saturado, reintentá en unos segundos",
    }.get(status, "error del proveedor")
    detail = exc.response.text[:200].replace("\n", " ")
    return ModelResult(
        provider, model, False, latency_s=elapsed() - started,
        error=f"HTTP {status} - {hint} :: {detail}",
    )


PROVIDERS: tuple[tuple[str, ProviderCall], ...] = (
    ("gemini", gemini_call),
    ("groq", groq_call),
    ("local_llama", local_llama_call),
)
