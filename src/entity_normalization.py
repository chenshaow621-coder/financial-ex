from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_SHEET = "完整实体参考表"
NORMALIZATION_SHEET = "实体规范化对照表"

ACTOR_NORMALIZATION_CATEGORIES = ("机构简称", "主体别称")
OBJECT_NORMALIZATION_CATEGORIES = ("账户简称", "法规简称")
REFERENCE_CATEGORY_ALIASES = {
    "WHO": ("法律主体 (WHO)", "法律主体(WHO)", "法律主体", "LEGAL_ACTORS"),
    "WHAT": ("核心事项 (WHAT)", "核心事项(WHAT)", "核心事项", "WHAT_CONCEPTS"),
    "WHEN": ("时间情境 (WHEN)", "时间情境(WHEN)", "时间情境", "WHEN_CONTEXTS"),
    "WHERE": ("空间情境 (WHERE)", "空间情境(WHERE)", "空间情境", "WHERE_CONTEXTS"),
}
OBJECT_SUFFIX_HINTS = (
    "账户",
    "汇票",
    "本票",
    "支票",
    "票据",
    "银行卡",
    "信用卡",
    "存折",
    "存单",
    "凭证",
    "通知",
    "身份证",
    "证件",
    "证明",
    "合同",
    "协议",
    "资料",
    "信息",
    "记录",
    "清单",
    "文件",
    "执照",
    "许可证",
    "名称",
)
OBJECT_BLOCKLIST = {
    "文字", "标识", "地点", "相近", "销户", "签名", "后手", "承兑", "损失", "通知", "全体", "保证", "本法",
    "一般", "专用", "市", "机关", "军队", "资料", "证明", "规章", "法规", "工资", "奖金", "稿费", "继承", "赠",
    "证券", "期货", "信托", "账务", "幅度", "佣金", "经办", "查复", "涂改", "倒卖", "协调", "管理", "监督",
    "面签", "媒体", "折页", "微博", "收集", "情况", "其", "资金", "照片", "转账", "占比", "时性", "金额",
    "号码", "退款", "签发", "资信", "打孔", "剪角", "毁坏", "户名", "信", "航运", "全部", "规格", "数量",
    "价格", "货物", "丢失", "错投", "错拍", "漏拍", "重拍", "止付", "变更", "伪造",
}
TIME_BLOCKLIST = {
    "变更", "持续性", "开户", "签发", "票据签发", "法定代表人", "承付人", "承兑", "棉", "洗钱", "组织", "流通",
    "票据", "一般", "常态化", "逃匿", "设计", "考勤", "终止", "受托人", "托管人", "收款", "网络", "账户",
    "机构", "有效性", "后续", "同步", "全程", "导出", "任何时候", "事前", "事中", "事后",
}
TIME_HIGH_CONFIDENCE_PATTERNS = (
    r"[Tt]\s*[-+]\s*\d+\s*(?:个)?(?:工作日|营业日|自然日|交易日|日|天|周|月|年)?(?:内|以内|之内|前|后)?",
    r"\d{4}年\d{1,2}月\d{1,2}日(?:起|前|后|内|以内|之内)?",
    r"\d+[个]?(?:工作日|营业日|自然日|交易日|日|天|周|星期|个月|月|季度|年)(?:内|以内|之内|前|后)",
    r"(?:每|各)(?:日|周|星期|月|季度|年)(?:末|初)?",
    r"(?:当日|次日|当月|次月|当年|次年|每日|每月|每年|实时|及时|定期|立即|长期)",
    r".*(?:时|前|后|期间|期内|以内|之内|之后|之前)$",
)
TIME_FIXED_TERMS = (
    "当日",
    "次日",
    "当月",
    "次月",
    "当年",
    "次年",
    "每日",
    "每月",
    "每年",
    "实时",
    "及时",
    "定期",
    "立即",
    "长期",
)
TIME_SCENE_PATTERNS = (
    r".*时$",
    r".*前$",
    r".*后$",
    r".*期间$",
    r".*以内$",
    r".*之内$",
    r".*之后$",
    r".*之前$",
    r".*届满前$",
    r".*届满后$",
    r".*期内$",
)
TIME_REGEX_PATTERNS = (
    r"[Tt]\s*[-+]\s*\d+\s*(?:个)?(?:工作日|营业日|自然日|交易日|日|天|周|月|年)?(?:内|以内|之内|前|后)?",
    r"\d+[个]?(?:工作日|营业日|自然日|交易日|日|天|周|星期|个月|月|季度|年)(?:内|以内|之内|前|后)",
    r"(?:每|各)?(?:日|周|星期|月|季度|年)(?:末|初)?",
    r"(?:到期日?|截止日?|生效日?|失效日?|起始日?|终止日?)",
    r"(?:提示付款期限|提示付款期|付款期限|办理期限|报告期限|保存期限)(?:内|前|后|以内|之内)?",
)
TIME_SUFFIXES = ("之前", "之后", "期间", "期内", "以内", "之内", "日前", "日后", "前", "后", "时")
TIME_ACTION_LABELS = (
    ("申请开立", "申请开立"),
    ("开立", "开立"),
    ("办理", "办理"),
    ("受理", "受理"),
    ("签发", "签发"),
    ("提示付款", "提示付款"),
    ("提出付款请求", "提出付款请求"),
    ("付款", "付款"),
    ("承付", "承付"),
    ("审核", "审核"),
    ("核验", "核验"),
    ("核实", "核实"),
    ("重新识别", "重新识别"),
    ("识别", "识别"),
    ("签章", "签章"),
    ("贴现", "贴现"),
    ("取得", "取得"),
    ("收到", "收到"),
    ("行使追索权", "行使追索权"),
    ("提出", "提出"),
)
TIME_OBJECT_FAMILIES = (
    ("银行结算账户", "账户"),
    ("支付账户", "账户"),
    ("存款账户", "账户"),
    ("账户", "账户"),
    ("银行卡", "银行卡"),
    ("信用卡", "信用卡"),
    ("银行汇票", "汇票"),
    ("商业汇票", "汇票"),
    ("汇票", "汇票"),
    ("银行本票", "本票"),
    ("本票", "本票"),
    ("支票", "支票"),
    ("票据", "票据"),
    ("身份证明文件", "身份证明"),
    ("身份证件", "身份证明"),
    ("身份证", "身份证明"),
    ("身份资料", "身份资料"),
    ("客户身份", "身份资料"),
    ("通知", "通知"),
    ("证明文件", "材料"),
    ("文件", "材料"),
    ("资料", "材料"),
    ("凭证", "凭证"),
    ("现金", "现金"),
    ("交易", "交易"),
)


