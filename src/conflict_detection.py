from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Any

import pandas as pd

from data_loader import clean_text


RULE_TYPE_POLARITY = {
    "PRO_FORBIDDEN": "forbid",
    "PER_AUTH": "allow",
    "OBL_MANDATORY": "require",
    "OBL_ONGOING": "require",
    "PRC_FLOW": "require",
    "RPT_DISCLOSURE": "require",
}

ACTIONABLE_RULE_TYPES = {
    "PRO_FORBIDDEN",
    "PER_AUTH",
    "OBL_MANDATORY",
    "OBL_ONGOING",
    "PRC_FLOW",
    "RPT_DISCLOSURE",
    "VAL_THRESHOLD",
}

FORBID_KEYWORDS = ("不得", "禁止", "不予", "中止", "暂停", "只收不付")
ALLOW_KEYWORDS = ("可以", "允许", "可恢复", "可办理", "可申请", "恢复")
GENERIC_OBJECTS = {"票据", "账户", "业务", "信息", "事项", "材料", "内容", "规定"}
ARABIC_NUMERIC_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(个|户|次|笔|日|天|月|年|小时|分钟|万元|元|%|倍)")
CHINESE_NUMERIC_TOKEN_RE = re.compile(r"([零〇一二两三四五六七八九十百千万]+)\s*(个|户|次|笔|日|天|月|年|小时|分钟|万元|元|%|倍)")


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        try:
            parsed = ast.literal_eval(text)
        except Exception:
            parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [text] if text else []


def _normalize_text(value: Any) -> str:
    text = clean_text(str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：、,.!！?？“”\"'‘’（）()【】\[\]<>《》/\\\-]", "", text)
    return text


def _normalize_display(value: Any) -> str:
    return clean_text(str(value or "")).strip()


