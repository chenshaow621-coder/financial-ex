from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from business_taxonomy_pipeline import (
    DEFAULT_CLASSIFIED_FILE,
    build_scene_match_rows,
    clean_json_string,
    dedupe_keep_order,
    extract_who_terms,
    parse_query_spec,
    parse_taxonomy,
    resolve_taxonomy_doc,
    safe_literal_list,
    strip_category_prefix,
)
from data_loader import clean_text, load_docx_lines, load_pdf_lines
from formal_rule_engine import (
    ATOM_ANALYSIS_MODES,
    FINAL_JUDGEMENT_MODES,
    RECALL_JUDGEMENT_MODES,
    build_symbolic_atom_analysis,
    build_symbolic_final_conclusion,
    build_symbolic_recall_judgement,
)
from mysql_traceability import add_mysql_sync_args, maybe_sync_artifacts_from_args
from prompt_manager import PROMPT_DOC_PATHS, load_prompt_text, render_prompt_template
from qwen_client import call_qwen, get_reasoning_model, normalize_api_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_REPORT_PATH = PROCESSED_DIR / "compliance_recall_loop_report.json"

PROMPT_DOCS = PROMPT_DOC_PATHS

DIRECTION_NAMES = {
    "A": "业务向下召回",
    "B": "同层横向召回",
    "C": "法规结构邻接召回",
    "D": "规则语义补全召回",
    "E": "例外禁止优先召回",
    "F": "上下位规范补充召回",
}

FINAL_CONCLUSION_OPTIONS = {
    "可办理",
    "不可办理",
    "有条件可办理",
    "需补材料后办理",
    "需人工复核",
    "证据不足待补召回",
}

GAP_TYPE_RULES = [
    ("例外/禁止缺口", ["不得", "不予", "禁止", "除外", "例外", "但书", "特殊情形", "限制性"]),
    ("主体范围缺口", ["主体", "持票人", "出票人", "收款人", "付款人", "适用对象", "主体范围"]),
    ("定义范围缺口", ["定义", "是指", "包括", "范围", "所称", "定义边界"]),
    ("判断条件缺口", ["前提条件", "触发条件", "办理条件", "判断条件", "实质性触发条件", "是否必须", "是否限于", "是否需验证", "是否可以"]),
    ("材料缺口", ["材料", "证明", "证件", "凭证", "身份证", "印鉴", "解讫通知", "附件", "原件", "复印件"]),
    ("流程动作缺口", ["流程", "步骤", "审核", "核查", "核对", "签章", "背书", "办理动作", "执行动作"]),
    ("时限阈值缺口", ["期限", "时限", "日内", "月内", "年内", "超过", "不超过", "阈值", "金额", "现金字样"]),
    ("规范依据缺口", ["出票行", "代理行", "上位法", "实施细则", "规范依据", "条款依据", "法条依据"]),
    ("事实核验缺口", ["备付", "头寸", "核验", "验证", "状态", "真实性", "一致性"]),
]

GAP_JUDGEMENT_CONDITIONS = {
    "例外/禁止缺口": "当结论涉及禁止、限制、例外或授权条款，但证据未覆盖完整的限制与例外边界时，不能直接输出确定性结论。",
    "主体范围缺口": "当问题依赖特定办理主体、权利主体或适用对象，而证据未明确主体范围时，会阻断最终结论。",
    "定义范围缺口": "当核心术语或适用范围未被定义清楚时，结论容易出现适用对象错误或边界错误。",
    "判断条件缺口": "当问题直接询问“能否办理/能否支取/能否放行”但证据未覆盖触发条件或适用前提时，不能直接输出确定性子结论；若检索已穷尽，可转人工复核或输出有条件判断。",
    "材料缺口": "当办理结论依赖明确提交材料，而证据未列全材料项时，不能直接输出“可办理”。",
    "流程动作缺口": "当办理结论依赖审核、签章、核验、留存等动作，而证据未覆盖必要动作时，结论只能停留在阶段性判断。",
    "时限阈值缺口": "当结论依赖提示付款期限、金额阈值、现金字样等限制条件，而证据未覆盖这些条件时，不能输出稳定结论。",
    "规范依据缺口": "当关键限制条件需要结合相邻条款、实施细则或其他规范理解，而当前依据不足时，结论需要补证或人工复核。",
    "事实核验缺口": "当问题不仅需要法条，还需要对资金、状态、票面或真实性做事实核验时，规则证据本身不足以独立完成结论。",
    "其他缺口": "当前缺口不属于既定模式，需要结合上下文人工判断其是否阻断最终结论。",
}

GAP_SEVERITY_BY_TYPE = {
    "例外/禁止缺口": "阻断型",
    "主体范围缺口": "阻断型",
    "定义范围缺口": "阻断型",
    "判断条件缺口": "关键型",
    "材料缺口": "关键型",
    "流程动作缺口": "关键型",
    "时限阈值缺口": "关键型",
    "规范依据缺口": "关键型",
    "事实核验缺口": "复核型",
    "其他缺口": "复核型",
}

GAP_SCOPE_BY_TYPE = {
    "例外/禁止缺口": "全局阻断",
    "主体范围缺口": "全局阻断",
    "定义范围缺口": "全局阻断",
    "判断条件缺口": "子结论阻断",
    "材料缺口": "子结论阻断",
    "流程动作缺口": "子结论阻断",
    "时限阈值缺口": "子结论阻断",
    "规范依据缺口": "子结论阻断",
    "事实核验缺口": "人工复核项",
    "其他缺口": "人工复核项",
}

GENERIC_FOCUS_TERMS = {
    "业务", "管理", "办理", "处理", "流程", "规则", "材料", "资料", "系统", "信息",
    "账户", "票据", "银行", "客户", "单位", "个人", "机构", "要求", "规定", "审核",
    "核查", "核对", "判断", "结论", "场景", "模块", "条款", "知识", "合规", "查验",
}

SEMANTIC_BUCKETS = {
    "definition": {
        "keywords": ["定义", "范围", "适用对象", "主体范围", "是指", "包括", "分为"],
        "text_terms": ["是指", "包括", "分为", "适用于", "所称"],
        "rule_types": {"DEF_SCOPE"},
    },
    "threshold": {
        "keywords": ["阈值", "金额", "期限", "时间", "天数", "时限", "边界"],
        "text_terms": ["日内", "年内", "个月", "以上", "以下", "超过", "不超过", "期限", "之日起"],
        "rule_types": {"VAL_THRESHOLD"},
    },
    "material": {
        "keywords": ["材料", "证明", "证件", "凭证", "印鉴", "签章", "原件", "复印件"],
        "text_terms": ["材料", "证明", "证件", "凭证", "印鉴", "签章", "原件", "复印件"],
        "rule_types": {"PRC_FLOW", "OBL_MANDATORY"},
    },
    "process": {
        "keywords": ["流程", "审核动作", "核查动作", "步骤", "方式", "如何审核", "比对字段"],
        "text_terms": ["应当", "必须", "须", "审查", "核查", "核对", "留存", "办理", "方可"],
        "rule_types": {"PRC_FLOW", "OBL_MANDATORY", "OBL_ONGOING"},
    },
    "exception": {
        "keywords": ["例外", "禁止", "除外", "但书", "特殊情形", "限制"],
        "text_terms": ["不得", "不予", "除外", "但是", "但", "特殊情况", "一律", "禁止"],
        "rule_types": {"PRO_FORBIDDEN", "PER_AUTH"},
    },
}

ARTICLE_PATTERN = re.compile(r"^(第[一二三四五六七八九十百零〇0-9]+条)")
SECTION_PATTERN = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+[章节]")
APPENDIX_HEADER_PATTERN = re.compile(r"^(附[一二三四五六七八九十0-9]+)")
APPENDIX_ITEM_PATTERN = re.compile(r"^([一二三四五六七八九十]+)、")


def safe_json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text)


def normalize_doc_name(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"[《》【】（）()\[\]<>“”\"'‘’、，,。；;：:·\-—_]", "", text)
    return text


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "y", "是"}


