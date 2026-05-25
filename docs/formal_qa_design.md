# 形式化问答优化设计文档

> **用途**：本文档用于指导金融法规合规问答系统的形式化推理层开发。  
> **背景**：系统已完成抽取 → MySQL → 冲突检测 → Neo4j 图谱 → 召回判断的完整链路。  
> **目标**：在现有图谱基础上增加形式化快速路径，当推理链路完整时跳过 LLM 判断直接返回确定性答案。

---

## 1. 核心认知：为什么你的系统不需要额外建答案实体

### 1.1 KGQA 论文的"答案实体"前提

GCR（ICML 2025）、RoG（ICLR 2024）、ToG 2.0（ICLR 2025）等顶会工作做的是**事实型问答**：

```
问题："法国的首都是什么？"
路径：[法国] --首都--> [巴黎]   ← "巴黎"必须是图里的节点
```

答案本身是一个图节点，所以"答案实体必须在图中"是硬性前提。

### 1.2 本系统的根本差异：规则检索型问答

本系统的问题类型是：

```
问题："存款人开立基本户时必须提交哪些材料？"
路径：[存款人:BusinessActor] --INVOLVES_ACTOR--> [BusinessAtom]
                                                      ↓
                                               atom.how = "应提交营业执照..."
                                               atom.rule_type = "OBL_MANDATORY"
                                               atom.article_reference = "第X条"
```

**`BusinessAtom` 节点本身就是答案容器，其属性值就是答案内容。**  
不需要额外建答案节点——Atom 已经承载了完整答案。

### 1.3 当前图谱资产盘点

| 节点/关系 | 数量 | 在形式化推理中的角色 |
|---|---|---|
| `BusinessAtom` | 1942 | **答案容器**，存储 `rule_type`/`how`/`article_reference` |
| `BusinessActor` | 914 | 问题实体 → 有时本身是答案（主体查询型） |
| `BusinessObject` | 2017 | 问题实体 |
| `BusinessTimeContext` | 1943 | 问题实体 |
| `BusinessScene` | 336 | 场景语义锚点 |
| `INVOLVES_ACTOR` | 3621 | 主干推理边 |
| `TARGETS_OBJECT` | 3155 | 主干推理边 |
| `HAS_TIME_CONTEXT` | 2409 | 主干推理边 |
| `MATCHES_SCENE` | 296 | 场景对齐边（覆盖率待提升） |

**当前形式化快速路径可覆盖范围**：有 `MATCHES_SCENE` 且 `rule_type` 为确定性类型的 Atom，约 233 条（1942 - 1709）。  
**快速路径扩展方向**：提升 scene 匹配覆盖率，每提升一批 `MATCHES_SCENE`，快速路径覆盖范围就扩大一批。

---

## 2. 问题类型分类

合规问答按"答案在 Atom 的哪个维度"分为三类，每类对应不同的 Cypher 模板和答案组装方式。

### Type A：义务内容型（最常见）

**触发特征**：问"必须做什么"、"有何要求"、"应如何处理"

```
问题示例：
- "存款人开立基本存款账户时需要提交什么？"
- "银行在发现可疑交易时应当怎么做？"
- "总包单位不得进行哪些操作？"

答案字段：atom.how
答案节点：BusinessAtom 本身（读属性）
```

**对应 rule_type**：`OBL_MANDATORY`、`OBL_ONGOING`、`PRO_FORBIDDEN`、`PRC_FLOW`

---

### Type B：主体查询型

**触发特征**：问"谁有权"、"哪些机构"、"什么主体"

```
问题示例：
- "谁可以开立同业银行结算账户？"
- "哪些机构有权核发开户登记证？"
- "哪些单位需要执行农民工工资专用账户制度？"

答案字段：BusinessActor.normalized_name（实体节点本身是答案）
答案节点：BusinessActor（这是唯一一种答案真的是图节点的情况）
```

**对应 rule_type**：`PER_AUTH`、`OBL_MANDATORY`

---

### Type C：条件/场景查询型

**触发特征**：问"什么情况下"、"何时触发"、"适用条件是什么"

