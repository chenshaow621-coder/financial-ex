from __future__ import annotations

import re
from typing import Any

import pandas as pd

from conflict_detection import detect_atom_conflicts
from entity_normalization import (
    extract_normalized_object_names,
    extract_normalized_time_names,
    load_reference_terms,
    normalize_actor_filter_terms,
)


DETERMINISTIC_RULE_TYPES = {
    "OBL_MANDATORY",
    "OBL_ONGOING",
    "PRO_FORBIDDEN",
    "PER_AUTH",
    "VAL_THRESHOLD",
    "PRC_FLOW",
}

TYPE_A_FAST_PATH_RULE_TYPES = {
    "OBL_MANDATORY",
    "OBL_ONGOING",
    "PRO_FORBIDDEN",
    "PER_AUTH",
    "PRC_FLOW",
}

TYPE_B_FAST_PATH_RULE_TYPES = {
    "OBL_MANDATORY",
    "OBL_ONGOING",
    "PER_AUTH",
    "PRC_FLOW",
}

TYPE_C_DEFINITION_RULE_TYPES = {"DEF_SCOPE"}

CONDITIONAL_RULE_TYPES = {"EVT_TRIGGER"}
DEFINITION_RULE_TYPES = {"DEF_SCOPE"}
MAX_FORMAL_ATOMS = 12
FORBIDDEN_QUESTION_TERMS = ("不得", "禁止", "不能", "不可", "不可以", "不予")
MATERIAL_QUESTION_TERMS = ("材料", "资料", "证明", "证件", "凭证", "文件", "提交", "提供", "出具")
MATERIAL_ACTION_TERMS = ("出具", "提交", "提供", "报送", "留存")
MATERIAL_OBJECT_TERMS = (
    "材料",
    "资料",
    "证明",
    "证件",
    "凭证",
    "文件",
    "营业执照",
    "批文",
    "申请书",
    "登记证",
    "许可证",
    "身份证",
)
GENERIC_HOW_VALUES = {
    "应出具",
    "还应出具",
    "申请开立",
    "可以申请开立",
    "应当开立",
    "应当按规定开立并启用",
}
COMPLEX_INTENT_TERMS = (
    "是否",
    "能否",
    "可否",
    "如何",
    "怎么",
    "哪些条件",
    "满足哪些",
    "什么材料",
    "哪些材料",
    "签章",
    "背书",
    "支取现金",
)
PERMISSION_QUESTION_TERMS = ("能否", "是否可以", "可以吗", "可否", "可不可以", "可以")
CASH_QUESTION_TERMS = ("现金", "支取现金")
CHANGE_QUESTION_TERMS = ("变更", "更名", "名称")
WEAK_EVIDENCE_TERMS = ("无具体条款", "隐含", "外部引用不明", "未指明")
DEFINITION_QUESTION_TERMS = ("是什么意思", "是指", "定义", "如何界定")
TYPE_B_ACTION_TERMS = (
    "核发",
    "签发",
    "核准",
    "审批",
    "批准",
    "审定",
    "审核",
    "审查",
    "留存",
    "出具",
    "开立",
    "办理",
    "报告",
    "制定",
    "规定",
    "管理",
    "监管",
    "负责",
)
PRECISE_OBJECT_SCOPE_TERMS = (
    "开户登记证",
    "基本存款账户开户登记证",
    "票据凭证的格式和印制管理办法",
    "本票出票人资格",
    "农民工工资专用账户",
    "临时存款账户",
    "空头支票",
    "只收不付",
    "客户身份识别义务",
    "客户身份资料",
    "交易记录",
    "银行汇票丧失",
)

CONDITION_SCOPE_GROUPS = (
    {
        "name": "offsite_account_scope",
        "atom_terms": ("异地", "经营地", "注册地", "不在同一行政区域", "不在同一"),
        "question_terms": ("异地", "经营地", "注册地", "不在同一行政区域", "不在同一"),
    },
    {
        "name": "overdue_payment_scope",
        "atom_terms": ("超过提示付款期限", "逾期", "不获付款", "未获付款", "提示付款期限内"),
        "question_terms": ("超过", "逾期", "不获付款", "未获付款", "期限后", "提示付款期限"),
    },
    {
        "name": "cash_mark_scope",
        "atom_terms": ("填明", "注明", "现金字样"),
        "question_terms": ("填明", "注明", "现金字样"),
    },
    {
        "name": "lost_instrument_scope",
        "atom_terms": ("票据丧失", "丧失", "遗失", "挂失"),
        "question_terms": ("票据丧失", "丧失", "遗失", "挂失"),
    },
    {
        "name": "qualified_condition_scope",
        "atom_terms": ("符合条件", "符合下列条件", "满足条件"),
        "question_terms": ("符合条件", "符合", "满足条件", "哪些条件"),
    },
    {
        "name": "unbanked_holder_scope",
        "atom_terms": ("未在银行开立存款账户", "未在银行开立账户"),
        "question_terms": ("未在银行开立存款账户", "未在银行开立账户"),
    },
)

