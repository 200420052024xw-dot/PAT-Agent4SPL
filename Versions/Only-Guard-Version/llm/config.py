"""Load OpenAI-compatible endpoint settings from the project .env file."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional
from dotenv import load_dotenv
from pathlib import Path
import os

from .errors import LLMConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
MODEL_ID = ""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    model_id: str

    def public_dict(self) -> Dict[str, str]:
        return {"base_url": self.base_url, "model_id": self.model_id}

def get_llm_config(env_file:Optional[Path]=None) -> LLMConfig:
    load_dotenv(dotenv_path=env_file or ENV_FILE, override=False)

    base_url=os.getenv("LLM_BASE_URL")
    api_key=os.getenv("LLM_API_KEY")
    model_id=os.getenv("LLM_MODEL_ID")

    if not all([base_url, api_key, model_id]):
        missing=[
            name for name,value in[
                ("LLM_BASE_URL", base_url),
                ("LLM_API_KEY", api_key),
                ("LLM_MODEL_ID", model_id),
            ] if not value
        ]
        raise LLMConfigurationError(
            f"Missing required env variables: {missing}"
        )

    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model_id=model_id
    )


load_llm_config = get_llm_config