```
问题示例：
- "什么情况下需要重新核验客户身份？"
- "临时存款账户在什么条件下可以开立？"
- "账户被中止业务是指什么状态？"

答案字段：atom.when + atom.how 组合，或 atom.content_original
答案节点：BusinessAtom + BusinessTimeContext
```

**对应 rule_type**：`EVT_TRIGGER`、`VAL_THRESHOLD`、`OBL_MANDATORY`

---

## 3. 可回答性判定（Answerability Check）

这是形式化快速路径的入口判断，**四个条件全部满足才走快速路径**。

```python
# src/formal_qa.py

DETERMINISTIC_RULE_TYPES = {
    "OBL_MANDATORY",   # 强制义务：应当/必须
    "PRO_FORBIDDEN",   # 禁止性规定：不得/禁止  
    "PER_AUTH",        # 授权许可：可以/有权
    "VAL_THRESHOLD",   # 定义/阈值：术语定义或数值标准
    "PRC_FLOW",        # 流程规范：操作步骤
}

def check_answerability(
    who: str | None,
    what: str | None,
    when: str | None,
    question_type: str,   # "A" | "B" | "C"
    graph,                # Neo4j driver session
    conflict_detector,    # 复用已有的 conflict_detection.py
) -> dict:
    """
    返回：
    {
        "answerable": bool,
        "confidence": "formal" | "llm-inferred" | "unresolved",
        "atoms": [...],
        "fail_reason": str | None   # 哪个条件不满足
    }
    """

    # ── 条件 1：证据 Atom 存在 ──────────────────────────────────────────
    atoms = retrieve_atoms(who, what, when, graph)
    if not atoms:
        return _fail("no_atoms", "图谱中无匹配原子，走慢速路径")

    # ── 条件 2：rule_type 确定性 ────────────────────────────────────────
    rule_types = {a["rule_type"] for a in atoms}
    if not rule_types.issubset(DETERMINISTIC_RULE_TYPES):
        ambiguous = rule_types - DETERMINISTIC_RULE_TYPES
        return _fail("ambiguous_rule_type", f"包含非确定性规则类型: {ambiguous}")

    # ── 条件 3：无冲突 ──────────────────────────────────────────────────
    conflicts = conflict_detector.check(atoms)
    if conflicts:
        return _fail("conflict_detected", f"发现 {len(conflicts)} 对冲突原子")

    # ── 条件 4：问题实体与 Atom 完整匹配 ───────────────────────────────
    if question_type == "A" and not any(a.get("how") for a in atoms):
        return _fail("missing_how_field", "Atom 缺少 how 字段内容")

    return {
        "answerable": True,
        "confidence": "formal",
        "atoms": atoms,
        "fail_reason": None
    }

def _fail(reason: str, msg: str) -> dict:
    return {
        "answerable": False,
        "confidence": "llm-inferred",
        "atoms": [],
        "fail_reason": f"{reason}: {msg}"
    }
```

---

## 4. Cypher 模板库

### 4.1 Type A — 义务内容型

```cypher
// 基础版：按 who + rule_type 检索
MATCH (actor:BusinessActor {normalized_name: $who})
      <-[:INVOLVES_ACTOR]-(atom:BusinessAtom)
WHERE atom.rule_type IN $rule_types
RETURN atom.atom_id,
       atom.rule_type,
       atom.how,
       atom.article_reference,
       atom.source_document,
       atom.content_original,
       atom.is_ambiguous
ORDER BY atom.legal_level, atom.article_reference
LIMIT 20
```

```cypher
// 精确版：who + when 双维度锁定
MATCH (actor:BusinessActor {normalized_name: $who})
      <-[:INVOLVES_ACTOR]-(atom:BusinessAtom)
      -[:HAS_TIME_CONTEXT]->(time:BusinessTimeContext)
WHERE atom.rule_type IN $rule_types
  AND time.normalized_name = $when
RETURN atom.atom_id,
       atom.how,
       atom.article_reference,
       atom.source_document
LIMIT 10
```

```cypher
// 场景版：通过 BusinessScene 精召回（仅适用于有 MATCHES_SCENE 的 Atom）
MATCH (scene:BusinessScene {scene_id: $scene_id})
      <-[:MATCHES_SCENE]-(atom:BusinessAtom)
      -[:INVOLVES_ACTOR]->(actor:BusinessActor)
WHERE atom.rule_type IN $rule_types
RETURN atom.atom_id,
       atom.how,
       atom.article_reference,
       atom.source_document,
       actor.normalized_name AS subject
LIMIT 20
```