QUESTION_TYPE_RULES = {
    "B": (
        "谁可以",
        "谁能",
        "哪些机构",
        "哪些单位",
        "什么主体",
        "哪方",
        "哪些人",
        "谁有权",
        "谁负责",
        "谁来",
    ),
    "C": (
        "什么情况",
        "何时",
        "在什么条件",
        "什么时候",
        "触发条件",
        "适用条件",
        "是什么意思",
        "是指",
        "定义",
        "如何界定",
    ),
    "A": (
        "必须",
        "应当",
        "应该",
        "需要",
        "有何要求",
        "怎么做",
        "如何处理",
        "不得",
        "禁止",
        "可以",
        "有权",
    ),
}


TYPE_A_ATOM_QUERY = """
MATCH (actor:BusinessActor)
WHERE actor.name IN $actor_names OR coalesce(actor.normalized_name, actor.name) IN $actor_names
MATCH (atom:BusinessAtom)-[:INVOLVES_ACTOR]->(actor)
WHERE atom.rule_type IN $rule_types
WITH DISTINCT atom
OPTIONAL MATCH (atom)-[:INVOLVES_ACTOR]->(actor:BusinessActor)
WITH atom,
     collect(DISTINCT actor.name) + collect(DISTINCT coalesce(actor.normalized_name, actor.name)) AS actor_values
OPTIONAL MATCH (atom)-[:TARGETS_OBJECT]->(obj:BusinessObject)
WITH atom, actor_values,
     collect(DISTINCT obj.name) + collect(DISTINCT coalesce(obj.normalized_name, obj.name)) AS object_values
OPTIONAL MATCH (atom)-[:HAS_TIME_CONTEXT]->(time_ctx:BusinessTimeContext)
WITH atom, actor_values, object_values,
     collect(DISTINCT time_ctx.name) + collect(DISTINCT coalesce(time_ctx.normalized_name, time_ctx.name)) AS time_values
OPTIONAL MATCH (atom)-[scene_rel:MATCHES_SCENE]->(:BusinessScene)
WITH atom, actor_values, object_values, time_values, count(scene_rel) > 0 AS has_scene_match,
     CASE WHEN size($actor_names) = 0 THEN false ELSE any(v IN actor_values WHERE v IN $actor_names) END AS actor_match,
     CASE WHEN size($object_names) = 0 THEN false ELSE any(v IN object_values WHERE v IN $object_names) END AS object_match,
     CASE WHEN size($time_names) = 0 THEN false ELSE any(v IN time_values WHERE v IN $time_names) END AS time_match
WITH atom, actor_values, object_values, time_values, has_scene_match, actor_match, object_match, time_match,
     (CASE WHEN actor_match THEN 1 ELSE 0 END
      + CASE WHEN object_match THEN 1 ELSE 0 END
      + CASE WHEN time_match THEN 1 ELSE 0 END) AS match_count
WHERE actor_match AND (object_match OR time_match)
RETURN atom.id AS atom_id,
       atom.rule_type AS rule_type,
       atom.source_document AS source_document,
       atom.article_reference AS article_reference,
       atom.who AS who,
       atom.what AS what,
       atom.when AS when,
       atom.how AS how,
       atom.content_original AS content_original,
       atom.is_ambiguous AS is_ambiguous,
       actor_values AS actor_values,
       object_values AS object_values,
       time_values AS time_values,
       has_scene_match AS has_scene_match,
       actor_match AS actor_match,
       object_match AS object_match,
       time_match AS time_match,
       match_count AS match_count
ORDER BY has_scene_match DESC,
         match_count DESC,
         atom.source_document,
         atom.article_reference,
         atom.id
LIMIT $limit
"""

TYPE_B_ACTOR_QUERY = """
MATCH (obj:BusinessObject)
WHERE obj.name IN $object_names OR coalesce(obj.normalized_name, obj.name) IN $object_names
MATCH (atom:BusinessAtom)-[:TARGETS_OBJECT]->(obj)
WHERE atom.rule_type IN $rule_types
MATCH (atom)-[:INVOLVES_ACTOR]->(actor:BusinessActor)
WITH DISTINCT atom, actor,
     collect(DISTINCT obj.name) + collect(DISTINCT coalesce(obj.normalized_name, obj.name)) AS object_values
OPTIONAL MATCH (atom)-[:HAS_TIME_CONTEXT]->(time_ctx:BusinessTimeContext)
WITH atom, actor, object_values,
     collect(DISTINCT time_ctx.name) + collect(DISTINCT coalesce(time_ctx.normalized_name, time_ctx.name)) AS time_values
OPTIONAL MATCH (atom)-[scene_rel:MATCHES_SCENE]->(:BusinessScene)
RETURN atom.id AS atom_id,
       atom.rule_type AS rule_type,
       atom.source_document AS source_document,
       atom.article_reference AS article_reference,
       atom.who AS who,
       atom.what AS what,
       atom.when AS when,
       atom.how AS how,
       atom.content_original AS content_original,
       atom.is_ambiguous AS is_ambiguous,
       coalesce(actor.normalized_name, actor.name) AS answer_actor,
       object_values AS object_values,
       time_values AS time_values,
       count(scene_rel) > 0 AS has_scene_match,
       true AS object_match,
       CASE WHEN size($time_names) = 0 THEN false ELSE any(v IN time_values WHERE v IN $time_names) END AS time_match,
       1 + CASE WHEN size($time_names) > 0 AND any(v IN time_values WHERE v IN $time_names) THEN 1 ELSE 0 END AS match_count
ORDER BY has_scene_match DESC,
         match_count DESC,
         atom.source_document,
         atom.article_reference,
         answer_actor
LIMIT $limit
"""

