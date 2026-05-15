import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / "qwen.env", override=False)

INPUT_FILE = "../data/processed/legal_atoms_v4_final.xlsx"
OUTPUT_FILE = "../data/processed/legal_atoms_evaluated_final.csv"

THRESHOLD_LOW = 0.60
THRESHOLD_HIGH = 0.92

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).strip()
LLM_MODEL = os.getenv("QWEN_MODEL", "qwen-plus").strip()
