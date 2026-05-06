# ==========================================
# Stage 1: 宏观类型扫描 (Type Scanning)
# 目标：给大模型减负，只做“定性”判断，不抓具体词汇
# ==========================================
def build_stage1_prompt(sentence: str) -> str:
    prompt = f"""你是一位严谨的金融合规数据架构师。当前任务是：金融法规的命名实体识别（NER）- 阶段一。

【任务目标】
请阅读下述法律条文，判断其中包含了以下哪些宏观实体分类：
1. 法律主体(WHO)：执行主体、相关当事人、监管机构等。
2. 核心事项(WHAT)：核心对象、金融工具、法定权利义务等。
3. 时间情境(WHEN)：触发时机、法定期间、时间条件等。
4. 空间情境(WHERE)：适用场景、地理范围、系统环境等。
5. 规则类型(RULES)：强制义务、禁止、授权允许等规则定性。
6. 业务分类：支付结算、票据业务等。

【输入条文】
{sentence}

【输出要求】
请仅输出条文中实际包含的分类名称，以英文逗号分隔。如果全都不包含，请输出：无。
严禁输出任何多余的解释说明或 Markdown 标记。

示例输出：法律主体(WHO),核心事项(WHAT),时间情境(WHEN)
"""
    return prompt


# ==========================================
# Stage 2: 实体精准提取 (Entity Extraction with RAG)
# 目标：结合你的 CSV 字典库，精准提取，并发现新词汇
# ==========================================
def build_stage2_prompt(sentence: str, target_category: str, reference_dict: list) -> str:
    # 取字典前 60 个词作为高频参考，防止上下文窗口溢出或注意力稀释
    dict_str = "、".join(reference_dict[:60]) if reference_dict else "暂无参考词汇"

    prompt = f"""你是一位严谨的金融合规数据架构师。当前任务是：金融法规的命名实体识别（NER）- 阶段二。

【当前提取目标】
宏观分类：{target_category}

【参考知识库（动态字典）】
为了统一标准，以下是该分类下我们已知的规范实体词汇（供参考）：
[{dict_str}]

【提取核心指令】
请从输入的法律条文中，提取出所有属于“{target_category}”类型的实体。
1. 字典对齐：如果条文中出现了参考知识库中的词汇，请务必提取。
2. 动态扩表（极重要）：如果条文中出现了不在参考知识库中，但在语义上绝对属于“{target_category}”的新实体，也必须提取出来！我们依赖你发现新概念。
3. 忠于原文：提取条文中的原始表述，不要擅自概括或缩写。

【输入条文】
{sentence}

【输出要求】
请严格以 JSON 数组格式输出提取结果（确保是合法的 JSON，不要带 ```json 代码块标记）。
格式规范：
[
  {{"entity_name": "提取出的词汇1", "entity_type": "{target_category}"}},
  {{"entity_name": "提取出的词汇2", "entity_type": "{target_category}"}}
]
如果在条文中未找到对应实体，请严格输出空数组：[]。
严禁输出任何解释性废话。
"""
    return prompt


# ==========================================
# Stage 3: 事件组装与逻辑裂变 (Event Assembly)
# 目标：基于已知实体碎片，构建 4W1H 原子规则
# ==========================================
def build_stage3_ee_prompt(sentence: str, ner_entities_json: str) -> str:
    prompt = f"""你是一位顶尖的金融合规数据架构师。当前任务是：基于已提取的实体，进行【事件组装与逻辑裂变】。

【已知情报】
我们已经从法规中提取了关键实体碎片（JSON格式）：
{ner_entities_json}

【原始法规文本】
{sentence}

【核心任务指令】
请你根据上述实体碎片和原始文本，将其组装为“原子规则（LegalAtom）”。

1. 核心规则分类 (Rule Type)：
   必须从以下8大类中选择：OBL_MANDATORY(强制义务), PRO_FORBIDDEN(禁止), PER_AUTH(授权允许), EVT_TRIGGER(条件触发), VAL_THRESHOLD(阈值约束), PRC_FLOW(程序流程), OBL_ONGOING(持续性义务), RPT_DISCLOSURE(披露报告)。

2. 逻辑裂变 (复合句拆分 - 极其重要)：
   - 若文本包含转折（“但是”、“但”、“除...外”）或条件分句，必须物理拆分为多个独立的原子规则。
   - 前半段一般性规定：relation_type 设为 "DEFAULT"，parent_atom_id 为 null。
   - “但是...”转折句：relation_type 设为 "EXCEPTION"，parent_atom_id 指向关联的前半段 temp_id。
   - “...的除外”排除句：relation_type 设为 "EXCLUSION"，parent_atom_id 指向关联的前半段 temp_id。

3. 人工审核护栏 (Quality Control)：
   - 扫描模糊程度词（重大过失、恶意等）、模糊时间词（及时、合理期限）、外部引用不明（另行规定且未写明出处）、缺少违反后果的禁止性条款。
   - 只要发现，必须将 `is_ambiguous` 设为 true，并对应填写 `review_reason` (AMBIGUOUS_TEXT 或 MISSING_PARAM)。

4. 4W1H 组装：
   利用已知情报中的实体，将 Who, When, Where, What, How 填充完整。如果文本中确实没有某一项，填 "未指定"。

【输出要求】
请严格输出符合以下结构的 JSON（不要带 Markdown 代码块标记，确保能被 json.loads 直接解析）：
{{
  "atoms": [
    {{
      "temp_id": "1",
      "rule_type": "OBL_MANDATORY",
      "behavior_struct": {{ "who": "...", "when": "...", "where": "...", "what": "...", "how": "..." }},
      "relation_type": "DEFAULT",
      "parent_atom_id": null,
      "is_ambiguous": false,
      "review_reason": "NONE",
      "content_original": "前半段原文"
    }}
  ]
}}
"""
    return prompt
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
