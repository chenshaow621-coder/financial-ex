import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / "qwen.env", override=False)

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
)

test_text = """
第九十三条 付款人依法支付票据金额的，对出票人不再承担受委托付款的责任，对持票人不再承担付款的责任。
但是，付款人以恶意或者重大过失付款的除外。
"""

system_prompt = """
你是一名挑剔的合规审计员。将法律文本转化为 Schema V4 原子。
特别注意：只要包含“重大过失”“恶意”“情节严重”等模糊词，必须标记 is_ambiguous=true。

输出 JSON 格式，包含 behavior_struct。
"""

response = client.chat.completions.create(
    model=os.getenv("QWEN_MODEL", "qwen-plus"),
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": test_text},
    ],
    response_format={"type": "json_object"},
)

print(json.dumps(json.loads(response.choices[0].message.content), indent=2, ensure_ascii=False))
