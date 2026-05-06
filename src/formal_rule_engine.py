from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from formal_final_judgement_catalog import get_final_judgement_rule_specs
from formal_scene_catalog import SCENE_PROFILE_CATALOG


FINAL_JUDGEMENT_MODES = ("symbolic", "llm")
RECALL_JUDGEMENT_MODES = ("symbolic", "llm")
ATOM_ANALYSIS_MODES = ("symbolic", "llm")


@dataclass(frozen=True)
class FactCondition:
    fact: str
    operator: str
    expected: Any = None


@dataclass(frozen=True)
class FormalDecisionRule:
    rule_id: str
    description: str
    conclusion: str
    status: str
    confidence: float
    summary: str
    all_conditions: tuple[FactCondition, ...] = ()
    any_conditions: tuple[FactCondition, ...] = ()
    follow_up_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConditionGroup:
    operator: str
    items: tuple[Any, ...]


@dataclass(frozen=True)
class RecallDecisionRule:
    rule_id: str
    description: str
    when: Any
    decision: str
    can_make_final: bool
    confidence: float
    summary: str


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _normalize_output_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    normalized = [str(item).strip() for item in items if str(item).strip()]
    return _dedupe_keep_order(normalized)[:limit]


def _count_keyword_hits(items: list[str], keywords: tuple[str, ...]) -> int:
    return sum(1 for item in items if any(keyword in item for keyword in keywords))


