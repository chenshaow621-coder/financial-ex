from __future__ import annotations


def _scene_gap(
    *,
    dimension: str,
    reason: str,
    gap_type: str,
    impact_scope: str,
    conclusion_hint: str,
    atom_label: str,
    recall_directions: tuple[tuple[str, str], ...],
    severity: str | None = None,
    handling: str | None = None,
    judgement_condition: str | None = None,
) -> dict[str, object]:
    return {
        "dimension": dimension,
        "reason": reason,
        "gap_type": gap_type,
        "impact_scope": impact_scope,
        "severity": severity or ("阻断型" if impact_scope == "全局阻断" else "关键型"),
        "handling": handling or f"优先补召回{dimension}相关规则；候选耗尽时转人工复核。",
        "judgement_condition": judgement_condition
        or f"当办理判断依赖{dimension}，而证据尚未覆盖时，不能直接输出稳定结论。",
        "conclusion_hint": conclusion_hint,
        "atom_label": atom_label,
        "recall_directions": recall_directions,
    }


BANK_DRAFT_PRESENTMENT_PROFILE = {
    "profile_id": "bank_draft_presentment",
    "title": "Bank Draft Presentment",
    "scene_names": ("银行汇票",),
    "question_keywords": ("提示付款", "汇票"),
    "min_evidence_count": 5,
    "min_direct_basis_count": 3,
    "derived_facts": (
        {
            "fact_id": "holder_scope_requested",
            "source": "who_terms",
            "mode": "non_empty",
        },
        {
            "fact_id": "non_account_holder_requested",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("未在银行开立存款账户", "未开立存款账户"),
        },
        {
            "fact_id": "asks_presentment_action",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("提示付款", "付款"),
        },
        {
            "fact_id": "asks_material_documents",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("材料", "提交", "身份证", "证件", "证明", "解讫通知"),
        },
        {
            "fact_id": "asks_identity_document",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("身份证", "身份证件", "有效身份证件", "证件"),
        },
        {
            "fact_id": "asks_discharge_notice",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("解讫通知", "进账单"),
        },
        {
            "fact_id": "asks_signature_requirements",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("签章", "签字", "背书", "印鉴"),
        },
        {
            "fact_id": "asks_signature_action",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("签章", "签字", "印鉴"),
        },
        {
            "fact_id": "asks_endorsement",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("背书", "委托收款"),
        },
        {
            "fact_id": "asks_cash_withdrawal",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("现金", "支取现金", "能否支取"),
        },
        {
            "fact_id": "asks_cash_wording",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("现金字样", "填明现金", "填明“现金”字样", "填明‘现金’字样", "填明'现金'字样"),
        },
        {
            "fact_id": "asks_decision_boundary",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("能否", "可否", "是否可以"),
        },
        {
            "fact_id": "holder_scope_hit",
            "source": "evidence",
            "mode": "actor_scope",
        },
        {
            "fact_id": "presentment_action_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("提示付款", "向银行提示付款", "向开户银行提示付款", "请求付款"),
        },
        {
            "fact_id": "identity_document_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("身份证", "身份证件", "有效身份证件", "本人身份证件", "证件名称", "证件号码", "发证机关"),
        },
        {
            "fact_id": "discharge_notice_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("解讫通知", "进账单", "送交开户银行"),
        },
        {
            "fact_id": "signature_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("持票人向银行提示付款签章", "汇票背面签章", "签章", "签字", "印鉴", "预留银行签章"),
        },
        {
            "fact_id": "endorsement_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("背书", "委托收款", "背书栏", "被背书人", "背书日期"),
        },
        {
            "fact_id": "cash_withdrawal_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("支取现金", "可支取现金", "还可支取现金", "用于支取现金", "也可以用于支取现金"),
        },
        {
            "fact_id": "cash_wording_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("现金字样", "填明“现金”字样", "填明‘现金’字样", "填明'现金'字样", "填明现金字样", "现金汇票"),
        },
        {
            "fact_id": "non_account_cash_boundary_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "未在银行开立存款账户的个人持票人",
                "可向选择的任何一家银行机构提示付款",
                "由其本人向银行提交身份证件",
                "本人身份证件名称",
                "本人身份证件号码",
                "发证机关",
            ),
        },
        {
            "fact_id": "prohibition_signal_hit",
            "source": "evidence",
            "mode": "keyword_or_rule",
            "keywords": ("不得", "不予", "禁止", "不能", "不可"),
            "rule_types": ("PRO_FORBIDDEN",),
        },
        {
            "fact_id": "exception_signal_hit",
            "source": "evidence",
            "mode": "keyword_or_rule",
            "keywords": ("除外", "例外", "但是", "但", "可以"),
            "rule_types": ("PER_AUTH",),
        },
    ),
    "rule_specs": (
        {
            "rule_id": "holder_scope_rule",
            "priority": 100,
            "applies_if": {"fact": "holder_scope_requested", "eq": True},
            "requires": {"fact": "holder_scope_hit", "eq": True},
            "on_fail": {
                "dimension": "主体范围",
                "reason": "当前问题带有明确主体约束，但证据中尚未稳定覆盖该主体的直接规则。",
                "gap_type": "主体范围缺口",
                "impact_scope": "全局阻断",
                "severity": "阻断型",
                "handling": "必须继续补召回，先稳定主体适用范围后再判断。",
                "judgement_condition": "当办理判断依赖特定主体范围，而证据未覆盖该主体时，不能直接输出最终结论。",
                "conclusion_hint": "证据不足待补召回",
                "atom_label": "主体适用范围",
                "recall_directions": (
                    ("A", "沿当前场景继续下钻主体适用规则。"),
                    ("D", "补齐主体范围、适用对象和身份条件。"),
                ),
            },
        },
        {
            "rule_id": "identity_document_rule",
            "priority": 93,
            "applies_if": {
                "any": [
                    {"fact": "asks_identity_document", "eq": True},
                    {
                        "all": [
                            {"fact": "asks_material_documents", "eq": True},
                            {"fact": "non_account_holder_requested", "eq": True},
                        ]
                    },
                ]
            },
            "requires": {"fact": "identity_document_hit", "eq": True},
            "on_fail": {
                "dimension": "身份证明材料",
                "reason": "问题涉及未开户个人持票人的提交材料，但当前证据未覆盖身份证件及证件要素。",
                "gap_type": "材料缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回本人身份证件、证件号码和发证机关等身份材料；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖未开户个人持票人的身份核验材料，而证据未覆盖身份证件时，不能直接输出可办理。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "身份证明材料",
                "recall_directions": (
                    ("D", "补齐身份证件、证件名称/号码、发证机关等材料规则。"),
                    ("A", "沿当前场景继续检索未开户个人持票人的提交材料。"),
                ),
            },
        },
        {
            "rule_id": "discharge_notice_rule",
            "priority": 92,
            "applies_if": {
                "any": [
                    {"fact": "asks_discharge_notice", "eq": True},
                    {
                        "all": [
                            {"fact": "asks_material_documents", "eq": True},
                            {"not": {"fact": "non_account_holder_requested", "eq": True}},
                        ]
                    },
                ]
            },
            "requires": {"fact": "discharge_notice_hit", "eq": True},
            "on_fail": {
                "dimension": "解讫通知/进账单",
                "reason": "问题涉及材料清单或开户行提示付款，但当前证据未覆盖解讫通知、进账单等交付材料。",
                "gap_type": "材料缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回解讫通知、进账单及送交开户银行的材料要求；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖解讫通知或进账单等票据附件，而证据未覆盖这些材料时，不能直接输出可办理。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "解讫通知/进账单",
                "recall_directions": (
                    ("D", "补齐解讫通知、进账单和送交流程类规则。"),
                    ("C", "补相邻条款中的材料清单细则。"),
                ),
            },
        },
        {
            "rule_id": "signature_rule",
            "priority": 88,
            "applies_if": {
                "any": [
                    {"fact": "asks_signature_action", "eq": True},
                    {
                        "all": [
                            {"fact": "asks_signature_requirements", "eq": True},
                            {"not": {"fact": "asks_endorsement", "eq": True}},
                        ]
                    },
                ]
            },
            "requires": {"fact": "signature_hit", "eq": True},
            "on_fail": {
                "dimension": "提示付款签章动作",
                "reason": "问题直接询问如何签章，但当前证据未覆盖汇票背面签章、签字或印鉴要求。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先继续补召回签章动作和位置要求；候选耗尽时转人工复核。",
                "judgement_condition": "当办理依赖提示付款签章动作，而证据未覆盖签章位置、签字或印鉴要求时，结论只能停留在阶段性判断。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "提示付款签章动作",
                "recall_directions": (
                    ("D", "补齐汇票背面签章、签字、印鉴类规则。"),
                    ("C", "补相邻条款中的提示付款签章细则。"),
                ),
            },
        },
        {
            "rule_id": "endorsement_rule",
            "priority": 86,
            "applies_if": {"fact": "asks_endorsement", "eq": True},
            "requires": {"fact": "endorsement_hit", "eq": True},
            "on_fail": {
                "dimension": "背书/委托收款动作",
                "reason": "问题已触及背书或委托收款，但当前证据未覆盖背书栏签章、被背书人或背书日期要求。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回背书栏、委托收款、被背书人等动作要件；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖背书或委托收款动作，而证据未覆盖背书要件时，不能输出稳定子结论。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "背书/委托收款动作",
                "recall_directions": (
                    ("D", "补齐背书栏、委托收款、被背书人、背书日期类规则。"),
                    ("C", "补相邻条款中的背书要式细则。"),
                ),
            },
        },
        {
            "rule_id": "cash_wording_rule",
            "priority": 82,
            "applies_if": {
                "any": [
                    {"fact": "asks_cash_wording", "eq": True},
                    {"fact": "asks_cash_withdrawal", "eq": True},
                ]
            },
            "requires": {"fact": "cash_wording_hit", "eq": True},
            "on_fail": {
                "dimension": "现金字样",
                "reason": "问题直接询问能否支取现金，但当前证据未覆盖“填明现金字样”这一关键前置条件。",
                "gap_type": "时限阈值缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回现金字样、票面记载和金额条件；候选耗尽时转人工复核。",
                "judgement_condition": "当现金支取结论依赖票面是否填明现金字样，而证据未覆盖该要件时，不能输出确定性子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "现金字样",
                "recall_directions": (
                    ("D", "补齐现金字样、票面记载和金额条件规则。"),
                    ("C", "补相邻条款中的票面要式边界。"),
                ),
            },
        },
        {
            "rule_id": "cash_withdrawal_boundary_rule",
            "priority": 81,
            "applies_if": {"fact": "asks_cash_withdrawal", "eq": True},
            "requires": {"fact": "cash_withdrawal_hit", "eq": True},
            "on_fail": {
                "dimension": "现金支取边界",
                "reason": "问题直接询问能否支取现金，但当前证据未覆盖支取现金的允许、禁止或条件边界。",
                "gap_type": "判断条件缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先继续补召回现金支取允许条件、禁止边界和例外授权；候选耗尽时转人工复核。",
                "judgement_condition": "当问题直接询问能否支取现金，而证据未覆盖允许/禁止边界时，不能直接输出确定性子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "现金支取边界",
                "recall_directions": (
                    ("D", "补齐现金支取、禁止边界和允许条件规则。"),
                    ("E", "优先补现金支取相关的限制与例外条款。"),
                ),
            },
        },
        {
            "rule_id": "non_account_cash_boundary_rule",
            "priority": 78,
            "applies_if": {
                "all": [
                    {"fact": "asks_cash_withdrawal", "eq": True},
                    {"fact": "non_account_holder_requested", "eq": True},
                ]
            },
            "requires": {"fact": "non_account_cash_boundary_hit", "eq": True},
            "on_fail": {
                "dimension": "未开户个人持票人边界",
                "reason": "当前问题限定为未在银行开立存款账户的个人持票人，但证据未覆盖该主体在提示付款/现金支取中的专门边界。",
                "gap_type": "主体范围缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回未开户个人持票人的身份证明、签章位置和办理边界；候选耗尽时转人工复核。",
                "judgement_condition": "当现金支取判断依赖未开户个人持票人的专门办理规则，而证据未覆盖该主体边界时，不能输出稳定子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "未开户个人持票人边界",
                "recall_directions": (
                    ("D", "补齐未开户个人持票人的身份证明、签章和提示付款规则。"),
                    ("A", "沿当前场景继续检索持票人主体边界。"),
                ),
            },
        },
        {
            "rule_id": "exception_boundary_rule",
            "priority": 75,
            "applies_if": {
                "all": [
                    {"fact": "asks_cash_withdrawal", "eq": True},
                    {"fact": "asks_decision_boundary", "eq": True},
                ]
            },
            "forbids": {
                "all": [
                    {"fact": "prohibition_signal_hit", "eq": True},
                    {"not": {"fact": "exception_signal_hit", "eq": True}},
                ]
            },
            "exception_if": {"fact": "exception_signal_hit", "eq": True},
            "on_fail": {
                "dimension": "例外/禁止边界",
                "reason": "当前证据已命中限制性规则，但尚未覆盖足以解除限制的例外或授权边界。",
                "gap_type": "例外/禁止缺口",
                "impact_scope": "全局阻断",
                "severity": "阻断型",
                "handling": "必须继续补召回限制与例外边界，不能直接下最终结论。",
                "judgement_condition": "当结论涉及禁止、限制或例外条款，但证据未覆盖完整边界时，不能输出确定性结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "例外/禁止边界",
                "recall_directions": (
                    ("E", "优先补例外、但书和禁止条款。"),
                    ("F", "补充实施细则或上下位规范中的边界依据。"),
                ),
            },
        },
        {
            "rule_id": "presentment_action_rule",
            "priority": 70,
            "applies_if": {"fact": "asks_presentment_action", "eq": True},
            "requires": {"fact": "presentment_action_hit", "eq": True},
            "on_fail": {
                "dimension": "提示付款流程",
                "reason": "问题直接涉及提示付款，但当前证据未稳定覆盖办理或审核动作。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回办理动作和审核步骤；候选耗尽时可转人工复核。",
                "judgement_condition": "当办理判断依赖提示付款流程或审核动作，而证据未覆盖这些动作时，不能直接输出确定性结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "提示付款动作",
                "recall_directions": (
                    ("A", "继续下钻提示付款流程和办理动作。"),
                    ("C", "补相邻条款中的办理步骤和审核动作。"),
                ),
            },
        },
    ),
    "requirements": (
        {
            "fact_id": "actor_scope",
            "label": "主体范围",
            "mode": "actor_scope",
            "activate_if_who_terms": True,
            "reason": "当前问题带有明确主体约束，但证据中尚未稳定覆盖该主体的直接规则。",
            "directions": (
                ("A", "沿当前场景继续下钻主体适用规则。"),
                ("D", "补齐主体范围、适用对象和身份条件。"),
            ),
            "atom_label": "主体适用范围",
        },
        {
            "fact_id": "presentment_action",
            "label": "提示付款流程",
            "mode": "keyword",
            "question_keywords": ("提示付款", "付款"),
            "evidence_keywords": ("提示付款", "向银行提示付款", "向开户银行提示付款", "请求付款"),
            "reason": "问题直接涉及提示付款，但当前证据未稳定覆盖办理或审核动作。",
            "directions": (
                ("A", "继续下钻提示付款流程和办理动作。"),
                ("C", "补相邻条款中的办理步骤和审核动作。"),
            ),
            "atom_label": "提示付款动作",
        },
        {
            "fact_id": "identity_document",
            "label": "身份证明材料",
            "mode": "keyword",
            "question_keywords": ("身份证", "身份证件", "有效身份证件", "未在银行开立存款账户", "个人持票人"),
            "evidence_keywords": ("身份证", "身份证件", "有效身份证件", "本人身份证件", "证件名称", "证件号码", "发证机关"),
            "reason": "问题涉及未开户个人持票人的提交材料，但当前证据未覆盖身份证件及证件要素。",
            "directions": (
                ("D", "补齐身份证件、证件名称/号码、发证机关等材料规则。"),
                ("A", "沿当前场景继续检索未开户个人持票人的提交材料。"),
            ),
            "atom_label": "身份证明材料",
        },
        {
            "fact_id": "discharge_notice",
            "label": "解讫通知/进账单",
            "mode": "keyword",
            "question_keywords": ("解讫通知", "进账单", "开户银行"),
            "evidence_keywords": ("解讫通知", "进账单", "送交开户银行"),
            "reason": "问题涉及材料清单或开户行提示付款，但当前证据未覆盖解讫通知、进账单等交付材料。",
            "directions": (
                ("D", "补齐解讫通知、进账单和送交流程类规则。"),
                ("C", "补相邻条款中的材料清单细则。"),
            ),
            "atom_label": "解讫通知/进账单",
        },
        {
            "fact_id": "signature_action",
            "label": "提示付款签章动作",
            "mode": "keyword",
            "question_keywords": ("签章", "签字", "印鉴"),
            "evidence_keywords": ("持票人向银行提示付款签章", "汇票背面签章", "签章", "签字", "印鉴", "预留银行签章"),
            "reason": "问题直接询问如何签章，但当前证据未覆盖汇票背面签章、签字或印鉴要求。",
            "directions": (
                ("D", "补齐汇票背面签章、签字、印鉴类规则。"),
                ("C", "补相邻条款中的提示付款签章细则。"),
            ),
            "atom_label": "提示付款签章动作",
        },
        {
            "fact_id": "endorsement",
            "label": "背书/委托收款动作",
            "mode": "keyword",
            "question_keywords": ("背书", "委托收款"),
            "evidence_keywords": ("背书", "委托收款", "背书栏", "被背书人", "背书日期"),
            "reason": "问题触及背书或委托收款，但当前证据未覆盖背书栏签章、被背书人或背书日期要求。",
            "directions": (
                ("D", "补齐背书栏、委托收款、被背书人、背书日期类规则。"),
                ("C", "补相邻条款中的背书要式细则。"),
            ),
            "atom_label": "背书/委托收款动作",
        },
        {
            "fact_id": "cash_wording",
            "label": "现金字样",
            "mode": "keyword",
            "question_keywords": ("现金", "支取现金", "现金字样"),
            "evidence_keywords": ("现金字样", "填明“现金”字样", "填明‘现金’字样", "填明'现金'字样", "填明现金字样", "现金汇票"),
            "reason": "问题直接询问能否支取现金，但当前证据未覆盖“填明现金字样”这一关键前置条件。",
            "directions": (
                ("D", "补齐现金字样、票面记载和金额条件规则。"),
                ("C", "补相邻条款中的票面要式边界。"),
            ),
            "atom_label": "现金字样",
        },
        {
            "fact_id": "cash_boundary",
            "label": "现金支取边界",
            "mode": "keyword",
            "question_keywords": ("现金", "支取现金", "能否支取"),
            "evidence_keywords": ("支取现金", "可支取现金", "还可支取现金", "用于支取现金", "也可以用于支取现金"),
            "reason": "问题直接询问能否支取现金，但当前证据未覆盖支取现金的允许、禁止或条件边界。",
            "directions": (
                ("D", "补齐现金支取、禁止边界和允许条件规则。"),
                ("E", "优先补现金支取相关的限制与例外条款。"),
            ),
            "atom_label": "现金支取边界",
        },
        {
            "fact_id": "non_account_cash_boundary",
            "label": "未开户个人持票人边界",
            "mode": "keyword",
            "question_keywords": ("未在银行开立存款账户", "未开立存款账户", "个人持票人"),
            "evidence_keywords": (
                "未在银行开立存款账户的个人持票人",
                "可向选择的任何一家银行机构提示付款",
                "由其本人向银行提交身份证件",
                "本人身份证件名称",
                "本人身份证件号码",
                "发证机关",
            ),
            "reason": "当前问题限定为未在银行开立存款账户的个人持票人，但证据未覆盖该主体在提示付款/现金支取中的专门边界。",
            "directions": (
                ("D", "补齐未开户个人持票人的身份证明、签章和提示付款规则。"),
                ("A", "沿当前场景继续检索持票人主体边界。"),
            ),
            "atom_label": "未开户个人持票人边界",
        },
        {
            "fact_id": "exception_boundary",
            "label": "例外/禁止边界",
            "mode": "keyword",
            "question_keywords": ("能否", "现金", "限制", "例外"),
            "evidence_keywords": ("不得", "不予", "禁止", "除外", "例外", "可以"),
            "evidence_rule_types": ("PER_AUTH", "PRO_FORBIDDEN"),
            "reason": "当前问题包含可否判断，但证据未完整覆盖限制与例外边界。",
            "directions": (
                ("E", "优先补例外、但书和禁止条款。"),
                ("F", "补充实施细则或上下位规范中的边界依据。"),
            ),
            "atom_label": "例外/禁止边界",
        },
    ),
}