### 4.2 Type B — 主体查询型

```cypher
// 查"谁有权做某件事"
MATCH (atom:BusinessAtom)-[:INVOLVES_ACTOR]->(actor:BusinessActor)
WHERE (atom)-[:TARGETS_OBJECT]->(:BusinessObject {normalized_name: $what})
  AND atom.rule_type IN ['PER_AUTH', 'OBL_MANDATORY']
RETURN DISTINCT actor.normalized_name,
                actor.normalized_name AS canonical,
                atom.article_reference,
                atom.source_document
ORDER BY actor.normalized_name
LIMIT 20
```

```cypher
// 查"谁被禁止做某件事"
MATCH (atom:BusinessAtom)-[:INVOLVES_ACTOR]->(actor:BusinessActor)
WHERE (atom)-[:TARGETS_OBJECT]->(:BusinessObject {normalized_name: $what})
  AND atom.rule_type = 'PRO_FORBIDDEN'
RETURN DISTINCT actor.normalized_name,
                atom.how,
                atom.article_reference
```

### 4.3 Type C — 条件/场景查询型

```cypher
// 查"什么情况下触发某规则"
MATCH (atom:BusinessAtom)-[:HAS_TIME_CONTEXT]->(time:BusinessTimeContext)
WHERE (atom)-[:INVOLVES_ACTOR]->(:BusinessActor {normalized_name: $who})
  AND atom.rule_type IN ['EVT_TRIGGER', 'OBL_MANDATORY']
RETURN time.normalized_name  AS trigger_condition,
       atom.how              AS required_action,
       atom.article_reference,
       atom.source_document
ORDER BY atom.legal_level
LIMIT 10
```

```cypher
// 查术语定义
MATCH (atom:BusinessAtom)-[:TARGETS_OBJECT]->(obj:BusinessObject {normalized_name: $term})
WHERE atom.rule_type = 'VAL_THRESHOLD'
RETURN atom.how            AS definition,
       atom.article_reference,
       atom.source_document,
       atom.content_original
LIMIT 5
```

---

## 5. 答案模板（快速路径直接填充）

```python
# src/formal_qa.py

ANSWER_TEMPLATES = {
    "OBL_MANDATORY": (
        "根据{source_document}·{article_reference}，"
        "{who}在{when}，应当{how}。"
    ),
    "PRO_FORBIDDEN": (
        "根据{source_document}·{article_reference}，"
        "{who}不得{how}。"
    ),
    "PER_AUTH": (
        "根据{source_document}·{article_reference}，"
        "{who}有权{how}。"
    ),
    "VAL_THRESHOLD": (
        "根据{source_document}·{article_reference}，"
        "{what}的定义为：{how}。"
    ),
    "PRC_FLOW": (
        "根据{source_document}·{article_reference}，"
        "操作流程如下：{how}。"
    ),
    "OBL_ONGOING": (
        "根据{source_document}·{article_reference}，"
        "{who}在{when}期间，持续负有{how}的义务。"
    ),
}

def build_formal_answer(atoms: list[dict], question_type: str) -> dict:
    """
    从 Atom 属性直接组装答案，不调用 LLM。
    """
    answers = []
    for atom in atoms:
        tmpl = ANSWER_TEMPLATES.get(atom["rule_type"], "{how}")
        text = tmpl.format(
            source_document=atom.get("source_document", ""),
            article_reference=atom.get("article_reference", ""),
            who=atom.get("who", "相关主体"),
            what=atom.get("what", ""),
            when=atom.get("when", "适用情形下"),
            how=atom.get("how", ""),
        )
        answers.append({
            "text": text,
            "atom_id": atom["atom_id"],
            "source": f"{atom['source_document']}·{atom['article_reference']}",
            "rule_type": atom["rule_type"],
            "confidence": "formal",
            "original": atom.get("content_original", ""),
        })
    return {
        "answer_count": len(answers),
        "answers": answers,
        "confidence": "formal",
        "routed_by": "fast_path",
    }
```