def _clean_value(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ").strip()
    if not text or text.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _lookup_key(value) -> str:
    return re.sub(r"\s+", "", _clean_value(value))


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _normalize_paren_whitespace(text: str) -> str:
    return (
        _clean_value(text)
        .replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
    )


def _contains_list_punctuation(text: str) -> bool:
    return any(token in text for token in ("、", "；", ";", "，", ",", "/", "／"))


def _is_low_value_object_term(term: str) -> bool:
    value = _clean_value(term)
    if not value or value in OBJECT_BLOCKLIST:
        return True
    if len(value) <= 1:
        return True
    if len(value) == 2 and value not in {"支票", "本票", "汇票", "票据", "现金"}:
        return True
    if re.fullmatch(r"[0-9A-Za-z+\-（）()]+", value):
        return True
    return False


def _is_confident_object_term(term: str) -> bool:
    value = _clean_value(term)
    if _is_low_value_object_term(value):
        return False
    if value.endswith(("人", "单位", "机关")):
        return False
    if any(token in value for token in ("开立的", "名下", "外的其他", "被冒用", "行为", "情况", "阶段")):
        return False
    if any(value.endswith(suffix) for suffix in OBJECT_SUFFIX_HINTS):
        return True
    if "账户" in value or "票据" in value or "汇票" in value or "本票" in value or "支票" in value:
        return True
    if value.startswith("《") and value.endswith("》"):
        return True
    return False


def _is_confident_time_term(term: str) -> bool:
    value = _normalize_time_fragment(term)
    if not value or value in TIME_BLOCKLIST:
        return False
    if value.startswith(("且", "并", "及", "和", "或")):
        return False
    if any(token in value for token in ("认定", "纳入名单", "联系电话", "法定代表人", "负责人")):
        return False
    if len(value) <= 2 and value not in TIME_FIXED_TERMS:
        return False
    if _contains_list_punctuation(value):
        return False
    return any(re.fullmatch(pattern, value) for pattern in TIME_HIGH_CONFIDENCE_PATTERNS)


def _normalize_time_action_phrase(value: str) -> str:
    text = _normalize_time_fragment(value)
    suffix = next((item for item in TIME_SUFFIXES if text.endswith(item)), "")
    if not suffix:
        return text
    stem = text[: -len(suffix)] if suffix else text
    if re.search(r"\d", stem) or stem.startswith(("T+", "T-")):
        return text

    action = next((label for token, label in TIME_ACTION_LABELS if token in stem), "")
    family = next((label for token, label in TIME_OBJECT_FAMILIES if token in stem), "")
    if action and family:
        return f"{action}{family}{suffix}"
    if action:
        return f"{action}{suffix}"
    if family:
        return f"{family}{suffix}"
    return text


def _build_record(name: str, matched_aliases: list[str] | None = None, source_categories: list[str] | None = None, is_normalized: bool = False) -> dict:
    cleaned_name = _clean_value(name)
    aliases = _dedupe_keep_order(([cleaned_name] if cleaned_name else []) + list(matched_aliases or []))
    return {
        "name": cleaned_name,
        "normalized_name": cleaned_name,
        "aliases": aliases or ([cleaned_name] if cleaned_name else []),
        "matched_aliases": _dedupe_keep_order(matched_aliases or ([cleaned_name] if cleaned_name else [])),
        "source_categories": sorted({_clean_value(item) for item in source_categories or [] if _clean_value(item)}),
        "is_normalized": is_normalized,
    }


def _record_confidence(record: dict) -> int:
    score = 0
    if record.get("is_normalized"):
        score += 3
    if record.get("source_categories"):
        score += 2
    if len(_clean_value(record.get("name", ""))) >= 4:
        score += 1
    return score


def _prune_subsumed_records(records: list[dict]) -> list[dict]:
    result = []
    seen_names = set()
    for record in records:
        name = _clean_value(record.get("name", ""))
        if not name:
            continue
        if name in seen_names:
            continue
        current_score = _record_confidence(record)
        should_drop = False
        for other in records:
            other_name = _clean_value(other.get("name", ""))
            if not other_name or other_name == name:
                continue
            other_score = _record_confidence(other)
            if name in other_name and other_score > current_score:
                should_drop = True
                break
            if other_name in name and other_score > current_score:
                should_drop = True
                break
            if name in other_name and other_score == current_score and len(name) < len(other_name):
                should_drop = True
                break
        if should_drop:
            continue
        result.append(record)
        seen_names.add(name)
    return result


def resolve_entity_table_path(path: str | Path | None = None) -> Path:
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"Entity table not found: {candidate}")
        return candidate

    candidates = sorted((PROJECT_ROOT / "data").glob("*entity_table_unified_v3.xlsx"))
    if not candidates:
        raise FileNotFoundError("Could not find entity table xlsx under data/.")
    return candidates[0]


