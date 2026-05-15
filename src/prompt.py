from prompt_manager import render_prompt_template


# ==========================================
# Stage 1: 宏观类型扫描 (Type Scanning)
# 目标：给大模型减负，只做“定性”判断，不抓具体词汇
# ==========================================
def build_stage1_prompt(sentence: str) -> str:
    return render_prompt_template("extract_stage1", sentence=sentence)


# ==========================================
# Stage 2: 实体精准提取 (Entity Extraction with RAG)
# 目标：结合你的 CSV 字典库，精准提取，并发现新词汇
# ==========================================
def build_stage2_prompt(sentence: str, target_category: str, reference_dict: list) -> str:
    # 取字典前 60 个词作为高频参考，防止上下文窗口溢出或注意力稀释
    dict_str = "、".join(reference_dict[:60]) if reference_dict else "暂无参考词汇"
    return render_prompt_template(
        "extract_stage2",
        sentence=sentence,
        target_category=target_category,
        reference_dict=dict_str,
    )


# ==========================================
# Stage 3: 事件组装与逻辑裂变 (Event Assembly)
# 目标：基于已知实体碎片，构建 4W1H 原子规则
# ==========================================
def build_stage3_ee_prompt(sentence: str, ner_entities_json: str) -> str:
    return render_prompt_template(
        "extract_stage3",
        sentence=sentence,
        ner_entities_json=ner_entities_json,
    )
# ==========================================
# 5. 辅助函数 (ID生成器)
# 请将此代码添加到 schema.py 的底部
# ==========================================
def generate_atom_id(source_document: str, rule_type: str, index: int) -> str:
    """
    生成语义化ID: YZ-{法规代码}-{类型}-{序号}
    例如: YZ-NIL-PRO-00123
    """
    src_map = {
        "票据法": "NIL",
        "支付结算": "PSM",
        "反洗钱": "AML",
        "账户管理": "BAM",
        "商业银行": "CBL"
    }
    src_code = "GEN"
    for k, v in src_map.items():
        if k in source_document:
            src_code = v
            break

    try:
        type_code = rule_type.split("_")[0]
    except:
        type_code = "UNK"

    return f"YZ-{src_code}-{type_code}-{index:05d}"
