import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QWEN_ENV_PATH = PROJECT_ROOT / "qwen.env"

load_dotenv(QWEN_ENV_PATH, override=False)


def normalize_api_config(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    reasoning_model: str | None = None,
) -> dict[str, str]:
    resolved_model = str(model or os.getenv("QWEN_MODEL", "qwen-plus")).strip()
    return {
        "api_key": str(api_key or os.getenv("DASHSCOPE_API_KEY", "")).strip(),
        "base_url": str(
            base_url or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        ).strip(),
        "model": resolved_model,
        "reasoning_model": str(reasoning_model or os.getenv("QWEN_REASONING_MODEL", resolved_model)).strip(),
    }


def get_dashscope_api_key() -> str:
    api_key = normalize_api_config()["api_key"]
    if not api_key:
        raise RuntimeError(
            f"Missing `DASHSCOPE_API_KEY`. Set it in `{QWEN_ENV_PATH}` or an environment variable. "
            "See `qwen.env.example`."
        )
    return api_key


def get_dashscope_base_url() -> str:
    return normalize_api_config()["base_url"]


def get_default_model() -> str:
    return normalize_api_config()["model"]


def get_reasoning_model() -> str:
    return normalize_api_config()["reasoning_model"]


def build_client(api_config: dict[str, Any] | None = None) -> OpenAI:
    resolved = normalize_api_config(**(api_config or {}))
    if not resolved["api_key"]:
        raise RuntimeError(
            f"Missing `DASHSCOPE_API_KEY`. Set it in `{QWEN_ENV_PATH}` or an environment variable. "
            "See `qwen.env.example`."
        )
    return OpenAI(
        api_key=resolved["api_key"],
        base_url=resolved["base_url"],
    )


def call_qwen(
    prompt_text: str,
    model: str | None = None,
    timeout: int = 600,
    api_config: dict[str, Any] | None = None,
) -> str:
    resolved = normalize_api_config(model=model, **(api_config or {}))
    client = build_client(resolved)
    completion = client.chat.completions.create(
        model=resolved["model"],
        messages=[{"role": "user", "content": prompt_text}],
        timeout=timeout,
    )
    return completion.choices[0].message.content