def _load_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet_name).fillna("")


def _build_normalization_index(path: Path, categories: tuple[str, ...]) -> dict:
    df = _load_sheet(path, NORMALIZATION_SHEET)
    category_column = "类别"
    alias_column = "模糊表述 / 简称 / 别称"
    canonical_column = "规范标准名称"
    required_columns = {category_column, alias_column, canonical_column}
    if not required_columns.issubset(set(df.columns)):
        raise ValueError(
            f"Normalization sheet `{NORMALIZATION_SHEET}` is missing columns: {sorted(required_columns - set(df.columns))}"
        )

    canonical_records = {}
    alias_targets = defaultdict(set)
    alias_surfaces = defaultdict(set)
    category_set = set(categories)

    for _, row in df.iterrows():
        category = _clean_value(row.get(category_column, ""))
        alias = _clean_value(row.get(alias_column, ""))
        canonical = _clean_value(row.get(canonical_column, ""))
        if not category or category.startswith("▶") or category not in category_set or not alias or not canonical:
            continue

        record = canonical_records.setdefault(
            canonical,
            {
                "name": canonical,
                "normalized_name": canonical,
                "aliases": set(),
                "source_categories": set(),
                "is_normalized": True,
            },
        )
        record["aliases"].update({alias, canonical})
        record["source_categories"].add(category)

        for surface in (alias, canonical):
            key = _lookup_key(surface)
            if key:
                alias_targets[key].add(canonical)
                alias_surfaces[key].add(surface)

    exact_map = {}
    alias_pairs = []
    seen_alias_pairs = set()
    for key, targets in alias_targets.items():
        if len(targets) != 1:
            continue
        canonical = next(iter(targets))
        exact_map[key] = canonical
        for surface in sorted(alias_surfaces[key]):
            pair = (surface, canonical)
            if pair not in seen_alias_pairs:
                seen_alias_pairs.add(pair)
                alias_pairs.append(pair)

    alias_pairs.sort(key=lambda item: (-len(item[0]), item[0], item[1]))
    finalized_records = {
        name: {
            "name": record["name"],
            "normalized_name": record["normalized_name"],
            "aliases": sorted(record["aliases"], key=lambda item: (-len(item), item)),
            "source_categories": sorted(record["source_categories"]),
            "is_normalized": record["is_normalized"],
        }
        for name, record in canonical_records.items()
    }
    return {
        "path": str(path),
        "canonical_records": finalized_records,
        "exact_map": exact_map,
        "alias_pairs": tuple(alias_pairs),
    }