TYPE_C_DEFINITION_QUERY = """
MATCH (obj:BusinessObject)
WHERE obj.name IN $object_names OR coalesce(obj.normalized_name, obj.name) IN $object_names
MATCH (atom:BusinessAtom)-[:TARGETS_OBJECT]->(obj)
WHERE atom.rule_type IN $rule_types
WITH DISTINCT atom, obj
OPTIONAL MATCH (atom)-[scene_rel:MATCHES_SCENE]->(:BusinessScene)
RETURN atom.id AS atom_id,
       atom.rule_type AS rule_type,
       atom.source_document AS source_document,
       atom.article_reference AS article_reference,
       atom.who AS who,
       atom.what AS what,
       atom.when AS when,
       atom.how AS how,
       atom.content_original AS content_original,
       atom.is_ambiguous AS is_ambiguous,
       coalesce(obj.normalized_name, obj.name) AS answer_object,
       collect(DISTINCT obj.name) + collect(DISTINCT coalesce(obj.normalized_name, obj.name)) AS object_values,
       count(scene_rel) > 0 AS has_scene_match,
       true AS object_match,
       1 AS match_count
ORDER BY has_scene_match DESC,
         atom.source_document,
         atom.article_reference,
         answer_object
LIMIT $limit
"""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = _clean_text(item)
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def _split_terms(value: str | None) -> list[str]:
    text = _clean_text(value)
    if not text:
        return []
    return [
        item.strip()
        for item in re.split(r"[、，,；;|/\t\n]+", text)
        if item.strip()
    ]


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "是的", "true-ish"}


