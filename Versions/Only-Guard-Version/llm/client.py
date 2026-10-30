from __future__ import annotations
from typing import Any, List, Mapping, Sequence
from .config import LLMConfig
from .errors import LLMConfigurationError, LLMRequestError


class OpenAICompatibleLLM:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError(
                "The openai package is not installed; run "
                "pip install -r requirements.txt."
            ) from exc

        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def complete(
        self,
        prompt: str | None = None,
        *,
        messages: Sequence[Mapping[str, str]] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send text messages through the configured OpenAI-compatible endpoint."""
        if messages is None:
            if prompt is None:
                raise ValueError("prompt or messages is required")
            request_messages = [{"role": "user", "content": prompt}]
        else:
            if prompt is not None:
                raise ValueError("prompt and messages cannot be used together")
            request_messages = [dict(message) for message in messages]
        request: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": request_messages,
        }
        if temperature is not None:
            request["temperature"] = temperature
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            raise LLMRequestError(f"OpenAI-compatible request failed: {exc}") from exc

        if not response.choices:
            raise LLMRequestError("OpenAI-compatible endpoint returned no choices.")

        content: Any = response.choices[0].message.content
        if isinstance(content, str):
            answer = content.strip()
        elif isinstance(content, list):
            text_parts: List[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text", "")))
                elif getattr(part, "type", None) == "text":
                    text_parts.append(str(getattr(part, "text", "")))
            answer = "\n".join(text_parts).strip()
        else:
            answer = ""

        if not answer:
            raise LLMRequestError("OpenAI-compatible endpoint returned empty content.")
        return answer