def _extract_records_from_index(
    text: str,
    raw_terms: list[str] | None,
    index: dict,
    fallback_to_raw_terms: bool = True,
) -> list[dict]:
    cleaned_text = _clean_value(text)
    cleaned_raw_terms = _dedupe_keep_order([_clean_value(term) for term in raw_terms or []])
    search_chunks = _dedupe_keep_order(([cleaned_text] if cleaned_text else []) + cleaned_raw_terms)
    canonical_records = index["canonical_records"]
    exact_map = index["exact_map"]
    alias_pairs = index["alias_pairs"]

    canonical_order = []
    matched_aliases = defaultdict(list)
    matched_raw_terms = set()

    def add_hit(canonical: str, alias: str) -> None:
        if canonical not in matched_aliases:
            canonical_order.append(canonical)
        matched_aliases[canonical].append(alias)

    for term in cleaned_raw_terms:
        canonical = exact_map.get(_lookup_key(term))
        if canonical:
            add_hit(canonical, term)
            matched_raw_terms.add(term)

    for chunk in search_chunks:
        for alias, canonical in alias_pairs:
            if alias and alias in chunk:
                add_hit(canonical, alias)
                if chunk in cleaned_raw_terms:
                    matched_raw_terms.add(chunk)

    results = []
    seen_names = set()
    for canonical in canonical_order:
        if canonical in seen_names:
            continue
        base_record = canonical_records.get(canonical) or _build_record(canonical, [canonical], [], True)
        results.append(
            {
                "name": base_record["name"],
                "normalized_name": base_record["normalized_name"],
                "aliases": base_record["aliases"],
                "matched_aliases": _dedupe_keep_order(matched_aliases.get(canonical, [])),
                "source_categories": base_record["source_categories"],
                "is_normalized": base_record["is_normalized"],
            }
        )
        seen_names.add(canonical)

    if fallback_to_raw_terms:
        fallback_terms = cleaned_raw_terms or ([cleaned_text] if cleaned_text else [])
        for term in fallback_terms:
            if term in matched_raw_terms or term in seen_names:
                continue
            results.append(_build_record(term, [term], [], False))
            seen_names.add(term)
    return results


