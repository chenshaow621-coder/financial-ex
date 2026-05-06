import os
import json
import pandas as pd
from qwen_client import build_client, get_reasoning_model
from tqdm import tqdm  # 引入进度条库
from mock_db_schema import BUSINESS_DATA_DICTIONARY

# 如果没有安装 tqdm，请先运行 pip install tqdm
try:
    from tqdm import tqdm
except ImportError:
    print("建议安装进度条库: pip install tqdm")


    # 简单的替代实现，防止报错
    def tqdm(iterable, total=None):
        return iterable

client = build_client()

SYSTEM_PROMPT = f"""
你是一位【金融数据架构师】。你的任务是构建“法规-系统”映射表。

### 目标系统数据字典
{BUSINESS_DATA_DICTIONARY}

### 任务要求
1. 分析输入的【法规原子内容】。
2. 提取其中涉及的核心【法律名词】（主体、客体、动作、状态）。
3. 将其映射到【数据字典】中具体的表、字段或接口。
4. 如果涉及具体数值或状态变更，请在 mapping_logic 中写出伪代码。

### 输出格式 (JSON)
请严格输出包含 "mappings" 列表的 JSON，每个元素包含：
- "legal_term": 法规原文名词
- "domain_mapping": 系统实体 (如 "Bill.status")
- "mapping_logic": 转换逻辑 (如 "== 'FROZEN'")
- "confidence": 置信度 (High/Medium/Low)
"""


def generate_mapping_for_row(content):
    try:
        completion = client.chat.completions.create(
            model=get_reasoning_model(),  # 全量跑建议用 qwen-plus 平衡速度和成本
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请提取术语映射：\n{content}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1  # 降低温度，让输出更稳定
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        # 记录错误但不中断程序
        return {"error": str(e), "mappings": []}


if __name__ == "__main__":
    # 1. 路径配置
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    input_path = os.path.join(project_root, "data", "processed", "legal_atoms_v4_final.xlsx")
    output_path = os.path.join(project_root, "data", "processed", "legal_entity_mapping_FULL.xlsx")

    if not os.path.exists(input_path):
        print("❌ 未找到原子数据文件，请先运行 main.py")
        exit()

    # 2. 读取全量数据
    df = pd.read_excel(input_path)

    # === 关键修改：移除所有过滤器，跑全量 ===
    # 如果你想先跑前 50 条试试水，可以取消下面这行的注释
    # target_df = df.head(50)
    target_df = df  # <--- 现在的设置：全量运行

    total_count = len(target_df)
    print(f"🚀 开始全量生成映射表，共计 {total_count} 条规则...")
    print("☕ 这可能需要几分钟，请耐心等待...")

    all_mappings = []

    # 使用 tqdm 显示进度条
    for idx, row in tqdm(target_df.iterrows(), total=total_count, unit="条"):
        atom_id = str(row.get('atom_id', 'UNK'))
        content = str(row.get('content_original', ''))

        # 跳过内容太短的无效行
        if len(content) < 5:
            continue

        result = generate_mapping_for_row(content)

        if "mappings" in result and isinstance(result["mappings"], list):
            for m in result["mappings"]:
                safe_mapping = {
                    'source_atom_id': atom_id,
                    'legal_term': m.get('legal_term', '未知'),
                    'domain_mapping': m.get('domain_mapping', m.get('domain_entity', '未映射')),
                    'mapping_logic': m.get('mapping_logic', '无'),
                    'confidence': m.get('confidence', 'Low'),
                    'source_content': content[:50] + "..."  # 截取一部分原文方便核对
                }
                all_mappings.append(safe_mapping)

    # 3. 保存结果
    if all_mappings:
        mapping_df = pd.DataFrame(all_mappings)

        # 规范列顺序
        cols = ['source_atom_id', 'legal_term', 'domain_mapping', 'mapping_logic', 'confidence', 'source_content']
        mapping_df = mapping_df.reindex(columns=cols)

        mapping_df.to_excel(output_path, index=False)
        print(f"\n✅ 全量映射表已生成！")
        print(f"📂 保存路径: {output_path}")
        print(f"📊 原始规则 {total_count} 条 -> 生成映射关系 {len(mapping_df)} 条")
    else:
        print("⚠️ 未生成任何数据，请检查网络或 Key 余额。")