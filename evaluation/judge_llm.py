import json

from openai import OpenAI

import config


class QwenJudge:
    def __init__(self):
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError(
                "Missing DASHSCOPE_API_KEY. Set it in an environment variable or a local qwen.env before running evaluation."
            )

        self.client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.DASHSCOPE_BASE_URL,
        )

    def evaluate_logic(self, original, reconstructed):
        prompt = f"""
You are a financial compliance reviewer.
Compare the original legal text and the reconstructed rule.

Focus on:
1. Lost negation or inverted meaning
2. Subject/entity mismatch
3. Missing required conditions

Return JSON only:
{{
  "is_pass": true,
  "reason": "short explanation",
  "risk_type": "none|negation_loss|missing_condition|entity_mismatch|other"
}}

Original:
{original}

Reconstructed:
{reconstructed}
"""

        try:
            response = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"LLM Error: {e}")
            return {"is_pass": False, "reason": "API call failed", "risk_type": "SystemError"}