@lru_cache(maxsize=4)
def load_actor_normalization_index(entity_table_path: str | None = None) -> dict:
    path = resolve_entity_table_path(entity_table_path)
    return _build_normalization_index(path, ACTOR_NORMALIZATION_CATEGORIES)


@lru_cache(maxsize=4)
def load_object_normalization_index(entity_table_path: str | None = None) -> dict:
    path = resolve_entity_table_path(entity_table_path)
    return _build_normalization_index(path, OBJECT_NORMALIZATION_CATEGORIES)


@lru_cache(maxsize=8)
def load_reference_terms(entity_table_path: str | None = None, group_key: str = "WHAT") -> tuple[str, ...]:
    path = resolve_entity_table_path(entity_table_path)
    df = _load_sheet(path, REFERENCE_SHEET)
    category_column = "实体类别"
    entity_column = "词语"
    if category_column not in df.columns or entity_column not in df.columns:
        raise ValueError(f"Reference sheet `{REFERENCE_SHEET}` is missing required columns.")

    aliases = {_normalize_paren_whitespace(item) for item in REFERENCE_CATEGORY_ALIASES.get(group_key, ())}
    items = []
    for _, row in df.iterrows():
        raw_category = _normalize_paren_whitespace(row.get(category_column, ""))
        entity_name = _clean_value(row.get(entity_column, ""))
        if not raw_category or raw_category.startswith("▶") or raw_category not in aliases or not entity_name:
            continue
        if group_key == "WHEN" and len(entity_name) < 2:
            continue
        items.append(entity_name)
    return tuple(_dedupe_keep_order(sorted(items, key=lambda item: (-len(item), item))))