def limit_text(value: Any, size: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= size:
        return text
    return text[: size - 3] + "..."


def extract_cited_titles(*values: Any) -> list[str]:
    titles: list[str] = []
    for value in values:
        for title in re.findall(r"《([^》]{2,80})》", str(value or "")):
            cleaned = clean_text(title)
            if cleaned:
                titles.append(cleaned)
    return dedupe_keep_order(titles)


def split_focus_text(value: Any) -> list[str]:
    text = clean_text(str(value or "")).strip()
    if not text:
        return []
    parts = re.split(r"[、，,；;|/（）()\[\]【】\n]+|以及|或者|并且|且|与|和|或|及", text)
    terms = []
    for part in parts:
        part = part.strip(" ：:.-")
        if len(part) < 2 or part in GENERIC_FOCUS_TERMS:
            continue
        terms.append(part)
    return dedupe_keep_order(terms)


def normalize_recall_decision(value: Any) -> str:
    text = str(value or "").strip()
    if "停止" in text:
        return "停止召回"
    if "继续" in text:
        return "继续召回"
    return text or "继续召回"


def normalize_atom_decision(value: Any) -> str:
    text = str(value or "").strip()
    if "停止" in text:
        return "停止拆解"
    if "继续" in text:
        return "继续拆解"
    return text or "继续拆解"


def parse_record_who_terms(record: dict[str, Any], who_terms: list[str]) -> list[str]:
    if not who_terms:
        return []
    who_text = clean_text(record.get("who", "")).strip()
    atom_terms = record.get("who_terms_list", [])
    matched = []
    for term in who_terms:
        if term in who_text or term in atom_terms:
            matched.append(term)
    return dedupe_keep_order(matched)


def parse_doc_lines(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return load_pdf_lines(str(path))
    if suffix == ".docx":
        return load_docx_lines(str(path))
    return []


def parse_legal_doc_segments(path: Path) -> list[dict[str, str]]:
    lines = parse_doc_lines(path)
    segments: list[dict[str, str]] = []
    appendix_name: str | None = None
    current_ref: str | None = None
    current_buf: list[str] = []

    def flush() -> None:
        nonlocal current_ref, current_buf
        if not current_ref or not current_buf:
            return
        segments.append({"ref": current_ref, "text": "\n".join(current_buf)})
        current_ref = None
        current_buf = []

    for line in lines:
        if line.startswith("正确填写票据和结算凭证的基本规定"):
            flush()
            appendix_name = "附一"
            continue

        appendix_header = APPENDIX_HEADER_PATTERN.match(line)
        if appendix_header:
            flush()
            appendix_name = appendix_header.group(1)
            current_ref = appendix_name
            current_buf = [line]
            continue

        article_match = ARTICLE_PATTERN.match(line)
        if article_match:
            flush()
            appendix_name = None
            current_ref = article_match.group(1)
            current_buf = [line]
            continue

        if SECTION_PATTERN.match(line):
            flush()
            continue

        appendix_item = APPENDIX_ITEM_PATTERN.match(line)
        if appendix_name and appendix_item:
            flush()
            current_ref = f"{appendix_name}-{appendix_item.group(1)}"
            current_buf = [line]
            continue

        if current_ref:
            current_buf.append(line)

    flush()
    return segments


class ComplianceRecallController:
    def __init__(
        self,
        atoms_file: Path | None = None,
        taxonomy_doc: str | None = None,
        model: str | None = None,
        api_config: dict | None = None,
        recall_judgement_mode: str = "llm",
        atom_analysis_mode: str = "llm",
        final_judgement_mode: str = "llm",
        initial_limit: int = 40,
        judge_evidence_limit: int = 18,
        max_atom_checks: int = 6,
    ) -> None:
        self.atoms_file = atoms_file or DEFAULT_CLASSIFIED_FILE
        if not self.atoms_file.exists():
            raise FileNotFoundError(f"Classified atom file not found: {self.atoms_file}")

        self.api_config = normalize_api_config(model=model, **(api_config or {}))
        self.model = self.api_config["reasoning_model"] or self.api_config["model"] or get_reasoning_model()
        if recall_judgement_mode not in RECALL_JUDGEMENT_MODES:
            raise ValueError(
                f"Unsupported recall_judgement_mode: {recall_judgement_mode}. "
                f"Expected one of {RECALL_JUDGEMENT_MODES}."
            )
        if atom_analysis_mode not in ATOM_ANALYSIS_MODES:
            raise ValueError(
                f"Unsupported atom_analysis_mode: {atom_analysis_mode}. "
                f"Expected one of {ATOM_ANALYSIS_MODES}."
            )
        if final_judgement_mode not in FINAL_JUDGEMENT_MODES:
            raise ValueError(
                f"Unsupported final_judgement_mode: {final_judgement_mode}. "
                f"Expected one of {FINAL_JUDGEMENT_MODES}."
            )
        self.recall_judgement_mode = recall_judgement_mode
        self.atom_analysis_mode = atom_analysis_mode
        self.final_judgement_mode = final_judgement_mode
        self.initial_limit = initial_limit
        self.judge_evidence_limit = judge_evidence_limit
        self.max_atom_checks = max_atom_checks

        self.taxonomy_doc = resolve_taxonomy_doc(taxonomy_doc)
        self.metadata, self.entries, self.scenes = parse_taxonomy(self.taxonomy_doc)
        self.code_to_entry = {entry["code"]: entry for entry in self.entries}
        self.scene_by_key = {scene["scene_key"]: scene for scene in self.scenes}
        self.prompt_texts = {
            "atom_enhanced": load_prompt_text("recall_atom_enhanced_base"),
            "atom_minimum": load_prompt_text("recall_atom_minimum_base"),
            "set_closure": load_prompt_text("recall_set_closure_base"),
        }

        df = pd.read_excel(self.atoms_file).fillna("")
        self.records: list[dict[str, Any]] = []
        self.record_by_id: dict[str, dict[str, Any]] = {}
        self.records_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.records_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for _, row in df.iterrows():
            atom_id = str(row.get("atom_id", "")).strip()
            if not atom_id:
                continue
            record = row.to_dict()
            record["atom_id"] = atom_id
            record["label_codes_list"] = safe_json_loads(record.get("business_taxonomy_label_codes", "[]"), [])
            record["label_paths_list"] = safe_json_loads(record.get("business_taxonomy_label_paths", "[]"), [])
            record["business_modules_v2_list"] = safe_json_loads(record.get("business_modules_v2", "[]"), [])
            record["business_categories_v2_list"] = safe_json_loads(record.get("business_categories_v2", "[]"), [])
            record["related_scenarios_list"] = safe_literal_list(record.get("related_scenarios", ""))
            record["business_categories_list"] = safe_literal_list(record.get("business_categories", ""))
            record["who_terms_list"] = extract_who_terms(record.get("who", ""))
            record["search_text"] = "\n".join(
                [
                    str(record.get("source_document", "")),
                    str(record.get("article_reference", "")),
                    str(record.get("who", "")),
                    str(record.get("what", "")),
                    str(record.get("how", "")),
                    str(record.get("where", "")),
                    str(record.get("content_original", "")),
                    " ".join(record["related_scenarios_list"]),
                    " ".join(record["business_categories_list"]),
                    " ".join(record["label_paths_list"]),
                ]
            )
            self.records.append(record)
            self.record_by_id[atom_id] = record
            self.records_by_doc[str(record.get("source_document", "")).strip()].append(record)
            for code in record["label_codes_list"]:
                self.records_by_module[code].append(record)

        scene_match_rows = build_scene_match_rows(df, self.scenes)
        self.scene_matches_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.scene_matches_by_atom: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scene_match_rows:
            self.scene_matches_by_scene[row["scene_key"]].append(row)
            self.scene_matches_by_atom[row["atom_id"]].append(row)

        self.module_to_scenes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.category_to_modules: dict[str, list[str]] = defaultdict(list)
        for scene in self.scenes:
            self.module_to_scenes[scene["module_code"]].append(scene)
        for entry in self.entries:
            category_key = f"{entry['section']}::{entry['category']}"
            self.category_to_modules[category_key].append(entry["code"])

        self.raw_doc_candidates = [
            path
            for pattern in ("*.docx", "*.pdf")
            for path in RAW_DIR.glob(pattern)
            if path not in PROMPT_DOCS.values() and path != self.taxonomy_doc
        ]
        self._raw_doc_match_cache: dict[str, Path | None] = {}
        self._doc_segment_cache: dict[str, list[dict[str, str]]] = {}

    def call_json_prompt(
        self,
        prompt_text: str,
        timeout: int = 600,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response = call_qwen(prompt_text, model=self.model, timeout=timeout, api_config=self.api_config)
                payload = json.loads(clean_json_string(response))
                if isinstance(payload, dict):
                    return payload
                raise ValueError("Model did not return a JSON object.")
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(2)
        raise RuntimeError(f"Qwen JSON call failed after {max_retries} attempts: {last_error}")

    def build_business_match(self, question: str, query_spec: dict[str, Any]) -> dict[str, Any]:
        query = query_spec.get("query", "").strip()
        question_text = clean_text(question).strip()
        scene_hits = []
        module_scores: dict[str, int] = defaultdict(int)

        for scene in self.scenes:
            score = 0
            scene_name = scene["scene_name"]
            if query and (query in scene_name or scene_name in query):
                score += 32
            if scene_name and scene_name in question_text:
                score += 24
            for term in scene.get("scene_terms", []):
                if term and term in question_text:
                    score += 8
            if score > 0:
                scene_hits.append({"scene_key": scene["scene_key"], "scene_name": scene_name, "module_code": scene["module_code"], "score": score})
                module_scores[scene["module_code"]] += score

        for entry in self.entries:
            module_name = entry["module"]
            category_core = strip_category_prefix(entry["category"])
            score = 0
            if query and (query in module_name or module_name in query):
                score += 22
            if module_name and module_name in question_text:
                score += 18
            if category_core and category_core in question_text:
                score += 8
            for project in entry["projects"]:
                if query and query in project:
                    score += 14
                if project and project in question_text:
                    score += 16
            if score > 0:
                module_scores[entry["code"]] += score

        ranked_scenes = sorted(scene_hits, key=lambda item: (-item["score"], item["scene_key"]))
        matched_scene_keys = dedupe_keep_order([item["scene_key"] for item in ranked_scenes[:5]])
        matched_module_codes = dedupe_keep_order(
            [item["module_code"] for item in ranked_scenes[:5]]
            + [code for code, score in sorted(module_scores.items(), key=lambda item: (-item[1], item[0])) if score >= 18][:5]
        )
        matched_entries = [self.code_to_entry[code] for code in matched_module_codes if code in self.code_to_entry]
        matched_categories = dedupe_keep_order([f"{entry['section']} > {entry['category']}" for entry in matched_entries])

        return {
            "query": query,
            "who_terms": query_spec.get("who_terms", []),
            "matched_scene_keys": matched_scene_keys,
            "matched_scene_names": [self.scene_by_key[key]["scene_name"] for key in matched_scene_keys if key in self.scene_by_key],
            "matched_module_codes": matched_module_codes,
            "matched_module_paths": [self.code_to_entry[code]["label_path"] for code in matched_module_codes if code in self.code_to_entry],
            "matched_categories": matched_categories,
            "scene_scores": ranked_scenes[:10],
        }

    def build_focus_terms(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        atom_analysis: list[dict[str, Any]],
    ) -> list[str]:
        terms = split_focus_text(question)
        terms.extend(split_focus_text(business_match.get("query", "")))
        terms.extend(business_match.get("who_terms", []))
        terms.extend(business_match.get("matched_scene_names", []))
        terms.extend([self.code_to_entry[code]["module"] for code in business_match.get("matched_module_codes", []) if code in self.code_to_entry])

        for atom_id in current_atom_ids[:8]:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            terms.extend(split_focus_text(record.get("what", "")))
            terms.extend(split_focus_text(record.get("where", "")))

        for item in atom_analysis:
            terms.extend(split_focus_text(item.get("next_split_focus", "")))
            for missing in item.get("missing_elements", []):
                terms.extend(split_focus_text(missing))

        return dedupe_keep_order([term for term in terms if term and term not in GENERIC_FOCUS_TERMS])[:24]

    def score_text_hits(self, record: dict[str, Any], terms: list[str]) -> tuple[int, list[str]]:
        matched_terms = []
        score = 0
        for term in terms:
            term_hits = []
            if term and term in str(record.get("what", "")):
                score += 8
                term_hits.append(term)
            elif term and term in str(record.get("how", "")):
                score += 7
                term_hits.append(term)
            elif term and term in str(record.get("where", "")):
                score += 6
                term_hits.append(term)
            elif term and term in str(record.get("content_original", "")):
                score += 5
                term_hits.append(term)
            elif term and term in str(record.get("source_document", "")):
                score += 3
                term_hits.append(term)
            if term_hits:
                matched_terms.extend(term_hits)
        return score, dedupe_keep_order(matched_terms)

    def add_candidate(
        self,
        candidate_map: dict[str, dict[str, Any]],
        atom_id: str,
        score: float,
        reason: str,
    ) -> None:
        if atom_id not in self.record_by_id:
            return
        item = candidate_map.setdefault(atom_id, {"score": 0.0, "reasons": []})
        item["score"] += score
        item["reasons"] = dedupe_keep_order(item["reasons"] + [reason])

    def build_initial_candidates(
        self,
        question: str,
        business_match: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        focus_terms = self.build_focus_terms(question, business_match, [], [])

        for scene_key in business_match.get("matched_scene_keys", []):
            scene = self.scene_by_key.get(scene_key)
            for row in self.scene_matches_by_scene.get(scene_key, []):
                scene_name = scene["scene_name"] if scene else scene_key
                self.add_candidate(candidate_map, row["atom_id"], 45 + row["score"], f"场景精召回:{scene_name}:{row['score']}")

        for module_code in business_match.get("matched_module_codes", []):
            entry = self.code_to_entry.get(module_code)
            for record in self.records_by_module.get(module_code, []):
                label = entry["label_path"] if entry else module_code
                self.add_candidate(candidate_map, record["atom_id"], 14, f"业务模块宽召回:{label}")

        for record in self.records:
            text_score, matched_terms = self.score_text_hits(record, focus_terms)
            if text_score >= 10:
                self.add_candidate(
                    candidate_map,
                    record["atom_id"],
                    text_score,
                    f"语义命中:{'/'.join(matched_terms[:5])}",
                )

            matched_who_terms = parse_record_who_terms(record, business_match.get("who_terms", []))
            if matched_who_terms:
                self.add_candidate(
                    candidate_map,
                    record["atom_id"],
                    18,
                    f"主体细筛:{'/'.join(matched_who_terms)}",
                )

        return candidate_map

    def rank_candidate_ids(
        self,
        candidate_map: dict[str, dict[str, Any]],
        limit: int,
        per_doc_limit: int = 8,
    ) -> list[str]:
        ranked = sorted(candidate_map.items(), key=lambda item: (-item[1]["score"], item[0]))
        picked: list[str] = []
        doc_counter: dict[str, int] = defaultdict(int)

        for atom_id, _payload in ranked:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            source_document = str(record.get("source_document", ""))
            if doc_counter[source_document] >= per_doc_limit:
                continue
            picked.append(atom_id)
            doc_counter[source_document] += 1
            if len(picked) >= limit:
                break
        return picked

    def serialize_record(
        self,
        atom_id: str,
        candidate_map: dict[str, dict[str, Any]],
        include_content: bool = True,
    ) -> dict[str, Any]:
        record = self.record_by_id[atom_id]
        payload = {
            "atom_id": atom_id,
            "score": round(candidate_map.get(atom_id, {}).get("score", 0.0), 2),
            "reasons": candidate_map.get(atom_id, {}).get("reasons", []),
            "source_document": record.get("source_document", ""),
            "article_reference": record.get("article_reference", ""),
            "rule_type": record.get("rule_type", ""),
            "who": limit_text(record.get("who", ""), 80),
            "where": limit_text(record.get("where", ""), 90),
            "what": limit_text(record.get("what", ""), 120),
            "how": limit_text(record.get("how", ""), 160),
            "is_ambiguous": normalize_bool(record.get("is_ambiguous", False)),
            "review_reason": record.get("review_reason", ""),
            "business_paths": record.get("label_paths_list", []),
        }
        if include_content:
            payload["content_original"] = limit_text(record.get("content_original", ""), 240)
        return payload

    def build_round_context(
        self,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
    ) -> dict[str, Any]:
        docs = defaultdict(int)
        modules = defaultdict(int)
        for atom_id in current_atom_ids:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            docs[str(record.get("source_document", ""))] += 1
            for code in record.get("label_codes_list", []):
                entry = self.code_to_entry.get(code)
                if entry:
                    modules[entry["label_path"]] += 1
        top_docs = sorted(docs.items(), key=lambda item: (-item[1], item[0]))[:8]
        top_modules = sorted(modules.items(), key=lambda item: (-item[1], item[0]))[:8]
        return {
            "query": business_match.get("query", ""),
            "who_terms": business_match.get("who_terms", []),
            "matched_scenes": business_match.get("matched_scene_names", []),
            "matched_modules": business_match.get("matched_module_paths", []),
            "matched_categories": business_match.get("matched_categories", []),
            "current_atom_count": len(current_atom_ids),
            "top_documents": top_docs,
            "top_modules": top_modules,
        }

    def judge_recall_set(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        candidate_map: dict[str, dict[str, Any]],
        round_index: int,
    ) -> dict[str, Any]:
        evidence = [
            self.serialize_record(atom_id, candidate_map, include_content=True)
            for atom_id in current_atom_ids[: self.judge_evidence_limit]
        ]
        round_context = self.build_round_context(business_match, current_atom_ids)
        if self.recall_judgement_mode == "symbolic":
            return build_symbolic_recall_judgement(
                question=question,
                business_match=business_match,
                evidence=evidence,
                round_context=round_context,
                round_index=round_index,
            )

        prompt_text = render_prompt_template(
            "recall_set_closure_wrapper",
            base_prompt=self.prompt_texts["set_closure"],
            question=question,
            round_context_json=json.dumps(round_context, ensure_ascii=False, indent=2),
            round_index=round_index,
            evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2),
        )
        payload = self.call_json_prompt(prompt_text)
        payload["decision"] = normalize_recall_decision(payload.get("decision"))
        payload["can_make_final_compliance_judgement"] = normalize_bool(payload.get("can_make_final_compliance_judgement"))
        payload["confidence"] = float(payload.get("confidence", 0.0) or 0.0)
        payload["missing_dimensions"] = payload.get("missing_dimensions", []) if isinstance(payload.get("missing_dimensions"), list) else []
        payload["recommended_recall_directions"] = (
            payload.get("recommended_recall_directions", [])
            if isinstance(payload.get("recommended_recall_directions"), list)
            else []
        )
        return payload

    def normalize_direction_suggestions(self, set_judge: dict[str, Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in set_judge.get("recommended_recall_directions", []):
            if isinstance(item, dict):
                direction = str(item.get("direction") or item.get("code") or "").strip().upper()
                reason = str(
                    item.get("reason")
                    or item.get("why")
                    or item.get("rationale")
                    or item.get("basis")
                    or item.get("detail")
                    or ""
                ).strip()
                missing_dimension = str(
                    item.get("missing_dimension")
                    or item.get("dimension")
                    or item.get("gap")
                    or ""
                ).strip()
            else:
                direction = str(item or "").strip().upper()
                reason = ""
                missing_dimension = ""
            if direction not in DIRECTION_NAMES or direction in seen:
                continue
            seen.add(direction)
            normalized.append(
                {
                    "direction": direction,
                    "direction_name": DIRECTION_NAMES[direction],
                    "reason": reason,
                    "missing_dimension": missing_dimension,
                }
            )
        return normalized

    def build_missing_summary(
        self,
        set_judge: dict[str, Any],
        atom_analysis: list[dict[str, Any]],
    ) -> list[str]:
        summary: list[str] = []
        for item in set_judge.get("missing_dimensions", []):
            if not isinstance(item, dict):
                text = str(item or "").strip()
                if text:
                    summary.append(text)
                continue
            dimension = str(item.get("dimension") or item.get("name") or "").strip()
            reason = str(item.get("reason") or item.get("detail") or item.get("gap") or "").strip()
            if dimension and reason:
                summary.append(f"{dimension}: {reason}")
            elif dimension:
                summary.append(dimension)
            elif reason:
                summary.append(reason)

        for item in atom_analysis:
            missing_elements = [str(value).strip() for value in item.get("missing_elements", []) if str(value).strip()]
            next_focus = str(item.get("next_split_focus", "")).strip()
            if not missing_elements and not next_focus:
                continue
            atom_id = str(item.get("atom_id", "")).strip()
            missing_text = "；".join(missing_elements[:4])
            if missing_text and next_focus:
                summary.append(f"{atom_id}: 缺少{missing_text}；建议继续拆到{next_focus}")
            elif missing_text:
                summary.append(f"{atom_id}: 缺少{missing_text}")
            else:
                summary.append(f"{atom_id}: 建议继续拆到{next_focus}")
        return dedupe_keep_order(summary)[:12]

    def serialize_records(
        self,
        atom_ids: list[str],
        candidate_map: dict[str, dict[str, Any]],
        include_content: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        picked_ids = atom_ids[:limit] if limit is not None else atom_ids
        return [
            self.serialize_record(atom_id, candidate_map, include_content=include_content)
            for atom_id in picked_ids
            if atom_id in self.record_by_id
        ]

    def rebuild_candidate_map_from_items(
        self,
        items: list[dict[str, Any]] | None,
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        atom_ids: list[str] = []
        candidate_map: dict[str, dict[str, Any]] = {}
        for item in items or []:
            atom_id = str((item or {}).get("atom_id", "")).strip()
            if not atom_id or atom_id not in self.record_by_id:
                continue
            atom_ids.append(atom_id)
            reasons = [
                str(reason).strip()
                for reason in ((item or {}).get("reasons") or [])
                if str(reason).strip()
            ]
            candidate_map[atom_id] = {
                "score": float((item or {}).get("score", 0.0) or 0.0),
                "reasons": dedupe_keep_order(reasons),
            }
        return dedupe_keep_order(atom_ids), candidate_map

    def build_evidence_brief(self, item: dict[str, Any], text_limit: int = 110) -> str:
        source_document = str(item.get("source_document", "")).strip()
        article_reference = str(item.get("article_reference", "")).strip()
        text = clean_text(
            str(item.get("how", ""))
            or str(item.get("what", ""))
            or str(item.get("content_original", ""))
        ).strip()
        text = limit_text(text, text_limit)

        source_label = f"《{source_document}》" if source_document else ""
        ref_label = article_reference or ""
        prefix = "".join(part for part in [source_label, ref_label] if part)
        if prefix and text:
            return f"{prefix}：{text}"
        return text or prefix

    def normalize_summary_direction_items(self, items: list[dict[str, Any]] | list[str]) -> list[str]:
        normalized = []
        for item in items or []:
            if isinstance(item, dict):
                direction = str(item.get("direction", "")).strip().upper()
                direction_name = str(item.get("direction_name", "")).strip() or DIRECTION_NAMES.get(direction, "")
                reason = str(
                    item.get("judge_reason")
                    or item.get("reason")
                    or item.get("judge_missing_dimension")
                    or item.get("missing_dimension")
                    or ""
                ).strip()
                label = " ".join(part for part in [direction, direction_name] if part)
                normalized.append(f"{label}：{reason}" if reason and label else (label or reason))
            else:
                text = str(item or "").strip()
                if text:
                    normalized.append(text)
        return dedupe_keep_order([item for item in normalized if item])[:6]

    def normalize_output_list(self, value: Any, limit: int = 6) -> list[str]:
        if isinstance(value, list):
            items = value
        elif value is None:
            items = []
        else:
            text = str(value).strip()
            items = re.split(r"[\r\n]+|[；;]", text) if text else []
        normalized = [str(item).strip() for item in items if str(item).strip()]
        return dedupe_keep_order(normalized)[:limit]

    def normalize_final_conclusion(self, value: Any) -> str:
        text = str(value or "").strip()
        if text in FINAL_CONCLUSION_OPTIONS:
            return text
        if "不可" in text or "不予" in text or "不能" in text:
            return "不可办理"
        if "补材料" in text or ("材料" in text and "办理" in text):
            return "需补材料后办理"
        if "条件" in text:
            return "有条件可办理"
        if "人工" in text or "复核" in text:
            return "需人工复核"
        if "证据" in text or "召回" in text or "不足" in text:
            return "证据不足待补召回"
        if "可办理" in text or "可以办理" in text:
            return "可办理"
        return "需人工复核"

    def infer_gap_type(self, text: str) -> str:
        normalized = clean_text(str(text or "")).strip()
        if not normalized:
            return "其他缺口"
        for gap_type, keywords in GAP_TYPE_RULES:
            if any(keyword in normalized for keyword in keywords):
                return gap_type
        return "其他缺口"

    def build_gap_diagnosis(
        self,
        report: dict[str, Any],
        latest_missing_summary: list[str],
    ) -> list[dict[str, str]]:
        stop_reason = str(report.get("stop_reason", "")).strip()
        diagnoses: list[dict[str, str]] = []
        seen: set[str] = set()

        def default_handling(impact_scope: str) -> str:
            if stop_reason == "no_new_candidates":
                if impact_scope == "全局阻断":
                    return "建议补充语料或人工补证后再下结论"
                if impact_scope == "子结论阻断":
                    return "建议转人工复核，允许输出阶段性判断，但不要直接给出完整确定性结论"
                return "保留为风险提示，交由人工复核确认"
            if impact_scope == "全局阻断":
                return "必须继续补召回，否则不能下最终结论"
            if impact_scope == "子结论阻断":
                return "优先继续补召回；若后续候选耗尽，可转人工复核"
            return "可先保留为风险提示，不必单独阻断整案"

        latest_round = (report.get("rounds") or [])[-1] if (report.get("rounds") or []) else {}
        structured_missing_items = latest_round.get("judge", {}).get("missing_dimensions", [])

        for item in structured_missing_items:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension", "")).strip()
            reason = str(item.get("reason", "")).strip()
            gap_text = f"{dimension}: {reason}" if dimension and reason else (dimension or reason)
            if not gap_text or gap_text in seen:
                continue
            seen.add(gap_text)

            gap_type = str(item.get("gap_type", "")).strip() or self.infer_gap_type(gap_text)
            impact_scope = str(item.get("impact_scope", "")).strip() or GAP_SCOPE_BY_TYPE.get(gap_type, "人工复核项")
            severity = str(item.get("severity", "")).strip() or GAP_SEVERITY_BY_TYPE.get(gap_type, "复核型")
            handling = str(item.get("handling", "")).strip() or default_handling(impact_scope)
            judgement_condition = str(item.get("judgement_condition", "")).strip() or GAP_JUDGEMENT_CONDITIONS.get(
                gap_type,
                GAP_JUDGEMENT_CONDITIONS["其他缺口"],
            )

            diagnoses.append(
                {
                    "gap_text": gap_text,
                    "gap_type": gap_type,
                    "impact_scope": impact_scope,
                    "severity": severity,
                    "handling": handling,
                    "judgement_condition": judgement_condition,
                }
            )

        for item in latest_missing_summary:
            gap_text = str(item or "").strip()
            if not gap_text or gap_text in seen:
                continue
            seen.add(gap_text)

            gap_type = self.infer_gap_type(gap_text)
            impact_scope = GAP_SCOPE_BY_TYPE.get(gap_type, "人工复核项")
            severity = GAP_SEVERITY_BY_TYPE.get(gap_type, "复核型")
            diagnoses.append(
                {
                    "gap_text": gap_text,
                    "gap_type": gap_type,
                    "impact_scope": impact_scope,
                    "severity": severity,
                    "handling": default_handling(impact_scope),
                    "judgement_condition": GAP_JUDGEMENT_CONDITIONS.get(gap_type, GAP_JUDGEMENT_CONDITIONS["其他缺口"]),
                }
            )
        return diagnoses[:8]

    def build_gap_summary_cards(
        self,
        gap_diagnosis: list[dict[str, str]],
        stop_reason: str,
    ) -> list[dict[str, Any]]:
        card_specs = [
            {
                "card_key": "fatal_gaps",
                "card_title": "致命缺口总卡",
                "blocking_level": "整案阻断",
                "empty_summary": "当前未识别到会直接阻断整案最终结论的缺口。",
                "non_empty_summary": "这类缺口会直接阻断整案最终合规结论，未补齐前不应输出确定性结论。",
                "decision_hint": "优先继续补召回；若候选已耗尽，应补充法规语料或人工补证。",
            },
            {
                "card_key": "reviewable_gaps",
                "card_title": "可人工复核缺口总卡",
                "blocking_level": "子结论阻断",
                "empty_summary": "当前未识别到需要转人工复核的子结论阻断缺口。",
                "non_empty_summary": "这类缺口通常阻断局部子结论，不必自动否定整案，但需要重点复核。",
                "decision_hint": "优先继续补召回；若候选已耗尽，可转人工复核并输出阶段性判断。",
            },
            {
                "card_key": "risk_notice_gaps",
                "card_title": "仅风险提示总卡",
                "blocking_level": "风险提示",
                "empty_summary": "当前未识别到仅需保留为风险提示的缺口。",
                "non_empty_summary": "这类缺口更适合作为风险提示或核验提醒保留，不单独阻断整案。",
                "decision_hint": "保留为风险提示，纳入人工审核清单即可。",
            },
        ]
        card_map: dict[str, dict[str, Any]] = {
            spec["card_key"]: {**spec, "items": []}
            for spec in card_specs
        }

        for item in gap_diagnosis:
            impact_scope = str(item.get("impact_scope", "")).strip()
            if impact_scope == "全局阻断":
                card_key = "fatal_gaps"
            elif impact_scope == "子结论阻断":
                card_key = "reviewable_gaps"
            else:
                card_key = "risk_notice_gaps"
            card_map[card_key]["items"].append(
                {
                    "gap_text": str(item.get("gap_text", "")).strip(),
                    "gap_type": str(item.get("gap_type", "")).strip(),
                    "severity": str(item.get("severity", "")).strip(),
                    "impact_scope": impact_scope,
                    "handling": str(item.get("handling", "")).strip(),
                    "judgement_condition": str(item.get("judgement_condition", "")).strip(),
                }
            )

        cards: list[dict[str, Any]] = []
        for spec in card_specs:
            card = card_map[spec["card_key"]]
            items = card["items"]
            gap_types = dedupe_keep_order([item["gap_type"] for item in items if item.get("gap_type")])[:4]
            if items:
                summary = f"{spec['non_empty_summary']} 当前共 {len(items)} 项，主要涉及：{' / '.join(gap_types[:3]) or '待人工判读'}。"
                card_status = "有缺口"
                decision_hint = spec["decision_hint"]
                if spec["card_key"] == "fatal_gaps" and stop_reason == "no_new_candidates":
                    decision_hint = "召回候选已耗尽但仍存在整案阻断缺口，应补充法规语料或人工补证。"
                elif spec["card_key"] == "reviewable_gaps" and stop_reason == "no_new_candidates":
                    decision_hint = "召回候选已耗尽时优先转人工复核，不建议无限继续召回。"
            else:
                summary = spec["empty_summary"]
                card_status = "无缺口"
                decision_hint = "当前无对应缺口。"

            cards.append(
                {
                    "card_key": spec["card_key"],
                    "card_title": spec["card_title"],
                    "card_status": card_status,
                    "blocking_level": spec["blocking_level"],
                    "count": len(items),
                    "summary": summary,
                    "decision_hint": decision_hint,
                    "gap_types": gap_types,
                    "items": items[:4],
                }
            )
        return cards

    def build_compliance_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        question = str(report.get("question", "")).strip()
        query_spec = report.get("query_spec") or {}
        business_match = report.get("business_match") or {}
        rounds = report.get("rounds") or []
        latest_round = rounds[-1] if rounds else {}
        final_evidence = report.get("final_evidence") or report.get("initial_evidence") or []

        focus_terms = dedupe_keep_order(
            split_focus_text(question)
            + split_focus_text(query_spec.get("query", ""))
            + [str(term).strip() for term in query_spec.get("who_terms", []) if str(term).strip()]
            + [str(name).strip() for name in business_match.get("matched_scene_names", []) if str(name).strip()]
            + [str(term).strip() for term in latest_round.get("focus_terms", []) if str(term).strip()]
        )[:24]

        scored_evidence = []
        for item in final_evidence:
            score = float(item.get("score", 0.0) or 0.0)
            search_blob = "\n".join(
                [
                    str(item.get("what", "")),
                    str(item.get("how", "")),
                    str(item.get("where", "")),
                    str(item.get("content_original", "")),
                    str(item.get("source_document", "")),
                ]
            )
            for term in focus_terms[:12]:
                if term and term in search_blob:
                    score += 4
            scored_evidence.append((score, item))
        scored_evidence.sort(key=lambda pair: (-pair[0], str(pair[1].get("atom_id", ""))))

        buckets: dict[str, list[str]] = {
            "key_basis": [],
            "required_materials": [],
            "required_actions": [],
            "prohibitions": [],
            "exceptions": [],
            "time_limits": [],
            "definitions": [],
        }
        seen_bucket_values: dict[str, set[str]] = {key: set() for key in buckets}

        def add_bucket_item(bucket_name: str, value: str, limit: int) -> None:
            normalized_value = clean_text(value).strip()
            if not normalized_value or normalized_value in seen_bucket_values[bucket_name]:
                return
            if len(buckets[bucket_name]) >= limit:
                return
            seen_bucket_values[bucket_name].add(normalized_value)
            buckets[bucket_name].append(value)

        for _score, item in scored_evidence[:12]:
            brief = self.build_evidence_brief(item)
            if brief:
                add_bucket_item("key_basis", brief, limit=6)

            search_blob = "\n".join(
                [
                    str(item.get("what", "")),
                    str(item.get("how", "")),
                    str(item.get("where", "")),
                    str(item.get("content_original", "")),
                ]
            )
            rule_type = str(item.get("rule_type", "")).strip().upper()

            if rule_type in {"PRC_FLOW", "OBL_MANDATORY", "OBL_ONGOING", "EVT_TRIGGER"} or any(
                keyword in search_blob for keyword in ["应当", "必须", "须", "办理", "审核", "核查", "核对", "提交", "提供", "留存", "签章", "背书"]
            ):
                add_bucket_item("required_actions", brief, limit=5)

            if any(
                keyword in search_blob
                for keyword in ["材料", "证明", "证件", "凭证", "身份证", "印鉴", "签章", "原件", "复印件", "解讫通知", "背书"]
            ):
                add_bucket_item("required_materials", brief, limit=5)

            if rule_type == "PRO_FORBIDDEN" or any(keyword in search_blob for keyword in ["不得", "不予", "禁止", "不能", "不可"]):
                add_bucket_item("prohibitions", brief, limit=5)

            if rule_type == "PER_AUTH" or any(keyword in search_blob for keyword in ["除外", "但", "但是", "特殊情况", "例外", "可以"]):
                add_bucket_item("exceptions", brief, limit=5)

            if rule_type == "VAL_THRESHOLD" or any(
                keyword in search_blob for keyword in ["期限", "日内", "天内", "月内", "年内", "以上", "以下", "超过", "不超过", "届满"]
            ) or re.search(r"\d", search_blob):
                add_bucket_item("time_limits", brief, limit=5)

            if rule_type == "DEF_SCOPE" or any(keyword in search_blob for keyword in ["是指", "包括", "分为", "所称", "定义"]):
                add_bucket_item("definitions", brief, limit=4)

        final_decision = str(report.get("final_decision", "")).strip()
        judge_final_decision = str(report.get("judge_final_decision", final_decision)).strip()
        can_make_final = bool(report.get("can_make_final_compliance_judgement"))
        stop_reason = str(report.get("stop_reason", "")).strip()

        latest_missing_summary = [
            str(item).strip()
            for item in latest_round.get("judge_missing_summary", [])
            if str(item).strip()
        ][:8]
        latest_directions = self.normalize_summary_direction_items(
            latest_round.get("applied_directions") or latest_round.get("judge_recommended_directions") or []
        )
        gap_diagnosis = self.build_gap_diagnosis(report, latest_missing_summary)
        gap_summary_cards = self.build_gap_summary_cards(gap_diagnosis, stop_reason)

        risk_points: list[str] = []
        if final_decision == "LLM_ERROR":
            risk_points.append("Qwen 本次未连通，当前只能基于本地召回结果输出审查摘要，不能视为完整合规结论。")
        elif final_decision == "DRY_RUN":
            risk_points.append("本次尚未进入 LLM 闭环判断，当前结果仅用于查看召回覆盖面，不应直接下最终合规结论。")
        elif not can_make_final:
            risk_points.append("当前证据仍未闭环，暂不适合直接输出“可办理/不可办理”的最终结论。")

        if stop_reason == "max_rounds":
            risk_points.append("本次停止是因为达到轮次上限，不代表缺口已经补齐。")
        if judge_final_decision == "继续召回":
            risk_points.append("模型仍建议继续补召回，说明现有证据集对关键缺口的覆盖还不够。")
        if stop_reason == "no_new_candidates":
            risk_points.append("当前召回候选已经耗尽，继续增加轮次通常不会再带来新增证据。")

        ambiguous_count = sum(1 for item in final_evidence if normalize_bool(item.get("is_ambiguous", False)))
        if ambiguous_count:
            risk_points.append(f"最终证据中仍有 {ambiguous_count} 条原子知识带有歧义/复核标记，需要人工再看。")
        if buckets["prohibitions"]:
            risk_points.append("已命中限制性/禁止性规则，输出最终结论前需要优先核查是否存在例外条款或授权条款。")

        if latest_missing_summary:
            for item in latest_missing_summary[:3]:
                risk_points.append(f"关键缺口：{item}")

        risk_points = dedupe_keep_order(risk_points)[:6]

        if final_decision == "LLM_ERROR":
            readiness = "llm_error"
            headline = "LLM 未连通，本次仅能输出本地合规审查摘要"
            next_step = "先恢复 Qwen 连通性，再继续运行闭环召回与最终结论生成。"
        elif final_decision == "DRY_RUN":
            readiness = "dry_run"
            headline = "当前只完成了本地召回摘要，尚未进入闭环合规判断"
            next_step = "继续运行闭环召回，让模型判断证据是否足以支撑最终合规结论。"
        elif can_make_final:
            readiness = "ready"
            headline = "证据已基本闭环，可进入最终合规结论生成"
            next_step = "基于当前证据集生成最终结论卡片，输出结论、依据、缺失项和风险提示。"
        elif stop_reason == "no_new_candidates":
            global_blockers = [item for item in gap_diagnosis if item["impact_scope"] == "全局阻断"]
            if len(buckets["key_basis"]) >= 4 and not global_blockers and len(latest_missing_summary) <= 3:
                readiness = "exhausted_partial"
                headline = "召回候选已耗尽，当前可形成阶段性判断，但仍有少量关键缺口"
                next_step = (
                    f"建议转人工复核，优先核查：{latest_missing_summary[0]}"
                    if latest_missing_summary
                    else "建议转人工复核，确认剩余限制条件是否影响最终结论。"
                )
            else:
                readiness = "exhausted_insufficient"
                headline = "召回候选已耗尽，但证据仍未闭环"
                next_step = "建议补充法规语料或人工补证，而不是单纯继续增加轮次。"
        else:
            readiness = "insufficient_evidence"
            headline = "证据尚未闭环，暂不直接输出最终合规结论"
            next_step = (
                f"优先继续补召回：{latest_directions[0]}"
                if latest_directions
                else "优先补定义、材料、时限、例外和流程类证据。"
            )

        modules = [str(item).strip() for item in business_match.get("matched_module_paths", []) if str(item).strip()]
        scenes = [str(item).strip() for item in business_match.get("matched_scene_names", []) if str(item).strip()]

        return {
            "readiness": readiness,
            "headline": headline,
            "next_step": next_step,
            "route_hint": " | ".join(scenes[:3] or modules[:3]),
            "recommended_directions": latest_directions,
            "missing_items": latest_missing_summary,
            "gap_diagnosis": gap_diagnosis,
            "gap_summary_cards": gap_summary_cards,
            "risk_points": risk_points,
            "key_basis": buckets["key_basis"],
            "required_materials": buckets["required_materials"],
            "required_actions": buckets["required_actions"],
            "prohibitions": buckets["prohibitions"],
            "exceptions": buckets["exceptions"],
            "time_limits": buckets["time_limits"],
            "definitions": buckets["definitions"],
        }

    def build_local_final_conclusion(
        self,
        report: dict[str, Any],
        summary: dict[str, Any],
        generation_mode: str,
        error: str = "",
    ) -> dict[str, Any]:
        final_decision = str(report.get("final_decision", "")).strip()
        can_make_final = bool(report.get("can_make_final_compliance_judgement"))
        stop_reason = str(report.get("stop_reason", "")).strip()

        if final_decision in {"DRY_RUN", "LLM_ERROR"} or not can_make_final:
            if final_decision == "LLM_ERROR":
                conclusion = "证据不足待补召回"
                conclusion_summary = "闭环判断过程中 LLM 未连通，当前只能基于本地证据摘要说明现状，不能直接输出最终合规结论。"
                confidence = 0.25
                status = "not_ready"
            elif final_decision == "DRY_RUN":
                conclusion = "证据不足待补召回"
                conclusion_summary = "当前仅完成本地召回摘要，尚未进入完整闭环判断，因此不能直接给出最终合规结论。"
                confidence = 0.25
                status = "not_ready"
            elif stop_reason == "no_new_candidates" and len(summary.get("key_basis") or []) >= 4 and not any(
                item.get("impact_scope") == "全局阻断" for item in (summary.get("gap_diagnosis") or [])
            ) and len(summary.get("missing_items") or []) <= 3:
                conclusion = "需人工复核"
                conclusion_summary = "当前法规检索已基本穷尽，现有证据足以支持大部分审查判断，但仍有少量关键缺口未补齐，建议转人工复核而不是继续无限补召回。"
                confidence = 0.4
                status = "exhausted_partial"
            else:
                conclusion = "证据不足待补召回"
                conclusion_summary = "当前证据尚未闭环，仍需继续补召回或人工复核后，才能给出最终合规结论。"
                confidence = 0.25
                status = "not_ready"
        else:
            if summary.get("prohibitions") and not summary.get("exceptions"):
                conclusion = "不可办理"
                conclusion_summary = "当前证据中已命中明确限制性/禁止性规则，且未见足以覆盖该限制的例外条款，倾向于不可办理。"
                confidence = 0.48
            elif summary.get("required_materials"):
                conclusion = "需补材料后办理"
                conclusion_summary = "当前证据显示该事项办理前需要补齐明确材料，材料满足后才适合继续办理。"
                confidence = 0.45
            elif summary.get("required_actions") or summary.get("exceptions") or summary.get("time_limits"):
                conclusion = "有条件可办理"
                conclusion_summary = "当前证据显示该事项并非绝对禁止，但需满足材料、审核动作、时限或例外条件。"
                confidence = 0.42
            else:
                conclusion = "需人工复核"
                conclusion_summary = "证据已接近闭环，但本地规则不足以稳定输出正负结论，建议人工复核。"
                confidence = 0.35
            status = "ready_local_fallback"

        risk_points = self.normalize_output_list(summary.get("risk_points"), limit=6)
        if error:
            risk_points = dedupe_keep_order([f"最终结论生成回退：{error}"] + risk_points)[:6]

        follow_up_actions = dedupe_keep_order(
            self.normalize_output_list(summary.get("recommended_directions"), limit=4)
            + ([str(summary.get("next_step", "")).strip()] if str(summary.get("next_step", "")).strip() else [])
        )[:5]

        return {
            "status": status,
            "generation_mode": generation_mode,
            "ready_for_final_judgement": can_make_final,
            "conclusion": conclusion,
            "conclusion_summary": conclusion_summary,
            "confidence": confidence,
            "legal_basis": self.normalize_output_list(summary.get("key_basis"), limit=6),
            "required_materials": self.normalize_output_list(summary.get("required_materials"), limit=6),
            "required_actions": self.normalize_output_list(summary.get("required_actions"), limit=6),
            "exceptions_and_limits": dedupe_keep_order(
                self.normalize_output_list(summary.get("prohibitions"), limit=4)
                + self.normalize_output_list(summary.get("exceptions"), limit=4)
                + self.normalize_output_list(summary.get("time_limits"), limit=4)
            )[:8],
            "missing_items": self.normalize_output_list(summary.get("missing_items"), limit=6),
            "risk_points": risk_points,
            "follow_up_actions": follow_up_actions,
            "error": error,
        }

    def build_final_conclusion_prompt(
        self,
        report: dict[str, Any],
        summary: dict[str, Any],
    ) -> str:
        business_match = report.get("business_match") or {}
        evidence = (report.get("final_evidence") or [])[:16]
        latest_round = (report.get("rounds") or [])[-1] if (report.get("rounds") or []) else {}

        prompt_payload = {
            "question": report.get("question", ""),
            "query": (report.get("query_spec") or {}).get("query", ""),
            "who_terms": (report.get("query_spec") or {}).get("who_terms", []),
            "matched_module_paths": business_match.get("matched_module_paths", []),
            "matched_scene_names": business_match.get("matched_scene_names", []),
            "summary": {
                "headline": summary.get("headline", ""),
                "route_hint": summary.get("route_hint", ""),
                "key_basis": summary.get("key_basis", []),
                "required_materials": summary.get("required_materials", []),
                "required_actions": summary.get("required_actions", []),
                "prohibitions": summary.get("prohibitions", []),
                "exceptions": summary.get("exceptions", []),
                "time_limits": summary.get("time_limits", []),
                "definitions": summary.get("definitions", []),
                "missing_items": summary.get("missing_items", []),
                "gap_summary_cards": summary.get("gap_summary_cards", []),
                "risk_points": summary.get("risk_points", []),
            },
            "latest_round_missing_summary": latest_round.get("judge_missing_summary", []),
            "latest_round_directions": self.normalize_summary_direction_items(
                latest_round.get("applied_directions") or latest_round.get("judge_recommended_directions") or []
            ),
            "final_evidence": evidence,
        }

        return render_prompt_template(
            "recall_final_conclusion",
            allowed_conclusions=" / ".join(sorted(FINAL_CONCLUSION_OPTIONS)),
            prompt_payload_json=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
        )

    def build_final_conclusion(self, report: dict[str, Any]) -> dict[str, Any]:
        summary = report.get("compliance_summary") or self.build_compliance_summary(report)
        if self.final_judgement_mode == "symbolic":
            return build_symbolic_final_conclusion(report, summary)

        final_decision = str(report.get("final_decision", "")).strip()
        can_make_final = bool(report.get("can_make_final_compliance_judgement"))

        if final_decision in {"DRY_RUN", "LLM_ERROR"} or not can_make_final:
            return self.build_local_final_conclusion(
                report,
                summary,
                generation_mode="local_not_ready",
            )

        try:
            payload = self.call_json_prompt(
                self.build_final_conclusion_prompt(report, summary),
                timeout=180,
                max_retries=2,
            )
            conclusion = self.normalize_final_conclusion(payload.get("conclusion"))
            confidence = float(payload.get("confidence", 0.0) or 0.0)
            confidence = max(0.0, min(1.0, confidence))

            return {
                "status": "generated",
                "generation_mode": "llm",
                "ready_for_final_judgement": True,
                "conclusion": conclusion,
                "conclusion_summary": str(payload.get("conclusion_summary", "")).strip()
                or str(summary.get("headline", "")).strip()
                or "已完成最终合规结论生成。",
                "confidence": confidence,
                "legal_basis": self.normalize_output_list(payload.get("legal_basis"), limit=6)
                or self.normalize_output_list(summary.get("key_basis"), limit=6),
                "required_materials": self.normalize_output_list(payload.get("required_materials"), limit=6)
                or self.normalize_output_list(summary.get("required_materials"), limit=6),
                "required_actions": self.normalize_output_list(payload.get("required_actions"), limit=6)
                or self.normalize_output_list(summary.get("required_actions"), limit=6),
                "exceptions_and_limits": self.normalize_output_list(payload.get("exceptions_and_limits"), limit=8)
                or dedupe_keep_order(
                    self.normalize_output_list(summary.get("prohibitions"), limit=4)
                    + self.normalize_output_list(summary.get("exceptions"), limit=4)
                    + self.normalize_output_list(summary.get("time_limits"), limit=4)
                )[:8],
                "missing_items": self.normalize_output_list(payload.get("missing_items"), limit=6)
                or self.normalize_output_list(summary.get("missing_items"), limit=6),
                "risk_points": self.normalize_output_list(payload.get("risk_points"), limit=6)
                or self.normalize_output_list(summary.get("risk_points"), limit=6),
                "follow_up_actions": self.normalize_output_list(payload.get("follow_up_actions"), limit=5)
                or dedupe_keep_order(
                    self.normalize_output_list(summary.get("recommended_directions"), limit=4)
                    + ([str(summary.get("next_step", "")).strip()] if str(summary.get("next_step", "")).strip() else [])
                )[:5],
                "error": "",
            }
        except Exception as exc:
            return self.build_local_final_conclusion(
                report,
                summary,
                generation_mode="llm_fallback",
                error=str(exc),
            )

    def attach_compliance_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        summary = self.build_compliance_summary(report)
        report["compliance_summary"] = summary
        report["final_conclusion"] = self.build_final_conclusion(report)
        return report

    def select_atoms_for_analysis(
        self,
        current_atom_ids: list[str],
        candidate_map: dict[str, dict[str, Any]],
    ) -> list[str]:
        scored = []
        for atom_id in current_atom_ids:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            ambiguity_bonus = 10 if normalize_bool(record.get("is_ambiguous", False)) else 0
            review_bonus = 5 if str(record.get("review_reason", "")).strip() not in {"", "NONE"} else 0
            rule_bonus = 6 if str(record.get("rule_type", "")) in {"DEF_SCOPE", "VAL_THRESHOLD", "PRC_FLOW"} else 0
            score = candidate_map.get(atom_id, {}).get("score", 0.0) + ambiguity_bonus + review_bonus + rule_bonus
            scored.append((score, atom_id))
        scored.sort(reverse=True)
        return [atom_id for _, atom_id in scored[: self.max_atom_checks]]

    def analyze_atom_executability(
        self,
        question: str,
        business_match: dict[str, Any],
        atom_id: str,
        candidate_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        record = self.serialize_record(atom_id, candidate_map, include_content=True)
        if self.atom_analysis_mode == "symbolic":
            return build_symbolic_atom_analysis(
                question=question,
                business_match=business_match,
                record=record,
            )

        prompt_text = render_prompt_template(
            "recall_atom_analysis_wrapper",
            atom_minimum_prompt=self.prompt_texts["atom_minimum"],
            atom_enhanced_prompt=self.prompt_texts["atom_enhanced"],
            question=question,
            round_context_json=json.dumps(self.build_round_context(business_match, [atom_id]), ensure_ascii=False, indent=2),
            record_json=json.dumps(record, ensure_ascii=False, indent=2),
        )
        payload = self.call_json_prompt(prompt_text)
        payload["atom_id"] = atom_id
        payload["decision"] = normalize_atom_decision(payload.get("decision"))
        payload["reason"] = str(payload.get("reason", "")).strip()
        payload["missing_elements"] = payload.get("missing_elements", []) if isinstance(payload.get("missing_elements"), list) else []
        payload["next_split_focus"] = str(payload.get("next_split_focus", "")).strip()
        return payload

    def aggregate_missing_profiles(
        self,
        set_judge: dict[str, Any],
        atom_analysis: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snippets = []
        for item in set_judge.get("missing_dimensions", []):
            if isinstance(item, dict):
                snippets.append(str(item.get("dimension", "")))
                snippets.append(str(item.get("reason", "")))
            else:
                snippets.append(str(item or ""))
        for item in atom_analysis:
            snippets.extend([str(missing) for missing in item.get("missing_elements", [])])
            snippets.append(str(item.get("next_split_focus", "")))
        joined = "\n".join(snippets)
        profiles = []
        for name, spec in SEMANTIC_BUCKETS.items():
            if any(keyword in joined for keyword in spec["keywords"]):
                profiles.append(name)
        return {
            "profiles": profiles or ["process", "definition"],
            "raw_signals": snippets,
        }

    def expand_business_downward(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        atom_analysis: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        focus_terms = self.build_focus_terms(question, business_match, current_atom_ids, atom_analysis)
        scene_keys = business_match.get("matched_scene_keys", [])
        module_codes = business_match.get("matched_module_codes", [])

        for scene_key in scene_keys:
            scene = self.scene_by_key.get(scene_key)
            for row in self.scene_matches_by_scene.get(scene_key, []):
                boost = 26 + row["score"]
                reason = f"A-业务向下:{scene['scene_name'] if scene else scene_key}"
                self.add_candidate(candidate_map, row["atom_id"], boost, reason)

        for module_code in module_codes:
            entry = self.code_to_entry.get(module_code)
            scene_terms = []
            for scene in self.module_to_scenes.get(module_code, []):
                scene_terms.append(scene["scene_name"])
                scene_terms.extend(scene.get("scene_terms", []))
            scene_terms = dedupe_keep_order([term for term in scene_terms if term not in GENERIC_FOCUS_TERMS])
            for record in self.records_by_module.get(module_code, []):
                extra_terms = dedupe_keep_order(scene_terms[:10] + focus_terms[:10])
                text_score, matched_terms = self.score_text_hits(record, extra_terms)
                if text_score >= 12:
                    label = entry["label_path"] if entry else module_code
                    self.add_candidate(
                        candidate_map,
                        record["atom_id"],
                        10 + text_score,
                        f"A-业务向下:{label}:{'/'.join(matched_terms[:4])}",
                    )
        return candidate_map

    def expand_lateral(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        atom_analysis: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        focus_terms = self.build_focus_terms(question, business_match, current_atom_ids, atom_analysis)
        current_scene_keys = set(business_match.get("matched_scene_keys", []))
        related_module_codes = business_match.get("matched_module_codes", [])

        sibling_scenes = []
        for module_code in related_module_codes:
            sibling_scenes.extend(self.module_to_scenes.get(module_code, []))
            entry = self.code_to_entry.get(module_code)
            if entry:
                category_key = f"{entry['section']}::{entry['category']}"
                for sibling_module in self.category_to_modules.get(category_key, []):
                    sibling_scenes.extend(self.module_to_scenes.get(sibling_module, []))

        for scene in sibling_scenes:
            if scene["scene_key"] in current_scene_keys:
                continue
            score = 0
            if any(term in question for term in scene.get("scene_terms", [])):
                score += 12
            if any(term in scene["scene_name"] for term in focus_terms):
                score += 10
            if score == 0:
                score = 6
            for row in self.scene_matches_by_scene.get(scene["scene_key"], []):
                self.add_candidate(
                    candidate_map,
                    row["atom_id"],
                    score + row["score"] * 0.5,
                    f"B-同层横向:{scene['scene_name']}",
                )
        return candidate_map

    def resolve_raw_doc_path(self, source_document: str) -> Path | None:
        if source_document in self._raw_doc_match_cache:
            return self._raw_doc_match_cache[source_document]

        normalized_target = normalize_doc_name(source_document)
        best_path = None
        best_score = -1
        for path in self.raw_doc_candidates:
            normalized_path = normalize_doc_name(path.stem)
            score = 0
            if normalized_target and normalized_target in normalized_path:
                score += len(normalized_target)
            if normalized_path and normalized_path in normalized_target:
                score += len(normalized_path)
            common = len(set(normalized_target) & set(normalized_path))
            score += common
            if score > best_score:
                best_score = score
                best_path = path

        resolved = best_path if best_score > 3 else None
        self._raw_doc_match_cache[source_document] = resolved
        return resolved

    def get_doc_segments(self, source_document: str) -> list[dict[str, str]]:
        if source_document in self._doc_segment_cache:
            return self._doc_segment_cache[source_document]
        path = self.resolve_raw_doc_path(source_document)
        segments = parse_legal_doc_segments(path) if path else []
        self._doc_segment_cache[source_document] = segments
        return segments

    def get_adjacent_refs(self, source_document: str, article_reference: str, window: int = 1) -> list[str]:
        article_reference = str(article_reference or "").strip()
        if not article_reference:
            return []
        refs = [segment["ref"] for segment in self.get_doc_segments(source_document)]
        if not refs:
            return []

        match_idx = None
        match_len = -1
        for idx, ref in enumerate(refs):
            if ref and (article_reference.startswith(ref) or ref in article_reference or article_reference in ref):
                if len(ref) > match_len:
                    match_idx = idx
                    match_len = len(ref)

        if match_idx is None:
            return []

        left = max(0, match_idx - window)
        right = min(len(refs), match_idx + window + 1)
        return [ref for idx, ref in enumerate(refs[left:right], start=left) if idx != match_idx]

    def expand_regulatory_adjacency(
        self,
        current_atom_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        for atom_id in current_atom_ids[:12]:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            source_document = str(record.get("source_document", "")).strip()
            article_reference = str(record.get("article_reference", "")).strip()
            for neighbor_ref in self.get_adjacent_refs(source_document, article_reference, window=1):
                for candidate in self.records_by_doc.get(source_document, []):
                    candidate_ref = str(candidate.get("article_reference", "")).strip()
                    if neighbor_ref and (candidate_ref.startswith(neighbor_ref) or neighbor_ref in candidate_ref):
                        self.add_candidate(
                            candidate_map,
                            candidate["atom_id"],
                            18,
                            f"C-法规邻接:{source_document}:{neighbor_ref}",
                        )
        return candidate_map

    def expand_semantic_completion(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        set_judge: dict[str, Any],
        atom_analysis: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        missing_profile = self.aggregate_missing_profiles(set_judge, atom_analysis)
        focus_terms = self.build_focus_terms(question, business_match, current_atom_ids, atom_analysis)
        current_docs = {self.record_by_id[atom_id]["source_document"] for atom_id in current_atom_ids if atom_id in self.record_by_id}
        current_modules = {
            code
            for atom_id in current_atom_ids
            if atom_id in self.record_by_id
            for code in self.record_by_id[atom_id].get("label_codes_list", [])
        }

        for profile_name in missing_profile["profiles"]:
            spec = SEMANTIC_BUCKETS[profile_name]
            search_terms = dedupe_keep_order(spec["text_terms"] + focus_terms)
            for record in self.records:
                score = 0
                text_score, matched_terms = self.score_text_hits(record, search_terms)
                score += text_score
                if str(record.get("rule_type", "")) in spec["rule_types"]:
                    score += 12
                if str(record.get("source_document", "")) in current_docs:
                    score += 8
                if any(code in current_modules for code in record.get("label_codes_list", [])):
                    score += 6
                if score >= 16:
                    self.add_candidate(
                        candidate_map,
                        record["atom_id"],
                        score,
                        f"D-语义补全:{profile_name}:{'/'.join(matched_terms[:4])}",
                    )
        return candidate_map

    def expand_exception_priority(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        atom_analysis: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        focus_terms = self.build_focus_terms(question, business_match, current_atom_ids, atom_analysis)
        focus_terms.extend(["不得", "不予", "除外", "但是", "特殊情况"])
        current_docs = {self.record_by_id[atom_id]["source_document"] for atom_id in current_atom_ids if atom_id in self.record_by_id}

        for record in self.records:
            score = 0
            if str(record.get("rule_type", "")) in {"PRO_FORBIDDEN", "PER_AUTH"}:
                score += 14
            text_score, matched_terms = self.score_text_hits(record, dedupe_keep_order(focus_terms))
            score += text_score
            if str(record.get("source_document", "")) in current_docs:
                score += 6
            if score >= 18:
                self.add_candidate(
                    candidate_map,
                    record["atom_id"],
                    score,
                    f"E-例外禁止:{'/'.join(matched_terms[:4])}",
                )
        return candidate_map

    def expand_norm_supplement(
        self,
        question: str,
        current_atom_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        candidate_map: dict[str, dict[str, Any]] = {}
        cited_titles = extract_cited_titles(question)
        current_docs = []
        for atom_id in current_atom_ids:
            record = self.record_by_id.get(atom_id)
            if not record:
                continue
            current_docs.append(str(record.get("source_document", "")))
            cited_titles.extend(extract_cited_titles(record.get("content_original", ""), record.get("how", "")))

        current_docs = dedupe_keep_order(current_docs)
        cited_titles = dedupe_keep_order(cited_titles)
        doc_terms = []
        for title in current_docs + cited_titles:
            doc_terms.extend(split_focus_text(title))
        doc_terms = dedupe_keep_order([term for term in doc_terms if term not in GENERIC_FOCUS_TERMS])[:12]

        for record in self.records:
            score = 0
            source_document = str(record.get("source_document", ""))
            if source_document in current_docs:
                continue
            if any(title and (title in source_document or source_document in title) for title in cited_titles):
                score += 18
            matched_terms = [term for term in doc_terms if term in source_document]
            score += len(matched_terms) * 5
            if score >= 10:
                self.add_candidate(
                    candidate_map,
                    record["atom_id"],
                    score,
                    f"F-上下位规范:{'/'.join(matched_terms[:4]) or source_document}",
                )
        return candidate_map

    def apply_expansions(
        self,
        question: str,
        business_match: dict[str, Any],
        current_atom_ids: list[str],
        set_judge: dict[str, Any],
        atom_analysis: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        merged: dict[str, dict[str, Any]] = {}
        direction_reports: list[dict[str, Any]] = []

        recommended_items = self.normalize_direction_suggestions(set_judge)
        recommended = {item["direction"] for item in recommended_items}
        recommendation_map = {item["direction"]: item for item in recommended_items}
        if not recommended:
            recommended = {"D", "E"}

        expansion_funcs = {
            "A": lambda: self.expand_business_downward(question, business_match, current_atom_ids, atom_analysis),
            "B": lambda: self.expand_lateral(question, business_match, current_atom_ids, atom_analysis),
            "C": lambda: self.expand_regulatory_adjacency(current_atom_ids),
            "D": lambda: self.expand_semantic_completion(question, business_match, current_atom_ids, set_judge, atom_analysis),
            "E": lambda: self.expand_exception_priority(question, business_match, current_atom_ids, atom_analysis),
            "F": lambda: self.expand_norm_supplement(question, current_atom_ids),
        }

        for direction in sorted(recommended):
            expanded = expansion_funcs[direction]()
            for atom_id, payload in expanded.items():
                for reason in payload["reasons"]:
                    self.add_candidate(merged, atom_id, payload["score"], reason)

            sample_atom_ids = self.rank_candidate_ids(expanded, limit=6, per_doc_limit=3) if expanded else []
            direction_hint = recommendation_map.get(direction, {})
            direction_reports.append(
                {
                    "direction": direction,
                    "direction_name": DIRECTION_NAMES[direction],
                    "judge_reason": direction_hint.get("reason", ""),
                    "judge_missing_dimension": direction_hint.get("missing_dimension", ""),
                    "added_candidate_count": len(expanded),
                    "net_new_candidate_count": len([atom_id for atom_id in expanded if atom_id not in current_atom_ids]),
                    "sample_atom_ids": sample_atom_ids,
                    "sample_evidence": self.serialize_records(sample_atom_ids, expanded, include_content=False, limit=4),
                }
            )

        return merged, direction_reports

    def merge_candidate_maps(
        self,
        base: dict[str, dict[str, Any]],
        extra: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        merged = {
            atom_id: {"score": payload["score"], "reasons": list(payload["reasons"])}
            for atom_id, payload in base.items()
        }
        for atom_id, payload in extra.items():
            for reason in payload["reasons"]:
                self.add_candidate(merged, atom_id, payload["score"], reason)
        return merged

    def run(
        self,
        question: str,
        query: str | None = None,
        who: str | None = None,
        max_rounds: int = 3,
        dry_run: bool = False,
        resume_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_query = (query or question).strip()
        if who:
            raw_query = f"{raw_query}::{who}"

        query_spec = parse_query_spec(raw_query)
        business_match = self.build_business_match(question, query_spec)
        fresh_candidate_map = self.build_initial_candidates(question, business_match)
        candidate_map = fresh_candidate_map
        current_atom_ids = self.rank_candidate_ids(candidate_map, limit=self.initial_limit)

        resume_mode = False
        resume_from_atom_count = 0
        if resume_report:
            resume_items = (
                resume_report.get("final_ranked_evidence")
                or resume_report.get("final_evidence")
                or []
            )
            resume_atom_ids, resume_candidate_map = self.rebuild_candidate_map_from_items(resume_items)
            if resume_atom_ids:
                resume_mode = True
                resume_from_atom_count = len(resume_atom_ids)
                candidate_map = self.merge_candidate_maps(fresh_candidate_map, resume_candidate_map)
                current_atom_ids = [atom_id for atom_id in resume_atom_ids if atom_id in self.record_by_id]

        report: dict[str, Any] = {
            "question": question,
            "raw_query": raw_query,
            "query_spec": query_spec,
            "prompt_sources": {name: str(path) for name, path in PROMPT_DOCS.items()},
            "atoms_file": str(self.atoms_file),
            "taxonomy_doc": str(self.taxonomy_doc),
            "model": self.model,
            "recall_judgement_mode": self.recall_judgement_mode,
            "atom_analysis_mode": self.atom_analysis_mode,
            "final_judgement_mode": self.final_judgement_mode,
            "business_match": business_match,
            "resume_mode": resume_mode,
            "fresh_initial_recall_atom_count": len(self.rank_candidate_ids(fresh_candidate_map, limit=self.initial_limit)),
            "resume_from_atom_count": resume_from_atom_count if resume_mode else None,
            "initial_recall_atom_count": len(current_atom_ids),
            "initial_evidence": [self.serialize_record(atom_id, candidate_map, include_content=False) for atom_id in current_atom_ids[:12]],
            "rounds": [],
        }

        if dry_run or max_rounds <= 0:
            report["final_decision"] = "DRY_RUN"
            report["stop_reason"] = "dry_run"
            report["final_recall_atom_count"] = len(current_atom_ids)
            report["final_atom_ids"] = list(current_atom_ids)
            report["final_ranked_evidence"] = self.serialize_records(current_atom_ids, candidate_map, include_content=False)
            report["final_evidence"] = [self.serialize_record(atom_id, candidate_map, include_content=True) for atom_id in current_atom_ids[:20]]
            return self.attach_compliance_summary(report)

        final_decision = "继续召回"
        stop_reason = "max_rounds"
        can_make_final = False
        round_limit_base = max(self.initial_limit, len(current_atom_ids))

        for round_index in range(1, max_rounds + 1):
            set_judge = self.judge_recall_set(question, business_match, current_atom_ids, candidate_map, round_index)
            analyzed_atom_ids = self.select_atoms_for_analysis(current_atom_ids, candidate_map)
            atom_analysis = [
                self.analyze_atom_executability(question, business_match, atom_id, candidate_map)
                for atom_id in analyzed_atom_ids
            ]
            round_focus_terms = self.build_focus_terms(question, business_match, current_atom_ids, atom_analysis)
            missing_profile = self.aggregate_missing_profiles(set_judge, atom_analysis)
            round_payload = {
                "round": round_index,
                "input_atom_count": len(current_atom_ids),
                "input_evidence": self.serialize_records(current_atom_ids, candidate_map, include_content=False, limit=10),
                "focus_terms": round_focus_terms,
                "judge": set_judge,
                "judge_recommended_directions": self.normalize_direction_suggestions(set_judge),
                "judge_missing_summary": self.build_missing_summary(set_judge, atom_analysis),
                "semantic_gap_profiles": missing_profile.get("profiles", []),
                "atom_analysis": atom_analysis,
            }

            final_decision = set_judge["decision"]
            can_make_final = set_judge.get("can_make_final_compliance_judgement", False)
            if final_decision == "停止召回":
                stop_reason = "llm_stop"
                report["rounds"].append(round_payload)
                break

            expanded_map, direction_reports = self.apply_expansions(
                question,
                business_match,
                current_atom_ids,
                set_judge,
                atom_analysis,
            )
            merged_map = self.merge_candidate_maps(candidate_map, expanded_map)
            next_atom_ids = self.rank_candidate_ids(merged_map, limit=round_limit_base + round_index * 8)
            new_atom_ids = [atom_id for atom_id in next_atom_ids if atom_id not in current_atom_ids]

            round_payload["applied_directions"] = direction_reports
            round_payload["new_atom_count"] = len(new_atom_ids)
            round_payload["new_atom_ids"] = new_atom_ids[:20]
            round_payload["new_evidence"] = self.serialize_records(new_atom_ids, merged_map, include_content=True, limit=10)
            round_payload["output_atom_count"] = len(next_atom_ids)
            report["rounds"].append(round_payload)

            candidate_map = merged_map
            current_atom_ids = next_atom_ids

            if not new_atom_ids:
                stop_reason = "no_new_candidates"
                break

        report["judge_final_decision"] = final_decision
        effective_final_decision = final_decision
        if stop_reason == "no_new_candidates" and final_decision == "继续召回":
            effective_final_decision = "停止召回"
        report["final_decision"] = effective_final_decision
        report["can_make_final_compliance_judgement"] = can_make_final
        report["stop_reason"] = stop_reason
        report["final_recall_atom_count"] = len(current_atom_ids)
        report["final_atom_ids"] = list(current_atom_ids)
        report["final_ranked_evidence"] = self.serialize_records(current_atom_ids, candidate_map, include_content=False)
        report["final_evidence"] = [
            self.serialize_record(atom_id, candidate_map, include_content=True)
            for atom_id in current_atom_ids[:24]
        ]
        return self.attach_compliance_summary(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Closed-loop compliance recall controller driven by prompt docs.")
    parser.add_argument("--question", required=True, help="具体业务问题或审核问题。")
    parser.add_argument("--query", help="可选：用于业务图谱命中的短查询。默认回退为 question。")
    parser.add_argument("--who", help="可选：主体关键词，会拼接进 query_spec。")
    parser.add_argument("--atoms-file", default=str(DEFAULT_CLASSIFIED_FILE))
    parser.add_argument("--taxonomy-doc", help="可选：业务分类体系 docx 路径。")
    parser.add_argument("--model", default=None)
    parser.add_argument("--recall-judgement-mode", default="llm", choices=RECALL_JUDGEMENT_MODES)
    parser.add_argument("--atom-analysis-mode", default="llm", choices=ATOM_ANALYSIS_MODES)
    parser.add_argument("--final-judgement-mode", default="llm", choices=FINAL_JUDGEMENT_MODES)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--initial-limit", type=int, default=40)
    parser.add_argument("--judge-evidence-limit", type=int, default=18)
    parser.add_argument("--max-atom-checks", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="只跑本地初始召回，不调用 LLM。")
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    return add_mysql_sync_args(parser)


def main() -> None:
    args = build_parser().parse_args()
    controller = ComplianceRecallController(
        atoms_file=Path(args.atoms_file),
        taxonomy_doc=args.taxonomy_doc,
        model=args.model,
        recall_judgement_mode=args.recall_judgement_mode,
        atom_analysis_mode=args.atom_analysis_mode,
        final_judgement_mode=args.final_judgement_mode,
        initial_limit=args.initial_limit,
        judge_evidence_limit=args.judge_evidence_limit,
        max_atom_checks=args.max_atom_checks,
    )
    try:
        report = controller.run(
            question=args.question,
            query=args.query,
            who=args.who,
            max_rounds=args.max_rounds,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        report = controller.run(
            question=args.question,
            query=args.query,
            who=args.who,
            max_rounds=0,
            dry_run=True,
        )
        report["final_decision"] = "LLM_ERROR"
        report["stop_reason"] = "llm_connection_error"
        report["error"] = str(exc)
        controller.attach_compliance_summary(report)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Recall loop report saved to: {output_path}")
    print(f"Final decision: {report['final_decision']}")
    print(f"Final evidence count: {report['final_recall_atom_count']}")
    sync_results = maybe_sync_artifacts_from_args(
        args,
        items=[("compliance_recall_report", output_path)],
        default_batch_label=f"compliance-recall-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        source_dir=output_path.parent,
        batch_extra_json={
            "pipeline_step": "compliance_recall_controller",
            "question": args.question,
            "query": args.query,
            "who": args.who,
            "dry_run": args.dry_run,
        },
    )
    for item in sync_results:
        print(f"MySQL sync [{item['status']}] {item['artifact_type']}: {item['path']}")


if __name__ == "__main__":
    main()