COMMERCIAL_BILL_ACCEPTANCE_DISCOUNT_PROFILE = {
    "profile_id": "commercial_bill_acceptance_discount",
    "title": "Commercial Bill Acceptance / Discount",
    "scene_names": ("商业汇票（承兑/贴现）",),
    "question_keywords": ("商业汇票", "承兑", "贴现"),
    "min_evidence_count": 5,
    "min_direct_basis_count": 3,
    "derived_facts": (
        {
            "fact_id": "holder_scope_requested",
            "source": "who_terms",
            "mode": "non_empty",
        },
        {
            "fact_id": "asks_acceptance_definition",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("什么是提示承兑", "提示承兑是指", "提示承兑的含义"),
        },
        {
            "fact_id": "asks_acceptance_path",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("提示承兑", "承兑或者拒绝承兑", "拒绝承兑", "承兑"),
        },
        {
            "fact_id": "asks_acceptance_timing",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("到期日前", "1个月内", "期限", "多久", "几日内"),
        },
        {
            "fact_id": "asks_acceptance_receipt",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("回单", "提示承兑日期", "签发收到汇票的回单"),
        },
        {
            "fact_id": "asks_acceptance_decision",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("承兑或者拒绝承兑", "承兑或拒绝承兑", "3日内", "多久内承兑"),
        },
        {
            "fact_id": "asks_refusal_proof",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("拒绝承兑证明", "拒绝证明", "退票理由书"),
        },
        {
            "fact_id": "asks_conditional_acceptance",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("附有条件", "条件承兑", "视为拒绝承兑"),
        },
        {
            "fact_id": "asks_discount_path",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("贴现", "转贴现", "再贴现"),
        },
        {
            "fact_id": "asks_discount_eligibility",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("哪些条件", "贴现条件", "贴现资格", "符合条件", "准入"),
        },
        {
            "fact_id": "asks_trade_background",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("真实交易关系", "商品交易关系", "债权债务关系", "贸易背景"),
        },
        {
            "fact_id": "asks_discount_materials",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("材料", "增值税发票", "商品发运单据", "贴现凭证"),
        },
        {
            "fact_id": "asks_transfer_endorsement",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("转让背书", "背书"),
        },
        {
            "fact_id": "holder_scope_hit",
            "source": "evidence",
            "mode": "actor_scope",
        },
        {
            "fact_id": "acceptance_definition_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("提示承兑是指", "要求付款人承诺付款", "向付款人出示汇票"),
        },
        {
            "fact_id": "acceptance_prompt_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("提示承兑", "向付款人提示承兑", "要求付款人承诺付款"),
        },
        {
            "fact_id": "acceptance_timing_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "到期日前向付款人提示承兑",
                "自出票日起1个月内向付款人提示承兑",
                "见票后定期付款",
                "定日付款或者出票后定期付款",
                "到期日前",
                "1个月内",
            ),
        },
        {
            "fact_id": "acceptance_receipt_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("收到汇票的回单", "签发收到汇票的回单", "提示承兑日期", "记明汇票提示承兑日期并签章"),
        },
        {
            "fact_id": "acceptance_decision_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("3日内承兑或者拒绝承兑", "3日内承兑或拒绝承兑", "自收到提示承兑的汇票之日起3日内"),
        },
        {
            "fact_id": "refusal_proof_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("拒绝承兑的证明", "拒绝证明", "退票理由书"),
        },
        {
            "fact_id": "conditional_acceptance_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("不得附有条件", "视为拒绝承兑", "承兑附有条件"),
        },
        {
            "fact_id": "discount_application_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("申请贴现", "向银行申请贴现", "未到期的商业汇票", "贴现凭证"),
        },
        {
            "fact_id": "discount_eligibility_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("在银行开立存款账户的企业法人以及其他组织", "企业法人以及其他组织", "在银行开立存款账户", "符合条件的商业汇票持票人"),
        },
        {
            "fact_id": "trade_background_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("真实的商品交易关系", "真实的交易关系", "商品交易关系", "债权债务关系"),
        },
        {
            "fact_id": "discount_materials_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("增值税发票", "商品发运单据复印件", "贴现凭证"),
        },
        {
            "fact_id": "transfer_endorsement_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("转让背书", "作成转让背书"),
        },
    ),
    "rule_specs": (
        {
            "rule_id": "commercial_holder_scope_rule",
            "priority": 100,
            "applies_if": {"fact": "holder_scope_requested", "eq": True},
            "requires": {"fact": "holder_scope_hit", "eq": True},
            "on_fail": {
                "dimension": "主体范围",
                "reason": "当前问题带有明确主体约束，但证据中尚未稳定覆盖该主体的直接规则。",
                "gap_type": "主体范围缺口",
                "impact_scope": "全局阻断",
                "severity": "阻断型",
                "handling": "必须继续补召回，先稳定主体适用范围后再判断。",
                "judgement_condition": "当办理判断依赖特定主体范围，而证据未覆盖该主体时，不能直接输出最终结论。",
                "conclusion_hint": "证据不足待补召回",
                "atom_label": "主体适用范围",
                "recall_directions": (
                    ("A", "沿当前商业汇票场景继续下钻主体适用规则。"),
                    ("D", "补齐持票人、付款人、承兑银行等主体边界。"),
                ),
            },
        },
        {
            "rule_id": "acceptance_definition_rule",
            "priority": 98,
            "applies_if": {"fact": "asks_acceptance_definition", "eq": True},
            "requires": {"fact": "acceptance_definition_hit", "eq": True},
            "on_fail": {
                "dimension": "提示承兑定义",
                "reason": "问题直接询问提示承兑的含义，但当前证据未覆盖提示承兑的法定定义。",
                "gap_type": "定义范围缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回提示承兑定义条款；候选耗尽时转人工复核。",
                "judgement_condition": "当问题要求先界定提示承兑含义，而证据未覆盖法定定义时，不能输出稳定解释。",
                "conclusion_hint": "证据不足待补召回",
                "atom_label": "提示承兑定义",
                "recall_directions": (
                    ("D", "补齐提示承兑定义、行为对象和法律含义。"),
                    ("F", "补充票据法中的定义性依据。"),
                ),
            },
        },
        {
            "rule_id": "acceptance_prompt_rule",
            "priority": 96,
            "applies_if": {
                "all": [
                    {"fact": "asks_acceptance_path", "eq": True},
                    {"not": {"fact": "asks_acceptance_definition", "eq": True}},
                ]
            },
            "requires": {"fact": "acceptance_prompt_hit", "eq": True},
            "on_fail": {
                "dimension": "提示承兑动作",
                "reason": "问题涉及商业汇票提示承兑，但当前证据未覆盖向付款人提示承兑这一核心动作。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回提示承兑动作、对象和基本流程；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖提示承兑动作，而证据未覆盖向付款人提示承兑这一行为时，不能输出稳定子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "提示承兑动作",
                "recall_directions": (
                    ("A", "沿当前场景继续检索提示承兑流程。"),
                    ("C", "补相邻条款中的提示承兑操作细则。"),
                ),
            },
        },
        {
            "rule_id": "acceptance_timing_rule",
            "priority": 94,
            "applies_if": {"fact": "asks_acceptance_timing", "eq": True},
            "requires": {"fact": "acceptance_timing_hit", "eq": True},
            "on_fail": {
                "dimension": "提示承兑期限",
                "reason": "问题直接询问提示承兑期限，但当前证据未覆盖到期日前或出票后1个月内等时限规则。",
                "gap_type": "时限阈值缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回提示承兑期限和适用票据类型；候选耗尽时转人工复核。",
                "judgement_condition": "当问题直接询问提示承兑时限，而证据未覆盖到期日前或1个月内等边界时，不能输出确定性子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "提示承兑期限",
                "recall_directions": (
                    ("D", "补齐到期日前、出票后1个月内等时限规则。"),
                    ("F", "补充票据法与支付结算办法中的期限依据。"),
                ),
            },
        },
        {
            "rule_id": "acceptance_receipt_rule",
            "priority": 92,
            "applies_if": {"fact": "asks_acceptance_receipt", "eq": True},
            "requires": {"fact": "acceptance_receipt_hit", "eq": True},
            "on_fail": {
                "dimension": "提示承兑回单",
                "reason": "问题涉及提示承兑后的回单要求，但当前证据未覆盖回单签发、日期记载或签章要求。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回回单签发、日期记载和签章要求；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖提示承兑回单及其记载要求，而证据未覆盖相关动作时，不能输出稳定子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "提示承兑回单",
                "recall_directions": (
                    ("D", "补齐回单签发、提示承兑日期和签章规则。"),
                    ("C", "补相邻条款中的承兑受理动作。"),
                ),
            },
        },
        {
            "rule_id": "acceptance_decision_rule",
            "priority": 90,
            "applies_if": {"fact": "asks_acceptance_decision", "eq": True},
            "requires": {"fact": "acceptance_decision_hit", "eq": True},
            "on_fail": {
                "dimension": "承兑/拒承时限",
                "reason": "问题直接询问付款人多久内承兑或拒绝承兑，但当前证据未覆盖3日内处理规则。",
                "gap_type": "时限阈值缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回3日内承兑或拒承规则；候选耗尽时转人工复核。",
                "judgement_condition": "当问题直接询问承兑或拒承时限，而证据未覆盖3日内处理边界时，不能输出确定性子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "承兑/拒承时限",
                "recall_directions": (
                    ("D", "补齐收到提示承兑汇票后3日内处理规则。"),
                    ("F", "补充票据法中的承兑处理期限依据。"),
                ),
            },
        },
        {
            "rule_id": "refusal_proof_rule",
            "priority": 88,
            "applies_if": {"fact": "asks_refusal_proof", "eq": True},
            "requires": {"fact": "refusal_proof_hit", "eq": True},
            "on_fail": {
                "dimension": "拒绝承兑证明",
                "reason": "问题涉及拒绝承兑后的证明要求，但当前证据未覆盖拒绝证明或退票理由书。",
                "gap_type": "材料缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回拒绝承兑证明、拒绝证明或退票理由书规则；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖拒绝承兑后的证明文件，而证据未覆盖相关材料时，不能输出稳定子结论。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "拒绝承兑证明",
                "recall_directions": (
                    ("D", "补齐拒绝承兑证明、拒绝证明、退票理由书规则。"),
                    ("C", "补相邻条款中的拒承后续处理。"),
                ),
            },
        },
        {
            "rule_id": "conditional_acceptance_rule",
            "priority": 86,
            "applies_if": {"fact": "asks_conditional_acceptance", "eq": True},
            "requires": {"fact": "conditional_acceptance_hit", "eq": True},
            "on_fail": {
                "dimension": "附条件承兑边界",
                "reason": "问题涉及是否可以附条件承兑，但当前证据未覆盖不得附有条件及视为拒绝承兑的边界。",
                "gap_type": "例外/禁止缺口",
                "impact_scope": "全局阻断",
                "severity": "阻断型",
                "handling": "必须继续补召回附条件承兑的禁止边界，不能直接下最终结论。",
                "judgement_condition": "当结论涉及附条件承兑是否成立，而证据未覆盖禁止边界和法律后果时，不能输出确定性结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "附条件承兑边界",
                "recall_directions": (
                    ("E", "优先补不得附有条件及视为拒绝承兑规则。"),
                    ("F", "补充票据法中的承兑效力边界。"),
                ),
            },
        },
        {
            "rule_id": "discount_application_rule",
            "priority": 84,
            "applies_if": {"fact": "asks_discount_path", "eq": True},
            "requires": {"fact": "discount_application_hit", "eq": True},
            "on_fail": {
                "dimension": "贴现申请动作",
                "reason": "问题涉及商业汇票贴现，但当前证据未覆盖持未到期汇票及贴现凭证向银行申请贴现这一核心动作。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回贴现申请动作、未到期汇票和贴现凭证要求；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖贴现申请动作，而证据未覆盖申请路径与基本凭证时，不能输出稳定子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "贴现申请动作",
                "recall_directions": (
                    ("A", "沿当前场景继续检索贴现申请流程。"),
                    ("D", "补齐未到期商业汇票与贴现凭证规则。"),
                ),
            },
        },
        {
            "rule_id": "discount_eligibility_rule",
            "priority": 82,
            "applies_if": {
                "any": [
                    {"fact": "asks_discount_eligibility", "eq": True},
                    {"fact": "asks_discount_path", "eq": True},
                ]
            },
            "requires": {"fact": "discount_eligibility_hit", "eq": True},
            "on_fail": {
                "dimension": "贴现主体资格",
                "reason": "问题涉及商业汇票贴现条件，但当前证据未覆盖在银行开户的企业法人或其他组织等准入主体要求。",
                "gap_type": "主体范围缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回贴现主体准入条件；候选耗尽时转人工复核。",
                "judgement_condition": "当贴现判断依赖申请主体资格，而证据未覆盖开户主体及组织类型要求时，不能输出稳定子结论。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "贴现主体资格",
                "recall_directions": (
                    ("D", "补齐在银行开户、企业法人或其他组织等准入规则。"),
                    ("A", "沿当前场景继续检索贴现主体边界。"),
                ),
            },
        },
        {
            "rule_id": "trade_background_rule",
            "priority": 80,
            "applies_if": {
                "any": [
                    {"fact": "asks_trade_background", "eq": True},
                    {"fact": "asks_discount_path", "eq": True},
                ]
            },
            "requires": {"fact": "trade_background_hit", "eq": True},
            "on_fail": {
                "dimension": "真实交易背景",
                "reason": "问题涉及商业汇票贴现条件，但当前证据未覆盖真实商品交易关系或债权债务关系。",
                "gap_type": "判断条件缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回真实交易关系、商品交易关系和债权债务关系规则；候选耗尽时转人工复核。",
                "judgement_condition": "当贴现判断依赖真实交易背景，而证据未覆盖贸易背景真实性要求时，不能输出确定性子结论。",
                "conclusion_hint": "需人工复核",
                "atom_label": "真实交易背景",
                "recall_directions": (
                    ("D", "补齐真实交易关系、商品交易关系和债权债务关系规则。"),
                    ("E", "优先补贸易背景真实性审查条款。"),
                ),
            },
        },
        {
            "rule_id": "discount_materials_rule",
            "priority": 78,
            "applies_if": {
                "any": [
                    {"fact": "asks_discount_materials", "eq": True},
                    {"fact": "asks_discount_path", "eq": True},
                ]
            },
            "requires": {"fact": "discount_materials_hit", "eq": True},
            "on_fail": {
                "dimension": "贴现申请材料",
                "reason": "问题涉及贴现所需材料，但当前证据未覆盖增值税发票、商品发运单据或贴现凭证。",
                "gap_type": "材料缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回增值税发票、商品发运单据复印件和贴现凭证；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖贴现申请材料，而证据未覆盖关键凭证时，不能直接输出可办理。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "贴现申请材料",
                "recall_directions": (
                    ("D", "补齐增值税发票、商品发运单据复印件和贴现凭证规则。"),
                    ("C", "补相邻条款中的材料清单细则。"),
                ),
            },
        },
        {
            "rule_id": "transfer_endorsement_rule",
            "priority": 76,
            "applies_if": {
                "any": [
                    {"fact": "asks_transfer_endorsement", "eq": True},
                    {"fact": "asks_discount_path", "eq": True},
                ]
            },
            "requires": {"fact": "transfer_endorsement_hit", "eq": True},
            "on_fail": {
                "dimension": "转让背书动作",
                "reason": "问题涉及贴现/转贴现/再贴现办理，但当前证据未覆盖作成转让背书这一关键动作。",
                "gap_type": "流程动作缺口",
                "impact_scope": "子结论阻断",
                "severity": "关键型",
                "handling": "优先补召回转让背书动作及其配套材料要求；候选耗尽时转人工复核。",
                "judgement_condition": "当办理结论依赖转让背书动作，而证据未覆盖作成转让背书这一要件时，不能输出稳定子结论。",
                "conclusion_hint": "需补材料后办理",
                "atom_label": "转让背书动作",
                "recall_directions": (
                    ("D", "补齐作成转让背书及相关材料规则。"),
                    ("C", "补相邻条款中的贴现操作细则。"),
                ),
            },
        },
    ),
}


