from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / "qwen.env"

load_dotenv(ENV_PATH, override=False)

DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


def get_neo4j_password() -> str:
    return os.getenv("NEO4J_PASSWORD", "")


def get_neo4j_config() -> tuple[str, str, str]:
    return DEFAULT_NEO4J_URI, DEFAULT_NEO4J_USER, get_neo4j_password()


def get_neo4j_encrypted(default: bool = False) -> bool:
    value = os.getenv("NEO4J_ENCRYPTED")
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def require_neo4j_password(password: str | None = None) -> str:
    value = password if password is not None else get_neo4j_password()
    if not value:
        raise RuntimeError("Neo4j password is not configured. Set NEO4J_PASSWORD or pass --neo4j-password.")
    return value
