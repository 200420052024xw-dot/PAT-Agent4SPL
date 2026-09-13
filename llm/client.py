"""OpenAI-compatible Chat Completions client."""

from __future__ import annotations

from typing import Any, List

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

    def complete(self, prompt: str, max_tokens: int) -> str:
        """Send one repair prompt through an OpenAI-compatible endpoint."""
        try:
            response = self._client.chat.completions.create(
                model=self.config.model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
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
