import json
import os
from pathlib import Path

import pandas as pd

from mock_db_schema import BUSINESS_DATA_DICTIONARY
from qwen_client import build_client, get_reasoning_model

try:
    from tqdm import tqdm
except ImportError:
    print("建议安装 tqdm: pip install tqdm")

    def tqdm(iterable, total=None, unit=None):
        return iterable


SYSTEM_PROMPT = f"""
你是一位金融数据架构师。你的任务是构建“法规-系统”映射表。

目标系统数据字典：
{BUSINESS_DATA_DICTIONARY}

任务要求：
1. 分析输入的法规原子内容。
2. 提取其中涉及的核心法律名词。
3. 将其映射到数据字典中的表、字段或接口。
4. 如果涉及具体数值或状态变化，请在 mapping_logic 中写出转换逻辑。

输出 JSON，包含 mappings 列表；每个元素包含：
- legal_term
- domain_mapping
- mapping_logic
- confidence
"""


def generate_mapping_for_row(content):
    try:
        client = build_client()
        completion = client.chat.completions.create(
            model=get_reasoning_model(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请提取术语映射：\n{content}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        return {"error": str(e), "mappings": []}


def main():
    project_root = Path(__file__).resolve().parent.parent
    input_path = project_root / "data" / "processed" / "legal_atoms_v4_final.xlsx"
    output_path = project_root / "data" / "processed" / "legal_entity_mapping_FULL.xlsx"

    if not input_path.exists():
        raise FileNotFoundError(f"Missing input file: {input_path}")

    df = pd.read_excel(input_path)
    total_count = len(df)
    print(f"开始生成映射表，共 {total_count} 条规则")

    all_mappings = []
    for _, row in tqdm(df.iterrows(), total=total_count, unit="条"):
        atom_id = str(row.get("atom_id", "UNK"))
        content = str(row.get("content_original", ""))
        if len(content) < 5:
            continue

        result = generate_mapping_for_row(content)
        mappings = result.get("mappings", [])
        if not isinstance(mappings, list):
            continue

        for item in mappings:
            all_mappings.append(
                {
                    "source_atom_id": atom_id,
                    "legal_term": item.get("legal_term", "未知"),
                    "domain_mapping": item.get("domain_mapping", item.get("domain_entity", "未映射")),
                    "mapping_logic": item.get("mapping_logic", ""),
                    "confidence": item.get("confidence", "Low"),
                    "source_content": content[:50] + "...",
                }
            )

    if not all_mappings:
        print("未生成任何映射数据，请检查模型配置或输入文件。")
        return

    mapping_df = pd.DataFrame(all_mappings).reindex(
        columns=[
            "source_atom_id",
            "legal_term",
            "domain_mapping",
            "mapping_logic",
            "confidence",
            "source_content",
        ]
    )
    mapping_df.to_excel(output_path, index=False)
    print(f"映射表已生成: {output_path}")
    print(f"生成关系数: {len(mapping_df)}")


if __name__ == "__main__":
    main()
