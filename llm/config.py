"""Load OpenAI-compatible endpoint settings from the project .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

from .errors import LLMConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
REQUIRED_VARIABLES = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_ID")


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    model_id: str

    def public_dict(self) -> Dict[str, str]:
        """Return configuration safe to write into result manifests."""
        return {"base_url": self.base_url, "model_id": self.model_id}


def load_env_file(path: Path) -> None:
    """Load a small dotenv file without overriding existing environment values."""
    if not path.is_file():
        return

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise LLMConfigurationError(
                f"Invalid .env entry at {path}:{line_number}; expected NAME=value."
            )
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            os.environ.setdefault(name, value)


def load_llm_config(env_file: Optional[Path] = None) -> LLMConfig:
    path = (env_file or DEFAULT_ENV_FILE).expanduser().resolve()
    load_env_file(path)

    values = {name: os.environ.get(name, "").strip() for name in REQUIRED_VARIABLES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise LLMConfigurationError(
            f"Missing LLM configuration: {', '.join(missing)}. "
            f"Set them in {path} or as environment variables."
        )

    parsed_url = urlparse(values["LLM_BASE_URL"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise LLMConfigurationError(
            "LLM_BASE_URL must be a complete http:// or https:// URL."
        )

    return LLMConfig(
        base_url=values["LLM_BASE_URL"].rstrip("/"),
        api_key=values["LLM_API_KEY"],
        model_id=values["LLM_MODEL_ID"],
    )