def extract_normalized_actor_records(
    who_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[dict]:
    index = load_actor_normalization_index(str(entity_table_path) if entity_table_path else None)
    return _extract_records_from_index(who_text, raw_terms, index, fallback_to_raw_terms=True)


def extract_normalized_actor_names(
    who_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[str]:
    return [item["name"] for item in extract_normalized_actor_records(who_text, raw_terms, entity_table_path)]


def extract_object_dictionary_terms(what_text: str, raw_terms: list[str] | None = None, entity_table_path: str | Path | None = None) -> list[str]:
    cleaned_text = _clean_value(what_text)
    candidates = _dedupe_keep_order(([cleaned_text] if cleaned_text else []) + [_clean_value(item) for item in raw_terms or []])
    dictionary_terms = load_reference_terms(str(entity_table_path) if entity_table_path else None, group_key="WHAT")
    matches = []
    for chunk in candidates:
        for term in dictionary_terms:
            if term and term in chunk:
                matches.append(term)
    return [term for term in _dedupe_keep_order(matches) if _is_confident_object_term(term)]


def extract_normalized_object_records(
    what_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[dict]:
    cleaned_text = _clean_value(what_text)
    cleaned_raw_terms = [
        term
        for term in _dedupe_keep_order([_clean_value(term) for term in raw_terms or []])
        if _is_confident_object_term(term)
    ]
    if not cleaned_raw_terms and cleaned_text:
        if _is_confident_object_term(cleaned_text):
            cleaned_raw_terms = [cleaned_text]

    index = load_object_normalization_index(str(entity_table_path) if entity_table_path else None)
    normalized_records = _extract_records_from_index(cleaned_text, cleaned_raw_terms, index, fallback_to_raw_terms=False)
    results = []
    seen_names = set()

    for item in normalized_records:
        if item["name"] not in seen_names:
            results.append(item)
            seen_names.add(item["name"])

    for term in extract_object_dictionary_terms(cleaned_text, cleaned_raw_terms, entity_table_path):
        if term not in seen_names:
            results.append(_build_record(term, [term], ["核心事项 (WHAT)"], False))
            seen_names.add(term)

    for term in cleaned_raw_terms:
        if term not in seen_names:
            results.append(_build_record(term, [term], [], False))
            seen_names.add(term)
    return _prune_subsumed_records(results)


def extract_normalized_object_names(
    what_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[str]:
    return [item["name"] for item in extract_normalized_object_records(what_text, raw_terms, entity_table_path)]


def _normalize_time_fragment(fragment: str) -> str:
    value = _clean_value(fragment)
    value = value.replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", "", value)
    return value


def _extract_time_dictionary_terms(when_text: str, raw_terms: list[str] | None = None, entity_table_path: str | Path | None = None) -> list[str]:
    cleaned_text = _clean_value(when_text)
    candidates = _dedupe_keep_order(([cleaned_text] if cleaned_text else []) + [_clean_value(item) for item in raw_terms or []])
    dictionary_terms = load_reference_terms(str(entity_table_path) if entity_table_path else None, group_key="WHEN")
    matches = []
    for chunk in candidates:
        normalized_chunk = _normalize_time_fragment(chunk)
        for term in dictionary_terms:
            normalized_term = _normalize_time_fragment(term)
            if normalized_term and normalized_term in normalized_chunk:
                matches.append(term)
    return _dedupe_keep_order(matches)


def extract_normalized_time_records(
    when_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[dict]:
    cleaned_text = _clean_value(when_text)
    cleaned_raw_terms = [
        term
        for term in _dedupe_keep_order([_clean_value(term) for term in raw_terms or []])
        if _is_confident_time_term(term)
    ]
    search_chunks = _dedupe_keep_order(([cleaned_text] if cleaned_text else []) + cleaned_raw_terms)
    candidates = []

    for chunk in search_chunks:
        normalized_chunk = _normalize_time_fragment(chunk)
        if not normalized_chunk:
            continue
        for term in TIME_FIXED_TERMS:
            if term in normalized_chunk:
                candidates.append(term)
        for pattern in TIME_REGEX_PATTERNS:
            regex_matches = re.findall(pattern, normalized_chunk)
            for match in sorted(regex_matches, key=len, reverse=True):
                if not any(match != existing and match in existing for existing in candidates):
                    candidates.append(match)
        for part in re.split(r"[、，,；;|/]+", normalized_chunk):
            part = part.strip()
            if not part:
                continue
            if any(re.fullmatch(pattern, part) for pattern in TIME_SCENE_PATTERNS):
                candidates.append(part)

    candidates.extend(_extract_time_dictionary_terms(cleaned_text, cleaned_raw_terms, entity_table_path))
    results = []
    seen_names = set()
    for term in _dedupe_keep_order(candidates):
        cleaned_term = _normalize_time_action_phrase(term)
        if not cleaned_term or cleaned_term in seen_names or not _is_confident_time_term(cleaned_term):
            continue
        results.append(_build_record(cleaned_term, [cleaned_term], ["时间情境 (WHEN)"], True))
        seen_names.add(cleaned_term)

    fallback_terms = cleaned_raw_terms
    for term in fallback_terms:
        cleaned_term = _normalize_time_action_phrase(term)
        if not cleaned_term or cleaned_term in seen_names or not _is_confident_time_term(cleaned_term):
            continue
        results.append(_build_record(cleaned_term, [cleaned_term], [], False))
        seen_names.add(cleaned_term)
    return _prune_subsumed_records(results)


def extract_normalized_time_names(
    when_text: str,
    raw_terms: list[str] | None = None,
    entity_table_path: str | Path | None = None,
) -> list[str]:
    return [item["name"] for item in extract_normalized_time_records(when_text, raw_terms, entity_table_path)]


def normalize_actor_filter_terms(actor_terms: list[str] | None, entity_table_path: str | Path | None = None) -> list[str]:
    normalized_terms = []
    for term in actor_terms or []:
        cleaned = _clean_value(term)
        if not cleaned:
            continue
        records = extract_normalized_actor_records(cleaned, raw_terms=[cleaned], entity_table_path=entity_table_path)
        normalized_terms.extend(item["name"] for item in records if item.get("name"))
    return _dedupe_keep_order(normalized_terms)