def _chinese_number_to_int(text: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not text:
        return None
    if all(char in digits for char in text):
        value = 0
        for char in text:
            value = value * 10 + digits[char]
        return value

    total = 0
    section = 0
    number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif char in units:
            unit = units[char]
            if unit == 10000:
                section = (section + max(number, 1)) * unit
                total += section
                section = 0
            else:
                section += max(number, 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def _normalize_numeric_token(number_text: str, unit_text: str) -> str:
    raw_number = str(number_text or "").strip()
    raw_unit = str(unit_text or "").strip()
    if not raw_number:
        return ""
    if raw_number.isdigit():
        normalized_number = raw_number
    else:
        parsed = _chinese_number_to_int(raw_number)
        normalized_number = str(parsed) if parsed is not None else raw_number
    return f"{normalized_number}{raw_unit}"


def _extract_numeric_signatures(text: Any) -> list[str]:
    signatures = []
    seen = set()
    raw_text = str(text or "")
    for number_text, unit_text in ARABIC_NUMERIC_TOKEN_RE.findall(raw_text):
        normalized = _normalize_numeric_token(number_text, unit_text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            signatures.append(normalized)
    for number_text, unit_text in CHINESE_NUMERIC_TOKEN_RE.findall(raw_text):
        normalized = _normalize_numeric_token(number_text, unit_text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            signatures.append(normalized)
    return signatures


def _derive_polarity(rule_type: str, how_text: str, content_text: str) -> str:
    polarity = RULE_TYPE_POLARITY.get(rule_type, "neutral")
    signal_text = f"{how_text} {content_text}"
    if any(keyword in signal_text for keyword in FORBID_KEYWORDS):
        return "forbid"
    if any(keyword in signal_text for keyword in ALLOW_KEYWORDS):
        return "allow"
    return polarity


def _build_scope_key(who_text: str, what_text: str) -> str:
    who_key = _normalize_text(who_text)
    what_key = _normalize_text(what_text)
    if not who_key or not what_key:
        return ""
    if len(what_key) < 4 or what_key in GENERIC_OBJECTS:
        return ""
    return f"{who_key}|{what_key}"


def _standardize_atoms_df(df: pd.DataFrame) -> pd.DataFrame:
    source = df.fillna("").copy()
    result = pd.DataFrame()
    result["atom_id"] = source.get("atom_id", "")
    result["source_document"] = source.get("source_document", "")
    result["article_reference"] = source.get("article_reference", "")
    result["rule_type"] = source.get("rule_type", "")
    result["who"] = source.get("who", source.get("who_text", ""))
    result["what"] = source.get("what", source.get("what_text", ""))
    result["how"] = source.get("how", source.get("how_text", ""))
    result["content_original"] = source.get("content_original", "")
    result["business_taxonomy_label_paths"] = source.get("business_taxonomy_label_paths", "")
    return result


def detect_atom_conflicts(df: pd.DataFrame) -> dict[str, Any]:
    atoms_df = _standardize_atoms_df(df)
    if atoms_df.empty:
        empty_df = pd.DataFrame()
        return {
            "summary": {
                "group_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "affected_atom_count": 0,
            },
            "group_df": empty_df,
            "detail_df": empty_df,
        }

    prepared_rows = []
    for row in atoms_df.to_dict(orient="records"):
        rule_type = _normalize_display(row.get("rule_type"))
        if rule_type not in ACTIONABLE_RULE_TYPES:
            continue
        who_text = _normalize_display(row.get("who"))
        what_text = _normalize_display(row.get("what"))
        how_text = _normalize_display(row.get("how"))
        scope_key = _build_scope_key(who_text, what_text)
        if not scope_key:
            continue
        prepared_rows.append(
            {
                "atom_id": _normalize_display(row.get("atom_id")),
                "source_document": _normalize_display(row.get("source_document")),
                "article_reference": _normalize_display(row.get("article_reference")),
                "rule_type": rule_type,
                "who": who_text,
                "what": what_text,
                "how": how_text,
                "content_original": _normalize_display(row.get("content_original")),
                "label_paths": _safe_list(row.get("business_taxonomy_label_paths")),
                "scope_key": scope_key,
                "polarity": _derive_polarity(rule_type, how_text, _normalize_display(row.get("content_original"))),
                "numeric_signatures": _extract_numeric_signatures(f"{what_text} {how_text}"),
            }
        )

    if not prepared_rows:
        empty_df = pd.DataFrame()
        return {
            "summary": {
                "group_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "affected_atom_count": 0,
            },
            "group_df": empty_df,
            "detail_df": empty_df,
        }

    prepared_df = pd.DataFrame(prepared_rows)
    conflict_rows = []
    detail_rows = []
    conflict_index = 1

    for scope_key, group_df in prepared_df.groupby("scope_key", sort=False):
        if len(group_df) < 2:
            continue

        polarity_counter = Counter(group_df["polarity"].tolist())
        subject = str(group_df.iloc[0]["who"])
        item_name = str(group_df.iloc[0]["what"])
        doc_count = int(group_df["source_document"].nunique())
        label_path_values = []
        for values in group_df["label_paths"].tolist():
            label_path_values.extend(values)
        label_path_values = [value for value in label_path_values if value]

        def append_conflict(conflict_type: str, risk_level: str, reason_text: str) -> None:
            nonlocal conflict_index
            conflict_id = f"CF-{conflict_index:04d}"
            conflict_index += 1
            conflict_rows.append(
                {
                    "冲突ID": conflict_id,
                    "冲突类型": conflict_type,
                    "风险级别": risk_level,
                    "主体": subject,
                    "事项": item_name,
                    "涉及规则数": len(group_df),
                    "涉及文档数": doc_count,
                    "涉及类目数": len(set(label_path_values)),
                    "说明": reason_text,
                }
            )
            for detail in group_df.to_dict(orient="records"):
                detail_rows.append(
                    {
                        "冲突ID": conflict_id,
                        "冲突类型": conflict_type,
                        "风险级别": risk_level,
                        "atom_id": detail["atom_id"],
                        "rule_type": detail["rule_type"],
                        "source_document": detail["source_document"],
                        "article_reference": detail["article_reference"],
                        "who": detail["who"],
                        "what": detail["what"],
                        "how": detail["how"],
                        "business_label_paths": " | ".join(detail["label_paths"][:3]),
                    }
                )

        if polarity_counter.get("forbid", 0) > 0 and polarity_counter.get("allow", 0) > 0:
            append_conflict(
                conflict_type="禁止/允许并存",
                risk_level="高",
                reason_text="同一主体、同一事项下同时出现禁止性规则与允许性规则，需人工核对适用条件、例外条款和时点差异。",
            )

        numeric_signatures = sorted(
            {
                signature
                for signatures in group_df["numeric_signatures"].tolist()
                for signature in signatures
                if signature
            }
        )
        if len(numeric_signatures) >= 2:
            append_conflict(
                conflict_type="阈值口径不一致",
                risk_level="中",
                reason_text=f"同一主体、同一事项下出现多个数值口径：{' / '.join(numeric_signatures[:5])}，需检查是否属于不同条件、不同期限或版本差异。",
            )

    group_result_df = pd.DataFrame(conflict_rows)
    detail_result_df = pd.DataFrame(detail_rows)
    if not group_result_df.empty:
        group_result_df = group_result_df.sort_values(
            by=["风险级别", "涉及规则数", "涉及文档数", "冲突ID"],
            ascending=[True, False, False, True],
        ).reset_index(drop=True)
    if not detail_result_df.empty:
        detail_result_df = detail_result_df.sort_values(
            by=["冲突ID", "风险级别", "rule_type", "atom_id"],
            ascending=[True, True, True, True],
        ).reset_index(drop=True)

    affected_atom_count = int(detail_result_df["atom_id"].nunique()) if not detail_result_df.empty else 0
    return {
        "summary": {
            "group_count": int(len(group_result_df)),
            "high_count": int((group_result_df["风险级别"] == "高").sum()) if not group_result_df.empty else 0,
            "medium_count": int((group_result_df["风险级别"] == "中").sum()) if not group_result_df.empty else 0,
            "affected_atom_count": affected_atom_count,
        },
        "group_df": group_result_df,
        "detail_df": detail_result_df,
    }
