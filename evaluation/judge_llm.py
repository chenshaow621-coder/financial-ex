# evaluation/judge_llm.py
import json
from openai import OpenAI
import config  # 导入配置文件


class QwenJudge:
    def __init__(self):
        # 使用 OpenAI 兼容协议连接阿里云 Qwen
        self.client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def evaluate_logic(self, original, reconstructed):
        """
        调用 Qwen 进行逻辑一致性判断
        """
        prompt = f"""
        你是一名金融合规风控专家。请对比[原文]和[还原规则]，判断逻辑是否一致。
        重点检查：1.否定词是否遗漏（如"不得"变"可以"）；2.主体是否颠倒；3.必要条件是否缺失。

        【原文】：{original}
        【还原】：{reconstructed}

        请仅返回如下JSON格式（不要包含Markdown标记）：
        {{
            "is_pass": true/false,
            "reason": "简短的判定理由",
            "risk_type": "无风险/否定语义丢失/条件缺失/幻觉"
        }}
        """

        try:
            response = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"LLM Error: {e}")
            return {"is_pass": False, "reason": "API调用失败", "risk_type": "SystemError"}