def _fail(
    reason: str,
    message: str,
    atoms: list[dict[str, Any]] | None = None,
    question_type: str | None = None,
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    return {
        "answerable": False,
        "answer_count": 0,
        "answers": [],
        "confidence": "llm-inferred",
        "routed_by": "slow_path",
        "question_type": question_type,
        "detected_entities": detected_entities or {},
        "atoms": atoms or [],
        "fail_reason": f"{reason}: {message}",
    }


def classify_question(question: str) -> str:
    for question_type in ("B", "C", "A"):
        if any(keyword in question for keyword in QUESTION_TYPE_RULES[question_type]):
            return question_type
    return "A"


def is_complex_multi_intent_question(question: str) -> bool:
    text = _clean_text(question)
    hit_count = sum(1 for term in COMPLEX_INTENT_TERMS if term in text)
    has_list_separator = any(separator in text for separator in ("、", "；", ";"))
    return has_list_separator and hit_count >= 2


def _question_has_any(question: str, terms: tuple[str, ...]) -> bool:
    return any(term in question for term in terms)


def _atom_text(atom: dict[str, Any]) -> str:
    return " ".join(
        [
            str(atom.get("who", "")),
            str(atom.get("what", "")),
            str(atom.get("when", "")),
            str(atom.get("how", "")),
            str(atom.get("content_original", "")),
            str(atom.get("article_reference", "")),
        ]
    )


def _missing_condition_scope_groups(question: str, atom: dict[str, Any]) -> list[str]:
    question_text = _clean_text(question)
    atom_text = _atom_text(atom)
    missing = []
    for group in CONDITION_SCOPE_GROUPS:
        if not any(term in atom_text for term in group["atom_terms"]):
            continue
        if any(term in question_text for term in group["question_terms"]):
            continue
        missing.append(group["name"])
    return missing


def filter_atoms_by_condition_scope(question: str, atoms: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted = []
    rejected = []
    for atom in atoms:
        missing_groups = _missing_condition_scope_groups(question, atom)
        if missing_groups:
            rejected.append({**atom, "unmatched_condition_groups": missing_groups})
        else:
            accepted.append(atom)
    return accepted, rejected


def _type_b_question_action_terms(question: str) -> list[str]:
    return [term for term in TYPE_B_ACTION_TERMS if term in _clean_text(question)]


def filter_type_b_atoms_by_question_action(question: str, atoms: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    action_terms = _type_b_question_action_terms(question)
    if not action_terms:
        return atoms, None
    filtered = [atom for atom in atoms if any(term in _atom_text(atom) for term in action_terms)]
    if len(filtered) != len(atoms):
        return filtered, "type_b_action"
    return atoms, None


def filter_definition_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [atom for atom in atoms if atom.get("rule_type") in TYPE_C_DEFINITION_RULE_TYPES]


def filter_atoms_by_precise_object_scope(question: str, query: str | None, atoms: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    search_text = _clean_text(" ".join(item for item in (question, query) if item))
    precise_terms = [term for term in PRECISE_OBJECT_SCOPE_TERMS if term in search_text]
    if not precise_terms:
        return atoms, None
    filtered = [atom for atom in atoms if all(term in _atom_text(atom) for term in precise_terms)]
    if len(filtered) != len(atoms):
        return filtered, "precise_object_scope"
    return atoms, None


def _has_precise_article_reference(atom: dict[str, Any]) -> bool:
    return "条" in str(atom.get("article_reference", ""))


def _is_high_quality_evidence(atom: dict[str, Any]) -> bool:
    text = _atom_text(atom)
    return _has_precise_article_reference(atom) and not _question_has_any(text, WEAK_EVIDENCE_TERMS)


def _is_material_evidence(atom: dict[str, Any]) -> bool:
    if atom.get("rule_type") == "PER_AUTH":
        return False
    text = _atom_text(atom)
    has_action = _question_has_any(text, MATERIAL_ACTION_TERMS)
    has_object = _question_has_any(text, MATERIAL_OBJECT_TERMS)
    return has_action and has_object


def filter_atoms_by_question_intent(question: str, atoms: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    if not atoms:
        return atoms, None

    filter_reason = None
    filtered = [atom for atom in atoms if _is_high_quality_evidence(atom)]
    if len(filtered) != len(atoms):
        atoms = filtered
        filter_reason = "citation_quality"

    if _question_has_any(question, PERMISSION_QUESTION_TERMS):
        filtered = [atom for atom in atoms if atom.get("rule_type") == "PER_AUTH"]
        if len(filtered) != len(atoms):
            atoms = filtered
            filter_reason = "permission_intent"

    if _question_has_any(question, CASH_QUESTION_TERMS):
        filtered = [atom for atom in atoms if "现金" in _atom_text(atom) and "支取" in _atom_text(atom)]
        if len(filtered) != len(atoms):
            return filtered, "cash_intent"

    if _question_has_any(question, CHANGE_QUESTION_TERMS):
        filtered = [atom for atom in atoms if _question_has_any(_atom_text(atom), CHANGE_QUESTION_TERMS)]
        if len(filtered) != len(atoms):
            return filtered, "change_intent"

    if _question_has_any(question, FORBIDDEN_QUESTION_TERMS):
        filtered = [atom for atom in atoms if atom.get("rule_type") == "PRO_FORBIDDEN"]
        if len(filtered) != len(atoms):
            return filtered, "forbidden_intent"

    if _question_has_any(question, MATERIAL_QUESTION_TERMS):
        filtered = [atom for atom in atoms if _is_material_evidence(atom)]
        if len(filtered) != len(atoms):
            return filtered, "material_intent"

    return atoms, filter_reason


def extract_formal_query_terms(
    question: str,
    query: str | None = None,
    who: str | None = None,
    entity_table_path: str | None = None,
) -> dict[str, list[str]]:
    search_text = _clean_text(" ".join(item for item in (question, query) if item))
    actor_raw_terms = _split_terms(who)

    try:
        actor_dictionary_terms = [
            term
            for term in load_reference_terms(entity_table_path, group_key="WHO")
            if term and term in search_text
        ]
    except Exception:
        actor_dictionary_terms = []

    actor_names = normalize_actor_filter_terms(
        _dedupe_keep_order(actor_raw_terms + actor_dictionary_terms),
        entity_table_path=entity_table_path,
    )
    object_names = extract_normalized_object_names(
        search_text,
        raw_terms=[],
        entity_table_path=entity_table_path,
    )
    time_names = extract_normalized_time_names(
        search_text,
        raw_terms=[],
        entity_table_path=entity_table_path,
    )
    return {
        "actors": _dedupe_keep_order(actor_names)[:6],
        "objects": _dedupe_keep_order(object_names)[:8],
        "times": _dedupe_keep_order(time_names)[:6],
    }


def _run_query(graph: Any, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if hasattr(graph, "run"):
        return [dict(record) for record in graph.run(cypher, **params)]
    if hasattr(graph, "session"):
        with graph.session() as session:
            return [dict(record) for record in session.run(cypher, **params)]
    raise TypeError("graph must be a Neo4j driver or session-like object")


def retrieve_type_a_atoms(
    graph: Any,
    actor_names: list[str],
    object_names: list[str],
    time_names: list[str],
    limit: int = MAX_FORMAL_ATOMS + 1,
) -> list[dict[str, Any]]:
    if not actor_names or not (object_names or time_names):
        return []

    rows = _run_query(
        graph,
        TYPE_A_ATOM_QUERY,
        {
            "actor_names": actor_names,
            "object_names": object_names,
            "time_names": time_names,
            "rule_types": sorted(TYPE_A_FAST_PATH_RULE_TYPES),
            "limit": int(limit),
        },
    )
    return [
        {
            **row,
            "atom_id": _clean_text(row.get("atom_id")),
            "rule_type": _clean_text(row.get("rule_type")),
            "source_document": _clean_text(row.get("source_document")),
            "article_reference": _clean_text(row.get("article_reference")),
            "who": _clean_text(row.get("who")),
            "what": _clean_text(row.get("what")),
            "when": _clean_text(row.get("when")),
            "how": _clean_text(row.get("how")),
            "content_original": _clean_text(row.get("content_original")),
            "is_ambiguous": _boolish(row.get("is_ambiguous")),
            "actor_values": _dedupe_keep_order(list(row.get("actor_values") or [])),
            "object_values": _dedupe_keep_order(list(row.get("object_values") or [])),
            "time_values": _dedupe_keep_order(list(row.get("time_values") or [])),
            "match_count": int(row.get("match_count") or 0),
        }
        for row in rows
        if _clean_text(row.get("atom_id"))
    ]


def _standardize_atom_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "atom_id": _clean_text(row.get("atom_id")),
        "rule_type": _clean_text(row.get("rule_type")),
        "source_document": _clean_text(row.get("source_document")),
        "article_reference": _clean_text(row.get("article_reference")),
        "who": _clean_text(row.get("who")),
        "what": _clean_text(row.get("what")),
        "when": _clean_text(row.get("when")),
        "how": _clean_text(row.get("how")),
        "content_original": _clean_text(row.get("content_original")),
        "is_ambiguous": _boolish(row.get("is_ambiguous")),
        "answer_actor": _clean_text(row.get("answer_actor")),
        "answer_object": _clean_text(row.get("answer_object")),
        "actor_values": _dedupe_keep_order(list(row.get("actor_values") or [])),
        "object_values": _dedupe_keep_order(list(row.get("object_values") or [])),
        "time_values": _dedupe_keep_order(list(row.get("time_values") or [])),
        "has_scene_match": bool(row.get("has_scene_match")),
        "actor_match": _boolish(row.get("actor_match")),
        "object_match": _boolish(row.get("object_match")),
        "time_match": _boolish(row.get("time_match")),
        "match_count": int(row.get("match_count") or 0),
    }


def retrieve_type_b_actor_answers(
    graph: Any,
    object_names: list[str],
    time_names: list[str],
    limit: int = MAX_FORMAL_ATOMS + 1,
) -> list[dict[str, Any]]:
    if not object_names:
        return []

    rows = _run_query(
        graph,
        TYPE_B_ACTOR_QUERY,
        {
            "object_names": object_names,
            "time_names": time_names,
            "rule_types": sorted(TYPE_B_FAST_PATH_RULE_TYPES),
            "limit": int(limit),
        },
    )
    return [
        _standardize_atom_row(row)
        for row in rows
        if _clean_text(row.get("atom_id")) and _clean_text(row.get("answer_actor"))
    ]


def retrieve_type_c_definition_answers(
    graph: Any,
    object_names: list[str],
    limit: int = MAX_FORMAL_ATOMS + 1,
) -> list[dict[str, Any]]:
    if not object_names:
        return []

    rows = _run_query(
        graph,
        TYPE_C_DEFINITION_QUERY,
        {
            "object_names": object_names,
            "rule_types": sorted(TYPE_C_DEFINITION_RULE_TYPES),
            "limit": int(limit),
        },
    )
    return [
        _standardize_atom_row(row)
        for row in rows
        if _clean_text(row.get("atom_id")) and _clean_text(row.get("answer_object"))
    ]


def check_answerability(
    atoms: list[dict[str, Any]],
    question_type: str,
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if question_type != "A":
        return _fail(
            "unsupported_question_type",
            "第一版形式化快速路径仅处理 Type A 义务/要求类问题。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    if not atoms:
        return _fail(
            "no_atoms",
            "图谱中没有找到满足保守实体匹配条件的法规原子。",
            question_type=question_type,
            detected_entities=detected_entities,
        )

    if len(atoms) > MAX_FORMAL_ATOMS:
        return _fail(
            "too_many_atoms",
            f"候选原子数超过 {MAX_FORMAL_ATOMS}，说明召回范围仍偏宽。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    rule_types = {_clean_text(atom.get("rule_type")) for atom in atoms}
    if not rule_types.issubset(DETERMINISTIC_RULE_TYPES):
        ambiguous = sorted(rule_types - DETERMINISTIC_RULE_TYPES)
        return _fail(
            "ambiguous_rule_type",
            f"包含非确定性规则类型：{', '.join(ambiguous)}。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    ambiguous_atoms = [atom["atom_id"] for atom in atoms if _boolish(atom.get("is_ambiguous"))]
    if ambiguous_atoms:
        return _fail(
            "ambiguous_atom",
            f"存在已标记歧义的原子：{', '.join(ambiguous_atoms[:5])}。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    missing_required = [
        atom["atom_id"]
        for atom in atoms
        if not atom.get("how") or not atom.get("source_document") or not atom.get("article_reference")
    ]
    if missing_required:
        return _fail(
            "missing_required_fields",
            f"存在缺少 how/source_document/article_reference 的原子：{', '.join(missing_required[:5])}。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    weak_matches = [
        atom["atom_id"]
        for atom in atoms
        if not atom.get("actor_match") or not (atom.get("object_match") or atom.get("time_match"))
    ]
    if weak_matches:
        return _fail(
            "weak_entity_alignment",
            f"存在实体维度未满足 actor + object/time 双重命中的原子：{', '.join(weak_matches[:5])}。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    conflict_result = detect_atom_conflicts(pd.DataFrame(atoms))
    conflict_count = int((conflict_result.get("summary") or {}).get("group_count", 0) or 0)
    if conflict_count:
        return _fail(
            "conflict_detected",
            f"候选证据中发现 {conflict_count} 组疑似冲突。",
            atoms=atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    return {
        "answerable": True,
        "confidence": "formal",
        "question_type": question_type,
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def check_type_b_answerability(
    atoms: list[dict[str, Any]],
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if not atoms:
        return _fail(
            "no_atoms",
            "图谱中没有找到可确定回答主体查询的法规原子。",
            question_type="B",
            detected_entities=detected_entities,
        )

    if len(atoms) > MAX_FORMAL_ATOMS:
        return _fail(
            "too_many_atoms",
            f"主体候选原子数超过 {MAX_FORMAL_ATOMS}，说明召回范围仍偏宽。",
            atoms=atoms,
            question_type="B",
            detected_entities=detected_entities,
        )

    rule_types = {_clean_text(atom.get("rule_type")) for atom in atoms}
    if not rule_types.issubset(TYPE_B_FAST_PATH_RULE_TYPES):
        ambiguous = sorted(rule_types - TYPE_B_FAST_PATH_RULE_TYPES)
        return _fail(
            "ambiguous_rule_type",
            f"主体查询包含不适合形式化回答的规则类型：{', '.join(ambiguous)}。",
            atoms=atoms,
            question_type="B",
            detected_entities=detected_entities,
        )

    missing_required = [
        atom["atom_id"]
        for atom in atoms
        if not atom.get("answer_actor") or not atom.get("source_document") or not atom.get("article_reference")
    ]
    if missing_required:
        return _fail(
            "missing_required_fields",
            f"存在缺少 answer_actor/source_document/article_reference 的原子：{', '.join(missing_required[:5])}。",
            atoms=atoms,
            question_type="B",
            detected_entities=detected_entities,
        )

    ambiguous_atoms = [atom["atom_id"] for atom in atoms if _boolish(atom.get("is_ambiguous"))]
    if ambiguous_atoms:
        return _fail(
            "ambiguous_atom",
            f"存在已标记歧义的主体证据原子：{', '.join(ambiguous_atoms[:5])}。",
            atoms=atoms,
            question_type="B",
            detected_entities=detected_entities,
        )

    conflict_result = detect_atom_conflicts(pd.DataFrame(atoms))
    conflict_count = int((conflict_result.get("summary") or {}).get("group_count", 0) or 0)
    if conflict_count:
        return _fail(
            "conflict_detected",
            f"主体候选证据中发现 {conflict_count} 组疑似冲突。",
            atoms=atoms,
            question_type="B",
            detected_entities=detected_entities,
        )

    return {
        "answerable": True,
        "confidence": "formal",
        "question_type": "B",
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def check_type_c_definition_answerability(
    atoms: list[dict[str, Any]],
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if not atoms:
        return _fail(
            "no_atoms",
            "图谱中没有找到可确定回答定义查询的法规原子。",
            question_type="C",
            detected_entities=detected_entities,
        )

    if len(atoms) > MAX_FORMAL_ATOMS:
        return _fail(
            "too_many_atoms",
            f"定义候选原子数超过 {MAX_FORMAL_ATOMS}，说明召回范围仍偏宽。",
            atoms=atoms,
            question_type="C",
            detected_entities=detected_entities,
        )

    rule_types = {_clean_text(atom.get("rule_type")) for atom in atoms}
    if not rule_types.issubset(TYPE_C_DEFINITION_RULE_TYPES):
        ambiguous = sorted(rule_types - TYPE_C_DEFINITION_RULE_TYPES)
        return _fail(
            "ambiguous_rule_type",
            f"定义查询包含不适合形式化回答的规则类型：{', '.join(ambiguous)}。",
            atoms=atoms,
            question_type="C",
            detected_entities=detected_entities,
        )

    missing_required = [
        atom["atom_id"]
        for atom in atoms
        if not (atom.get("how") or atom.get("content_original")) or not atom.get("source_document") or not atom.get("article_reference")
    ]
    if missing_required:
        return _fail(
            "missing_required_fields",
            f"存在缺少 definition/source_document/article_reference 的原子：{', '.join(missing_required[:5])}。",
            atoms=atoms,
            question_type="C",
            detected_entities=detected_entities,
        )

    ambiguous_atoms = [atom["atom_id"] for atom in atoms if _boolish(atom.get("is_ambiguous"))]
    if ambiguous_atoms:
        return _fail(
            "ambiguous_atom",
            f"存在已标记歧义的定义证据原子：{', '.join(ambiguous_atoms[:5])}。",
            atoms=atoms,
            question_type="C",
            detected_entities=detected_entities,
        )

    conflict_result = detect_atom_conflicts(pd.DataFrame(atoms))
    conflict_count = int((conflict_result.get("summary") or {}).get("group_count", 0) or 0)
    if conflict_count:
        return _fail(
            "conflict_detected",
            f"定义候选证据中发现 {conflict_count} 组疑似冲突。",
            atoms=atoms,
            question_type="C",
            detected_entities=detected_entities,
        )

    return {
        "answerable": True,
        "confidence": "formal",
        "question_type": "C",
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def _source_label(atom: dict[str, Any]) -> str:
    source_document = _clean_text(atom.get("source_document"))
    article_reference = _clean_text(atom.get("article_reference"))
    if source_document and article_reference:
        return f"{source_document}·{article_reference}"
    return source_document or article_reference or "未标注来源"


def _strip_sentence_end(text: str) -> str:
    return _clean_text(text).rstrip("。；;，, ")


def _answer_detail(atom: dict[str, Any]) -> str:
    how = _strip_sentence_end(atom.get("how", ""))
    original = _strip_sentence_end(atom.get("content_original", ""))
    if original and (len(how) <= 8 or how in GENERIC_HOW_VALUES):
        return original
    return how or original


def _answer_subject(atom: dict[str, Any]) -> str:
    return _clean_text(atom.get("who")) or "相关主体"


def _answer_when(atom: dict[str, Any]) -> str:
    when_text = _clean_text(atom.get("when"))
    if not when_text:
        time_values = [item for item in atom.get("time_values") or [] if item]
        when_text = time_values[0] if time_values else ""
    return f"在{when_text}" if when_text else ""


def build_formal_answer(
    atoms: list[dict[str, Any]],
    question_type: str,
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    answers = []
    for atom in atoms:
        source = _source_label(atom)
        detail = _answer_detail(atom)
        subject = _answer_subject(atom)
        when_phrase = _answer_when(atom)
        text = f"根据{source}，{subject}{when_phrase}：{detail}。"
        answers.append(
            {
                "text": text,
                "atom_id": atom["atom_id"],
                "source": source,
                "rule_type": atom["rule_type"],
                "confidence": "formal",
                "original": atom.get("content_original", ""),
                "has_scene_match": bool(atom.get("has_scene_match")),
                "match_count": int(atom.get("match_count") or 0),
            }
        )

    return {
        "answerable": True,
        "answer_count": len(answers),
        "answers": answers,
        "confidence": "formal",
        "routed_by": "fast_path",
        "question_type": question_type,
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def build_type_b_answer(
    atoms: list[dict[str, Any]],
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    answers = []
    seen_actors: set[str] = set()
    for atom in atoms:
        actor = _clean_text(atom.get("answer_actor"))
        if not actor or actor in seen_actors:
            continue
        seen_actors.add(actor)
        source = _source_label(atom)
        detail = _answer_detail(atom)
        text = f"根据{source}，{actor}可以作为该问题的确定主体"
        if detail:
            text = f"{text}；相关规则为：{detail}"
        text = f"{text}。"
        answers.append(
            {
                "text": text,
                "atom_id": atom["atom_id"],
                "source": source,
                "rule_type": atom["rule_type"],
                "confidence": "formal",
                "answer_actor": actor,
                "original": atom.get("content_original", ""),
                "has_scene_match": bool(atom.get("has_scene_match")),
                "match_count": int(atom.get("match_count") or 0),
            }
        )

    return {
        "answerable": bool(answers),
        "answer_count": len(answers),
        "answers": answers,
        "confidence": "formal",
        "routed_by": "fast_path",
        "question_type": "B",
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def build_type_c_definition_answer(
    atoms: list[dict[str, Any]],
    detected_entities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    answers = []
    for atom in atoms:
        source = _source_label(atom)
        obj = _clean_text(atom.get("what")) or _clean_text(atom.get("answer_object")) or "该术语"
        detail = _answer_detail(atom)
        text = f"根据{source}，{obj}的定义为：{detail}。"
        answers.append(
            {
                "text": text,
                "atom_id": atom["atom_id"],
                "source": source,
                "rule_type": atom["rule_type"],
                "confidence": "formal",
                "answer_object": obj,
                "original": atom.get("content_original", ""),
                "has_scene_match": bool(atom.get("has_scene_match")),
                "match_count": int(atom.get("match_count") or 0),
            }
        )

    return {
        "answerable": True,
        "answer_count": len(answers),
        "answers": answers,
        "confidence": "formal",
        "routed_by": "fast_path",
        "question_type": "C",
        "detected_entities": detected_entities or {},
        "atoms": atoms,
        "fail_reason": None,
    }


def answer_question_formally(
    question: str,
    graph: Any,
    query: str | None = None,
    who: str | None = None,
    entity_table_path: str | None = None,
) -> dict[str, Any]:
    question_type = classify_question(question)
    detected_entities = extract_formal_query_terms(
        question,
        query=query,
        who=who,
        entity_table_path=entity_table_path,
    )

    if is_complex_multi_intent_question(question):
        return _fail(
            "complex_multi_intent",
            "问题包含多个子意图，第一版形式化快速路径不直接输出确定性答案。",
            question_type=question_type,
            detected_entities=detected_entities,
        )

    if question_type == "B":
        if not detected_entities["objects"]:
            return _fail(
                "insufficient_entities",
                "主体查询未形成 object 约束，拒绝输出形式化答案。",
                question_type=question_type,
                detected_entities=detected_entities,
            )
        atoms = retrieve_type_b_actor_answers(
            graph,
            object_names=detected_entities["objects"],
            time_names=detected_entities["times"],
        )
        original_atoms = atoms
        atoms, precise_scope_filter = filter_atoms_by_precise_object_scope(question, query, atoms)
        if original_atoms and not atoms:
            return _fail(
                "intent_mismatch",
                f"主体候选原子存在，但没有满足精确对象的证据过滤条件：{precise_scope_filter}。",
                atoms=original_atoms,
                question_type=question_type,
                detected_entities=detected_entities,
            )
        original_atoms = atoms
        atoms, action_filter = filter_type_b_atoms_by_question_action(question, atoms)
        if original_atoms and not atoms:
            return _fail(
                "intent_mismatch",
                f"主体候选原子存在，但没有满足问题动作的证据过滤条件：{action_filter}。",
                atoms=original_atoms,
                question_type=question_type,
                detected_entities=detected_entities,
            )
        atoms, unmatched_scope_atoms = filter_atoms_by_condition_scope(question, atoms)
        if unmatched_scope_atoms:
            return _fail(
                "unmatched_condition_scope",
                "主体查询候选证据包含问题未显式覆盖的适用条件，拒绝输出形式化答案。",
                atoms=unmatched_scope_atoms,
                question_type=question_type,
                detected_entities=detected_entities,
            )
        check = check_type_b_answerability(
            atoms,
            detected_entities=detected_entities,
        )
        if not check["answerable"]:
            return check
        return build_type_b_answer(
            atoms,
            detected_entities=detected_entities,
        )

    if question_type == "C":
        if not _question_has_any(question, DEFINITION_QUESTION_TERMS):
            return _fail(
                "unsupported_question_type",
                "Type C 当前仅对定义类问题启用保守形式化快路径，条件/触发类问题仍走慢速路径。",
                question_type=question_type,
                detected_entities=detected_entities,
            )
        if not detected_entities["objects"]:
            return _fail(
                "insufficient_entities",
                "定义查询未形成 object 约束，拒绝输出形式化答案。",
                question_type=question_type,
                detected_entities=detected_entities,
            )
        atoms = retrieve_type_c_definition_answers(
            graph,
            object_names=detected_entities["objects"],
        )
        original_atoms = atoms
        atoms, precise_scope_filter = filter_atoms_by_precise_object_scope(question, query, atoms)
        if original_atoms and not atoms:
            return _fail(
                "intent_mismatch",
                f"定义候选原子存在，但没有满足精确对象的证据过滤条件：{precise_scope_filter}。",
                atoms=original_atoms,
                question_type=question_type,
                detected_entities=detected_entities,
            )
        original_atoms = atoms
        atoms = filter_definition_atoms(atoms)
        if original_atoms and not atoms:
            return _fail(
                "intent_mismatch",
                "定义候选原子存在，但没有满足定义规则类型的证据过滤条件。",
                atoms=original_atoms,
                question_type=question_type,
                detected_entities=detected_entities,
            )
        check = check_type_c_definition_answerability(
            atoms,
            detected_entities=detected_entities,
        )
        if not check["answerable"]:
            return check
        return build_type_c_definition_answer(
            atoms,
            detected_entities=detected_entities,
        )

    if question_type != "A":
        return _fail(
            "unsupported_question_type",
            "当前形式化快速路径仅处理 Type A、窄口径 Type B、定义型 Type C 问题。",
            question_type=question_type,
            detected_entities=detected_entities,
        )

    if not detected_entities["actors"] or not (detected_entities["objects"] or detected_entities["times"]):
        return _fail(
            "insufficient_entities",
            "未形成 actor + object/time 的双重实体约束，拒绝输出形式化答案。",
            question_type=question_type,
            detected_entities=detected_entities,
        )

    atoms = retrieve_type_a_atoms(
        graph,
        actor_names=detected_entities["actors"],
        object_names=detected_entities["objects"],
        time_names=detected_entities["times"],
        )
    original_atoms = atoms
    atoms, precise_scope_filter = filter_atoms_by_precise_object_scope(question, query, atoms)
    if original_atoms and not atoms:
        return _fail(
            "intent_mismatch",
            f"候选原子存在，但没有满足精确对象的证据过滤条件：{precise_scope_filter}。",
            atoms=original_atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    original_atoms = atoms
    atoms, intent_filter = filter_atoms_by_question_intent(question, atoms)
    if original_atoms and not atoms:
        return _fail(
            "intent_mismatch",
            f"候选原子存在，但没有满足问题意图的证据过滤条件：{intent_filter}。",
            atoms=original_atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )

    scoped_atoms, unmatched_scope_atoms = filter_atoms_by_condition_scope(question, atoms)
    if unmatched_scope_atoms:
        return _fail(
            "unmatched_condition_scope",
            "候选证据包含问题未显式覆盖的适用条件，拒绝输出形式化答案。",
            atoms=unmatched_scope_atoms,
            question_type=question_type,
            detected_entities=detected_entities,
        )
    atoms = scoped_atoms

    check = check_answerability(
        atoms,
        question_type=question_type,
        detected_entities=detected_entities,
    )
    if not check["answerable"]:
        return check
    return build_formal_answer(
        atoms,
        question_type=question_type,
        detected_entities=detected_entities,
    )
