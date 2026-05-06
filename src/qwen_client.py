import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parent.parent
QWEN_ENV_PATH = PROJECT_ROOT / "qwen.env"

load_dotenv(QWEN_ENV_PATH, override=True)


def get_dashscope_api_key() -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(f"Missing `DASHSCOPE_API_KEY`. Expected it in `{QWEN_ENV_PATH}` or environment.")
    return api_key


def get_dashscope_base_url() -> str:
    return os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).strip()


def get_default_model() -> str:
    return os.getenv("QWEN_MODEL", "qwen-plus").strip()


def get_reasoning_model() -> str:
    return os.getenv("QWEN_REASONING_MODEL", get_default_model()).strip()


def build_client() -> OpenAI:
    return OpenAI(
        api_key=get_dashscope_api_key(),
        base_url=get_dashscope_base_url(),
    )


def call_qwen(prompt_text: str, model: str | None = None, timeout: int = 600) -> str:
    client = build_client()
    completion = client.chat.completions.create(
        model=model or get_default_model(),
        messages=[{"role": "user", "content": prompt_text}],
        timeout=timeout,
    )
    return completion.choices[0].message.content