---

## 6. 主路由逻辑

```python
# src/formal_qa.py

def answer_question(
    question: str,
    graph,
    llm_client,
    conflict_detector,
    entity_extractor,      # 复用已有抽取逻辑
    normalizer,            # 复用 src/entity_normalization.py
) -> dict:
    """
    主入口：根据可回答性判定结果路由到快速路径或慢速路径。
    """

    # Step 1: 问题类型识别 + 实体抽取
    q_type, raw_entities = entity_extractor.parse(question)
    # q_type: "A" | "B" | "C"
    # raw_entities: {"who": "人民银行", "what": "同业账户", "when": "开立时"}

    # Step 2: 实体规范化（复用 src/entity_normalization.py）
    entities = {
        k: normalizer.normalize(v)
        for k, v in raw_entities.items()
        if v is not None
    }
    # entities: {"who": "中国人民银行", "what": "同业银行结算账户", "when": "开立账户时"}

    # Step 3: 可回答性判定
    check = check_answerability(
        who=entities.get("who"),
        what=entities.get("what"),
        when=entities.get("when"),
        question_type=q_type,
        graph=graph,
        conflict_detector=conflict_detector,
    )

    # Step 4a: 快速路径（形式化直接返回）
    if check["answerable"]:
        return build_formal_answer(check["atoms"], q_type)

    # Step 4b: 慢速路径（LLM 综合判断）
    return llm_judge(
        question=question,
        entities=entities,
        atoms=check["atoms"],          # 可能为空或不完整
        fail_reason=check["fail_reason"],
        llm_client=llm_client,
    )
```

---

## 7. 慢速路径（LLM 判断）接口规范

慢速路径复用现有 LLM 推理逻辑，但需要显式传入失败原因以优化 prompt。

```python
def llm_judge(
    question: str,
    entities: dict,
    atoms: list[dict],
    fail_reason: str,
    llm_client,
) -> dict:
    """
    慢速路径：将已召回的 Atom 作为证据集传给 LLM 判断。
    """
    evidence_text = "\n".join([
        f"[{a['atom_id']}] {a['source_document']}·{a['article_reference']}: {a.get('content_original', '')}"
        for a in atoms
    ]) if atoms else "（未检索到直接匹配的法规原子）"

    prompt = f"""你是一个金融法规合规分析专家。请根据以下法规证据回答问题。

问题：{question}

已识别实体：{entities}

法规证据：
{evidence_text}

注意：系统形式化路径无法直接回答，原因是：{fail_reason}
请综合判断后给出合规结论，并标注依据条文。"""

    response = llm_client.complete(prompt)

    return {
        "answer_count": 1,
        "answers": [{
            "text": response,
            "confidence": "llm-inferred",
            "evidence_atoms": [a["atom_id"] for a in atoms],
        }],
        "confidence": "llm-inferred",
        "routed_by": "slow_path",
        "fail_reason": fail_reason,
    }
```

---

## 8. 文件结构与集成点

```
src/
├── formal_qa.py               # 新建：本文档的核心实现
│   ├── check_answerability()
│   ├── retrieve_atoms()       # 封装 Cypher 模板库（第4节）
│   ├── build_formal_answer()
│   ├── llm_judge()
│   └── answer_question()      # 主路由入口
│
├── entity_normalization.py    # 已有：复用 normalize()
├── conflict_detection.py      # 已有：复用 check()
├── business_taxonomy_app.py   # 已有：在 Streamlit 中接入 answer_question()
└── reasoning_engine.py        # 已有：慢速路径可调用现有逻辑
```

**在 `business_taxonomy_app.py` 中的接入点**：

```python
# 在现有 "模型推理 Demo" tab 中，替换或并排展示形式化结果
from formal_qa import answer_question

result = answer_question(
    question=user_input,
    graph=neo4j_session,
    llm_client=llm_client,
    conflict_detector=conflict_detector,
    entity_extractor=entity_extractor,
    normalizer=normalizer,
)

# 根据 confidence 字段显示不同样式
if result["confidence"] == "formal":
    st.success(f"✅ 形式化答案（确定性）")
elif result["confidence"] == "llm-inferred":
    st.warning(f"⚠️ 模型推断答案（请人工复核）")
```

