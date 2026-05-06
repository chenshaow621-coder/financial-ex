from __future__ import annotations

from typing import Any


GENERIC_FINAL_JUDGEMENT_RULE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "upstream_llm_error",
        "description": "If upstream recall judgement fails, emit a safe symbolic fallback.",
        "conclusion": "证据不足待补召回",
        "status": "not_ready",
        "confidence": 0.25,
        "summary": "上游召回判断发生异常，本次最终结论按本地证据做安全回退，不能视为完整合规结论。",
        "all_conditions": ({"fact": "final_decision", "eq": "LLM_ERROR"},),
        "follow_up_actions": ("先恢复召回链路的模型连通性，再重新执行闭环查验。",),
    },
    {
        "rule_id": "dry_run_only",
        "description": "If only dry-run executed, final compliance judgement must stay pending.",
        "conclusion": "证据不足待补召回",
        "status": "not_ready",
        "confidence": 0.25,
        "summary": "当前仅完成本地召回摘要，尚未进入完整闭环判断，不能直接输出最终合规结论。",
        "all_conditions": ({"fact": "final_decision", "eq": "DRY_RUN"},),
        "follow_up_actions": ("继续执行闭环召回，再进入最终合规查验。",),
    },
    {
        "rule_id": "fatal_gap_blocks_case",
        "description": "If any fatal gap remains, the case cannot reach a final deterministic judgement.",
        "conclusion": "证据不足待补召回",
        "status": "not_ready",
        "confidence": 0.28,
        "summary": "当前仍存在整案级阻断缺口，证据未闭环，不能输出确定性最终结论。",
        "all_conditions": ({"fact": "fatal_gap_count", "gt": 0},),
        "follow_up_actions": ("优先补齐整案级阻断缺口，再判断能否进入最终结论。",),
    },
    {
        "rule_id": "exhausted_reviewable_gap",
        "description": "If candidates are exhausted and only reviewable gaps remain, route to manual review.",
        "conclusion": "需人工复核",
        "status": "exhausted_partial",
        "confidence": 0.4,
        "summary": "召回候选已基本耗尽，现有证据足以支撑阶段性判断，但仍存在子结论阻断缺口，建议转人工复核。",
        "all_conditions": (
            {"fact": "stop_reason", "eq": "no_new_candidates"},
            {"fact": "fatal_gap_count", "eq": 0},
            {"fact": "reviewable_gap_count", "gt": 0},
        ),
        "follow_up_actions": ("转人工复核，重点核查剩余子结论阻断缺口。",),
    },
    {
        "rule_id": "not_ready_needs_more_recall",
        "description": "If final judgement readiness is false, keep the case in recall mode.",
        "conclusion": "证据不足待补召回",
        "status": "not_ready",
        "confidence": 0.25,
        "summary": "当前证据尚未闭环，仍需继续补召回或补充法规依据后，才能形成最终合规结论。",
        "all_conditions": ({"fact": "can_make_final", "eq": False},),
        "follow_up_actions": ("继续补召回，并优先处理当前缺口最高的方向。",),
    },
    {
        "rule_id": "conflicting_prohibition_and_exception",
        "description": "If prohibitions and exceptions coexist, force manual review instead of a hard decision.",
        "conclusion": "需人工复核",
        "status": "generated",
        "confidence": 0.38,
        "summary": "当前证据同时命中限制性规则与例外条款，边界尚未完全消解，建议人工复核后再下结论。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "has_conflicting_rules", "eq": True},
        ),
        "follow_up_actions": ("人工复核限制条款与例外条款的适用边界。",),
    },
    {
        "rule_id": "prohibition_without_exception",
        "description": "If a prohibition exists without a balancing exception, conclude not processable.",
        "conclusion": "不可办理",
        "status": "generated",
        "confidence": 0.62,
        "summary": "当前证据命中明确限制或禁止性规则，且未发现足以解除限制的例外条款，倾向于不可办理。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "prohibition_count", "gt": 0},
            {"fact": "exception_count", "eq": 0},
        ),
        "follow_up_actions": ("如需放行，先补充可覆盖该限制的明确例外依据。",),
    },
    {
        "rule_id": "material_gap_requires_supplement",
        "description": "If the case is ready but still signals missing materials, require material completion first.",
        "conclusion": "需补材料后办理",
        "status": "generated",
        "confidence": 0.58,
        "summary": "当前证据显示办理前仍需补齐关键材料或签章，材料补齐后才适合继续办理。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "missing_item_count", "gt": 0},
        ),
        "any_conditions": ({"fact": "material_signal_count", "gt": 0},),
        "follow_up_actions": ("先补齐关键材料或签章，再重新确认办理结论。",),
    },
    {
        "rule_id": "high_ambiguity_requires_review",
        "description": "If ambiguity is still high, prefer manual review over a deterministic answer.",
        "conclusion": "需人工复核",
        "status": "generated",
        "confidence": 0.36,
        "summary": "最终证据中仍存在较多歧义原子，建议人工复核后再输出确定性结论。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "ambiguity_count", "gte": 2},
        ),
        "follow_up_actions": ("人工复核歧义证据，并确认其是否影响最终结论。",),
    },
    {
        "rule_id": "reviewable_gap_requires_review",
        "description": "If reviewable gaps remain even when ready, route to manual review.",
        "conclusion": "需人工复核",
        "status": "generated",
        "confidence": 0.35,
        "summary": "当前仍保留子结论阻断缺口，适合转人工复核，而不宜直接输出绝对性结论。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "reviewable_gap_count", "gt": 0},
        ),
        "follow_up_actions": ("转人工复核，重点核查未完全闭环的子结论。",),
    },
    {
        "rule_id": "conditional_approval",
        "description": "If the case is ready but still carries explicit conditions, treat it as conditional approval.",
        "conclusion": "有条件可办理",
        "status": "generated",
        "confidence": 0.48,
        "summary": "当前证据显示该事项并非绝对禁止，但需满足材料、动作、时限或例外条件后方可办理。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "conditional_signal_count", "gt": 0},
        ),
        "follow_up_actions": ("按当前条件清单逐项核验后，再执行办理动作。",),
    },
    {
        "rule_id": "clear_approval",
        "description": "If the case is ready and the basis is sufficient without blockers, approve it.",
        "conclusion": "可办理",
        "status": "generated",
        "confidence": 0.55,
        "summary": "当前证据已基本闭环，且未识别到阻断性限制，倾向于可办理。",
        "all_conditions": (
            {"fact": "can_make_final", "eq": True},
            {"fact": "key_basis_count", "gt": 0},
        ),
        "follow_up_actions": ("保留关键依据与核验记录后执行办理。",),
    },
    {
        "rule_id": "fallback_manual_review",
        "description": "Fallback rule when no stronger symbolic rule matches.",
        "conclusion": "需人工复核",
        "status": "generated",
        "confidence": 0.3,
        "summary": "当前证据已接近闭环，但仍不适合自动输出绝对性结论，建议人工复核。",
        "follow_up_actions": ("人工复核剩余不确定项。",),
    },
)


SCENE_FINAL_JUDGEMENT_RULE_SPECS: dict[str, tuple[dict[str, Any], ...]] = {
    "bank_draft_presentment": (),
    "commercial_bill_acceptance_discount": (),
    "bank_note_lifecycle": (),
}


def get_final_judgement_rule_specs(scene_profile_id: str = "") -> tuple[dict[str, Any], ...]:
    scene_rules = SCENE_FINAL_JUDGEMENT_RULE_SPECS.get(str(scene_profile_id or "").strip(), ())
    return tuple(scene_rules) + GENERIC_FINAL_JUDGEMENT_RULE_SPECS
