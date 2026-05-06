import pandas as pd
from collections import defaultdict
import os


def build_entity_dictionary(csv_file_path: str) -> dict:
    """
    读取实体对照表 CSV，并转化为按 4W1R 宏观分类分组的 Python 字典。
    """
    if not os.path.exists(csv_file_path):
        print(f"❌ 找不到文件: {csv_file_path}")
        return {}

    try:
        df = pd.read_excel(csv_file_path, sheet_name="完整实体参考表")

        # ==========================================
        # 已经替换为你表格中真实的 Column Headers
        # ==========================================
        category_column = "实体类别"
        entity_column = "词语"

        if category_column not in df.columns or entity_column not in df.columns:
            print(f"❌ 表格中找不到指定的列名：'{category_column}' 或 '{entity_column}'")
            return {}

        # 映射逻辑：将你表格里的分类名对齐到 Schema 的 Enum 值
        category_mapping = {
            "LEGAL_ACTORS": "法律主体(WHO)",
            "法律主体": "法律主体(WHO)",
            "WHAT_CONCEPTS": "核心事项(WHAT)",
            "核心事项": "核心事项(WHAT)",
            "WHEN_CONTEXTS": "时间情境(WHEN)",
            "时间情境": "时间情境(WHEN)",
            "WHERE_CONTEXTS": "空间情境(WHERE)",
            "空间情境": "空间情境(WHERE)",
            "RULE_TYPES": "规则类型(RULES)",
            "规则类型": "规则类型(RULES)",
            "BUSINESS_CATEGORIES": "业务分类",
            "业务分类": "业务分类"
        }

        entity_dict = defaultdict(list)

        # 遍历数据行，过滤掉空值
        for index, row in df.dropna(subset=[category_column, entity_column]).iterrows():
            raw_category = str(row[category_column]).strip()
            entity_name = str(row[entity_column]).strip()

            # 防御性编程：如果你是把5个表格拼在了一个CSV里，中间可能会有重复的表头
            if raw_category == "实体类别" or entity_name == "词语":
                continue

            # 使用映射表进行名称对齐
            standard_category = category_mapping.get(raw_category, raw_category)

            # 存入字典 (去重)
            if entity_name not in entity_dict[standard_category]:
                entity_dict[standard_category].append(entity_name)

        print(f"✅ 成功构建实体字典！共加载 {len(entity_dict)} 个宏观类别。")
        return dict(entity_dict)

    except Exception as e:
        print(f"❌ 解析 CSV 时发生错误: {e}")
        return {}