---

## 9. 问题类型识别器（轻量实现）

不需要专门训练分类器，用关键词规则即可覆盖大多数情况：

```python
# src/formal_qa.py

QUESTION_TYPE_RULES = {
    "B": [  # 主体查询型：优先匹配
        "谁可以", "谁能", "哪些机构", "哪些单位", "什么主体",
        "哪方", "哪些人", "谁有权", "谁负责", "谁来",
    ],
    "C": [  # 条件查询型
        "什么情况", "何时", "在什么条件", "什么时候", "触发条件",
        "适用条件", "是什么意思", "是指", "定义", "如何界定",
    ],
    "A": [  # 义务内容型：兜底
        "必须", "应当", "应该", "需要", "有何要求", "怎么做",
        "如何处理", "不得", "禁止", "可以", "有权",
    ],
}

def classify_question(question: str) -> str:
    for q_type in ["B", "C", "A"]:   # B、C 优先，A 兜底
        if any(kw in question for kw in QUESTION_TYPE_RULES[q_type]):
            return q_type
    return "A"   # 默认兜底
```

---

## 10. 快速路径覆盖率扩展策略

形式化路径的覆盖范围 = `有 MATCHES_SCENE 的 Atom` 数量。  
当前：约 233 条，目标：逐步扩展至 1000+ 条。

**推进优先级**：

| 优先级 | 操作 | 预期收益 |
|---|---|---|
| P0 | 对 `INVOLVES_ACTOR` 边完整的 Atom，即使无 `MATCHES_SCENE` 也允许 Type A 快速路径 | 立即扩大覆盖 |
| P1 | 改进 scene 匹配算法，降低匹配阈值或引入模糊匹配 | 系统性提升 |
| P2 | 对高频业务场景（账户开立、现金管理、反洗钱）手动补充 `MATCHES_SCENE` 边 | 重点业务优先 |
| P3 | 引入 GCR 的 KG-Trie 思路，将所有合法推理路径预编码为索引 | 长期架构升级 |

**P0 的 Cypher 补丁**（无需等待 scene 匹配完善）：

```cypher
// 无 MATCHES_SCENE 但有完整实体边的 Atom，同样允许走快速路径
// 在 check_answerability 中放宽条件 4：不强制要求 MATCHES_SCENE
MATCH (actor:BusinessActor {normalized_name: $who})
      <-[:INVOLVES_ACTOR]-(atom:BusinessAtom)
WHERE atom.rule_type IN $deterministic_types
  AND EXISTS { (atom)-[:INVOLVES_ACTOR]->() }   // 至少有实体边
RETURN atom
```

---

## 11. 置信度标记规范

每条返回结果必须携带 `confidence` 字段，前端据此显示不同样式：

| `confidence` 值 | 含义 | 前端样式 |
|---|---|---|
| `"formal"` | 四条件全部满足，确定性答案 | ✅ 绿色，可直接使用 |
| `"llm-inferred"` | 模型综合判断，有不确定性 | ⚠️ 黄色，建议人工复核 |
| `"unresolved"` | 无法回答，图谱和模型均无充分依据 | ❌ 红色，需人工处理 |

---

## 12. 参考文献

| 论文 | 会议 | 与本系统的关联 |
|---|---|---|
| Graph-constrained Reasoning (GCR) — Luo et al. | ICML 2025 | KG-Trie 约束解码，零幻觉形式化推理的最新 SOTA |
| Reasoning on Graphs (RoG) — Luo et al. | ICLR 2024 | 关系路径规划→检索→推理的标准三阶段范式 |
| Think-on-Graph 2.0 (ToG 2.0) — Sun et al. | ICLR 2025 | 路径充分性作为推理终止条件 |
| Logic-LM — Pan et al. | EMNLP 2023 | LLM 翻译为符号形式化 → 确定性求解器，快慢路径理论基础 |
| Language Models and Logic Programs for Tax Reasoning | arXiv 2025 | 法规 Prolog 形式化 + LLM 的神经符号混合，最接近本系统场景 |

---

*文档版本：v1.0 / 2026-05*  
*对应代码分支：feature/formal-qa*