def _build_facts(report: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    gap_cards = {
        str(card.get("card_key", "")).strip(): card
        for card in summary.get("gap_summary_cards") or []
    }
    gap_diagnosis = summary.get("gap_diagnosis") or []
    gap_type_counter = Counter(
        str(item.get("gap_type", "")).strip()
        for item in gap_diagnosis
        if str(item.get("gap_type", "")).strip()
    )
    missing_items = _normalize_output_list(summary.get("missing_items"), limit=12)
    final_evidence = report.get("final_evidence") or []
    scene_profile = _detect_scene_profile(
        str(report.get("question", "")).strip(),
        report.get("business_match") or {},
    )

    facts = {
        "final_decision": str(report.get("final_decision", "")).strip(),
        "stop_reason": str(report.get("stop_reason", "")).strip(),
        "can_make_final": bool(report.get("can_make_final_compliance_judgement")),
        "scene_profile_id": str((scene_profile or {}).get("profile_id", "")).strip(),
        "fatal_gap_count": int(gap_cards.get("fatal_gaps", {}).get("count", 0) or 0),
        "reviewable_gap_count": int(gap_cards.get("reviewable_gaps", {}).get("count", 0) or 0),
        "risk_notice_gap_count": int(gap_cards.get("risk_notice_gaps", {}).get("count", 0) or 0),
        "key_basis_count": len(_normalize_output_list(summary.get("key_basis"), limit=12)),
        "required_material_count": len(_normalize_output_list(summary.get("required_materials"), limit=12)),
        "required_action_count": len(_normalize_output_list(summary.get("required_actions"), limit=12)),
        "prohibition_count": len(_normalize_output_list(summary.get("prohibitions"), limit=12)),
        "exception_count": len(_normalize_output_list(summary.get("exceptions"), limit=12)),
        "time_limit_count": len(_normalize_output_list(summary.get("time_limits"), limit=12)),
        "missing_item_count": len(missing_items),
        "ambiguity_count": sum(1 for item in final_evidence if bool(item.get("is_ambiguous"))),
        "material_gap_count": gap_type_counter.get("材料缺口", 0),
        "process_gap_count": gap_type_counter.get("流程动作缺口", 0),
        "threshold_gap_count": gap_type_counter.get("时限阈值缺口", 0),
        "exception_gap_count": gap_type_counter.get("例外/禁止缺口", 0),
        "definition_gap_count": gap_type_counter.get("定义范围缺口", 0),
        "scope_gap_count": gap_type_counter.get("主体范围缺口", 0),
        "judgement_gap_count": gap_type_counter.get("判断条件缺口", 0),
        "norm_basis_gap_count": gap_type_counter.get("规范依据缺口", 0),
        "fact_check_gap_count": gap_type_counter.get("事实核验缺口", 0),
        "missing_material_item_count": _count_keyword_hits(
            missing_items,
            ("材料", "证明", "证件", "凭证", "印鉴", "签章", "原件", "复印件"),
        ),
    }

    facts["material_signal_count"] = facts["material_gap_count"] + facts["missing_material_item_count"]
    facts["conditional_signal_count"] = (
        facts["required_action_count"] + facts["exception_count"] + facts["time_limit_count"]
    )
    facts["has_conflicting_rules"] = facts["prohibition_count"] > 0 and facts["exception_count"] > 0
    return facts


def _condition_holds(facts: dict[str, Any], condition: FactCondition) -> bool:
    actual = facts.get(condition.fact)
    expected = condition.expected

    if condition.operator == "eq":
        return actual == expected
    if condition.operator == "ne":
        return actual != expected
    if condition.operator == "gt":
        return actual > expected
    if condition.operator == "gte":
        return actual >= expected
    if condition.operator == "lt":
        return actual < expected
    if condition.operator == "lte":
        return actual <= expected
    if condition.operator == "in":
        return actual in expected
    if condition.operator == "not_in":
        return actual not in expected
    if condition.operator == "truthy":
        return bool(actual)
    if condition.operator == "falsy":
        return not bool(actual)
    raise ValueError(f"Unsupported operator: {condition.operator}")


def _render_condition(condition: FactCondition) -> str:
    op_map = {
        "eq": "==",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "in": "in",
        "not_in": "not in",
        "truthy": "is truthy",
        "falsy": "is falsy",
    }
    operator = op_map.get(condition.operator, condition.operator)
    if condition.operator in {"truthy", "falsy"}:
        return f"{condition.fact} {operator}"
    return f"{condition.fact} {operator} {condition.expected!r}"


def _compile_fact_condition(spec: Any) -> FactCondition:
    if isinstance(spec, FactCondition):
        return spec
    if not isinstance(spec, dict):
        raise ValueError(f"Unsupported final judgement condition spec: {spec!r}")

    fact = str(spec.get("fact", "")).strip()
    if not fact:
        raise ValueError(f"Missing fact in final judgement condition spec: {spec!r}")

    for operator in ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"):
        if operator in spec:
            return FactCondition(fact, operator, spec[operator])
    if "truthy" in spec:
        return FactCondition(fact, "truthy")
    if "falsy" in spec:
        return FactCondition(fact, "falsy")
    return FactCondition(fact, "truthy")


def _compile_fact_conditions(specs: Any) -> tuple[FactCondition, ...]:
    if not specs:
        return ()
    if isinstance(specs, tuple):
        items = specs
    elif isinstance(specs, list):
        items = tuple(specs)
    else:
        items = (specs,)
    return tuple(_compile_fact_condition(item) for item in items)


def _compile_formal_decision_rule(spec: dict[str, Any]) -> FormalDecisionRule:
    return FormalDecisionRule(
        rule_id=str(spec.get("rule_id", "")).strip(),
        description=str(spec.get("description", "")).strip(),
        conclusion=str(spec.get("conclusion", "")).strip(),
        status=str(spec.get("status", "")).strip(),
        confidence=float(spec.get("confidence", 0.0) or 0.0),
        summary=str(spec.get("summary", "")).strip(),
        all_conditions=_compile_fact_conditions(spec.get("all_conditions")),
        any_conditions=_compile_fact_conditions(spec.get("any_conditions")),
        follow_up_actions=tuple(
            str(item).strip()
            for item in spec.get("follow_up_actions", ())
            if str(item).strip()
        ),
    )


def _build_final_decision_rules(scene_profile_id: str) -> tuple[FormalDecisionRule, ...]:
    return tuple(
        _compile_formal_decision_rule(spec)
        for spec in get_final_judgement_rule_specs(scene_profile_id)
    )


def _match_rule(rule: FormalDecisionRule, facts: dict[str, Any]) -> bool:
    if rule.all_conditions and not all(_condition_holds(facts, item) for item in rule.all_conditions):
        return False
    if rule.any_conditions and not any(_condition_holds(facts, item) for item in rule.any_conditions):
        return False
    return True


def _select_rule(
    facts: dict[str, Any],
    rules: tuple[FormalDecisionRule, ...],
) -> FormalDecisionRule:
    for rule in rules:
        if _match_rule(rule, facts):
            return rule
    return rules[-1]


def _build_follow_up_actions(
    summary: dict[str, Any],
    rule: FormalDecisionRule,
) -> list[str]:
    actions = list(rule.follow_up_actions)
    actions.extend(_normalize_output_list(summary.get("recommended_directions"), limit=4))
    next_step = str(summary.get("next_step", "")).strip()
    if next_step:
        actions.append(next_step)
    return _dedupe_keep_order([item for item in actions if item])[:5]


def _build_risk_points(
    report: dict[str, Any],
    summary: dict[str, Any],
    rule: FormalDecisionRule,
) -> list[str]:
    risk_points = _normalize_output_list(summary.get("risk_points"), limit=6)
    upstream_error = str(report.get("error", "")).strip()
    if upstream_error and rule.rule_id == "upstream_llm_error":
        risk_points.insert(0, f"上游召回判断异常：{upstream_error}")
    return _dedupe_keep_order([item for item in risk_points if item])[:6]


def build_symbolic_final_conclusion(
    report: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    facts = _build_facts(report, summary)
    rules = _build_final_decision_rules(str(facts.get("scene_profile_id", "")).strip())
    rule = _select_rule(facts, rules)
    trace = [f"scene_profile={str(facts.get('scene_profile_id', '')).strip() or 'generic'}"]
    trace.extend(_render_condition(item) for item in rule.all_conditions)
    if rule.any_conditions:
        trace.append(
            " OR ".join(_render_condition(item) for item in rule.any_conditions)
        )

    return {
        "status": rule.status,
        "generation_mode": "symbolic",
        "ready_for_final_judgement": bool(facts["can_make_final"]),
        "conclusion": rule.conclusion,
        "conclusion_summary": rule.summary,
        "confidence": rule.confidence,
        "legal_basis": _normalize_output_list(summary.get("key_basis"), limit=6),
        "required_materials": _normalize_output_list(summary.get("required_materials"), limit=6),
        "required_actions": _normalize_output_list(summary.get("required_actions"), limit=6),
        "exceptions_and_limits": _dedupe_keep_order(
            _normalize_output_list(summary.get("prohibitions"), limit=4)
            + _normalize_output_list(summary.get("exceptions"), limit=4)
            + _normalize_output_list(summary.get("time_limits"), limit=4)
        )[:8],
        "missing_items": _normalize_output_list(summary.get("missing_items"), limit=6),
        "risk_points": _build_risk_points(report, summary, rule),
        "follow_up_actions": _build_follow_up_actions(summary, rule),
        "matched_rules": [
            item for item in [str(facts.get("scene_profile_id", "")).strip(), rule.rule_id] if item
        ],
        "decision_trace": trace,
        "error": str(report.get("error", "")).strip() if rule.rule_id == "upstream_llm_error" else "",
    }


MATERIAL_TERMS = ("材料", "证明", "证件", "凭证", "身份证", "印鉴", "解讫通知", "签章", "原件", "复印件", "背书")
PROCESS_TERMS = ("办理", "审核", "核查", "核对", "签章", "背书", "提示付款", "提交", "留存", "执行", "动作")
THRESHOLD_TERMS = ("金额", "现金", "时限", "期限", "日内", "月内", "年内", "超过", "不超过", "阈值", "字样")
DEFINITION_TERMS = ("定义", "是指", "包括", "范围", "适用对象", "所称")
PROHIBITION_TERMS = ("不得", "不予", "禁止", "不能", "不可")
EXCEPTION_TERMS = ("除外", "例外", "但是", "但", "特殊情形", "可以")


def _build_text_blob(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(item.get("who", "")),
            str(item.get("what", "")),
            str(item.get("how", "")),
            str(item.get("where", "")),
            str(item.get("content_original", "")),
            str(item.get("source_document", "")),
        ]
    )


def _text_has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _count_text_hits(evidence: list[dict[str, Any]], keywords: tuple[str, ...]) -> int:
    return sum(1 for item in evidence if _text_has_any(_build_text_blob(item), keywords))


def _count_rule_hits(evidence: list[dict[str, Any]], rule_types: tuple[str, ...]) -> int:
    return sum(1 for item in evidence if str(item.get("rule_type", "")).strip() in rule_types)


def _detect_scene_profile(
    question: str,
    business_match: dict[str, Any],
) -> dict[str, Any] | None:
    question_text = str(question or "").strip()
    matched_scenes = [str(item).strip() for item in business_match.get("matched_scene_names", []) if str(item).strip()]
    for profile in SCENE_PROFILE_CATALOG:
        scene_hit = any(
            profile_scene in matched_scene or matched_scene in profile_scene
            for matched_scene in matched_scenes
            for profile_scene in profile.get("scene_names", ())
        )
        keyword_hit = any(keyword in question_text for keyword in profile.get("question_keywords", ()))
        if scene_hit and keyword_hit:
            return profile
    return None


def _evaluate_scene_requirement(
    requirement: dict[str, Any],
    question: str,
    who_terms: list[str],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    question_text = str(question or "").strip()
    question_keywords = tuple(requirement.get("question_keywords", ()))
    evidence_keywords = tuple(requirement.get("evidence_keywords", ()))
    evidence_rule_types = tuple(requirement.get("evidence_rule_types", ()))

    needed = bool(requirement.get("always_required", False))
    if requirement.get("activate_if_who_terms") and who_terms:
        needed = True
    if question_keywords and any(keyword in question_text for keyword in question_keywords):
        needed = True

    hit_count = 0
    if requirement.get("mode") == "actor_scope":
        for item in evidence:
            actor_blob = "\n".join([str(item.get("who", "")), str(item.get("what", "")), str(item.get("how", ""))])
            if any(term in actor_blob for term in who_terms):
                hit_count += 1
    else:
        for item in evidence:
            blob = _build_text_blob(item)
            keyword_hit = bool(evidence_keywords) and any(keyword in blob for keyword in evidence_keywords)
            rule_hit = bool(evidence_rule_types) and str(item.get("rule_type", "")).strip() in evidence_rule_types
            if keyword_hit or rule_hit:
                hit_count += 1

    covered = hit_count >= int(requirement.get("min_evidence_hits", 1) or 1)
    return {
        "fact_id": str(requirement.get("fact_id", "")).strip(),
        "label": str(requirement.get("label", "")).strip(),
        "needed": needed,
        "covered": covered,
        "hit_count": hit_count,
        "reason": str(requirement.get("reason", "")).strip(),
        "directions": tuple(requirement.get("directions", ())),
        "atom_label": str(requirement.get("atom_label", "")).strip() or str(requirement.get("label", "")).strip(),
    }


def _evaluate_scene_condition_spec(facts: dict[str, Any], spec: Any) -> bool:
    if spec is None:
        return True
    if isinstance(spec, bool):
        return spec
    if isinstance(spec, FactCondition):
        return _condition_holds(facts, spec)
    if isinstance(spec, ConditionGroup):
        return _evaluate_condition_group(facts, spec)
    if not isinstance(spec, dict):
        raise TypeError(f"Unsupported scene condition spec: {type(spec)!r}")

    if "all" in spec:
        return all(_evaluate_scene_condition_spec(facts, item) for item in spec["all"])
    if "any" in spec:
        return any(_evaluate_scene_condition_spec(facts, item) for item in spec["any"])
    if "not" in spec:
        return not _evaluate_scene_condition_spec(facts, spec["not"])

    fact = str(spec.get("fact", "")).strip()
    if not fact:
        raise ValueError(f"Invalid scene condition spec without fact: {spec!r}")

    for operator in ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"):
        if operator in spec:
            return _condition_holds(facts, FactCondition(fact, operator, spec[operator]))
    if spec.get("truthy") is True:
        return _condition_holds(facts, FactCondition(fact, "truthy"))
    if spec.get("falsy") is True:
        return _condition_holds(facts, FactCondition(fact, "falsy"))
    return _condition_holds(facts, FactCondition(fact, "truthy"))


def _render_scene_condition_spec(spec: Any) -> str:
    if spec is None:
        return "TRUE"
    if isinstance(spec, FactCondition):
        return _render_condition(spec)
    if isinstance(spec, ConditionGroup):
        return _render_condition_group(spec)
    if isinstance(spec, bool):
        return str(spec)
    if not isinstance(spec, dict):
        return str(spec)

    if "all" in spec:
        return "(" + " AND ".join(_render_scene_condition_spec(item) for item in spec["all"]) + ")"
    if "any" in spec:
        return "(" + " OR ".join(_render_scene_condition_spec(item) for item in spec["any"]) + ")"
    if "not" in spec:
        return f"NOT ({_render_scene_condition_spec(spec['not'])})"

    fact = str(spec.get("fact", "")).strip()
    for operator in ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"):
        if operator in spec:
            return _render_condition(FactCondition(fact, operator, spec[operator]))
    if spec.get("truthy") is True:
        return _render_condition(FactCondition(fact, "truthy"))
    if spec.get("falsy") is True:
        return _render_condition(FactCondition(fact, "falsy"))
    return fact or "TRUE"


def _derive_scene_fact_value(
    derive_spec: dict[str, Any],
    question: str,
    business_match: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> Any:
    source = str(derive_spec.get("source", "")).strip()
    mode = str(derive_spec.get("mode", "")).strip()
    keywords = tuple(derive_spec.get("keywords", ()))
    rule_types = tuple(derive_spec.get("rule_types", ()))
    who_terms = [str(item).strip() for item in business_match.get("who_terms", []) if str(item).strip()]
    question_text = str(question or "").strip()

    if source == "who_terms":
        if mode == "non_empty":
            return bool(who_terms)
        raise ValueError(f"Unsupported who_terms derive mode: {mode}")

    if source == "question":
        if mode == "keyword_any":
            return any(keyword in question_text for keyword in keywords)
        if mode == "keyword_all":
            return all(keyword in question_text for keyword in keywords)
        raise ValueError(f"Unsupported question derive mode: {mode}")

    if source == "evidence":
        if mode == "actor_scope":
            for item in evidence:
                actor_blob = "\n".join([str(item.get("who", "")), str(item.get("what", "")), str(item.get("how", ""))])
                if any(term in actor_blob for term in who_terms):
                    return True
            return False
        if mode == "keyword_any":
            return any(_text_has_any(_build_text_blob(item), keywords) for item in evidence)
        if mode == "rule_type_any":
            return any(str(item.get("rule_type", "")).strip() in rule_types for item in evidence)
        if mode == "keyword_or_rule":
            return any(
                _text_has_any(_build_text_blob(item), keywords)
                or str(item.get("rule_type", "")).strip() in rule_types
                for item in evidence
            )
        raise ValueError(f"Unsupported evidence derive mode: {mode}")

    raise ValueError(f"Unsupported derive source: {source}")


def _evaluate_scene_rule_specs(
    profile: dict[str, Any],
    question: str,
    business_match: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    derived_facts: dict[str, Any] = {}
    for derive_spec in profile.get("derived_facts", ()):
        fact_id = str(derive_spec.get("fact_id", "")).strip()
        if not fact_id:
            continue
        derived_facts[fact_id] = _derive_scene_fact_value(derive_spec, question, business_match, evidence)

    states: list[dict[str, Any]] = []
    missing_dimensions: list[dict[str, str]] = []
    recommended_directions: list[dict[str, str]] = []
    ordered_rules = sorted(
        profile.get("rule_specs", ()),
        key=lambda item: (-int(item.get("priority", 0) or 0), str(item.get("rule_id", ""))),
    )

    for rule_spec in ordered_rules:
        rule_id = str(rule_spec.get("rule_id", "")).strip()
        priority = int(rule_spec.get("priority", 0) or 0)
        applies = (
            _evaluate_scene_condition_spec(derived_facts, rule_spec.get("applies_if"))
            if "applies_if" in rule_spec
            else True
        )
        requirement_met = (
            _evaluate_scene_condition_spec(derived_facts, rule_spec.get("requires"))
            if "requires" in rule_spec
            else True
        )
        exception_hit = (
            _evaluate_scene_condition_spec(derived_facts, rule_spec.get("exception_if"))
            if "exception_if" in rule_spec
            else False
        )
        forbid_hit = (
            _evaluate_scene_condition_spec(derived_facts, rule_spec.get("forbids"))
            if "forbids" in rule_spec
            else False
        )
        failed = applies and (((not requirement_met) and not exception_hit) or (forbid_hit and not exception_hit))
        on_fail = rule_spec.get("on_fail") or {}
        atom_label = str(on_fail.get("atom_label", "")).strip() or str(rule_spec.get("label", "")).strip()
        state = {
            "rule_id": rule_id,
            "priority": priority,
            "needed": applies,
            "covered": applies and not failed,
            "requirement_met": requirement_met,
            "exception_hit": exception_hit,
            "forbid_hit": forbid_hit,
            "failed": failed,
            "atom_label": atom_label,
            "decision_trace": [
                f"APPLIES_IF {_render_scene_condition_spec(rule_spec.get('applies_if'))}",
                f"REQUIRES {_render_scene_condition_spec(rule_spec.get('requires'))}",
                f"EXCEPTION_IF {_render_scene_condition_spec(rule_spec.get('exception_if'))}",
                f"FORBIDS {_render_scene_condition_spec(rule_spec.get('forbids'))}",
            ],
        }
        states.append(state)

        if not failed:
            continue

        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            str(on_fail.get("dimension", "")).strip() or atom_label or rule_id,
            str(on_fail.get("reason", "")).strip() or f"{rule_id} 未满足",
            tuple(on_fail.get("recall_directions", ())),
            meta={
                "rule_id": rule_id,
                "priority": priority,
                "gap_type": str(on_fail.get("gap_type", "")).strip(),
                "impact_scope": str(on_fail.get("impact_scope", "")).strip(),
                "severity": str(on_fail.get("severity", "")).strip(),
                "handling": str(on_fail.get("handling", "")).strip(),
                "judgement_condition": str(on_fail.get("judgement_condition", "")).strip(),
                "conclusion_hint": str(on_fail.get("conclusion_hint", "")).strip(),
                "atom_label": atom_label,
            },
        )

    needed_count = sum(1 for state in states if state["needed"])
    covered_count = sum(1 for state in states if state["needed"] and state["covered"])
    missing_count = sum(1 for state in states if state["needed"] and state["failed"])
    return {
        "states": states,
        "missing_dimensions": missing_dimensions[:8],
        "recommended_directions": recommended_directions[:6],
        "needed_count": needed_count,
        "covered_count": covered_count,
        "missing_count": missing_count,
        "derived_facts": derived_facts,
    }


def _evaluate_scene_profile(
    profile: dict[str, Any] | None,
    question: str,
    business_match: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    if not profile:
        return {
            "profile": None,
            "states": [],
            "missing_dimensions": [],
            "recommended_directions": [],
            "needed_count": 0,
            "covered_count": 0,
            "missing_count": 0,
            "derived_facts": {},
        }

    if profile.get("rule_specs"):
        evaluated = _evaluate_scene_rule_specs(profile, question, business_match, evidence)
        return {
            "profile": profile,
            "states": evaluated["states"],
            "missing_dimensions": evaluated["missing_dimensions"],
            "recommended_directions": evaluated["recommended_directions"],
            "needed_count": evaluated["needed_count"],
            "covered_count": evaluated["covered_count"],
            "missing_count": evaluated["missing_count"],
            "derived_facts": evaluated["derived_facts"],
        }

    who_terms = [str(item).strip() for item in business_match.get("who_terms", []) if str(item).strip()]
    states = [
        _evaluate_scene_requirement(requirement, question, who_terms, evidence)
        for requirement in profile.get("requirements", ())
    ]

    missing_dimensions: list[dict[str, str]] = []
    recommended_directions: list[dict[str, str]] = []
    for state in states:
        if not state["needed"] or state["covered"]:
            continue
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            state["label"],
            state["reason"],
            tuple(state["directions"]),
        )

    needed_count = sum(1 for state in states if state["needed"])
    covered_count = sum(1 for state in states if state["needed"] and state["covered"])
    missing_count = sum(1 for state in states if state["needed"] and not state["covered"])
    return {
        "profile": profile,
        "states": states,
        "missing_dimensions": missing_dimensions[:8],
        "recommended_directions": recommended_directions[:6],
        "needed_count": needed_count,
        "covered_count": covered_count,
        "missing_count": missing_count,
        "derived_facts": {},
    }


def _evaluate_condition_group(facts: dict[str, Any], node: Any) -> bool:
    if isinstance(node, FactCondition):
        return _condition_holds(facts, node)
    if not isinstance(node, ConditionGroup):
        raise TypeError(f"Unsupported condition node: {type(node)!r}")
    if node.operator == "all":
        return all(_evaluate_condition_group(facts, item) for item in node.items)
    if node.operator == "any":
        return any(_evaluate_condition_group(facts, item) for item in node.items)
    if node.operator == "not":
        if len(node.items) != 1:
            raise ValueError("ConditionGroup(operator='not') expects exactly one item.")
        return not _evaluate_condition_group(facts, node.items[0])
    raise ValueError(f"Unsupported ConditionGroup operator: {node.operator}")


def _render_condition_group(node: Any) -> str:
    if isinstance(node, FactCondition):
        return _render_condition(node)
    if not isinstance(node, ConditionGroup):
        raise TypeError(f"Unsupported condition node: {type(node)!r}")
    if node.operator == "not":
        return f"NOT ({_render_condition_group(node.items[0])})"
    joiner = " AND " if node.operator == "all" else " OR "
    return "(" + joiner.join(_render_condition_group(item) for item in node.items) + ")"


def _append_missing_dimension(
    missing_dimensions: list[dict[str, str]],
    recommended_directions: list[dict[str, str]],
    dimension: str,
    reason: str,
    direction_pairs: tuple[tuple[str, str], ...],
    meta: dict[str, Any] | None = None,
) -> None:
    payload = {"dimension": dimension, "reason": reason}
    if meta:
        for key, value in meta.items():
            if key in {"dimension", "reason"}:
                continue
            payload[key] = value
    missing_dimensions.append(payload)
    for direction, direction_reason in direction_pairs:
        direction_payload = {
            "direction": direction,
            "reason": direction_reason,
            "missing_dimension": dimension,
        }
        if meta:
            if meta.get("rule_id"):
                direction_payload["rule_id"] = meta["rule_id"]
            if meta.get("priority") is not None:
                direction_payload["priority"] = meta["priority"]
        recommended_directions.append(
            direction_payload
        )


def _build_symbolic_recall_facts(
    question: str,
    business_match: dict[str, Any],
    evidence: list[dict[str, Any]],
    round_context: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    question_text = str(question or "").strip()
    who_terms = [str(item).strip() for item in business_match.get("who_terms", []) if str(item).strip()]
    actor_hit_count = 0
    for item in evidence:
        actor_blob = "\n".join([str(item.get("who", "")), str(item.get("what", "")), str(item.get("how", ""))])
        if any(term in actor_blob for term in who_terms):
            actor_hit_count += 1

    evidence_count = len(evidence)
    direct_basis_count = sum(
        1
        for item in evidence
        if str(item.get("article_reference", "")).strip() and str(item.get("source_document", "")).strip()
    )
    distinct_doc_count = len(
        {
            str(item.get("source_document", "")).strip()
            for item in evidence
            if str(item.get("source_document", "")).strip()
        }
    )

    prohibition_rule_count = _count_rule_hits(evidence, ("PRO_FORBIDDEN",))
    prohibition_rule_count += _count_text_hits(evidence, PROHIBITION_TERMS)
    exception_rule_count = _count_rule_hits(evidence, ("PER_AUTH",))
    exception_rule_count += _count_text_hits(evidence, EXCEPTION_TERMS)
    definition_rule_count = _count_rule_hits(evidence, ("DEF_SCOPE",))
    definition_rule_count += _count_text_hits(evidence, DEFINITION_TERMS)
    threshold_rule_count = _count_rule_hits(evidence, ("VAL_THRESHOLD",))
    threshold_rule_count += _count_text_hits(evidence, THRESHOLD_TERMS)
    process_rule_count = _count_rule_hits(evidence, ("PRC_FLOW", "OBL_MANDATORY", "OBL_ONGOING"))
    process_rule_count += _count_text_hits(evidence, PROCESS_TERMS)
    material_evidence_count = _count_text_hits(evidence, MATERIAL_TERMS)
    ambiguity_count = sum(1 for item in evidence if bool(item.get("is_ambiguous")))
    scene_profile_eval = _evaluate_scene_profile(
        _detect_scene_profile(question, business_match),
        question,
        business_match,
        evidence,
    )
    scene_profile = scene_profile_eval["profile"]
    min_evidence_count = int((scene_profile or {}).get("min_evidence_count", 6) or 6)
    min_direct_basis_count = int((scene_profile or {}).get("min_direct_basis_count", 3) or 3)

    facts = {
        "round_index": round_index,
        "scene_profile_id": str((scene_profile or {}).get("profile_id", "")).strip(),
        "has_scene_profile": bool(scene_profile),
        "question_mentions_actor": bool(who_terms),
        "question_mentions_material": _text_has_any(question_text, MATERIAL_TERMS),
        "question_mentions_process": _text_has_any(question_text, PROCESS_TERMS) or "如何" in question_text or "怎么" in question_text,
        "question_mentions_threshold": _text_has_any(question_text, THRESHOLD_TERMS),
        "question_mentions_definition": _text_has_any(question_text, DEFINITION_TERMS),
        "matched_scene_count": len(business_match.get("matched_scene_names", []) or []),
        "matched_module_count": len(business_match.get("matched_module_codes", []) or []),
        "who_term_count": len(who_terms),
        "evidence_count": evidence_count,
        "direct_basis_count": direct_basis_count,
        "distinct_doc_count": distinct_doc_count,
        "actor_hit_count": actor_hit_count,
        "material_evidence_count": material_evidence_count,
        "process_rule_count": process_rule_count,
        "threshold_rule_count": threshold_rule_count,
        "definition_rule_count": definition_rule_count,
        "prohibition_rule_count": prohibition_rule_count,
        "exception_rule_count": exception_rule_count,
        "ambiguity_count": ambiguity_count,
        "top_document_count": len(round_context.get("top_documents", []) or []),
        "scene_requirement_needed_count": scene_profile_eval["needed_count"],
        "scene_requirement_covered_count": scene_profile_eval["covered_count"],
        "scene_requirement_missing_count": scene_profile_eval["missing_count"],
        "min_evidence_count": min_evidence_count,
        "min_direct_basis_count": min_direct_basis_count,
        "scene_derived_facts": scene_profile_eval["derived_facts"],
    }

    facts["route_ready"] = facts["matched_scene_count"] > 0 or facts["matched_module_count"] > 0
    facts["basis_ready"] = facts["evidence_count"] >= min_evidence_count and facts["direct_basis_count"] >= min_direct_basis_count
    facts["has_conflicting_rules"] = facts["prohibition_rule_count"] > 0 and facts["exception_rule_count"] > 0
    facts["gap_business_route"] = not facts["route_ready"]
    facts["gap_scope"] = facts["question_mentions_actor"] and facts["actor_hit_count"] == 0
    facts["gap_material"] = facts["question_mentions_material"] and facts["material_evidence_count"] == 0
    facts["gap_process"] = facts["question_mentions_process"] and facts["process_rule_count"] == 0
    facts["gap_threshold"] = facts["question_mentions_threshold"] and facts["threshold_rule_count"] == 0
    facts["gap_definition"] = facts["question_mentions_definition"] and facts["definition_rule_count"] == 0
    facts["gap_exception_boundary"] = facts["prohibition_rule_count"] > 0 and facts["exception_rule_count"] == 0
    facts["gap_direct_basis"] = facts["direct_basis_count"] < min_direct_basis_count
    facts["structural_gap_count"] = sum(
        1
        for key in (
            "gap_business_route",
            "gap_scope",
            "gap_material",
            "gap_process",
            "gap_threshold",
            "gap_definition",
            "gap_exception_boundary",
        )
        if facts[key]
    )
    facts["ready_without_missing"] = (
        facts["route_ready"]
        and facts["basis_ready"]
        and facts["structural_gap_count"] == 0
        and facts["scene_requirement_missing_count"] == 0
    )
    facts["scene_missing_dimensions"] = scene_profile_eval["missing_dimensions"]
    facts["scene_recommended_directions"] = scene_profile_eval["recommended_directions"]
    facts["scene_requirement_states"] = scene_profile_eval["states"]
    return facts


RECALL_DECISION_RULES: tuple[RecallDecisionRule, ...] = (
    RecallDecisionRule(
        rule_id="scene_profile_missing_requirement",
        description="When a scene profile is active and required scene evidence is missing, continue recall.",
        when=ConditionGroup(
            "all",
            (
                FactCondition("has_scene_profile", "eq", True),
                FactCondition("scene_requirement_missing_count", "gt", 0),
            ),
        ),
        decision="继续召回",
        can_make_final=False,
        confidence=0.34,
        summary="当前场景规则仍有关键 requirement 未满足，应继续按场景规则补召回。",
    ),
    RecallDecisionRule(
        rule_id="scene_profile_ready_with_conflict",
        description="When a scene profile is active and requirements are met but restrictive and permissive rules coexist, stop recall and hand over.",
        when=ConditionGroup(
            "all",
            (
                FactCondition("has_scene_profile", "eq", True),
                FactCondition("scene_requirement_missing_count", "eq", 0),
                FactCondition("basis_ready", "eq", True),
                FactCondition("has_conflicting_rules", "eq", True),
            ),
        ),
        decision="停止召回",
        can_make_final=True,
        confidence=0.52,
        summary="当前场景 requirement 已满足，但限制与例外规则并存，更适合停止召回并进入最终审查。",
    ),
    RecallDecisionRule(
        rule_id="scene_profile_ready",
        description="When a scene profile is active and requirements are met, stop recall and enable final judgement.",
        when=ConditionGroup(
            "all",
            (
                FactCondition("has_scene_profile", "eq", True),
                FactCondition("scene_requirement_missing_count", "eq", 0),
                FactCondition("basis_ready", "eq", True),
            ),
        ),
        decision="停止召回",
        can_make_final=True,
        confidence=0.63,
        summary="当前场景 requirement 已满足，证据可进入最终合规结论生成。",
    ),
    RecallDecisionRule(
        rule_id="route_or_scope_gap",
        description="Keep recalling when route or actor scope is still unresolved.",
        when=ConditionGroup(
            "any",
            (
                FactCondition("gap_business_route", "eq", True),
                FactCondition("gap_scope", "eq", True),
            ),
        ),
        decision="继续召回",
        can_make_final=False,
        confidence=0.22,
        summary="当前业务路由或主体范围仍未收敛，需优先补齐语境定位后再判断是否闭环。",
    ),
    RecallDecisionRule(
        rule_id="semantic_gap_needs_more_evidence",
        description="Keep recalling when material/process/threshold/definition or exception gaps remain.",
        when=ConditionGroup(
            "any",
            (
                FactCondition("gap_material", "eq", True),
                FactCondition("gap_process", "eq", True),
                FactCondition("gap_threshold", "eq", True),
                FactCondition("gap_definition", "eq", True),
                FactCondition("gap_exception_boundary", "eq", True),
            ),
        ),
        decision="继续召回",
        can_make_final=False,
        confidence=0.3,
        summary="当前仍存在关键语义缺口，继续补召回比直接下结论更合理。",
    ),
    RecallDecisionRule(
        rule_id="direct_basis_gap",
        description="Keep recalling when direct legal basis is still too thin.",
        when=ConditionGroup(
            "any",
            (
                ConditionGroup(
                    "all",
                    (
                        FactCondition("gap_direct_basis", "eq", True),
                        ConditionGroup("not", (FactCondition("basis_ready", "eq", True),)),
                    ),
                ),
                ConditionGroup(
                    "all",
                    (
                        FactCondition("evidence_count", "lt", 6),
                        FactCondition("direct_basis_count", "lt", 3),
                    ),
                ),
            ),
        ),
        decision="继续召回",
        can_make_final=False,
        confidence=0.26,
        summary="当前直接法条依据仍偏薄，继续补证据更稳妥。",
    ),
    RecallDecisionRule(
        rule_id="ready_with_conflict",
        description="If evidence is sufficient but restrictive and permissive rules coexist, stop recall and hand over to final review.",
        when=ConditionGroup(
            "all",
            (
                FactCondition("ready_without_missing", "eq", True),
                FactCondition("has_conflicting_rules", "eq", True),
            ),
        ),
        decision="停止召回",
        can_make_final=True,
        confidence=0.54,
        summary="当前证据已基本闭环，但限制与例外规则并存，更适合停止补召回并进入最终审查或人工复核。",
    ),
    RecallDecisionRule(
        rule_id="ready_for_final",
        description="If route, basis and semantic coverage are all ready, stop recall and enable final judgement.",
        when=ConditionGroup(
            "all",
            (
                FactCondition("ready_without_missing", "eq", True),
                ConditionGroup("not", (FactCondition("gap_direct_basis", "eq", True),)),
            ),
        ),
        decision="停止召回",
        can_make_final=True,
        confidence=0.62,
        summary="当前证据覆盖已满足最终判断的最低要求，可以停止召回并进入最终合规结论生成。",
    ),
    RecallDecisionRule(
        rule_id="fallback_continue",
        description="Default to continuing recall when symbolic readiness is still uncertain.",
        when=FactCondition("evidence_count", "gte", 0),
        decision="继续召回",
        can_make_final=False,
        confidence=0.2,
        summary="当前证据尚未达到稳定闭环状态，继续召回更安全。",
    ),
)


def _select_recall_rule(facts: dict[str, Any]) -> RecallDecisionRule:
    for rule in RECALL_DECISION_RULES:
        if _evaluate_condition_group(facts, rule.when):
            return rule
    return RECALL_DECISION_RULES[-1]


def _build_missing_dimensions_and_directions(
    facts: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if facts.get("has_scene_profile") and facts.get("scene_missing_dimensions"):
        missing_dimensions = list(facts.get("scene_missing_dimensions", []))
        recommended_directions = list(facts.get("scene_recommended_directions", []))
        deduped_directions: list[dict[str, str]] = []
        seen_direction_keys: set[tuple[str, str]] = set()
        for item in recommended_directions:
            key = (str(item.get("direction", "")).strip(), str(item.get("missing_dimension", "")).strip())
            if key in seen_direction_keys:
                continue
            deduped_directions.append(item)
            seen_direction_keys.add(key)
        return missing_dimensions[:8], deduped_directions[:6]

    missing_dimensions: list[dict[str, str]] = []
    recommended_directions: list[dict[str, str]] = []

    if facts["gap_business_route"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "业务场景定位",
            "当前证据仍不足以稳定定位到具体业务场景或模块，需先补齐场景路由证据。",
            (("A", "沿当前业务路径继续向下收敛场景。"), ("B", "补充同层相邻场景，确认是否路由偏移。")),
        )
    if facts["gap_scope"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "主体范围",
            "问题存在明确主体约束，但当前证据未命中该主体的直接规则依据。",
            (("A", "优先沿当前场景继续下钻主体相关规则。"), ("D", "围绕主体适用对象补齐定义与条件。")),
        )
    if facts["gap_definition"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "定义范围",
            "当前证据未覆盖关键术语或适用对象的定义边界，结论边界仍不稳定。",
            (("D", "优先补定义、适用范围和术语解释。"), ("F", "补充上位法或实施细则中的定义依据。")),
        )
    if facts["gap_material"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "材料要求",
            "问题直接涉及提交材料或签章，但当前证据未覆盖明确材料清单。",
            (("D", "补齐材料、证件、签章类规则。"), ("A", "沿当前业务场景继续下钻办理材料。")),
        )
    if facts["gap_process"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "流程动作",
            "问题直接涉及办理动作或审核步骤，但当前证据未覆盖关键执行动作。",
            (("D", "补齐流程、审核、核查动作类规则。"), ("C", "查找相邻条款中的流程细节。")),
        )
    if facts["gap_threshold"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "时限阈值",
            "问题涉及金额、现金、期限等边界条件，但当前证据未覆盖明确阈值或时限。",
            (("D", "补齐金额、期限、现金字样等条件规则。"), ("C", "优先补相邻条款中的阈值边界。")),
        )
    if facts["gap_exception_boundary"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "例外/禁止边界",
            "当前证据已命中限制性规则，但尚未覆盖例外、授权或边界条款。",
            (("E", "优先补例外、但书和禁止边界。"), ("F", "补充上位法、细则或配套规范。")),
        )
    if facts["gap_direct_basis"] and not facts["ready_without_missing"]:
        _append_missing_dimension(
            missing_dimensions,
            recommended_directions,
            "规范依据",
            "当前直接法条依据仍偏薄，尚不足以稳定支撑最终判断。",
            (("C", "优先补相邻条款和同文档上下文。"), ("F", "补充上下位规范与实施细则。")),
        )

    deduped_directions: list[dict[str, str]] = []
    seen_direction_keys: set[tuple[str, str]] = set()
    for item in recommended_directions:
        key = (str(item.get("direction", "")).strip(), str(item.get("missing_dimension", "")).strip())
        if key in seen_direction_keys:
            continue
        deduped_directions.append(item)
        seen_direction_keys.add(key)

    return missing_dimensions[:8], deduped_directions[:6]


def build_symbolic_recall_judgement(
    question: str,
    business_match: dict[str, Any],
    evidence: list[dict[str, Any]],
    round_context: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    facts = _build_symbolic_recall_facts(question, business_match, evidence, round_context, round_index)
    rule = _select_recall_rule(facts)
    missing_dimensions, recommended_directions = _build_missing_dimensions_and_directions(facts)
    fact_snapshot = {
        key: facts[key]
        for key in (
            "scene_profile_id",
            "evidence_count",
            "direct_basis_count",
            "matched_scene_count",
            "matched_module_count",
            "actor_hit_count",
            "material_evidence_count",
            "process_rule_count",
            "threshold_rule_count",
            "definition_rule_count",
            "prohibition_rule_count",
            "exception_rule_count",
            "ambiguity_count",
            "structural_gap_count",
            "scene_requirement_needed_count",
            "scene_requirement_covered_count",
            "scene_requirement_missing_count",
        )
    }
    if facts.get("scene_profile_id"):
        fact_snapshot["scene_derived_facts"] = dict(facts.get("scene_derived_facts", {}))

    if rule.decision == "停止召回" and rule.can_make_final:
        missing_dimensions = []
        recommended_directions = []

    return {
        "decision": rule.decision,
        "can_make_final_compliance_judgement": rule.can_make_final,
        "confidence": rule.confidence,
        "missing_dimensions": missing_dimensions,
        "recommended_recall_directions": recommended_directions,
        "summary": rule.summary,
        "matched_rules": [item for item in [facts.get("scene_profile_id", ""), rule.rule_id] if item],
        "decision_trace": [_render_condition_group(rule.when)],
        "fact_snapshot": fact_snapshot,
    }


def build_symbolic_atom_analysis(
    question: str,
    business_match: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    question_text = str(question or "").strip()
    record_blob = _build_text_blob(record)
    who_terms = [str(item).strip() for item in business_match.get("who_terms", []) if str(item).strip()]
    missing_elements: list[str] = []
    scene_profile_eval = _evaluate_scene_profile(
        _detect_scene_profile(question, business_match),
        question,
        business_match,
        [record],
    )

    if scene_profile_eval["profile"]:
        for state in scene_profile_eval["states"]:
            if state["needed"] and not state["covered"] and state["atom_label"]:
                missing_elements.append(state["atom_label"])

    if not scene_profile_eval["profile"] and who_terms and not any(term in record_blob for term in who_terms):
        missing_elements.append("主体范围")
    if not scene_profile_eval["profile"] and _text_has_any(question_text, MATERIAL_TERMS) and not _text_has_any(record_blob, MATERIAL_TERMS):
        missing_elements.append("材料或签章要求")
    if not scene_profile_eval["profile"] and (_text_has_any(question_text, PROCESS_TERMS) or "如何" in question_text or "怎么" in question_text) and not _text_has_any(record_blob, PROCESS_TERMS):
        missing_elements.append("流程动作")
    if not scene_profile_eval["profile"] and _text_has_any(question_text, THRESHOLD_TERMS) and not _text_has_any(record_blob, THRESHOLD_TERMS):
        missing_elements.append("时限或阈值条件")
    if not scene_profile_eval["profile"] and _text_has_any(question_text, DEFINITION_TERMS) and not _text_has_any(record_blob, DEFINITION_TERMS):
        missing_elements.append("定义范围")
    if not str(record.get("article_reference", "")).strip():
        missing_elements.append("法条依据定位")
    if bool(record.get("is_ambiguous")):
        missing_elements.append("歧义条款需复核")

    missing_elements = _dedupe_keep_order(missing_elements)[:5]
    next_split_focus = missing_elements[0] if missing_elements else ""
    decision = "停止拆解" if not missing_elements else "继续拆解"
    matched_rules = [
        str((scene_profile_eval["profile"] or {}).get("profile_id", "")).strip(),
        *[
            str(state.get("rule_id", "")).strip()
            for state in scene_profile_eval.get("states", [])
            if state.get("needed")
        ],
    ]
    matched_rules = [item for item in _dedupe_keep_order(matched_rules) if item]
    reason = (
        "当前原子已具备直接执行所需的关键信息。"
        if not missing_elements
        else f"当前原子仍缺少{ '、'.join(missing_elements[:3]) }，建议继续细化。"
    )

    return {
        "atom_id": str(record.get("atom_id", "")).strip(),
        "decision": decision,
        "reason": reason,
        "missing_elements": missing_elements,
        "next_split_focus": next_split_focus,
        "matched_rules": matched_rules or ["symbolic_atom_minimum_v1"],
        "decision_trace": [
            f"scene_profile={str((scene_profile_eval['profile'] or {}).get('profile_id', '')).strip() or 'generic'}",
            f"who_terms={len(who_terms)}",
            f"has_material_signal={_text_has_any(record_blob, MATERIAL_TERMS)}",
            f"has_process_signal={_text_has_any(record_blob, PROCESS_TERMS)}",
            f"has_threshold_signal={_text_has_any(record_blob, THRESHOLD_TERMS)}",
            f"is_ambiguous={bool(record.get('is_ambiguous'))}",
        ],
    }
