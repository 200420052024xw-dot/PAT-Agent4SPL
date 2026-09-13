"""Errors exposed by the LLM adapter."""


class LLMError(RuntimeError):
    """Base error for configuration and request failures."""


class LLMConfigurationError(LLMError):
    """The OpenAI-compatible endpoint is not configured correctly."""


class LLMRequestError(LLMError):
    """The OpenAI-compatible endpoint rejected or failed a request."""
