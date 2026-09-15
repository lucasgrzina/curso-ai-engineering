"""Cliente asíncrono de Gemini, sobre su capa compatible con OpenAI.

Google expone los modelos Gemini en un endpoint que habla el mismo protocolo
que la API de Chat Completions de OpenAI. Eso permite reusar `OpenAIClient`
entero: el proveedor nuevo son cuatro líneas de configuración, no una
implementación paralela. Es también la prueba de que la abstracción de
`BaseLLMClient` paga — y la razón por la que `OPENAI_BASE_URL` existe.

El precio de esta decisión: las funciones propias de Gemini que no tienen
equivalente en el protocolo de OpenAI (por ejemplo `thinking_config` o las
`safety_settings` granulares) no quedan expuestas. Para eso haría falta el SDK
`google-genai` y una implementación directa de `_raw_generate` / `_raw_stream`,
que es exactamente lo que la clase base deja abierto.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..schemas import ChatMessage, ModelConfig
from .openai_client import OpenAIClient

# Capa de compatibilidad OpenAI de Google AI Studio.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiClient(OpenAIClient):
    """Gemini a través del protocolo de OpenAI.

    Hereda de `OpenAIClient` en vez de `BaseLLMClient` porque el formato del
    request y de la respuesta es el mismo; lo único que cambia es el endpoint.
    """

    def __init__(self, config: ModelConfig, api_key: str, base_url: str | None = None):
        super().__init__(config, api_key, base_url or GEMINI_BASE_URL)

    def _payload(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        """Agrega el control de razonamiento, que Gemini llama distinto.

        Los modelos Gemini 3 razonan antes de responder y esos tokens salen
        del mismo presupuesto que `max_tokens`: con un techo bajo la respuesta
        llega cortada (`finish_reason="length"`) aunque el texto visible sea
        mínimo. `reasoning_effort="none"` desactiva el razonamiento; subir
        `max_tokens` es la otra salida.
        """
        payload = super()._payload(messages)
        if self.config.effort:
            payload["extra_body"] = {"reasoning_effort": self.config.effort}
        return payload