BANK_NOTE_LIFECYCLE_PROFILE = {
    "profile_id": "bank_note_lifecycle",
    "title": "Bank Note Lifecycle",
    "scene_names": ("银行本票",),
    "question_keywords": ("银行本票", "本票"),
    "min_evidence_count": 5,
    "min_direct_basis_count": 3,
    "derived_facts": (
        {
            "fact_id": "holder_scope_requested",
            "source": "who_terms",
            "mode": "non_empty",
        },
        {
            "fact_id": "non_account_holder_requested",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("未在银行开立存款账户", "未开户", "个人持票人"),
        },
        {
            "fact_id": "account_holder_requested",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("在银行开立存款账户", "开户银行", "进账单"),
        },
        {
            "fact_id": "asks_definition_use_scope",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("什么是银行本票", "银行本票是什么", "适用范围", "票据交换区域"),
        },
        {
            "fact_id": "asks_note_type_cash_wording",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("现金字样", "转账字样", "支付金额栏", "定额本票", "不定额本票"),
        },
        {
            "fact_id": "asks_mandatory_fields",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("记载事项", "法定事项", "无效", "出票人签章", "收款人名称"),
        },
        {
            "fact_id": "asks_issuance_path",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("申请书", "申请银行本票", "签发银行本票", "出票", "交付收款人"),
        },
        {
            "fact_id": "asks_cash_note_boundary",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("现金银行本票", "现金字样", "支取现金", "申请人为单位", "收款人为单位"),
        },
        {
            "fact_id": "asks_presentment_review",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("见票即付", "审查", "审核", "审查要点"),
        },
        {
            "fact_id": "asks_presentment_deadline",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("提示付款期限", "2个月", "超过提示付款期限", "逾期"),
        },
        {
            "fact_id": "asks_transfer_endorsement",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("背书", "转让", "被背书人"),
        },
        {
            "fact_id": "asks_cash_withdrawal",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("支取现金", "现金", "身份证件"),
        },
        {
            "fact_id": "asks_entrusted_presentment",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("委托他人", "委托收款", "被委托人", "代为提示付款"),
        },
        {
            "fact_id": "asks_overdue_remedy",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("超过提示付款期限", "不获付款", "作出说明", "请求付款"),
        },
        {
            "fact_id": "asks_refund_path",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("退款", "退付", "原申请人账户"),
        },
        {
            "fact_id": "asks_loss_remedy",
            "source": "question",
            "mode": "keyword_any",
            "keywords": ("丧失", "挂失止付", "失票", "人民法院", "票据权利证明"),
        },
        {
            "fact_id": "holder_scope_hit",
            "source": "evidence",
            "mode": "actor_scope",
        },
        {
            "fact_id": "definition_use_scope_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "银行签发的，承诺自己在见票时无条件支付",
                "同一票据交换区域",
                "可以用于转账",
                "注明“现金”字样的银行本票可以用于支取现金",
            ),
        },
        {
            "fact_id": "note_type_cash_wording_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "定额本票和不定额本票",
                "支付金额栏先填写“现金”字样",
                "划去“现金”字样",
                "划去“转账”字样",
            ),
        },
        {
            "fact_id": "mandatory_fields_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "表明“银行本票”的字样",
                "无条件支付的承诺",
                "确定的金额",
                "收款人名称",
                "出票日期",
                "出票人签章",
                "银行本票无效",
            ),
        },
        {
            "fact_id": "issuance_application_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "银行本票申请书",
                "填写收款人名称",
                "申请人名称",
                "支付金额",
                "申请日期",
                "并签章",
            ),
        },
        {
            "fact_id": "issuance_action_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "收妥款项签发银行本票",
                "签章后交给申请人",
                "交付给本票上记明的收款人",
            ),
        },
        {
            "fact_id": "cash_unit_prohibition_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("不得签发现金银行本票", "申请人或收款人为单位"),
        },
        {
            "fact_id": "presentment_review_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "受理银行本票时，应审查",
                "收款人是否确为本单位或本人",
                "必须记载的事项是否齐全",
                "出票金额、出票日期、收款人名称是否更改",
            ),
        },
        {
            "fact_id": "signature_amount_consistency_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("签章是否符合规定", "压数机压印的出票金额", "与大写出票金额一致"),
        },
        {
            "fact_id": "prompt_payment_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("银行本票见票即付",),
        },
        {
            "fact_id": "presentment_deadline_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("提示付款期限自出票日起最长不得超过2个月", "是否在提示付款期限内"),
        },
        {
            "fact_id": "account_holder_presentment_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "在银行开立存款账户的持票人",
                "提示付款签章",
                "预留银行签章相同",
                "银行本票、进账单送交开户银行",
            ),
        },
        {
            "fact_id": "cash_withdrawal_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "未在银行开立存款账户的个人持票人",
                "注明“现金”字样的银行本票",
                "向出票银行支取现金",
                "交验本人身份证件及其复印件",
            ),
        },
        {
            "fact_id": "entrusted_presentment_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("委托收款", "被委托人姓名", "背书日期", "委托人身份证件名称"),
        },
        {
            "fact_id": "entrusted_agent_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "被委托人向出票银行提示付款时",
                "交验委托人和被委托人的身份证件及其复印件",
            ),
        },
        {
            "fact_id": "transfer_endorsement_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("可将银行本票背书转让", "背书连续", "背书人为个人的身份证件"),
        },
        {
            "fact_id": "transfer_region_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("仅限于在其票据交换区域内背书转让",),
        },
        {
            "fact_id": "cash_transfer_prohibition_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("填明“现金”字样的银行本票不得背书转让",),
        },
        {
            "fact_id": "overdue_payment_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "超过提示付款期限不获付款",
                "在票据权利时效内",
                "作出说明",
                "提供本人身份证件或单位证明",
                "向出票银行请求付款",
            ),
        },
        {
            "fact_id": "refund_path_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": (
                "要求退款",
                "应将银行本票提交到出票银行",
                "单位的证明",
                "本人的身份证件",
                "只能将款项转入原申请人账户",
                "才能退付现金",
            ),
        },
        {
            "fact_id": "loss_stop_payment_permission_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("银行本票丧失", "通知付款人或者代理付款人挂失止付"),
        },
        {
            "fact_id": "loss_stop_payment_prohibition_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("未填明“现金”字样的银行本票丧失，不得挂失止付",),
        },
        {
            "fact_id": "loss_court_proof_hit",
            "source": "evidence",
            "mode": "keyword_any",
            "keywords": ("人民法院出具的其享有票据权利的证明", "请求付款或退款"),
        },
    ),
    "rule_specs": (
        {
            "rule_id": "bank_note_holder_scope_rule",
            "priority": 100,
            "applies_if": {"fact": "holder_scope_requested", "eq": True},
            "requires": {"fact": "holder_scope_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="主体范围",
                reason="当前问题带有明确主体约束，但证据中尚未稳定覆盖持票人、申请人或出票银行的适用边界。",
                gap_type="主体范围缺口",
                impact_scope="全局阻断",
                conclusion_hint="证据不足待补召回",
                atom_label="主体适用范围",
                recall_directions=(
                    ("A", "沿当前银行本票场景继续下钻主体适用规则。"),
                    ("D", "补齐持票人、申请人、出票银行及被委托人的主体边界。"),
                ),
                handling="必须先补齐主体范围，再进入银行本票的具体业务判断。",
            ),
        },
        {
            "rule_id": "bank_note_definition_scope_rule",
            "priority": 98,
            "applies_if": {"fact": "asks_definition_use_scope", "eq": True},
            "requires": {"fact": "definition_use_scope_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="银行本票定义/适用范围",
                reason="问题直接询问银行本票的定义、适用对象或票据交换区域，但证据尚未覆盖基础法律属性。",
                gap_type="定义范围缺口",
                impact_scope="子结论阻断",
                conclusion_hint="证据不足待补召回",
                atom_label="银行本票定义/适用范围",
                recall_directions=(
                    ("D", "补齐银行本票定义、转账/现金用途和票据交换区域规则。"),
                    ("F", "补充票据法及支付结算办法中的定义性依据。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_type_cash_wording_rule",
            "priority": 96,
            "applies_if": {"fact": "asks_note_type_cash_wording", "eq": True},
            "requires": {"fact": "note_type_cash_wording_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="本票种类/现金字样",
                reason="问题涉及定额/不定额、现金/转账字样或支付金额栏填写，但证据尚未覆盖票面类型与字样处理规则。",
                gap_type="判断条件缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需人工复核",
                atom_label="本票种类/现金字样",
                recall_directions=(
                    ("D", "补齐定额/不定额本票、现金字样和转账字样的票面处理规则。"),
                    ("C", "补相邻条款中的申请书填写细则。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_mandatory_fields_rule",
            "priority": 94,
            "applies_if": {"fact": "asks_mandatory_fields", "eq": True},
            "requires": {"fact": "mandatory_fields_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="法定记载事项/效力",
                reason="问题直接询问票面必须记载事项或缺项后的效力，但证据未完整覆盖六项法定记载事项及无效后果。",
                gap_type="规范依据缺口",
                impact_scope="全局阻断",
                conclusion_hint="证据不足待补召回",
                atom_label="法定记载事项/效力",
                recall_directions=(
                    ("D", "补齐银行本票六项法定记载事项及缺项无效规则。"),
                    ("F", "补充票据法和支付结算办法中的效力条款。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_issuance_application_rule",
            "priority": 92,
            "applies_if": {"fact": "asks_issuance_path", "eq": True},
            "requires": {
                "all": [
                    {"fact": "issuance_application_hit", "eq": True},
                    {"fact": "issuance_action_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="出票申请与签发流程",
                reason="问题涉及银行本票申请、出票或交付流程，但证据尚未闭合申请书填写与出票银行签发动作。",
                gap_type="流程动作缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需人工复核",
                atom_label="出票申请与签发流程",
                recall_directions=(
                    ("A", "沿银行本票开立场景继续补召回申请书和签发流程。"),
                    ("D", "补齐申请书字段、收妥款项签发及交付收款人规则。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_cash_boundary_rule",
            "priority": 90,
            "applies_if": {"fact": "asks_cash_note_boundary", "eq": True},
            "requires": {
                "all": [
                    {"fact": "note_type_cash_wording_hit", "eq": True},
                    {"fact": "cash_unit_prohibition_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="现金银行本票签发边界",
                reason="问题涉及现金银行本票、现金字样或是否可以为单位签发，但证据未完整覆盖现金本票票面规则和单位禁止边界。",
                gap_type="例外/禁止缺口",
                impact_scope="全局阻断",
                conclusion_hint="需人工复核",
                atom_label="现金银行本票签发边界",
                recall_directions=(
                    ("E", "优先补现金银行本票签发限制和单位禁止规则。"),
                    ("D", "补齐现金/转账字样的票面处理要求。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_presentment_review_rule",
            "priority": 88,
            "applies_if": {"fact": "asks_presentment_review", "eq": True},
            "requires": {
                "all": [
                    {"fact": "presentment_review_hit", "eq": True},
                    {"fact": "signature_amount_consistency_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="提示付款审查要点",
                reason="问题涉及银行本票提示付款的审查、审核或真实性校验，但证据尚未覆盖审查清单和金额一致性核验。",
                gap_type="事实核验缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需人工复核",
                atom_label="提示付款审查要点",
                recall_directions=(
                    ("D", "补齐受理银行本票时的五项审查要点。"),
                    ("C", "补相邻条款中的签章合规和压数金额一致性规则。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_presentment_deadline_rule",
            "priority": 86,
            "applies_if": {
                "any": [
                    {"fact": "asks_presentment_review", "eq": True},
                    {"fact": "asks_presentment_deadline", "eq": True},
                ]
            },
            "requires": {
                "all": [
                    {"fact": "prompt_payment_hit", "eq": True},
                    {"fact": "presentment_deadline_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="见票即付/提示付款期限",
                reason="问题涉及银行本票何时付款或提示付款期限，但证据尚未同时覆盖见票即付属性与两个月期限边界。",
                gap_type="时限阈值缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需人工复核",
                atom_label="见票即付/提示付款期限",
                recall_directions=(
                    ("D", "补齐银行本票见票即付和提示付款期限最长两个月规则。"),
                    ("F", "补充票据法和支付结算办法中的时限依据。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_account_holder_presentment_rule",
            "priority": 84,
            "applies_if": {
                "all": [
                    {"fact": "asks_presentment_review", "eq": True},
                    {"fact": "account_holder_requested", "eq": True},
                ]
            },
            "requires": {"fact": "account_holder_presentment_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="开户持票人提示付款",
                reason="问题限定为已开户持票人通过开户银行提示付款，但证据尚未覆盖签章、进账单和开户银行受理动作。",
                gap_type="流程动作缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需补材料后办理",
                atom_label="开户持票人提示付款",
                recall_directions=(
                    ("D", "补齐开户持票人的签章、进账单和开户银行受理规则。"),
                    ("A", "沿当前提示付款场景继续补召回开户银行路径。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_cash_withdrawal_rule",
            "priority": 82,
            "applies_if": {
                "any": [
                    {"fact": "asks_cash_withdrawal", "eq": True},
                    {"fact": "non_account_holder_requested", "eq": True},
                ]
            },
            "requires": {
                "all": [
                    {"fact": "note_type_cash_wording_hit", "eq": True},
                    {"fact": "cash_withdrawal_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="未开户个人现金支取",
                reason="问题涉及未开户个人凭现金银行本票支取现金，但证据未完整覆盖现金字样、签章和身份证件要求。",
                gap_type="材料缺口",
                impact_scope="全局阻断",
                conclusion_hint="需补材料后办理",
                atom_label="未开户个人现金支取",
                recall_directions=(
                    ("D", "补齐现金字样本票、本人签章、身份证件及复印件规则。"),
                    ("E", "优先补现金支取条件与主体限制条款。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_entrusted_presentment_rule",
            "priority": 80,
            "applies_if": {"fact": "asks_entrusted_presentment", "eq": True},
            "requires": {
                "all": [
                    {"fact": "entrusted_presentment_hit", "eq": True},
                    {"fact": "entrusted_agent_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="委托提示付款",
                reason="问题涉及持票人委托他人提示付款，但证据尚未同时覆盖委托收款记载和被委托人身份核验动作。",
                gap_type="流程动作缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需补材料后办理",
                atom_label="委托提示付款",
                recall_directions=(
                    ("D", "补齐委托收款记载、被委托人签章和双方身份证件要求。"),
                    ("C", "补相邻条款中的委托提示付款细则。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_transfer_boundary_rule",
            "priority": 78,
            "applies_if": {"fact": "asks_transfer_endorsement", "eq": True},
            "requires": {
                "all": [
                    {"fact": "transfer_endorsement_hit", "eq": True},
                    {"fact": "transfer_region_hit", "eq": True},
                    {"fact": "cash_transfer_prohibition_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="背书转让边界",
                reason="问题涉及银行本票背书转让，但证据未同时覆盖一般可转让规则、票据交换区域限制和现金本票禁止转让边界。",
                gap_type="例外/禁止缺口",
                impact_scope="全局阻断",
                conclusion_hint="需人工复核",
                atom_label="背书转让边界",
                recall_directions=(
                    ("D", "补齐银行本票背书转让、区域限制和现金本票禁止转让规则。"),
                    ("E", "优先补禁止性条款与允许条款之间的边界。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_overdue_payment_rule",
            "priority": 76,
            "applies_if": {"fact": "asks_overdue_remedy", "eq": True},
            "requires": {
                "all": [
                    {"fact": "presentment_deadline_hit", "eq": True},
                    {"fact": "overdue_payment_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="逾期提示付款救济",
                reason="问题涉及超过提示付款期限后的付款请求，但证据尚未覆盖逾期说明、身份证明和出票银行继续付款路径。",
                gap_type="判断条件缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需补材料后办理",
                atom_label="逾期提示付款救济",
                recall_directions=(
                    ("D", "补齐逾期不获付款后的说明、身份证件或单位证明要求。"),
                    ("F", "补充票据权利时效内仍可请求付款的规范依据。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_refund_rule",
            "priority": 74,
            "applies_if": {"fact": "asks_refund_path", "eq": True},
            "requires": {"fact": "refund_path_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="银行本票退款路径",
                reason="问题涉及申请人退款或退付方式，但证据尚未覆盖提交银行本票、证明材料和原账户/现金退付边界。",
                gap_type="材料缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需补材料后办理",
                atom_label="银行本票退款路径",
                recall_directions=(
                    ("D", "补齐申请人提交本票、单位证明/身份证件和退付方式规则。"),
                    ("C", "补相邻条款中的原账户转账与现金退付边界。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_loss_stop_payment_rule",
            "priority": 72,
            "applies_if": {"fact": "asks_loss_remedy", "eq": True},
            "requires": {
                "all": [
                    {"fact": "loss_stop_payment_permission_hit", "eq": True},
                    {"fact": "loss_stop_payment_prohibition_hit", "eq": True},
                ]
            },
            "on_fail": _scene_gap(
                dimension="挂失止付边界",
                reason="问题涉及银行本票丧失后的挂失止付，但证据尚未同时覆盖现金本票可挂失与非现金本票不得挂失的边界。",
                gap_type="例外/禁止缺口",
                impact_scope="全局阻断",
                conclusion_hint="需人工复核",
                atom_label="挂失止付边界",
                recall_directions=(
                    ("E", "优先补现金本票允许挂失止付与非现金本票禁止挂失止付规则。"),
                    ("F", "补充失票救济中的挂失止付规范依据。"),
                ),
            ),
        },
        {
            "rule_id": "bank_note_loss_court_proof_rule",
            "priority": 70,
            "applies_if": {"fact": "asks_loss_remedy", "eq": True},
            "requires": {"fact": "loss_court_proof_hit", "eq": True},
            "on_fail": _scene_gap(
                dimension="失票法院证明救济",
                reason="问题涉及银行本票丧失后的付款或退款请求，但证据尚未覆盖人民法院权利证明这一关键材料。",
                gap_type="材料缺口",
                impact_scope="子结论阻断",
                conclusion_hint="需补材料后办理",
                atom_label="失票法院证明救济",
                recall_directions=(
                    ("D", "补齐人民法院出具票据权利证明后请求付款或退款规则。"),
                    ("C", "补相邻条款中的失票救济材料清单。"),
                ),
            ),
        },
    ),
}


SCENE_PROFILE_CATALOG = (
    BANK_DRAFT_PRESENTMENT_PROFILE,
    COMMERCIAL_BILL_ACCEPTANCE_DISCOUNT_PROFILE,
    BANK_NOTE_LIFECYCLE_PROFILE,
)
