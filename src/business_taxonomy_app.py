import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase
from streamlit_agraph import Config, Edge, Node, agraph

from business_taxonomy_pipeline import (
    UNCLASSIFIED_CODE,
    build_scene_actor_rows,
    build_scene_match_rows,
    classify_atoms,
    load_business_graph,
    parse_taxonomy,
    resolve_taxonomy_doc,
)
from compliance_recall_controller import ComplianceRecallController
from main import run_pipeline
from run_stage1_2_ner import run_phase1

URI = "bolt://localhost:7687"
AUTH = ("neo4j", "123456")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

SCENARIO_PRESETS = {
    "bank_draft_presentment": {
        "title": "银行汇票提示付款",
        "question": "未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？",
        "query": "银行汇票",
        "recall_who": "持票人",
        "module_code": "BIZ-02-03",
        "scene_key": "BIZ-02-03-SCENE-01",
        "actor_terms": ["持票人", "未在银行开立存款账户的个人持票人"],
        "focus_groups": [
            {"label": "提示付款", "terms": ["提示付款"]},
            {"label": "提交材料", "terms": ["身份证件", "解讫通知", "银行汇票", "证明", "材料"]},
            {"label": "签章要求", "terms": ["签章", "背书"]},
            {"label": "现金支取", "terms": ["现金", "支取现金"]},
        ],
    },
    "commercial_bill_discount": {
        "title": "商业汇票贴现申请",
        "question": "符合条件的商业汇票持票人向银行申请贴现，需要满足哪些条件、提供哪些材料、是否需要作成转让背书？",
        "query": "商业汇票",
        "recall_who": "持票人",
        "module_code": "BIZ-02-03",
        "scene_key": "BIZ-02-03-SCENE-02",
        "actor_terms": ["持票人", "商业汇票持票人", "贴现银行"],
        "focus_groups": [
            {"label": "贴现准入", "terms": ["贴现", "符合条件", "在银行开立存款账户"]},
            {"label": "贸易背景", "terms": ["真实交易关系", "商品交易关系", "债权债务关系"]},
            {"label": "申请材料", "terms": ["增值税发票", "商品发运单据", "贴现凭证", "材料"]},
            {"label": "转让背书", "terms": ["转让背书", "背书", "转贴现", "再贴现"]},
        ],
    },
    "bank_note_cash_remedy": {
        "title": "银行本票现金兑付与救济",
        "question": "未在银行开立存款账户的个人持注明“现金”字样的银行本票向出票银行支取现金，是否可以委托他人提示付款；若超过提示付款期限未获付款或票据丧失，还应如何办理？",
        "query": "银行本票",
        "recall_who": "持票人",
        "module_code": "BIZ-02-03",
        "scene_key": "BIZ-02-03-SCENE-03",
        "actor_terms": ["持票人", "未在银行开立存款账户的个人持票人", "被委托人", "出票银行"],
        "focus_groups": [
            {"label": "现金支取", "terms": ["现金", "支取现金", "身份证件", "复印件"]},
            {"label": "委托提示付款", "terms": ["委托收款", "被委托人", "背书日期"]},
            {"label": "逾期救济", "terms": ["提示付款期限", "2个月", "作出说明", "请求付款"]},
            {"label": "失票救济", "terms": ["丧失", "挂失止付", "人民法院", "退款"]},
        ],
    },
}


@st.cache_resource
def get_driver():
    return GraphDatabase.driver(URI, auth=AUTH)


@st.cache_resource
def get_recall_controller(model_name):
    return ComplianceRecallController(
        model=model_name,
        recall_judgement_mode="llm",
        atom_analysis_mode="llm",
        final_judgement_mode="llm",
    )


def split_keyword_lines(text):
    parts = re.split(r"[\r\n,，;；]+", str(text or ""))
    return [part.strip() for part in parts if part.strip()]


def list_raw_docs():
    return sorted([path for path in RAW_DIR.glob("*.docx") if not path.name.startswith("~$")], key=lambda item: item.name)


def build_unique_raw_doc_path(filename):
    safe_name = Path(str(filename or "").strip()).name.replace("\\", "_").replace("/", "_")
    candidate = RAW_DIR / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        alt_path = RAW_DIR / f"{stem}_{index}{suffix}"
        if not alt_path.exists():
            return alt_path
        index += 1


def save_uploaded_docx_files(uploaded_files):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    saved_names = []
    skipped_names = []

    for uploaded_file in uploaded_files or []:
        original_name = Path(str(getattr(uploaded_file, "name", "") or "").strip()).name
        if not original_name or Path(original_name).suffix.lower() != ".docx":
            skipped_names.append(original_name or "unnamed")
            continue

        target_path = build_unique_raw_doc_path(original_name)
        target_path.write_bytes(uploaded_file.getvalue())
        saved_names.append(target_path.name)

    return saved_names, skipped_names


def match_raw_docs_by_names(doc_names):
    selected_names = {str(name or "").strip() for name in doc_names or [] if str(name or "").strip()}
    if not selected_names:
        return []
    return [path for path in list_raw_docs() if path.name in selected_names]


def match_raw_docs(doc_keywords):
    docs = list_raw_docs()
    if not doc_keywords:
        return docs
    return [path for path in docs if any(keyword in path.name for keyword in doc_keywords)]


def normalize_flag(value):
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes", "y", "是"}


def safe_load_list(value):
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


@contextmanager
def temporary_api_env(api_key, base_url, model, reasoning_model):
    env_keys = {
        "DASHSCOPE_API_KEY": api_key,
        "DASHSCOPE_BASE_URL": base_url,
        "QWEN_MODEL": model,
        "QWEN_REASONING_MODEL": reasoning_model or model,
    }
    old_values = {key: os.environ.get(key) for key in env_keys}
    try:
        for key, value in env_keys.items():
            os.environ[key] = str(value or "").strip()
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def validate_llm_inputs(api_key, base_url, model, model_label="模型"):
    missing = []
    if not str(api_key or "").strip():
        missing.append("API Key")
    if not str(base_url or "").strip():
        missing.append("Base URL")
    if not str(model or "").strip():
        missing.append(model_label)
    return missing


def preview_dataframe(df, columns, limit=12):
    available = [column for column in columns if column in df.columns]
    if not available:
        return df.head(limit)
    return df[available].head(limit)


def summarize_phase1_file(path):
    df = pd.read_excel(path).fillna("")
    entity_counts = [len(safe_load_list(value)) for value in df.get("ner_entities_json", [])]
    summary = {
        "path": str(path),
        "rows": len(df),
        "doc_count": int(df["source_document"].nunique()) if "source_document" in df.columns else 0,
        "chunk_count": len(df),
        "non_empty_chunk_count": sum(1 for count in entity_counts if count > 0),
        "entity_total": sum(entity_counts),
        "preview": preview_dataframe(
            df.assign(entity_count=entity_counts),
            ["source_document", "chunk_index", "entity_count", "content_original"],
        ),
    }
    return summary


def summarize_atoms_file(path):
    df = pd.read_excel(path).fillna("")
    summary = {
        "path": str(path),
        "rows": len(df),
        "doc_count": int(df["source_document"].nunique()) if "source_document" in df.columns else 0,
        "rule_type_count": int(df["rule_type"].nunique()) if "rule_type" in df.columns else 0,
        "ambiguous_count": sum(1 for value in df.get("is_ambiguous", []) if normalize_flag(value)),
        "preview": preview_dataframe(
            df,
            ["atom_id", "source_document", "rule_type", "who", "what", "how", "is_ambiguous"],
            limit=15,
        ),
    }
    return summary


def summarize_classified_file(path):
    df = pd.read_excel(path).fillna("")
    label_lists = [safe_load_list(value) for value in df.get("business_taxonomy_label_codes", [])]
    labelled_rows = sum(1 for labels in label_lists if any(label != UNCLASSIFIED_CODE for label in labels))
    distinct_modules = sorted({label for labels in label_lists for label in labels if label and label != UNCLASSIFIED_CODE})
    _, entries, scenes = parse_taxonomy(resolve_taxonomy_doc())
    scene_match_rows = build_scene_match_rows(df, scenes)
    scene_actor_rows = build_scene_actor_rows(df, scene_match_rows)

    summary = {
        "path": str(path),
        "rows": len(df),
        "labelled_rows": labelled_rows,
        "unclassified_rows": len(df) - labelled_rows,
        "module_count": len(distinct_modules),
        "scene_match_count": len(scene_match_rows),
        "scene_actor_count": len(scene_actor_rows),
        "preview": preview_dataframe(
            df.assign(
                label_paths_display=[
                    " | ".join(safe_load_list(value)[:3])
                    for value in df.get("business_taxonomy_label_paths", [])
                ]
            ),
            ["atom_id", "source_document", "rule_type", "label_paths_display", "business_classification_reason"],
            limit=15,
        ),
        "entries": entries,
        "scenes": scenes,
    }
    return summary


SESSION_ARTIFACT_SUMMARY_KEYS = (
    "extract_phase1_summary",
    "extract_atoms_summary",
    "extract_classified_summary",
)


def build_artifact_row(path_value):
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists() or not path.is_file():
        return None
    return {
        "name": path.name,
        "size_kb": round(path.stat().st_size / 1024, 1),
        "path": str(path),
    }


def get_session_generated_artifacts():
    artifacts = st.session_state.get("extract_generated_artifacts")
    if isinstance(artifacts, list):
        return artifacts

    restored = []
    seen_paths = set()
    for key in SESSION_ARTIFACT_SUMMARY_KEYS:
        summary = st.session_state.get(key) or {}
        artifact = build_artifact_row(summary.get("path"))
        if not artifact or artifact["path"] in seen_paths:
            continue
        restored.append(artifact)
        seen_paths.add(artifact["path"])

    restored.sort(key=lambda item: item["name"])
    st.session_state["extract_generated_artifacts"] = restored
    return restored


def remember_generated_artifact(path_value):
    artifact = build_artifact_row(path_value)
    if not artifact:
        return

    artifacts = [
        item for item in get_session_generated_artifacts()
        if item.get("path") != artifact["path"]
    ]
    artifacts.append(artifact)
    artifacts.sort(key=lambda item: item["name"])
    st.session_state["extract_generated_artifacts"] = artifacts


def safe_ratio(numerator, denominator):
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def format_percent(value):
    if value is None:
        return "-"
    return f"{value * 100:.0f}%"


def format_checklist_status(status):
    mapping = {
        "pass": "通过",
        "warn": "关注",
        "missing": "缺失",
        "manual": "待人工",
    }
    text = str(status or "").strip()
    return mapping.get(text, text or "-")


def fetch_graph_health_stats(driver):
    queries = {
        "boards": "MATCH (n:BusinessBoard) RETURN count(n) AS c",
        "categories": "MATCH (n:BusinessCategory) RETURN count(n) AS c",
        "modules": "MATCH (n:BusinessModule) RETURN count(n) AS c",
        "scenes": "MATCH (n:BusinessScene) RETURN count(n) AS c",
        "atoms": "MATCH (n:BusinessAtom) RETURN count(n) AS c",
        "actors": "MATCH (n:BusinessActor) RETURN count(n) AS c",
        "tags": "MATCH ()-[r:TAGGED_AS]->() RETURN count(r) AS c",
        "scene_matches": "MATCH ()-[r:MATCHES_SCENE]->() RETURN count(r) AS c",
        "scene_actors": "MATCH ()-[r:SCENE_HAS_ACTOR]->() RETURN count(r) AS c",
    }
    stats = {}
    with driver.session() as session:
        for key, query in queries.items():
            row = session.run(query).single()
            stats[key] = int(row["c"] if row is not None else 0)
    return stats


CHECKLIST_SUMMARY_LOADERS = {
    "extract_phase1_summary": ("phase1_entities_checkpoint.xlsx", summarize_phase1_file),
    "extract_atoms_summary": ("legal_atoms_v4_final.xlsx", summarize_atoms_file),
    "extract_classified_summary": ("legal_atoms_business_taxonomy.xlsx", summarize_classified_file),
}


DEFAULT_TYPICAL_REVIEW_RATIO_PCT = 10
DEFAULT_TYPICAL_REVIEW_CAP = 24
DEFAULT_FUZZY_REVIEW_RATIO_PCT = 100
DEFAULT_FUZZY_REVIEW_CAP = 120
DEFAULT_RECALL_PARALLEL_WORKERS = 3
MAX_RECALL_PARALLEL_WORKERS = 8


def ensure_review_sampling_defaults():
    defaults = {
        "extract_review_typical_ratio_pct": DEFAULT_TYPICAL_REVIEW_RATIO_PCT,
        "extract_review_typical_cap": DEFAULT_TYPICAL_REVIEW_CAP,
        "extract_review_fuzzy_ratio_pct": DEFAULT_FUZZY_REVIEW_RATIO_PCT,
        "extract_review_fuzzy_cap": DEFAULT_FUZZY_REVIEW_CAP,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def get_review_sampling_settings():
    ensure_review_sampling_defaults()
    return {
        "typical_ratio_pct": int(st.session_state.get("extract_review_typical_ratio_pct", DEFAULT_TYPICAL_REVIEW_RATIO_PCT)),
        "typical_cap": int(st.session_state.get("extract_review_typical_cap", DEFAULT_TYPICAL_REVIEW_CAP)),
        "fuzzy_ratio_pct": int(st.session_state.get("extract_review_fuzzy_ratio_pct", DEFAULT_FUZZY_REVIEW_RATIO_PCT)),
        "fuzzy_cap": int(st.session_state.get("extract_review_fuzzy_cap", DEFAULT_FUZZY_REVIEW_CAP)),
    }


def resolve_review_sample_limit(total_rows, ratio_pct, minimum=1, cap=None):
    total_rows = int(total_rows or 0)
    if total_rows <= 0:
        return 0

    ratio_value = max(float(ratio_pct or 0.0), 0.0)
    sample_size = max(int(minimum or 1), math.ceil(total_rows * ratio_value / 100.0))
    if cap:
        sample_size = min(sample_size, int(cap))
    return min(sample_size, total_rows)


def resolve_typical_review_limit(total_rows, minimum=3):
    settings = get_review_sampling_settings()
    return resolve_review_sample_limit(
        total_rows,
        settings["typical_ratio_pct"],
        minimum=minimum,
        cap=settings["typical_cap"],
    )


def resolve_attention_review_limit(total_rows, minimum=1):
    settings = get_review_sampling_settings()
    attention_cap = max(3, min(settings["typical_cap"], 12))
    return resolve_review_sample_limit(
        total_rows,
        settings["typical_ratio_pct"],
        minimum=minimum,
        cap=attention_cap,
    )


def resolve_fuzzy_review_limit(total_rows, minimum=1):
    settings = get_review_sampling_settings()
    return resolve_review_sample_limit(
        total_rows,
        settings["fuzzy_ratio_pct"],
        minimum=minimum,
        cap=settings["fuzzy_cap"],
    )


def build_review_sampling_caption():
    settings = get_review_sampling_settings()
    return (
        f"典型样本按 {settings['typical_ratio_pct']}% 抽样，单表最多 {settings['typical_cap']} 条；"
        f"模糊原子按 {settings['fuzzy_ratio_pct']}% 进入复核，单表最多 {settings['fuzzy_cap']} 条。"
    )


def render_review_sampling_controls():
    ensure_review_sampling_defaults()
    with st.expander("抽样配置", expanded=False):
        st.caption("抽取与构建完成后，系统先按百分比抽取典型样本；被模型标记为模糊的原子单独进入模糊复核队列。")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.number_input("典型样本比例(%)", min_value=1, max_value=100, step=1, key="extract_review_typical_ratio_pct")
        with col2:
            st.number_input("典型样本上限", min_value=1, max_value=500, step=1, key="extract_review_typical_cap")
        with col3:
            st.number_input("模糊复核比例(%)", min_value=1, max_value=100, step=1, key="extract_review_fuzzy_ratio_pct")
        with col4:
            st.number_input("模糊复核上限", min_value=1, max_value=1000, step=1, key="extract_review_fuzzy_cap")
        st.caption(build_review_sampling_caption())


def get_checklist_summary(summary_key):
    summary = st.session_state.get(summary_key)
    if summary and Path(str(summary.get("path") or "")).exists():
        return summary

    filename, summarizer = CHECKLIST_SUMMARY_LOADERS.get(summary_key, (None, None))
    if not filename or summarizer is None:
        return None

    path = PROCESSED_DIR / filename
    if not path.exists():
        return None

    try:
        summary = summarizer(path)
        st.session_state[summary_key] = summary
        return summary
    except Exception:
        return None


def shorten_text(value, limit=120):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def pick_diverse_rows(df, group_col, limit):
    if df.empty:
        return df.head(0)

    selected_indexes = []
    seen_groups = set()

    if group_col in df.columns:
        for idx, row in df.iterrows():
            group_value = str(row.get(group_col, "") or "").strip()
            if group_value and group_value not in seen_groups:
                seen_groups.add(group_value)
                selected_indexes.append(idx)
            if len(selected_indexes) >= limit:
                break

    if len(selected_indexes) < limit:
        for idx in df.index:
            if idx not in selected_indexes:
                selected_indexes.append(idx)
            if len(selected_indexes) >= limit:
                break

    return df.loc[selected_indexes]


def build_phase1_representative_samples(path, limit=None):
    df = pd.read_excel(path).fillna("")
    if df.empty:
        return df

    if limit is None:
        limit = resolve_typical_review_limit(len(df), minimum=3)

    entity_lists = [safe_load_list(value) for value in df.get("ner_entities_json", [])]
    df = df.assign(
        entity_count=[len(items) for items in entity_lists],
        entity_preview=[
            format_joined(
                [
                    f"{item.get('entity_name', '')}({item.get('entity_type', '')})"
                    for item in items[:6]
                    if isinstance(item, dict)
                ],
                sep=" | ",
            )
            for items in entity_lists
        ],
        content_preview=[shorten_text(value, limit=90) for value in df.get("content_original", [])],
    )
    ranked = df.sort_values(["entity_count", "chunk_index"], ascending=[False, True], na_position="last")
    samples = pick_diverse_rows(ranked, "source_document", limit)
    return preview_dataframe(
        samples,
        ["source_document", "chunk_index", "entity_count", "entity_preview", "content_preview"],
        limit=limit,
    )


def build_atoms_representative_samples(path, typical_limit=None, fuzzy_limit=None):
    df = pd.read_excel(path).fillna("")
    if df.empty:
        return df.head(0), df.head(0)

    if typical_limit is None:
        typical_limit = resolve_typical_review_limit(len(df), minimum=3)

    df = df.assign(
        ambiguous_flag=[normalize_flag(value) for value in df.get("is_ambiguous", [])],
        text_score=[
            len(str(what or "")) + len(str(how or "")) + len(str(content or ""))
            for what, how, content in zip(df.get("what", []), df.get("how", []), df.get("content_original", []))
        ],
        content_preview=[shorten_text(value, limit=90) for value in df.get("content_original", [])],
    )
    df["rule_type_group_size"] = df.groupby("rule_type")["rule_type"].transform("size")
    ranked = df.sort_values(
        ["rule_type_group_size", "ambiguous_flag", "text_score"],
        ascending=[False, True, False],
        na_position="last",
    )
    typical = pick_diverse_rows(ranked, "rule_type", typical_limit)
    fuzzy_candidates = df[df["ambiguous_flag"]].copy()
    if fuzzy_limit is None:
        fuzzy_limit = resolve_fuzzy_review_limit(len(fuzzy_candidates), minimum=1)
    fuzzy_ranked = fuzzy_candidates.sort_values(["review_reason", "text_score"], ascending=[True, False], na_position="last")
    fuzzy_samples = pick_diverse_rows(fuzzy_ranked, "review_reason", fuzzy_limit)
    return (
        preview_dataframe(
            typical,
            ["atom_id", "rule_type", "who", "what", "how", "source_document", "content_preview"],
            limit=typical_limit,
        ),
        preview_dataframe(
            fuzzy_samples,
            ["atom_id", "rule_type", "who", "what", "how", "review_reason", "source_document", "content_preview"],
            limit=fuzzy_limit,
        ),
    )


def build_classified_representative_samples(path, limit=None, attention_limit=None):
    df = pd.read_excel(path).fillna("")
    if df.empty:
        empty = df.head(0)
        return empty, empty

    label_lists = [safe_load_list(value) for value in df.get("business_taxonomy_label_paths", [])]
    code_lists = [safe_load_list(value) for value in df.get("business_taxonomy_label_codes", [])]
    df = df.assign(
        primary_label=[labels[0] if labels else "未分类" for labels in label_lists],
        label_paths_display=[" | ".join(labels[:3]) if labels else "未分类" for labels in label_lists],
        reason_preview=[shorten_text(value, limit=90) for value in df.get("business_classification_reason", [])],
        unclassified_flag=[
            (not codes) or all(str(code or "").strip() == UNCLASSIFIED_CODE for code in codes)
            for codes in code_lists
        ],
    )
    classified = df[~df["unclassified_flag"]].copy()
    if not classified.empty:
        classified["label_group_size"] = classified.groupby("primary_label")["primary_label"].transform("size")
        classified = classified.sort_values(["label_group_size", "primary_label"], ascending=[False, True], na_position="last")
    unclassified = df[df["unclassified_flag"]].copy().sort_values(["rule_type", "atom_id"], ascending=[True, True], na_position="last")

    if limit is None:
        limit = resolve_typical_review_limit(len(classified) or len(df), minimum=3)
    if attention_limit is None:
        attention_limit = resolve_attention_review_limit(len(unclassified), minimum=1)

    typical = pick_diverse_rows(classified, "primary_label", limit) if not classified.empty else classified
    return (
        preview_dataframe(
            typical,
            ["atom_id", "rule_type", "what", "label_paths_display", "reason_preview", "source_document"],
            limit=limit,
        ),
        preview_dataframe(
            unclassified,
            ["atom_id", "rule_type", "what", "how", "source_document"],
            limit=attention_limit,
        ),
    )


REVIEW_JUDGEMENT_OPTIONS = ["待判断", "通过", "存疑", "明显错误"]


def build_sample_review_id(row, key_columns, fallback_prefix, fallback_index):
    parts = []
    for column in key_columns:
        value = str(row.get(column, "") or "").strip()
        if value:
            parts.append(value)
    if parts:
        return " | ".join(parts)
    return f"{fallback_prefix}-{fallback_index + 1}"


def prepare_review_dataframe(df, sample_group, sample_label, key_columns, storage_key):
    if df is None or df.empty:
        return pd.DataFrame()

    review_df = df.copy().reset_index(drop=True)
    review_df.insert(0, "样本组", sample_group)
    review_df.insert(1, "样本标签", sample_label)
    review_df.insert(
        2,
        "样本ID",
        [
            build_sample_review_id(row, key_columns, sample_group, index)
            for index, row in review_df.iterrows()
        ],
    )

    existing = st.session_state.get(storage_key)
    judgement_map = {}
    note_map = {}
    if isinstance(existing, pd.DataFrame) and not existing.empty and "样本ID" in existing.columns:
        if "判定" in existing.columns:
            judgement_map = existing.set_index("样本ID")["判定"].to_dict()
        if "备注" in existing.columns:
            note_map = existing.set_index("样本ID")["备注"].to_dict()

    review_df["判定"] = [judgement_map.get(sample_id, "待判断") for sample_id in review_df["样本ID"]]
    review_df["备注"] = [note_map.get(sample_id, "") for sample_id in review_df["样本ID"]]
    return review_df


def calculate_review_metrics(df):
    if df is None or df.empty or "判定" not in df.columns:
        return {
            "rows": 0,
            "pending": 0,
            "reviewed": 0,
            "pass": 0,
            "questionable": 0,
            "wrong": 0,
            "issue": 0,
            "completion_rate": None,
            "pass_review_rate": None,
        }

    counts = df["判定"].value_counts()
    rows = len(df)
    pending = int(counts.get("待判断", 0))
    passed = int(counts.get("通过", 0))
    questionable = int(counts.get("存疑", 0))
    wrong = int(counts.get("明显错误", 0))
    reviewed = rows - pending
    issue = questionable + wrong
    return {
        "rows": rows,
        "pending": pending,
        "reviewed": reviewed,
        "pass": passed,
        "questionable": questionable,
        "wrong": wrong,
        "issue": issue,
        "completion_rate": safe_ratio(reviewed, rows),
        "pass_review_rate": safe_ratio(passed, reviewed),
    }


def aggregate_review_metrics(review_tables):
    aggregate = {
        "rows": 0,
        "pending": 0,
        "reviewed": 0,
        "pass": 0,
        "questionable": 0,
        "wrong": 0,
        "issue": 0,
        "completion_rate": None,
        "pass_review_rate": None,
    }
    for df in (review_tables or {}).values():
        metrics = calculate_review_metrics(df)
        for key in ("rows", "pending", "reviewed", "pass", "questionable", "wrong", "issue"):
            aggregate[key] += metrics[key]
    aggregate["completion_rate"] = safe_ratio(aggregate["reviewed"], aggregate["rows"])
    aggregate["pass_review_rate"] = safe_ratio(aggregate["pass"], aggregate["reviewed"])
    return aggregate


def render_review_rate_metrics(review_tables):
    metrics = aggregate_review_metrics(review_tables)
    if metrics["rows"] <= 0:
        return

    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("复核样本", metrics["rows"])
    metric2.metric("已判定", metrics["reviewed"])
    metric3.metric("复核完成率", format_percent(metrics["completion_rate"]))
    metric4.metric("人工通过复核率", format_percent(metrics["pass_review_rate"]))
    st.caption("人工通过复核率 = “通过”样本数 / 已判定样本数，不包含“待判断”。")


def render_review_editor(title, review_df, editor_key, caption_text):
    st.markdown(f"**{title}**")
    if review_df.empty:
        st.info("当前没有可供判定的样本。")
        return review_df

    edited_df = st.data_editor(
        review_df,
        key=f"review_editor::{editor_key}",
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        disabled=[column for column in review_df.columns if column not in {"判定", "备注"}],
        column_config={
            "判定": st.column_config.SelectboxColumn("判定", options=REVIEW_JUDGEMENT_OPTIONS, required=True),
            "备注": st.column_config.TextColumn("备注", width="large"),
        },
    )
    st.session_state[f"sample_review::{editor_key}"] = edited_df

    metrics = calculate_review_metrics(edited_df)
    metric1, metric2, metric3, metric4, metric5 = st.columns(5)
    metric1.metric("样本数", len(edited_df))
    metric2.metric("已判定", metrics["reviewed"])
    metric3.metric("通过", metrics["pass"])
    metric4.metric("问题样本", metrics["issue"])
    metric5.metric("通过复核率", format_percent(metrics["pass_review_rate"]))
    st.caption(caption_text)
    return edited_df


def build_sample_review_summary(review_tables):
    rows = []
    for sheet_name, df in review_tables.items():
        if df is None or df.empty:
            continue
        metrics = calculate_review_metrics(df)
        rows.append(
            {
                "sheet_name": sheet_name,
                "rows": metrics["rows"],
                "pending": metrics["pending"],
                "reviewed": metrics["reviewed"],
                "pass": metrics["pass"],
                "questionable": metrics["questionable"],
                "wrong": metrics["wrong"],
                "completion_rate": metrics["completion_rate"],
                "pass_review_rate": metrics["pass_review_rate"],
            }
        )
    return pd.DataFrame(rows)


def export_sample_reviews_xlsx(review_tables):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = PROCESSED_DIR / f"sample_review_checklist_{timestamp}.xlsx"

    summary_df = build_sample_review_summary(review_tables)
    notes_df = pd.DataFrame(
        [
            {"section": "抽取与分类核查备注", "notes": st.session_state.get("checklist_notes_extraction", "")},
            {"section": "图谱与召回核查备注", "notes": st.session_state.get("checklist_notes_graph", "")},
        ]
    )

    with pd.ExcelWriter(output_path) as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        notes_df.to_excel(writer, sheet_name="notes", index=False)
        for sheet_name, df in review_tables.items():
            export_df = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
            export_df.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    remember_generated_artifact(output_path)
    return output_path


def render_representative_samples_panel():
    st.subheader("典型示例抽样")
    st.caption("这些样本直接从已完成的抽取结果文件中自动挑选，用于快速判断抽取方向、原子拆分和业务映射是否大体正确。")

    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")

    phase1_tab, atoms_tab, classified_tab = st.tabs(["Phase1/2 示例", "原子抽取示例", "业务分类示例"])

    with phase1_tab:
        if not phase1_summary:
            st.info("当前没有可用的 Phase1/2 文件。")
        else:
            samples = build_phase1_representative_samples(Path(phase1_summary["path"]))
            st.dataframe(samples, width="stretch", hide_index=True)
            st.caption("快速判断：实体是否集中在条款核心主体、事项、时间或空间，而不是标题噪声。")

    with atoms_tab:
        if not atoms_summary:
            st.info("当前没有可用的原子抽取文件。")
        else:
            typical, attention = build_atoms_representative_samples(Path(atoms_summary["path"]))
            st.markdown("**典型样本**")
            st.dataframe(typical, width="stretch", hide_index=True)
            st.caption("快速判断：who / what / how 是否能组合成可执行规则，而不是整段原文照搬。")
            if not attention.empty:
                st.markdown("**关注样本**")
                st.dataframe(attention, width="stretch", hide_index=True)
                st.caption("这些样本来自歧义原子，适合优先判断拆分粒度是否偏粗。")

    with classified_tab:
        if not classified_summary:
            st.info("当前没有可用的业务分类文件。")
        else:
            typical, attention = build_classified_representative_samples(Path(classified_summary["path"]))
            st.markdown("**典型样本**")
            st.dataframe(typical, width="stretch", hide_index=True)
            st.caption("快速判断：业务路径和分类理由是否和条款语义一致。")
            if not attention.empty:
                st.markdown("**关注样本**")
                st.dataframe(attention, width="stretch", hide_index=True)
                st.caption("这些样本当前未完成分类，适合判断是否需要补 taxonomy、补提示词或重跑分类。")


def render_representative_review_panel():
    st.subheader("样本判定")
    st.caption("这里可以直接对代表样本做通过 / 存疑 / 明显错误的判断，并在导出时一起写入 Excel。")

    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")
    review_tables = {}

    phase1_tab, atoms_tab, classified_tab = st.tabs(["Phase1/2 判定", "原子抽取判定", "业务分类判定"])

    with phase1_tab:
        if not phase1_summary:
            st.info("当前没有可用的 Phase1/2 文件。")
        else:
            samples = build_phase1_representative_samples(Path(phase1_summary["path"]))
            phase1_review_df = prepare_review_dataframe(
                samples,
                sample_group="Phase1/2",
                sample_label="代表实体样本",
                key_columns=["source_document", "chunk_index"],
                storage_key="sample_review::phase1_samples",
            )
            review_tables["phase1_samples"] = render_review_editor(
                "实体方向判定",
                phase1_review_df,
                "phase1_samples",
                "重点判断：实体是否命中了法规核心主体、事项和场景，而不是目录或标题噪声。",
            )

    with atoms_tab:
        if not atoms_summary:
            st.info("当前没有可用的原子抽取文件。")
        else:
            typical, attention = build_atoms_representative_samples(Path(atoms_summary["path"]))
            atoms_typical_review_df = prepare_review_dataframe(
                typical,
                sample_group="原子抽取",
                sample_label="典型样本",
                key_columns=["atom_id"],
                storage_key="sample_review::atoms_typical",
            )
            review_tables["atoms_typical"] = render_review_editor(
                "典型原子判定",
                atoms_typical_review_df,
                "atoms_typical",
                "重点判断：who / what / how 是否已经拆成可执行规则，而不是整段原文照搬。",
            )
            if not attention.empty:
                atoms_attention_review_df = prepare_review_dataframe(
                    attention,
                    sample_group="原子抽取",
                    sample_label="关注样本",
                    key_columns=["atom_id"],
                    storage_key="sample_review::atoms_attention",
                )
                review_tables["atoms_attention"] = render_review_editor(
                    "关注原子判定",
                    atoms_attention_review_df,
                    "atoms_attention",
                    "这些样本来自歧义原子，优先判断拆分粒度是否偏粗或字段是否缺失。",
                )

    with classified_tab:
        if not classified_summary:
            st.info("当前没有可用的业务分类文件。")
        else:
            typical, attention = build_classified_representative_samples(Path(classified_summary["path"]))
            classified_typical_review_df = prepare_review_dataframe(
                typical,
                sample_group="业务分类",
                sample_label="典型样本",
                key_columns=["atom_id"],
                storage_key="sample_review::classified_typical",
            )
            review_tables["classified_typical"] = render_review_editor(
                "典型分类判定",
                classified_typical_review_df,
                "classified_typical",
                "重点判断：label path 与 classification reason 是否和条款语义一致。",
            )
            if not attention.empty:
                classified_attention_review_df = prepare_review_dataframe(
                    attention,
                    sample_group="业务分类",
                    sample_label="关注样本",
                    key_columns=["atom_id"],
                    storage_key="sample_review::classified_attention",
                )
                review_tables["classified_attention"] = render_review_editor(
                    "关注分类判定",
                    classified_attention_review_df,
                    "classified_attention",
                    "这些样本当前未完成分类，适合判断是否需要补 taxonomy、补提示词或重跑分类。",
                )

    return review_tables


def build_extraction_checklist_items():
    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")

    items = []

    if phase1_summary:
        entity_ratio = safe_ratio(phase1_summary["non_empty_chunk_count"], phase1_summary["chunk_count"])
        phase1_status = "pass" if phase1_summary["entity_total"] > 0 and (entity_ratio or 0.0) >= 0.3 else "warn"
        items.append(
            {
                "key": "extract_phase1_presence",
                "title": "Phase1/2 实体抽取已产出有效结果",
                "status": phase1_status,
                "detail": (
                    f"文档 {phase1_summary['doc_count']} 份，切块 {phase1_summary['chunk_count']} 个，"
                    f"有实体切块 {phase1_summary['non_empty_chunk_count']} 个，占比 {format_percent(entity_ratio)}，"
                    f"实体总数 {phase1_summary['entity_total']}。"
                ),
                "suggestion": "抽样查看实体数为 0 的切块，确认是否为空页、扫描问题或规则未命中。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_phase1_presence",
                "title": "Phase1/2 实体抽取已产出有效结果",
                "status": "missing",
                "detail": "当前会话还没有 Phase1/2 输出摘要。",
                "suggestion": "先运行 Phase1/2，再回到本页做人工核查。",
            }
        )

    if atoms_summary:
        ambiguous_ratio = safe_ratio(atoms_summary["ambiguous_count"], atoms_summary["rows"])
        atom_status = "pass" if atoms_summary["rows"] > 0 and atoms_summary["rule_type_count"] > 0 else "warn"
        ambiguity_status = "pass" if (ambiguous_ratio or 0.0) <= 0.25 else "warn"
        items.append(
            {
                "key": "extract_atoms_presence",
                "title": "原子抽取结果已生成",
                "status": atom_status,
                "detail": (
                    f"原子 {atoms_summary['rows']} 条，来源法规 {atoms_summary['doc_count']} 份，"
                    f"规则类型 {atoms_summary['rule_type_count']} 种。"
                ),
                "suggestion": "优先查看 preview 中 what/how 为空或明显过短的原子。",
            }
        )
        items.append(
            {
                "key": "extract_atoms_ambiguity",
                "title": "歧义原子比例处于可复核范围",
                "status": ambiguity_status,
                "detail": (
                    f"歧义原子 {atoms_summary['ambiguous_count']} 条，"
                    f"占全部原子的 {format_percent(ambiguous_ratio)}。"
                ),
                "suggestion": "若比例偏高，建议回查抽取提示词或先做小样本重跑。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_atoms_presence",
                "title": "原子抽取结果已生成",
                "status": "missing",
                "detail": "当前会话还没有原子抽取输出摘要。",
                "suggestion": "先运行原子抽取，再核查字段质量。",
            }
        )

    if classified_summary:
        coverage_ratio = safe_ratio(classified_summary["labelled_rows"], classified_summary["rows"])
        coverage_status = "pass" if (coverage_ratio or 0.0) >= 0.7 else "warn"
        scene_status = "pass" if classified_summary["scene_match_count"] > 0 else "warn"
        items.append(
            {
                "key": "extract_classification_coverage",
                "title": "业务分类覆盖率可接受",
                "status": coverage_status,
                "detail": (
                    f"已分类 {classified_summary['labelled_rows']} 条，未分类 {classified_summary['unclassified_rows']} 条，"
                    f"覆盖率 {format_percent(coverage_ratio)}，命中业务模块 {classified_summary['module_count']} 个。"
                ),
                "suggestion": "重点核查未分类原子是否集中在同一法规主题或同一规则类型。",
            }
        )
        items.append(
            {
                "key": "extract_scene_match_presence",
                "title": "场景挂接结果已生成",
                "status": scene_status,
                "detail": (
                    f"场景挂接关系 {classified_summary['scene_match_count']} 条，"
                    f"场景主体关系 {classified_summary['scene_actor_count']} 条。"
                ),
                "suggestion": "若场景挂接偏少，优先检查 taxonomy 映射和场景关键词。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_classification_coverage",
                "title": "业务分类覆盖率可接受",
                "status": "missing",
                "detail": "当前会话还没有业务分类输出摘要。",
                "suggestion": "先运行分类，再判断覆盖率和场景挂接情况。",
            }
        )

    sample_size = 5
    if classified_summary:
        sample_size = min(max(len(classified_summary["preview"]), 3), 8)
    elif atoms_summary:
        sample_size = min(max(len(atoms_summary["preview"]), 3), 8)

    items.append(
        {
            "key": "extract_manual_sampling",
            "title": "已人工抽样复核抽取字段质量",
            "status": "manual",
            "detail": f"建议至少抽样 {sample_size} 条记录，核对 who / what / how、规则类型和分类理由是否可读且贴合原文。",
            "suggestion": "结合本页 preview 与图谱浏览页样本表逐条确认。",
        }
    )
    items.extend(
        [
            {
                "key": "extract_manual_phase1_examples",
                "title": "已基于实体示例判断抽取方向基本正确",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条 Phase1/2 示例，确认实体围绕法规核心主体、事项和场景展开，而不是标题噪声。",
                "suggestion": "重点看 entity_preview 与原文片段是否对得上。",
            },
            {
                "key": "extract_manual_atom_examples",
                "title": "已基于原子示例判断拆分粒度基本合理",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条原子示例，确认 who / what / how 能支持后续合规判断。",
                "suggestion": "若发现整段原文未拆开或 what/how 过空，记录到备注里。",
            },
            {
                "key": "extract_manual_classified_examples",
                "title": "已基于分类示例判断业务映射基本正确",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条分类示例，确认 label path 与 classification reason 和条款语义一致。",
                "suggestion": "优先核查一个典型正样本和一个未分类关注样本。",
            },
        ]
    )
    return items


def render_representative_samples_panel():
    ensure_review_sampling_defaults()
    st.subheader("典型示例抽样")
    st.caption("这些样本会按照当前抽样配置，从最新抽取结果中自动挑选，并同步进入人工核查清单。")
    st.caption(build_review_sampling_caption())

    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")

    phase1_tab, atoms_tab, classified_tab = st.tabs(["Phase1/2 示例", "原子抽取示例", "业务分类示例"])

    with phase1_tab:
        if not phase1_summary:
            st.info("当前没有可用的 Phase1/2 文件。")
        else:
            samples = build_phase1_representative_samples(Path(phase1_summary["path"]))
            st.dataframe(samples, width="stretch", hide_index=True)
            st.caption("快速判断：实体是否集中在法规核心主体、事项和场景，而不是标题噪声。")

    with atoms_tab:
        if not atoms_summary:
            st.info("当前没有可用的原子抽取文件。")
        else:
            typical, fuzzy = build_atoms_representative_samples(Path(atoms_summary["path"]))
            st.markdown("**典型样本**")
            st.dataframe(typical, width="stretch", hide_index=True)
            st.caption("快速判断：who / what / how 是否已经形成可执行规则，而不是整段原文照抄。")
            if not fuzzy.empty:
                st.markdown("**模糊样本**")
                st.dataframe(fuzzy, width="stretch", hide_index=True)
                st.caption("这些样本是模型主动打上模糊标签的原子，会同步进入人工“模糊判定”。")

    with classified_tab:
        if not classified_summary:
            st.info("当前没有可用的业务分类文件。")
        else:
            typical, attention = build_classified_representative_samples(Path(classified_summary["path"]))
            st.markdown("**典型样本**")
            st.dataframe(typical, width="stretch", hide_index=True)
            st.caption("快速判断：业务路径和分类理由是否与条款语义一致。")
            if not attention.empty:
                st.markdown("**关注样本**")
                st.dataframe(attention, width="stretch", hide_index=True)
                st.caption("这些样本当前未完成分类，适合优先判断是否需要补 taxonomy、补提示词或重跑分类。")


def render_representative_review_panel():
    ensure_review_sampling_defaults()
    st.subheader("样本判定")
    st.caption("这里可以直接对代表样本做通过 / 存疑 / 明显错误的判断，并在导出时一并写入 Excel。")
    st.caption(build_review_sampling_caption())

    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")
    review_tables = {}

    phase1_tab, atoms_tab, classified_tab, fuzzy_tab = st.tabs(["Phase1/2 判定", "原子抽取判定", "业务分类判定", "模糊判定"])

    with phase1_tab:
        if not phase1_summary:
            st.info("当前没有可用的 Phase1/2 文件。")
        else:
            samples = build_phase1_representative_samples(Path(phase1_summary["path"]))
            phase1_review_df = prepare_review_dataframe(
                samples,
                sample_group="Phase1/2",
                sample_label="代表实体样本",
                key_columns=["source_document", "chunk_index"],
                storage_key="sample_review::phase1_samples",
            )
            review_tables["phase1_samples"] = render_review_editor(
                "实体方向判定",
                phase1_review_df,
                "phase1_samples",
                "重点判断：实体是否命中了法规核心主体、事项和场景，而不是目录或标题噪声。",
            )

    with atoms_tab:
        if not atoms_summary:
            st.info("当前没有可用的原子抽取文件。")
        else:
            typical, _ = build_atoms_representative_samples(Path(atoms_summary["path"]))
            atoms_typical_review_df = prepare_review_dataframe(
                typical,
                sample_group="原子抽取",
                sample_label="典型样本",
                key_columns=["atom_id"],
                storage_key="sample_review::atoms_typical",
            )
            review_tables["atoms_typical"] = render_review_editor(
                "典型原子判定",
                atoms_typical_review_df,
                "atoms_typical",
                "重点判断：who / what / how 是否已经拆成可执行规则，而不是整段原文照抄。",
            )

    with classified_tab:
        if not classified_summary:
            st.info("当前没有可用的业务分类文件。")
        else:
            typical, attention = build_classified_representative_samples(Path(classified_summary["path"]))
            classified_typical_review_df = prepare_review_dataframe(
                typical,
                sample_group="业务分类",
                sample_label="典型样本",
                key_columns=["atom_id"],
                storage_key="sample_review::classified_typical",
            )
            review_tables["classified_typical"] = render_review_editor(
                "典型分类判定",
                classified_typical_review_df,
                "classified_typical",
                "重点判断：label path 与 classification reason 是否和条款语义一致。",
            )
            if not attention.empty:
                classified_attention_review_df = prepare_review_dataframe(
                    attention,
                    sample_group="业务分类",
                    sample_label="关注样本",
                    key_columns=["atom_id"],
                    storage_key="sample_review::classified_attention",
                )
                review_tables["classified_attention"] = render_review_editor(
                    "关注分类判定",
                    classified_attention_review_df,
                    "classified_attention",
                    "这些样本当前未完成分类，适合判断是否需要补 taxonomy、补提示词或重跑分类。",
                )

    with fuzzy_tab:
        if not atoms_summary:
            st.info("当前没有可用的原子抽取文件。")
        else:
            _, fuzzy = build_atoms_representative_samples(Path(atoms_summary["path"]))
            if fuzzy.empty:
                st.success("当前抽取结果中没有被模型标记为模糊的原子。")
            else:
                fuzzy_review_df = prepare_review_dataframe(
                    fuzzy,
                    sample_group="模糊原子",
                    sample_label="模糊样本",
                    key_columns=["atom_id"],
                    storage_key="sample_review::atoms_fuzzy",
                )
                review_tables["atoms_fuzzy"] = render_review_editor(
                    "模糊原子判定",
                    fuzzy_review_df,
                    "atoms_fuzzy",
                    "重点判断：模型标记为模糊是否合理，是否需要补参数、拆分或回源重抽。",
                )

    return review_tables


def build_extraction_checklist_items():
    ensure_review_sampling_defaults()
    settings = get_review_sampling_settings()
    phase1_summary = get_checklist_summary("extract_phase1_summary")
    atoms_summary = get_checklist_summary("extract_atoms_summary")
    classified_summary = get_checklist_summary("extract_classified_summary")

    items = []

    if phase1_summary:
        entity_ratio = safe_ratio(phase1_summary["non_empty_chunk_count"], phase1_summary["chunk_count"])
        phase1_status = "pass" if phase1_summary["entity_total"] > 0 and (entity_ratio or 0.0) >= 0.3 else "warn"
        items.append(
            {
                "key": "extract_phase1_presence",
                "title": "Phase1/2 实体抽取已产出有效结果",
                "status": phase1_status,
                "detail": (
                    f"文档 {phase1_summary['doc_count']} 份，切块 {phase1_summary['chunk_count']} 个，"
                    f"有实体切块 {phase1_summary['non_empty_chunk_count']} 个，占比 {format_percent(entity_ratio)}，"
                    f"实体总数 {phase1_summary['entity_total']}。"
                ),
                "suggestion": "抽样查看实体数为 0 的切块，确认是否为空页、扫描问题或规则未命中。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_phase1_presence",
                "title": "Phase1/2 实体抽取已产出有效结果",
                "status": "missing",
                "detail": "当前会话还没有 Phase1/2 输出摘要。",
                "suggestion": "先运行 Phase1/2，再回到本页做人工核查。",
            }
        )

    fuzzy_sample_size = 0
    if atoms_summary:
        ambiguous_ratio = safe_ratio(atoms_summary["ambiguous_count"], atoms_summary["rows"])
        fuzzy_sample_size = resolve_fuzzy_review_limit(atoms_summary["ambiguous_count"], minimum=1)
        atom_status = "pass" if atoms_summary["rows"] > 0 and atoms_summary["rule_type_count"] > 0 else "warn"
        ambiguity_status = "pass" if (ambiguous_ratio or 0.0) <= 0.25 else "warn"
        items.append(
            {
                "key": "extract_atoms_presence",
                "title": "原子抽取结果已生成",
                "status": atom_status,
                "detail": (
                    f"原子 {atoms_summary['rows']} 条，来源法规 {atoms_summary['doc_count']} 份，"
                    f"规则类型 {atoms_summary['rule_type_count']} 种。"
                ),
                "suggestion": "优先查看 preview 中 what/how 为空或明显过短的原子。",
            }
        )
        items.append(
            {
                "key": "extract_atoms_ambiguity",
                "title": "歧义原子比例处于可复核范围",
                "status": ambiguity_status,
                "detail": (
                    f"歧义原子 {atoms_summary['ambiguous_count']} 条，"
                    f"占全部原子的 {format_percent(ambiguous_ratio)}。"
                ),
                "suggestion": "若比例偏高，建议回查抽取提示词或先做小样本重跑。",
            }
        )
        items.append(
            {
                "key": "extract_atoms_fuzzy_queue",
                "title": "模糊原子已进入人工复核队列",
                "status": "warn" if atoms_summary["ambiguous_count"] > 0 else "pass",
                "detail": (
                    f"当前检测到 {atoms_summary['ambiguous_count']} 条模糊原子；"
                    f"按 {settings['fuzzy_ratio_pct']}% 已生成 {fuzzy_sample_size} 条模糊复核样本。"
                ),
                "suggestion": "前往“模糊判定”逐条确认是否保留模糊标签、补参数或回源重抽。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_atoms_presence",
                "title": "原子抽取结果已生成",
                "status": "missing",
                "detail": "当前会话还没有原子抽取输出摘要。",
                "suggestion": "先运行原子抽取，再核查字段质量。",
            }
        )

    if classified_summary:
        coverage_ratio = safe_ratio(classified_summary["labelled_rows"], classified_summary["rows"])
        coverage_status = "pass" if (coverage_ratio or 0.0) >= 0.7 else "warn"
        scene_status = "pass" if classified_summary["scene_match_count"] > 0 else "warn"
        items.append(
            {
                "key": "extract_classification_coverage",
                "title": "业务分类覆盖率可接受",
                "status": coverage_status,
                "detail": (
                    f"已分类 {classified_summary['labelled_rows']} 条，未分类 {classified_summary['unclassified_rows']} 条，"
                    f"覆盖率 {format_percent(coverage_ratio)}，命中业务模块 {classified_summary['module_count']} 个。"
                ),
                "suggestion": "重点核查未分类原子是否集中在同一法规主题或同一规则类型。",
            }
        )
        items.append(
            {
                "key": "extract_scene_match_presence",
                "title": "场景挂接结果已生成",
                "status": scene_status,
                "detail": (
                    f"场景挂接关系 {classified_summary['scene_match_count']} 条，"
                    f"场景主体关系 {classified_summary['scene_actor_count']} 条。"
                ),
                "suggestion": "若场景挂接偏少，优先检查 taxonomy 映射和场景关键词。",
            }
        )
    else:
        items.append(
            {
                "key": "extract_classification_coverage",
                "title": "业务分类覆盖率可接受",
                "status": "missing",
                "detail": "当前会话还没有业务分类输出摘要。",
                "suggestion": "先运行分类，再判断覆盖率和场景挂接情况。",
            }
        )

    sample_size = 5
    if classified_summary:
        sample_size = resolve_typical_review_limit(classified_summary["rows"], minimum=3)
    elif atoms_summary:
        sample_size = resolve_typical_review_limit(atoms_summary["rows"], minimum=3)

    items.append(
        {
            "key": "extract_manual_sampling",
            "title": "已人工抽样复核抽取字段质量",
            "status": "manual",
            "detail": (
                f"当前典型样本按 {settings['typical_ratio_pct']}% 抽样，"
                f"建议至少复核 {sample_size} 条记录，核对 who / what / how、规则类型和分类理由是否贴合原文。"
            ),
            "suggestion": "结合本页抽样预览与图谱浏览页样本表逐条确认。",
        }
    )
    items.extend(
        [
            {
                "key": "extract_manual_phase1_examples",
                "title": "已基于实体示例判断抽取方向基本正确",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条 Phase1/2 示例，确认实体围绕法规核心主体、事项和场景展开，而不是标题噪声。",
                "suggestion": "重点看 entity_preview 与原文片段是否对应。",
            },
            {
                "key": "extract_manual_atom_examples",
                "title": "已基于原子示例判断拆分粒度基本合理",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条原子示例，确认 who / what / how 能支持后续合规判断。",
                "suggestion": "若发现整段原文未拆开或 what/how 过空，记录到备注里。",
            },
            {
                "key": "extract_manual_classified_examples",
                "title": "已基于分类示例判断业务映射基本正确",
                "status": "manual",
                "detail": f"建议至少查看 {sample_size} 条分类示例，确认 label path 与 classification reason 和条款语义一致。",
                "suggestion": "优先核查一个典型正样本和一个未分类关注样本。",
            },
        ]
    )
    if fuzzy_sample_size > 0:
        items.append(
            {
                "key": "extract_manual_fuzzy_examples",
                "title": "已基于模糊标签完成人工复核",
                "status": "manual",
                "detail": f"建议至少复核 {fuzzy_sample_size} 条被模型标记为模糊的原子，确认 review_reason 是否合理，是否需要补参、拆分或回源重抽。",
                "suggestion": "如果同一类模糊原因集中出现，优先回查 Stage3 prompt 或原子拆分策略。",
            }
        )
    return items


def build_graph_checklist_items(graph_stats=None, overview_items=None, graph_error=None):
    overview_items = overview_items or []
    items = []

    if graph_error:
        items.append(
            {
                "key": "graph_connection",
                "title": "Neo4j 图谱当前可读取",
                "status": "missing",
                "detail": f"图谱查询失败：{graph_error}",
                "suggestion": "先检查 Neo4j 服务、连接参数和数据库是否已启动。",
            }
        )
        items.append(
            {
                "key": "graph_manual_browse",
                "title": "已人工浏览图谱样本",
                "status": "manual",
                "detail": "待 Neo4j 恢复后，再到“分类概览”和“图谱浏览”页抽样检查场景与原子关系。",
                "suggestion": "优先检查一个高频模块和一个具体场景的样本表。",
            }
        )
        return items

    graph_stats = graph_stats or {}
    node_ready = all(graph_stats.get(key, 0) > 0 for key in ("boards", "categories", "modules", "scenes", "atoms"))
    tag_ratio = safe_ratio(graph_stats.get("tags", 0), graph_stats.get("atoms", 0))
    tag_status = "pass" if (tag_ratio or 0.0) >= 0.8 else "warn"
    scene_status = "pass" if graph_stats.get("scene_matches", 0) > 0 else "warn"
    actor_status = "pass" if graph_stats.get("actors", 0) > 0 and graph_stats.get("scene_actors", 0) > 0 else "warn"
    overview_status = "pass" if len(overview_items) > 0 else "warn"

    items.append(
        {
            "key": "graph_nodes_presence",
            "title": "图谱基础节点已写入",
            "status": "pass" if node_ready else "warn",
            "detail": (
                f"Boards {graph_stats.get('boards', 0)}，Categories {graph_stats.get('categories', 0)}，"
                f"Modules {graph_stats.get('modules', 0)}，Scenes {graph_stats.get('scenes', 0)}，"
                f"Atoms {graph_stats.get('atoms', 0)}。"
            ),
            "suggestion": "若某类节点为 0，先回查分类输出文件与导图步骤。",
        }
    )
    items.append(
        {
            "key": "graph_tag_relationships",
            "title": "模块标签关系数量基本正常",
            "status": tag_status,
            "detail": (
                f"TAGGED_AS 关系 {graph_stats.get('tags', 0)} 条，"
                f"相对 atom 数的覆盖比约为 {format_percent(tag_ratio)}。"
            ),
            "suggestion": "若覆盖比偏低，优先检查未分类原子和模块映射结果。",
        }
    )
    items.append(
        {
            "key": "graph_scene_relationships",
            "title": "场景精召回关系已建立",
            "status": scene_status,
            "detail": f"MATCHES_SCENE 关系 {graph_stats.get('scene_matches', 0)} 条。",
            "suggestion": "若为 0 或明显偏少，检查 scene profile 与 recall 规则。",
        }
    )
    items.append(
        {
            "key": "graph_actor_relationships",
            "title": "主体侧关系已建立",
            "status": actor_status,
            "detail": (
                f"BusinessActor 节点 {graph_stats.get('actors', 0)} 个，"
                f"SCENE_HAS_ACTOR 关系 {graph_stats.get('scene_actors', 0)} 条。"
            ),
            "suggestion": "若主体侧关系不足，重点检查 who / who_terms 的抽取质量。",
        }
    )
    items.append(
        {
            "key": "graph_overview_presence",
            "title": "分类概览页已有统计结果",
            "status": overview_status,
            "detail": f"当前分类概览可读取 {len(overview_items)} 条业务大类统计。",
            "suggestion": "优先查看 atom 数异常高或异常低的业务大类。",
        }
    )
    items.append(
        {
            "key": "graph_manual_browse",
            "title": "已人工浏览图谱样本",
            "status": "manual",
            "detail": "建议至少检查 1 个业务模块和 1 个具体场景，确认图中节点连接、精召回样本、宽召回样本都能解释业务问题。",
            "suggestion": "优先检查“图谱浏览”页默认场景，再切换一个非默认场景做对比。",
        }
    )
    return items


def render_checklist_item(item):
    checkbox_key = f"manual_check::{item['key']}"
    checked_col, content_col = st.columns([0.5, 5.5])
    with checked_col:
        st.checkbox("确认", key=checkbox_key, label_visibility="collapsed")
    with content_col:
        st.markdown(f"**{item['title']}**")
        st.caption(f"自动状态：{format_checklist_status(item.get('status'))}")
        st.write(item.get("detail", ""))
        if item.get("suggestion"):
            st.caption(f"建议动作：{item['suggestion']}")


def render_checklist_section(title, items, notes_key):
    st.subheader(title)
    total_count = len(items)
    auto_pass_count = sum(1 for item in items if item.get("status") == "pass")
    attention_count = sum(1 for item in items if item.get("status") in {"warn", "missing"})
    manual_done_count = sum(1 for item in items if st.session_state.get(f"manual_check::{item['key']}", False))

    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("核查项", total_count)
    metric2.metric("自动通过", auto_pass_count)
    metric3.metric("待关注", attention_count)
    metric4.metric("已人工确认", manual_done_count)

    for index, item in enumerate(items):
        render_checklist_item(item)
        if index != total_count - 1:
            st.divider()

    st.text_area(
        "核查备注",
        key=notes_key,
        height=90,
        placeholder="记录抽样发现的问题、需要回退重跑的环节、人工判断结论等。",
    )


def render_checklist_tab(driver):
    st.subheader("人工核查清单")
    st.info("这个清单把自动摘要和人工勾选放在一起。自动状态只做提醒，不替代人工判断。")
    render_review_sampling_controls()

    extraction_items = build_extraction_checklist_items()

    graph_stats = None
    overview_items = []
    graph_error = None
    try:
        graph_stats = fetch_graph_health_stats(driver)
        overview_items = fetch_category_overview(driver)
    except Exception as exc:
        graph_error = str(exc)

    graph_items = build_graph_checklist_items(graph_stats, overview_items, graph_error)
    all_items = extraction_items + graph_items
    overall_done = sum(1 for item in all_items if st.session_state.get(f"manual_check::{item['key']}", False))
    overall_attention = sum(1 for item in all_items if item.get("status") in {"warn", "missing"})

    top1, top2, top3 = st.columns(3)
    top1.metric("总核查项", len(all_items))
    top2.metric("待关注项", overall_attention)
    top3.metric("已人工确认", overall_done)

    review_tables = render_representative_review_panel()
    render_review_rate_metrics(review_tables)
    st.divider()

    export_col, reset_col = st.columns(2)
    with export_col:
        if st.button("导出样本判定 XLSX", key="export_sample_reviews_xlsx", width="stretch"):
            output_path = export_sample_reviews_xlsx(review_tables)
            st.success(f"样本判定已导出：{output_path}")

    with reset_col:
        if st.button("清空本页勾选与备注", key="reset_manual_checklist", width="stretch"):
            for item in all_items:
                st.session_state[f"manual_check::{item['key']}"] = False
            for table_name in review_tables:
                st.session_state.pop(f"sample_review::{table_name}", None)
                st.session_state.pop(f"review_editor::{table_name}", None)
            st.session_state["checklist_notes_extraction"] = ""
            st.session_state["checklist_notes_graph"] = ""
            rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
            if rerun:
                rerun()

    render_checklist_section("抽取与分类核查", extraction_items, "checklist_notes_extraction")
    st.divider()
    render_checklist_section("图谱与召回核查", graph_items, "checklist_notes_graph")


def render_pipeline_summary_card(title, summary_lines, status="info"):
    text = "\n".join(summary_lines)
    if status == "success":
        st.success(f"{title}\n\n{text}")
    elif status == "warning":
        st.warning(f"{title}\n\n{text}")
    else:
        st.info(f"{title}\n\n{text}")


def fetch_boards(driver):
    with driver.session() as session:
        rows = session.run(
            "MATCH (b:BusinessBoard) RETURN b.name AS name ORDER BY CASE b.name WHEN '业务管理类' THEN 0 WHEN '基础管理类' THEN 1 ELSE 2 END, b.name"
        )
        return [row["name"] for row in rows]


def fetch_categories(driver, board_name):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (:BusinessBoard {name: $board_name})-[:HAS_CATEGORY]->(c:BusinessCategory)
            RETURN c.key AS key, c.name AS name
            ORDER BY c.name
            """,
            board_name=board_name,
        )
        return [dict(row) for row in rows]


def fetch_category_overview(driver, board_name=None):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (b:BusinessBoard)-[:HAS_CATEGORY]->(c:BusinessCategory)
            WHERE $board_name IS NULL OR b.name = $board_name
            OPTIONAL MATCH (c)-[:HAS_MODULE]->(m:BusinessModule)
            OPTIONAL MATCH (m)<-[:TAGGED_AS]-(a:BusinessAtom)
            RETURN b.name AS board_name,
                   c.key AS category_key,
                   c.name AS category_name,
                   count(DISTINCT m) AS module_count,
                   count(DISTINCT a) AS atom_count
            ORDER BY CASE b.name
                        WHEN '业务管理类' THEN 0
                        WHEN '基础管理类' THEN 1
                        ELSE 2
                     END,
                     c.key
            """,
            board_name=board_name,
        )
        return [dict(row) for row in rows]


def fetch_modules(driver, category_key):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (:BusinessCategory {key: $category_key})-[:HAS_MODULE]->(m:BusinessModule)
            RETURN m.code AS code, m.name AS name, m.label_path AS label_path
            ORDER BY m.code
            """,
            category_key=category_key,
        )
        return [dict(row) for row in rows]


def fetch_scenes(driver, module_code):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (:BusinessModule {code: $module_code})-[:HAS_SCENE]->(s:BusinessScene)
            OPTIONAL MATCH (:BusinessAtom)-[r:MATCHES_SCENE]->(s)
            RETURN s.key AS key, s.name AS name, count(r) AS precise_count
            ORDER BY precise_count DESC, s.name
            """,
            module_code=module_code,
        )
        return [dict(row) for row in rows]


def fetch_scene_actor_terms(driver, scene_key, limit=30):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:BusinessAtom)-[:MATCHES_SCENE]->(:BusinessScene {key: $scene_key})
            WITH CASE
                    WHEN size(coalesce(a.who_terms, [])) > 0 THEN coalesce(a.who_terms, [])
                    WHEN coalesce(a.who, '') <> '' THEN [a.who]
                    ELSE []
                 END AS who_terms
            UNWIND who_terms AS who_term
            RETURN who_term AS name, count(*) AS precise_count
            ORDER BY precise_count DESC, name
            LIMIT $limit
            """,
            scene_key=scene_key,
            limit=limit,
        )
        return [dict(row) for row in rows]


def fetch_context(driver, module_code, scene_key):
    with driver.session() as session:
        row = session.run(
            """
            MATCH (b:BusinessBoard)-[:HAS_CATEGORY]->(c:BusinessCategory)-[:HAS_MODULE]->(m:BusinessModule {code: $module_code})
            OPTIONAL MATCH (m)-[:HAS_SCENE]->(selected:BusinessScene {key: $scene_key})
            RETURN b.name AS board_name,
                   c.name AS category_name,
                   c.key AS category_key,
                   m.name AS module_name,
                   m.code AS module_code,
                   m.label_path AS module_path,
                   selected.name AS scene_name,
                   selected.key AS scene_key
            """,
            module_code=module_code,
            scene_key=scene_key,
        ).single()
        return dict(row) if row else {}


def fetch_broad_atoms(driver, module_code, limit):
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:BusinessAtom)-[:TAGGED_AS]->(m:BusinessModule {code: $module_code})
            RETURN a.id AS atom_id,
                   a.rule_type AS rule_type,
                   a.article_reference AS article_reference,
                   a.who AS who,
                   a.what AS what,
                   a.how AS how,
                   a.source_document AS source_document,
                   a.content_original AS content
            ORDER BY a.id
            LIMIT $limit
            """,
            module_code=module_code,
            limit=limit,
        )
        return [dict(row) for row in rows]


def fetch_precise_atoms(driver, scene_key, limit, who_terms=None):
    who_terms = who_terms or []
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:BusinessAtom)-[r:MATCHES_SCENE]->(s:BusinessScene {key: $scene_key})
            WHERE size($who_terms) = 0
               OR any(term IN $who_terms WHERE term IN coalesce(a.who_terms, []) OR coalesce(a.who, '') CONTAINS term)
            RETURN a.id AS atom_id,
                   a.rule_type AS rule_type,
                   a.article_reference AS article_reference,
                   a.who AS who,
                   coalesce(a.who_terms, []) AS who_terms,
                   a.what AS what,
                   a.how AS how,
                   a.source_document AS source_document,
                   a.content_original AS content,
                   r.score AS score,
                   r.matched_terms AS matched_terms,
                   [term IN $who_terms WHERE term IN coalesce(a.who_terms, []) OR coalesce(a.who, '') CONTAINS term] AS matched_who_terms
            ORDER BY r.score DESC, a.id
            LIMIT $limit
            """,
            scene_key=scene_key,
            who_terms=who_terms,
            limit=limit,
        )
        return [dict(row) for row in rows]


def fetch_counts(driver, module_code, scene_key, who_terms=None):
    who_terms = who_terms or []
    with driver.session() as session:
        broad = session.run(
            "MATCH (:BusinessAtom)-[r:TAGGED_AS]->(:BusinessModule {code: $module_code}) RETURN count(r) AS c",
            module_code=module_code,
        ).single()["c"]
        precise = 0
        refined = None
        if scene_key:
            precise = session.run(
                "MATCH (:BusinessAtom)-[r:MATCHES_SCENE]->(:BusinessScene {key: $scene_key}) RETURN count(r) AS c",
                scene_key=scene_key,
            ).single()["c"]
            if who_terms:
                refined = session.run(
                    """
                    MATCH (a:BusinessAtom)-[:MATCHES_SCENE]->(:BusinessScene {key: $scene_key})
                    WHERE any(term IN $who_terms WHERE term IN coalesce(a.who_terms, []) OR coalesce(a.who, '') CONTAINS term)
                    RETURN count(a) AS c
                    """,
                    scene_key=scene_key,
                    who_terms=who_terms,
                ).single()["c"]
        return broad, precise, refined


def fetch_scene_actor_links(driver, scene_key, actor_terms=None, limit=12):
    actor_terms = actor_terms or []
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (:BusinessScene {key: $scene_key})-[r:SCENE_HAS_ACTOR]->(w:BusinessActor)
            WHERE size($actor_terms) = 0 OR w.name IN $actor_terms
            RETURN w.name AS actor_name, r.atom_count AS atom_count
            ORDER BY atom_count DESC, actor_name
            LIMIT $limit
            """,
            scene_key=scene_key,
            actor_terms=actor_terms,
            limit=limit,
        )
        return [dict(row) for row in rows]


def text_contains_any(text, terms):
    value = str(text or "")
    return any(term and term in value for term in terms or [])


def atom_matches_actor(atom, actor_terms):
    actor_terms = actor_terms or []
    who = str(atom.get("who") or "")
    who_terms = atom.get("who_terms") or []
    return any(term in who or term in who_terms for term in actor_terms)


def filter_atoms_by_keywords(atoms, keyword_terms):
    keyword_terms = keyword_terms or []
    result = []
    for atom in atoms:
        if any(
            text_contains_any(atom.get(field, ""), keyword_terms)
            for field in ("what", "how", "content")
        ):
            result.append(atom)
    return result


def format_joined(values, sep=" | "):
    return sep.join([str(value).strip() for value in values or [] if str(value).strip()])


def format_decision_label(value):
    mapping = {
        "继续召回": "继续推理",
        "停止召回": "停止推理",
        "DRY_RUN": "本地 dry-run",
        "LLM_ERROR": "LLM 调用失败",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def format_generation_mode(value):
    mapping = {
        "llm": "模型生成",
        "local_not_ready": "本地回退",
        "llm_fallback": "本地回退",
        "symbolic": "规则兜底",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def build_recall_evidence_rows(items):
    return [
        {
            "atom_id": item.get("atom_id", ""),
            "score": item.get("score", ""),
            "rule_type": item.get("rule_type", ""),
            "source_document": item.get("source_document", ""),
            "article_reference": item.get("article_reference", ""),
            "who": item.get("who", ""),
            "what": item.get("what", ""),
            "how": item.get("how", ""),
            "reasons": format_joined(item.get("reasons")),
            "content_original": item.get("content_original", ""),
        }
        for item in items or []
    ]


def build_atom_analysis_rows(items):
    return [
        {
            "atom_id": item.get("atom_id", ""),
            "decision": item.get("decision", ""),
            "reason": item.get("reason", ""),
            "missing_elements": format_joined(item.get("missing_elements"), sep="；"),
            "next_split_focus": item.get("next_split_focus", ""),
        }
        for item in items or []
    ]


def build_direction_rows(items):
    return [
        {
            "direction": f"{item.get('direction', '')} {item.get('direction_name', '')}".strip(),
            "judge_missing_dimension": item.get("judge_missing_dimension", ""),
            "judge_reason": item.get("judge_reason", ""),
            "added_candidate_count": item.get("added_candidate_count", 0),
            "net_new_candidate_count": item.get("net_new_candidate_count", 0),
        }
        for item in items or []
    ]


def build_gap_rows(items):
    return [
        {
            "gap_type": item.get("gap_type", ""),
            "impact_scope": item.get("impact_scope", ""),
            "severity": item.get("severity", ""),
            "handling": item.get("handling", ""),
            "gap_text": item.get("gap_text", ""),
            "judgement_condition": item.get("judgement_condition", ""),
        }
        for item in items or []
    ]


def format_gap_card_item(item):
    parts = [item.get("gap_type", ""), item.get("severity", "")]
    prefix = "/".join([part for part in parts if part])
    gap_text = item.get("gap_text", "")
    return f"[{prefix}] {gap_text}" if prefix and gap_text else gap_text or prefix


def render_summary_list(title, items):
    items = [str(item).strip() for item in items or [] if str(item).strip()]
    if not items:
        return
    st.markdown(f"**{title}**")
    for item in items:
        st.write(f"- {item}")


def render_final_conclusion_card(card):
    if not card:
        return

    conclusion = card.get("conclusion", "") or "-"
    summary = card.get("conclusion_summary", "")
    generation_mode = format_generation_mode(card.get("generation_mode", "") or "-")
    confidence = float(card.get("confidence", 0.0) or 0.0)

    if conclusion == "可办理":
        st.success(f"最终结论：{conclusion}")
    elif conclusion == "不可办理":
        st.error(f"最终结论：{conclusion}")
    elif conclusion in {"有条件可办理", "需补材料后办理"}:
        st.warning(f"最终结论：{conclusion}")
    else:
        st.info(f"最终结论：{conclusion}")

    if summary:
        st.markdown(summary)

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("结论", conclusion)
    metric2.metric("生成方式", generation_mode)
    metric3.metric("置信度", f"{confidence:.2f}")

    left, right = st.columns(2)
    with left:
        render_summary_list("直接依据", card.get("legal_basis"))
        render_summary_list("需补材料", card.get("required_materials"))
        render_summary_list("需执行动作", card.get("required_actions"))
    with right:
        render_summary_list("例外/限制", card.get("exceptions_and_limits"))
        render_summary_list("仍缺项", card.get("missing_items"))
        render_summary_list("后续动作", card.get("follow_up_actions"))

    render_summary_list("风险提示", card.get("risk_points"))

def render_gap_summary_cards(cards):
    cards = cards or []
    if not cards:
        return

    st.markdown("**缺口三段总卡**")
    columns = st.columns(min(len(cards), 3))
    for idx, card in enumerate(cards[:3]):
        with columns[idx]:
            title = card.get("card_title", "缺口总卡")
            count = int(card.get("count", 0) or 0)
            summary = card.get("summary", "")
            item_texts = []
            for item in card.get("items") or []:
                text = format_gap_card_item(item)
                if text:
                    item_texts.append(text)

            if title == "致命缺口总卡" and count > 0:
                st.error(f"{title} | {count} 项")
            elif title == "可人工复核缺口总卡" and count > 0:
                st.warning(f"{title} | {count} 项")
            else:
                st.info(f"{title} | {count} 项")

            if summary:
                st.write(summary)
            if item_texts:
                for text in item_texts[:3]:
                    st.write(f"- {text}")
            else:
                st.write("- 当前无对应缺口")


def should_resume_recall(previous_report, question, query, who):
    if not previous_report:
        return False
    if str(previous_report.get("question", "")).strip() != str(question or "").strip():
        return False

    previous_query_spec = previous_report.get("query_spec") or {}
    previous_query = str(previous_query_spec.get("query", "")).strip()
    current_query = str(query or "").strip()
    if previous_query != current_query:
        return False

    previous_who = ",".join(previous_query_spec.get("who_terms") or [])
    current_who = str(who or "").strip()
    if previous_who != current_who:
        return False

    return (
        previous_report.get("final_decision") == "继续召回"
        and previous_report.get("stop_reason") == "max_rounds"
        and bool(previous_report.get("final_ranked_evidence") or previous_report.get("final_evidence"))
    )


def is_llm_runtime_error(exc):
    text = str(exc or "")
    lowered = text.lower()
    return (
        "qwen json call failed" in lowered
        or "dashscope_api_key" in lowered
        or "connection" in lowered
        or "connect" in lowered
        or "timeout" in lowered
    )


def render_compliance_recall_report(report):
    if report.get("final_decision") == "LLM_ERROR":
        st.warning("Qwen 当前未连通，下面展示的是本地回退结果。若你在使用 VPN，先关闭后再试。")
    elif report.get("final_decision") == "DRY_RUN":
        st.info("当前展示的是本地 dry-run 结果，尚未进入 LLM 模型推理。")

    top1, top2, top3, top4, top5 = st.columns(5)
    top1.metric("最终决策", format_decision_label(report.get("final_decision", "-")))
    top2.metric("初始召回", report.get("initial_recall_atom_count", 0))
    top3.metric("最终召回", report.get("final_recall_atom_count", 0))
    top4.metric("推理轮次", len(report.get("rounds") or []))
    top5.metric("可直接出结论", "是" if report.get("can_make_final_compliance_judgement") else "否")

    final_conclusion = report.get("final_conclusion") or {}
    if final_conclusion:
        render_final_conclusion_card(final_conclusion)

    compliance_summary = report.get("compliance_summary") or {}
    readiness = compliance_summary.get("readiness", "")
    if readiness == "ready":
        st.success(compliance_summary.get("headline", "证据已基本齐备，可进入最终结论生成"))
    elif readiness == "exhausted_partial":
        st.warning(compliance_summary.get("headline", "召回候选已耗尽，当前可形成阶段性判断，但仍有少量关键缺口。"))
    elif readiness == "exhausted_insufficient":
        st.error(compliance_summary.get("headline", "召回候选已耗尽，但证据仍不足以支撑最终结论。"))
    elif readiness in {"insufficient_evidence", "dry_run"}:
        st.info(compliance_summary.get("headline", "当前仍处于模型补证据阶段。"))
    elif readiness == "llm_error":
        st.warning(compliance_summary.get("headline", "LLM 未连通，本次仅输出本地审查摘要。"))

    if compliance_summary:
        head1, head2 = st.columns([1.2, 1.8])
        with head1:
            if compliance_summary.get("route_hint"):
                st.markdown(f"**当前业务语境**：`{compliance_summary['route_hint']}`")
        with head2:
            if compliance_summary.get("next_step"):
                st.markdown(f"**下一步建议**：{compliance_summary['next_step']}")

        summary_col1, summary_col2 = st.columns(2)
        with summary_col1:
            render_summary_list("关键依据", compliance_summary.get("key_basis"))
            render_summary_list("需提交材料", compliance_summary.get("required_materials"))
            render_summary_list("需执行动作", compliance_summary.get("required_actions"))
            render_summary_list("时限/阈值", compliance_summary.get("time_limits"))
        with summary_col2:
            render_summary_list("禁止/限制", compliance_summary.get("prohibitions"))
            render_summary_list("例外/授权", compliance_summary.get("exceptions"))
            render_summary_list("定义边界", compliance_summary.get("definitions"))
            render_summary_list("当前缺口", compliance_summary.get("missing_items"))

        render_gap_summary_cards(compliance_summary.get("gap_summary_cards"))

        gap_rows = build_gap_rows(compliance_summary.get("gap_diagnosis"))
        if gap_rows:
            st.markdown("**缺口诊断**")
            st.dataframe(gap_rows, width="stretch", hide_index=True)

        render_summary_list("风险提示", compliance_summary.get("risk_points"))
        render_summary_list("推荐方向", compliance_summary.get("recommended_directions"))

    business_match = report.get("business_match") or {}
    st.markdown(f"**query**：`{(report.get('query_spec') or {}).get('query', '') or '未单独指定'}`")
    if (report.get("query_spec") or {}).get("who_terms"):
        st.markdown(f"**who 约束**：`{', '.join((report.get('query_spec') or {}).get('who_terms') or [])}`")
    if business_match.get("matched_categories"):
        st.markdown(f"**业务大类命中**：`{' | '.join(business_match['matched_categories'])}`")
    if business_match.get("matched_module_paths"):
        st.markdown(f"**业务模块命中**：`{' | '.join(business_match['matched_module_paths'])}`")
    if business_match.get("matched_scene_names"):
        st.markdown(f"**场景命中**：`{' | '.join(business_match['matched_scene_names'])}`")

    summary_tab, rounds_tab, final_tab = st.tabs(["初始召回", "逐轮推理", "最终证据"])

    with summary_tab:
        scene_scores = business_match.get("scene_scores") or []
        if scene_scores:
            st.dataframe(scene_scores, width="stretch", hide_index=True)
        initial_rows = build_recall_evidence_rows(report.get("initial_evidence"))
        if initial_rows:
            st.dataframe(initial_rows, width="stretch", hide_index=True)
        else:
            st.info("当前没有初始召回证据。")

    with rounds_tab:
        rounds = report.get("rounds") or []
        if not rounds:
            st.info("当前没有进入 LLM 推理轮次；如果上方是 LLM_ERROR，说明本次已经自动回退为本地结果。")
        for item in rounds:
            judge = item.get("judge") or {}
            expander_title = (
                f"第 {item.get('round', '?')} 轮 | {format_decision_label(judge.get('decision', '-'))}"
                f" | 新增 {item.get('new_atom_count', 0)} 条"
            )
            with st.expander(expander_title, expanded=item.get("round") == 1):
                metric1, metric2, metric3, metric4 = st.columns(4)
                metric1.metric("输入证据", item.get("input_atom_count", 0))
                metric2.metric("输出证据", item.get("output_atom_count", item.get("input_atom_count", 0)))
                metric3.metric("judge 置信度", f"{float(judge.get('confidence', 0.0) or 0.0):.2f}")
                metric4.metric("语义缺口画像", format_joined(item.get("semantic_gap_profiles"), sep=", ") or "-")

                if item.get("judge_missing_summary"):
                    st.markdown("**为什么还要继续补充证据**")
                    for summary in item["judge_missing_summary"]:
                        st.write(f"- {summary}")

                direction_rows = build_direction_rows(item.get("applied_directions") or item.get("judge_recommended_directions"))
                if direction_rows:
                    st.markdown("**本轮补充方向**")
                    st.dataframe(direction_rows, width="stretch", hide_index=True)

                atom_analysis_rows = build_atom_analysis_rows(item.get("atom_analysis"))
                if atom_analysis_rows:
                    st.markdown("**原子最小可执行颗粒度分析**")
                    st.dataframe(atom_analysis_rows, width="stretch", hide_index=True)

                for direction_report in item.get("applied_directions") or []:
                    st.markdown(f"**{direction_report.get('direction', '')} {direction_report.get('direction_name', '')}**")
                    hint_parts = [
                        direction_report.get("judge_missing_dimension", ""),
                        direction_report.get("judge_reason", ""),
                    ]
                    hint_text = " | ".join([part for part in hint_parts if part])
                    prefix = f"{hint_text} | " if hint_text else ""
                    st.write(
                        f"{prefix}"
                        f"候选 {direction_report.get('added_candidate_count', 0)} 条，"
                        f"其中相对上一轮净新增候选 {direction_report.get('net_new_candidate_count', 0)} 条。"
                    )
                    sample_rows = build_recall_evidence_rows(direction_report.get("sample_evidence"))
                    if sample_rows:
                        st.dataframe(sample_rows, width="stretch", hide_index=True)

                new_rows = build_recall_evidence_rows(item.get("new_evidence"))
                if new_rows:
                    st.markdown("**本轮新增证据预览**")
                    st.dataframe(new_rows, width="stretch", hide_index=True)

    with final_tab:
        final_rows = build_recall_evidence_rows(report.get("final_evidence"))
        if final_rows:
            st.dataframe(final_rows, width="stretch", hide_index=True)
        else:
            st.info("当前没有最终证据。")


def format_decision_label(value):
    mapping = {
        "继续召回": "继续推理",
        "停止召回": "停止推理",
        "DRY_RUN": "本地 dry-run",
        "LLM_ERROR": "LLM 调用失败",
        "EXEC_ERROR": "执行失败",
    }
    text = str(value or "").strip()
    return mapping.get(text, text or "-")


def build_recall_error_report(controller, question, query, who, exc, resume_report=None):
    error_is_llm = is_llm_runtime_error(exc)
    report = controller.run(
        question=question,
        query=query,
        who=who,
        max_rounds=0,
        dry_run=True,
        resume_report=resume_report,
    )
    report["final_decision"] = "LLM_ERROR" if error_is_llm else "EXEC_ERROR"
    report["stop_reason"] = "llm_connection_error" if error_is_llm else "execution_error"
    report["error"] = str(exc)
    controller.attach_compliance_summary(report)
    return report


def run_recall_with_fallback(controller, question, query, who, max_rounds, resume_report=None):
    try:
        return controller.run(
            question=question,
            query=query,
            who=who,
            max_rounds=max_rounds,
            resume_report=resume_report,
        )
    except Exception as exc:
        return build_recall_error_report(controller, question, query, who, exc, resume_report=resume_report)


def split_recall_input_lines(text):
    lines = []
    for raw_line in str(text or "").splitlines():
        for part in re.split(r"[;；]+", raw_line):
            line = str(part or "").strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


def parse_multi_query_recall_inputs(question, query_text, default_who=""):
    question = str(question or "").strip()
    default_who = str(default_who or "").strip()
    if not question:
        return []

    lines = split_recall_input_lines(query_text)
    if not lines:
        lines = [question]

    items = []
    for line_no, line in enumerate(lines, 1):
        parts = [part.strip() for part in re.split(r"\s*(?:\t+|\||｜)\s*", line, maxsplit=1)]
        query = parts[0] if parts else ""
        explicit_who = parts[1] if len(parts) > 1 else ""
        if not query:
            continue
        who = explicit_who or ("" if "::" in query or "@@" in query else default_who)
        items.append(
            {
                "index": len(items) + 1,
                "line_no": line_no,
                "question": question,
                "query": query,
                "who": who,
            }
        )
    return items


def parse_batch_recall_inputs(text, default_query="", default_who=""):
    items = []
    for line_no, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in re.split(r"\s*(?:\t+|\||｜)\s*", line, maxsplit=2)]
        while len(parts) < 3:
            parts.append("")

        question = parts[0]
        if not question:
            continue
        query = parts[1] or str(default_query or "").strip() or question
        who = parts[2] or ("" if "::" in query or "@@" in query else str(default_who or "").strip())

        items.append(
            {
                "index": len(items) + 1,
                "line_no": line_no,
                "question": question,
                "query": query,
                "who": who,
            }
        )
    return items


def run_recall_items_parallel(controller, items, max_rounds, max_workers, progress=None):
    items = list(items or [])
    if not items:
        return []

    worker_count = min(max(1, int(max_workers or 1)), len(items), MAX_RECALL_PARALLEL_WORKERS)
    results = [None] * len(items)

    def update_progress(done_count, item):
        if progress is None:
            return
        progress.progress(
            int(done_count / len(items) * 100),
            text=f"已完成 {done_count}/{len(items)}：{shorten_text(item.get('question', ''), limit=24)}",
        )

    if worker_count == 1:
        for position, item in enumerate(items):
            report = run_recall_with_fallback(
                controller,
                question=item["question"],
                query=item["query"],
                who=item["who"],
                max_rounds=max_rounds,
                resume_report=None,
            )
            results[position] = {**item, "report": report}
            update_progress(position + 1, item)
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_position = {
            executor.submit(
                run_recall_with_fallback,
                controller,
                item["question"],
                item["query"],
                item["who"],
                max_rounds,
                None,
            ): position
            for position, item in enumerate(items)
        }
        for done_count, future in enumerate(as_completed(future_to_position), 1):
            position = future_to_position[future]
            item = items[position]
            results[position] = {**item, "report": future.result()}
            update_progress(done_count, item)

    return [item for item in results if item is not None]


def build_batch_recall_summary_rows(batch_reports):
    rows = []
    for item in batch_reports or []:
        report = item.get("report") or {}
        final_conclusion = report.get("final_conclusion") or {}
        compliance_summary = report.get("compliance_summary") or {}
        rows.append(
            {
                "序号": item.get("index", 0),
                "问题": shorten_text(item.get("question", ""), limit=72),
                "query": item.get("query", ""),
                "who": item.get("who", ""),
                "最终决策": format_decision_label(report.get("final_decision", "")),
                "最终结论": final_conclusion.get("conclusion", ""),
                "可直接出结论": "是" if report.get("can_make_final_compliance_judgement") else "否",
                "初始召回": report.get("initial_recall_atom_count", 0),
                "最终召回": report.get("final_recall_atom_count", 0),
                "轮次": len(report.get("rounds") or []),
                "当前状态": compliance_summary.get("readiness", ""),
            }
        )
    return rows


def render_compliance_recall_report(report):
    if report.get("final_decision") == "LLM_ERROR":
        st.warning("Qwen 当前未连通，下面展示的是本地回退结果。若你在使用 VPN，先关闭后再试。")
    elif report.get("final_decision") == "EXEC_ERROR":
        st.error(f"本次模型召回推理执行异常，下面展示的是回退到 dry-run 的结果。错误信息：{report.get('error', '-')}")
    elif report.get("final_decision") == "DRY_RUN":
        st.info("当前展示的是本地 dry-run 结果，尚未进入 LLM 模型推理。")

    top1, top2, top3, top4, top5 = st.columns(5)
    top1.metric("最终决策", format_decision_label(report.get("final_decision", "-")))
    top2.metric("初始召回", report.get("initial_recall_atom_count", 0))
    top3.metric("最终召回", report.get("final_recall_atom_count", 0))
    top4.metric("推理轮次", len(report.get("rounds") or []))
    top5.metric("可直接出结论", "是" if report.get("can_make_final_compliance_judgement") else "否")

    final_conclusion = report.get("final_conclusion") or {}
    if final_conclusion:
        render_final_conclusion_card(final_conclusion)

    compliance_summary = report.get("compliance_summary") or {}
    readiness = compliance_summary.get("readiness", "")
    if readiness == "ready":
        st.success(compliance_summary.get("headline", "证据已基本齐备，可进入最终结论生成。"))
    elif readiness == "exhausted_partial":
        st.warning(compliance_summary.get("headline", "召回候选已耗尽，当前可形成阶段性判断，但仍有少量关键缺口。"))
    elif readiness == "exhausted_insufficient":
        st.error(compliance_summary.get("headline", "召回候选已耗尽，但证据仍不足以支撑最终结论。"))
    elif readiness in {"insufficient_evidence", "dry_run"}:
        st.info(compliance_summary.get("headline", "当前仍处于模型补证据阶段。"))
    elif readiness == "llm_error":
        st.warning(compliance_summary.get("headline", "LLM 未连通，本次仅输出本地审查摘要。"))

    if compliance_summary:
        head1, head2 = st.columns([1.2, 1.8])
        with head1:
            if compliance_summary.get("route_hint"):
                st.markdown(f"**当前业务语境**：`{compliance_summary['route_hint']}`")
        with head2:
            if compliance_summary.get("next_step"):
                st.markdown(f"**下一步建议**：{compliance_summary['next_step']}")

        summary_col1, summary_col2 = st.columns(2)
        with summary_col1:
            render_summary_list("关键依据", compliance_summary.get("key_basis"))
            render_summary_list("需提交材料", compliance_summary.get("required_materials"))
            render_summary_list("需执行动作", compliance_summary.get("required_actions"))
            render_summary_list("时限/阈值", compliance_summary.get("time_limits"))
        with summary_col2:
            render_summary_list("禁止/限制", compliance_summary.get("prohibitions"))
            render_summary_list("例外/授权", compliance_summary.get("exceptions"))
            render_summary_list("定义边界", compliance_summary.get("definitions"))
            render_summary_list("当前缺口", compliance_summary.get("missing_items"))

        render_gap_summary_cards(compliance_summary.get("gap_summary_cards"))

        gap_rows = build_gap_rows(compliance_summary.get("gap_diagnosis"))
        if gap_rows:
            st.markdown("**缺口诊断**")
            st.dataframe(gap_rows, width="stretch", hide_index=True)

        render_summary_list("风险提示", compliance_summary.get("risk_points"))
        render_summary_list("推荐方向", compliance_summary.get("recommended_directions"))

    business_match = report.get("business_match") or {}
    st.markdown(f"**query**：`{(report.get('query_spec') or {}).get('query', '') or '未单独指定'}`")
    if (report.get("query_spec") or {}).get("who_terms"):
        st.markdown(f"**who 约束**：`{', '.join((report.get('query_spec') or {}).get('who_terms') or [])}`")
    if business_match.get("matched_categories"):
        st.markdown(f"**业务大类命中**：`{' | '.join(business_match['matched_categories'])}`")
    if business_match.get("matched_module_paths"):
        st.markdown(f"**业务模块命中**：`{' | '.join(business_match['matched_module_paths'])}`")
    if business_match.get("matched_scene_names"):
        st.markdown(f"**场景命中**：`{' | '.join(business_match['matched_scene_names'])}`")

    summary_tab, rounds_tab, final_tab = st.tabs(["初始召回", "逐轮推理", "最终证据"])

    with summary_tab:
        scene_scores = business_match.get("scene_scores") or []
        if scene_scores:
            st.dataframe(scene_scores, width="stretch", hide_index=True)
        initial_rows = build_recall_evidence_rows(report.get("initial_evidence"))
        if initial_rows:
            st.dataframe(initial_rows, width="stretch", hide_index=True)
        else:
            st.info("当前没有初始召回证据。")

    with rounds_tab:
        rounds = report.get("rounds") or []
        if not rounds:
            st.info("当前没有进入 LLM 推理轮次；如果上方是 LLM_ERROR 或 EXEC_ERROR，说明本次已经自动回退为本地结果。")
        for item in rounds:
            judge = item.get("judge") or {}
            expander_title = (
                f"第 {item.get('round', '?')} 轮 | {format_decision_label(judge.get('decision', '-'))}"
                f" | 新增 {item.get('new_atom_count', 0)} 条"
            )
            with st.expander(expander_title, expanded=item.get("round") == 1):
                metric1, metric2, metric3, metric4 = st.columns(4)
                metric1.metric("输入证据", item.get("input_atom_count", 0))
                metric2.metric("输出证据", item.get("output_atom_count", item.get("input_atom_count", 0)))
                metric3.metric("judge 置信度", f"{float(judge.get('confidence', 0.0) or 0.0):.2f}")
                metric4.metric("语义缺口画像", format_joined(item.get("semantic_gap_profiles"), sep=", ") or "-")

                if item.get("judge_missing_summary"):
                    st.markdown("**为什么还要继续补充证据**")
                    for summary in item["judge_missing_summary"]:
                        st.write(f"- {summary}")

                direction_rows = build_direction_rows(item.get("applied_directions") or item.get("judge_recommended_directions"))
                if direction_rows:
                    st.markdown("**本轮补充方向**")
                    st.dataframe(direction_rows, width="stretch", hide_index=True)

                atom_analysis_rows = build_atom_analysis_rows(item.get("atom_analysis"))
                if atom_analysis_rows:
                    st.markdown("**原子最小可执行颗粒度分析**")
                    st.dataframe(atom_analysis_rows, width="stretch", hide_index=True)

                for direction_report in item.get("applied_directions") or []:
                    st.markdown(f"**{direction_report.get('direction', '')} {direction_report.get('direction_name', '')}**")
                    hint_parts = [
                        direction_report.get("judge_missing_dimension", ""),
                        direction_report.get("judge_reason", ""),
                    ]
                    hint_text = " | ".join([part for part in hint_parts if part])
                    prefix = f"{hint_text} | " if hint_text else ""
                    st.write(
                        f"{prefix}"
                        f"候选 {direction_report.get('added_candidate_count', 0)} 条，"
                        f"其中相对上一轮净新增候选 {direction_report.get('net_new_candidate_count', 0)} 条。"
                    )
                    sample_rows = build_recall_evidence_rows(direction_report.get("sample_evidence"))
                    if sample_rows:
                        st.dataframe(sample_rows, width="stretch", hide_index=True)

                new_rows = build_recall_evidence_rows(item.get("new_evidence"))
                if new_rows:
                    st.markdown("**本轮新增证据预览**")
                    st.dataframe(new_rows, width="stretch", hide_index=True)

    with final_tab:
        final_rows = build_recall_evidence_rows(report.get("final_evidence"))
        if final_rows:
            st.dataframe(final_rows, width="stretch", hide_index=True)
        else:
            st.info("当前没有最终证据。")


def render_batch_recall_reports(batch_bundle):
    reports = (batch_bundle or {}).get("reports") or []
    if not reports:
        return

    st.subheader("批量召回结果")
    generated_at = (batch_bundle or {}).get("generated_at")
    caption_parts = []
    if generated_at:
        caption_parts.append(f"生成时间：{generated_at}")
    if (batch_bundle or {}).get("mode"):
        caption_parts.append(f"模式：{batch_bundle.get('mode')}")
    if (batch_bundle or {}).get("parallel_workers"):
        caption_parts.append(f"并行数：{batch_bundle.get('parallel_workers')}")
    if caption_parts:
        st.caption(" | ".join(caption_parts))
    st.dataframe(build_batch_recall_summary_rows(reports), width="stretch", hide_index=True)

    for item in reports:
        report = item.get("report") or {}
        title = (
            f"#{item.get('index', '?')} | "
            f"{shorten_text(item.get('question', ''), limit=36)} | "
            f"{format_decision_label(report.get('final_decision', '-'))}"
        )
        with st.expander(title, expanded=item.get("index") == 1):
            render_compliance_recall_report(report)


def atom_color(rule_type):
    text = str(rule_type or "").upper()
    if "PRO" in text:
        return "#ff6b6b"
    if "PER" in text:
        return "#51cf66"
    if "VAL" in text:
        return "#4dabf7"
    if "DEF" in text:
        return "#9775fa"
    return "#94d2bd"


def build_graph(context, scenes, broad_atoms, precise_atoms, mode, selected_who_terms=None, scene_actor_links=None):
    nodes = []
    edges = []
    selected_who_terms = selected_who_terms or []
    scene_actor_links = scene_actor_links or []

    category_id = context["category_key"]
    module_id = context["module_code"]
    selected_scene_id = context.get("scene_key")

    nodes.append(Node(id=category_id, label=context["category_name"], size=28, color="#f4a261", shape="box"))
    nodes.append(Node(id=module_id, label=context["module_name"], size=24, color="#2a9d8f", shape="diamond"))
    edges.append(Edge(source=category_id, target=module_id, color="#457b9d"))

    for scene in scenes:
        scene_color = "#e9c46a" if scene["key"] == selected_scene_id else "#cfe8d5"
        nodes.append(Node(id=scene["key"], label=scene["name"], size=18, color=scene_color, shape="ellipse"))
        edges.append(Edge(source=module_id, target=scene["key"], color="#6c757d"))

    if selected_scene_id:
        for item in scene_actor_links:
            actor_name = item["actor_name"]
            actor_node_id = f"scene-actor::{actor_name}"
            actor_color = "#577590" if actor_name in selected_who_terms else "#8ecae6"
            nodes.append(Node(id=actor_node_id, label=f"{actor_name} ({item['atom_count']})", size=16, color=actor_color, shape="box"))
            edges.append(Edge(source=selected_scene_id, target=actor_node_id, color="#577590"))

    if mode in {"模块宽召回", "对比"}:
        for atom in broad_atoms:
            atom_node_id = f"broad::{atom['atom_id']}"
            nodes.append(
                Node(
                    id=atom_node_id,
                    label=f"{atom['atom_id']} {str(atom['what'])[:10]}",
                    size=12,
                    color=atom_color(atom["rule_type"]),
                    shape="dot",
                )
            )
            edges.append(Edge(source=module_id, target=atom_node_id, color="#adb5bd"))

    if selected_scene_id and mode in {"场景精召回", "对比"}:
        for atom in precise_atoms:
            atom_node_id = f"precise::{atom['atom_id']}"
            nodes.append(
                Node(
                    id=atom_node_id,
                    label=f"{atom['atom_id']} {str(atom['what'])[:10]}",
                    size=14,
                    color=atom_color(atom["rule_type"]),
                    shape="dot",
                )
            )
            edges.append(Edge(source=selected_scene_id, target=atom_node_id, color="#d62828"))
            for actor_name in atom.get("matched_who_terms") or []:
                actor_node_id = f"scene-actor::{actor_name}"
                nodes.append(Node(id=actor_node_id, label=actor_name, size=16, color="#577590", shape="box"))
                edges.append(Edge(source=actor_node_id, target=atom_node_id, color="#577590"))

    config = Config(
        width="100%",
        height=680,
        directed=True,
        physics=True,
        nodeHighlightBehavior=True,
        highlightColor="#f77f00",
        collapsible=False,
    )
    dedup_nodes = list({node.id: node for node in nodes}.values())
    dedup_edges = list(
        {
            (
                getattr(edge, "source", getattr(edge, "from", "")),
                getattr(edge, "to", ""),
                getattr(edge, "label", ""),
            ): edge
            for edge in edges
        }.values()
    )
    return dedup_nodes, dedup_edges, config


def build_category_overview_graph(items):
    nodes = []
    edges = []
    board_colors = {
        "业务管理类": "#2a9d8f",
        "基础管理类": "#577590",
        "待分类": "#adb5bd",
    }

    for item in items:
        board_name = item["board_name"]
        board_id = f"board::{board_name}"
        category_id = item["category_key"]
        category_label = f"{item['category_name']}\n模块 {item['module_count']} | atoms {item['atom_count']}"
        category_size = 20 + min(int(item["module_count"]), 10)

        nodes.append(
            Node(
                id=board_id,
                label=board_name,
                size=30,
                color=board_colors.get(board_name, "#6c757d"),
                shape="box",
            )
        )
        nodes.append(
            Node(
                id=category_id,
                label=category_label,
                size=category_size,
                color="#f4a261",
                shape="ellipse",
            )
        )
        edges.append(Edge(source=board_id, target=category_id, color="#8d99ae"))

    config = Config(
        width="100%",
        height=620,
        directed=True,
        physics=True,
        nodeHighlightBehavior=True,
        highlightColor="#f77f00",
        collapsible=False,
    )
    dedup_nodes = list({node.id: node for node in nodes}.values())
    dedup_edges = list(
        {
            (
                getattr(edge, "source", getattr(edge, "from", "")),
                getattr(edge, "to", ""),
                getattr(edge, "label", ""),
            ): edge
            for edge in edges
        }.values()
    )
    return dedup_nodes, dedup_edges, config


def render_category_overview_tab(driver):
    boards = fetch_boards(driver)
    board_options = ["全部板块"] + boards
    selected_board = st.selectbox("查看范围", board_options, index=0, key="overview_board")
    overview_items = fetch_category_overview(driver, None if selected_board == "全部板块" else selected_board)

    if not overview_items:
        st.info("当前 Neo4j 中没有二级分类数据。")
        return

    board_count = len({item["board_name"] for item in overview_items})
    category_count = len(overview_items)
    module_count = sum(int(item["module_count"]) for item in overview_items)

    top1, top2, top3 = st.columns(3)
    top1.metric("业务板块", board_count)
    top2.metric("业务大类", category_count)
    top3.metric("业务模块", module_count)

    graph_col, table_col = st.columns([2, 1])
    with graph_col:
        nodes, edges, config = build_category_overview_graph(overview_items)
        agraph(nodes=nodes, edges=edges, config=config)

    with table_col:
        st.subheader("分类统计")
        st.dataframe(
            [
                {
                    "板块": item["board_name"],
                    "业务大类": item["category_name"],
                    "模块数": item["module_count"],
                    "atom数": item["atom_count"],
                }
                for item in overview_items
            ],
            width="stretch",
            hide_index=True,
        )


def render_browser_tab(driver):
    boards = fetch_boards(driver)
    board_name = st.sidebar.selectbox("板块", boards, index=0 if "业务管理类" in boards else 0)

    categories = fetch_categories(driver, board_name)
    default_category_idx = next((i for i, item in enumerate(categories) if item["name"] == "二、支付结算"), 0)
    category = st.sidebar.selectbox("业务大类", categories, index=default_category_idx, format_func=lambda item: item["name"])

    modules = fetch_modules(driver, category["key"])
    default_module_idx = next((i for i, item in enumerate(modules) if item["code"] == "BIZ-02-03"), 0)
    module = st.sidebar.selectbox("业务模块", modules, index=default_module_idx, format_func=lambda item: item["label_path"])

    scenes = fetch_scenes(driver, module["code"])
    if not scenes:
        st.warning("当前模块下没有具体场景节点。")
        return

    default_scene_idx = next((i for i, item in enumerate(scenes) if item["name"] == "银行汇票"), 0)
    scene = st.sidebar.selectbox("具体场景", scenes, index=default_scene_idx, format_func=lambda item: f"{item['name']} ({item['precise_count']})")
    actor_options = fetch_scene_actor_terms(driver, scene["key"])
    selected_who_terms = st.sidebar.multiselect(
        "主体细筛",
        options=[item["name"] for item in actor_options],
        default=[],
        help="在场景精召回结果内，再按 who 做最细筛选。",
    )

    mode = st.sidebar.radio("观察方式", ["对比", "场景精召回", "模块宽召回"], index=0)
    atom_limit = st.sidebar.slider("图中最多展示 atom 数", min_value=10, max_value=80, value=30, step=10)

    context = fetch_context(driver, module["code"], scene["key"])
    broad_count, precise_count, refined_count = fetch_counts(driver, module["code"], scene["key"], selected_who_terms)
    broad_atoms = fetch_broad_atoms(driver, module["code"], atom_limit if mode != "场景精召回" else 20)
    precise_atoms = fetch_precise_atoms(driver, scene["key"], atom_limit if mode != "模块宽召回" else 20, selected_who_terms)
    scene_actor_links = fetch_scene_actor_links(driver, scene["key"], selected_who_terms, limit=10) if selected_who_terms else []

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("业务大类", context["category_name"])
    top2.metric("业务模块宽召回", broad_count)
    top3.metric("场景精召回", precise_count)
    top4.metric("主体细筛", refined_count if refined_count is not None else "-")

    st.markdown(f"**当前路径**：`{context['category_name']} -> {context['module_name']} -> {context.get('scene_name') or '未选择场景'}`")
    if selected_who_terms:
        st.markdown(f"**当前主体筛选**：`{', '.join(selected_who_terms)}`")

    left_col, right_col = st.columns([2, 1])
    with left_col:
        nodes, edges, config = build_graph(context, scenes, broad_atoms, precise_atoms, mode, selected_who_terms, scene_actor_links)
        agraph(nodes=nodes, edges=edges, config=config)

    with right_col:
        st.subheader("模块下场景")
        for item in scenes[:20]:
            prefix = ">>" if item["key"] == scene["key"] else "-"
            st.write(f"{prefix} {item['name']} ({item['precise_count']})")
        if actor_options:
            st.subheader("场景高频主体")
            for item in actor_options[:10]:
                st.write(f"- {item['name']} ({item['precise_count']})")

    detail_left, detail_right = st.columns(2)
    with detail_left:
        st.subheader("场景精召回样本")
        if precise_atoms:
            st.dataframe(
                [
                    {
                        "atom_id": item["atom_id"],
                        "score": item["score"],
                        "who": item["who"],
                        "rule_type": item["rule_type"],
                        "what": item["what"],
                        "how": item["how"],
                        "matched_who_terms": ",".join(item.get("matched_who_terms") or []),
                        "matched_terms": ",".join(item.get("matched_terms") or []),
                    }
                    for item in precise_atoms
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("当前场景下还没有精召回 atom。")

    with detail_right:
        st.subheader("模块宽召回样本")
        st.dataframe(
            [
                {
                    "atom_id": item["atom_id"],
                    "who": item["who"],
                    "rule_type": item["rule_type"],
                    "what": item["what"],
                    "how": item["how"],
                }
                for item in broad_atoms
            ],
            width="stretch",
            hide_index=True,
        )


def render_scenario_tab(driver):
    preset_key = st.selectbox(
        "业务场景模板",
        list(SCENARIO_PRESETS.keys()),
        format_func=lambda key: SCENARIO_PRESETS[key]["title"],
    )
    preset = SCENARIO_PRESETS[preset_key]

    context = fetch_context(driver, preset["module_code"], preset["scene_key"])
    scenes = fetch_scenes(driver, preset["module_code"])
    actor_options = fetch_scene_actor_terms(driver, preset["scene_key"])
    actor_option_names = [item["name"] for item in actor_options]
    default_terms = [term for term in preset["actor_terms"] if term in actor_option_names]
    selected_actor_terms = st.multiselect(
        "主体范围",
        options=actor_option_names,
        default=default_terms,
        help="先按业务问题中的关键主体缩小场景精召回结果。",
    )

    extra_actor = st.text_input("补充主体关键词", value="")
    actor_terms = list(dict.fromkeys(selected_actor_terms + ([extra_actor.strip()] if extra_actor.strip() else [])))
    atom_limit = st.slider("问题图中最多展示 atom 数", min_value=10, max_value=80, value=25, step=5, key="scenario_atom_limit")

    broad_count, precise_count, refined_count = fetch_counts(driver, preset["module_code"], preset["scene_key"], actor_terms)
    precise_atoms = fetch_precise_atoms(driver, preset["scene_key"], 160, actor_terms)
    exact_persona_atoms = [item for item in precise_atoms if atom_matches_actor(item, [preset["actor_terms"][-1]])]
    scene_actor_links = fetch_scene_actor_links(driver, preset["scene_key"], actor_terms or preset["actor_terms"], limit=12)

    st.subheader(preset["title"])
    st.markdown(f"**问题**：{preset['question']}")
    st.markdown(f"**路径**：`{context['category_name']} -> {context['module_name']} -> {context.get('scene_name') or '未选择'}`")
    if actor_terms:
        st.markdown(f"**主体约束**：`{', '.join(actor_terms)}`")

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("模块宽召回", broad_count)
    top2.metric("场景精召回", precise_count)
    top3.metric("主体细筛", refined_count if refined_count is not None else len(precise_atoms))
    top4.metric("精确人物画像", len(exact_persona_atoms))

    graph_col, side_col = st.columns([2, 1])
    with graph_col:
        nodes, edges, config = build_graph(
            context,
            scenes,
            [],
            precise_atoms[:atom_limit],
            "场景精召回",
            actor_terms,
            scene_actor_links,
        )
        agraph(nodes=nodes, edges=edges, config=config)

    with side_col:
        st.subheader("相关主体")
        for item in scene_actor_links[:10]:
            st.write(f"- {item['actor_name']} ({item['atom_count']})")

    focus_tabs = st.tabs([group["label"] for group in preset["focus_groups"]] + ["全部证据"])
    for tab, group in zip(focus_tabs[:-1], preset["focus_groups"]):
        with tab:
            group_atoms = filter_atoms_by_keywords(precise_atoms, group["terms"])
            if not group_atoms:
                st.info(f"当前主体范围下，没有命中“{group['label']}”相关证据。")
            else:
                st.dataframe(
                    [
                        {
                            "atom_id": item["atom_id"],
                            "who": item["who"],
                            "rule_type": item["rule_type"],
                            "what": item["what"],
                            "how": item["how"],
                            "matched_terms": ",".join(item.get("matched_terms") or []),
                            "matched_who_terms": ",".join(item.get("matched_who_terms") or []),
                        }
                        for item in group_atoms
                    ],
                    width="stretch",
                    hide_index=True,
                )

    with focus_tabs[-1]:
        st.dataframe(
            [
                {
                    "atom_id": item["atom_id"],
                    "score": item["score"],
                    "who": item["who"],
                    "rule_type": item["rule_type"],
                    "what": item["what"],
                    "how": item["how"],
                    "matched_who_terms": ",".join(item.get("matched_who_terms") or []),
                    "matched_terms": ",".join(item.get("matched_terms") or []),
                }
                for item in precise_atoms
            ],
            width="stretch",
            hide_index=True,
        )

    recall_question_key = f"scenario_recall_question_{preset_key}"
    recall_query_key = f"scenario_recall_query_{preset_key}"
    recall_who_key = f"scenario_recall_who_{preset_key}"
    recall_rounds_key = f"scenario_recall_rounds_{preset_key}"
    recall_workers_key = f"scenario_recall_workers_{preset_key}"
    recall_report_key = f"scenario_recall_report_{preset_key}"

    if recall_question_key not in st.session_state:
        st.session_state[recall_question_key] = preset["question"]
    if recall_query_key not in st.session_state:
        st.session_state[recall_query_key] = preset.get("query") or context.get("scene_name") or preset["title"]
    if recall_who_key not in st.session_state:
        st.session_state[recall_who_key] = preset.get("recall_who") or (preset["actor_terms"][0] if preset["actor_terms"] else "")
    if recall_rounds_key not in st.session_state:
        st.session_state[recall_rounds_key] = 2
    if recall_workers_key not in st.session_state:
        st.session_state[recall_workers_key] = DEFAULT_RECALL_PARALLEL_WORKERS

    st.divider()
    st.subheader("模型召回推理")

    recall_api_col, recall_base_col, recall_model_col = st.columns([1.2, 1.4, 1])
    with recall_api_col:
        recall_api_key = st.text_input(
            "API Key",
            key="recall_api_key",
            value=st.session_state.get("extract_api_key", ""),
            type="password",
            help="用于召回推理阶段的 LLM 调用。",
        )
    with recall_base_col:
        recall_base_url = st.text_input(
            "Base URL",
            key="recall_base_url",
            value=st.session_state.get("extract_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
    with recall_model_col:
        recall_model = st.text_input(
            "召回模型",
            key="recall_model",
            value=st.session_state.get("extract_reasoning_model") or st.session_state.get("extract_model", "qwen-plus"),
        )

    with st.form(key=f"scenario_recall_form_{preset_key}"):
        st.text_area("合规问题", key=recall_question_key, height=110)
        form_left, form_mid, form_right, form_parallel = st.columns([1.4, 0.9, 0.7, 0.7])
        with form_left:
            st.text_area(
                "业务 query（可多行）",
                key=recall_query_key,
                height=88,
                help="每行 1 个 query；也支持 `query | who` 或 `query<TAB>who`。多行会并行召回并推理。",
            )
        with form_mid:
            st.text_input("主体关键词", key=recall_who_key)
        with form_right:
            st.slider("最多推理轮次", min_value=1, max_value=4, key=recall_rounds_key)
        with form_parallel:
            st.slider("并行数", min_value=1, max_value=MAX_RECALL_PARALLEL_WORKERS, key=recall_workers_key)
        submitted = st.form_submit_button("运行模型推理", width="stretch")

    if submitted:
        missing = validate_llm_inputs(recall_api_key, recall_base_url, recall_model, model_label="召回模型")
        if missing:
            st.error(f"请先填写：{'、'.join(missing)}")
        else:
            report = None
            try:
                previous_report = st.session_state.get(recall_report_key)
                recall_items = parse_multi_query_recall_inputs(
                    st.session_state[recall_question_key],
                    st.session_state[recall_query_key],
                    default_who=st.session_state[recall_who_key],
                )
                if not recall_items:
                    st.error("当前没有解析出可执行的 query，请至少输入 1 个业务 query。")
                    recall_items = []
                resume_report = None
                if len(recall_items) == 1 and not (isinstance(previous_report, dict) and "reports" in previous_report):
                    item = recall_items[0]
                    resume_report = previous_report if should_resume_recall(
                        previous_report,
                        item["question"],
                        item["query"],
                        item["who"],
                    ) else None
                with st.spinner("正在根据 3 份提示词执行模型召回与推理分析..."):
                    with temporary_api_env(recall_api_key, recall_base_url, recall_model, recall_model):
                        controller = get_recall_controller(recall_model)
                        if len(recall_items) > 1:
                            progress = st.progress(0, text="准备执行多 query 并行召回...")
                            batch_reports = run_recall_items_parallel(
                                controller,
                                recall_items,
                                max_rounds=int(st.session_state[recall_rounds_key]),
                                max_workers=int(st.session_state[recall_workers_key]),
                                progress=progress,
                            )
                            progress.empty()
                            report = {
                                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "mode": "multi_query",
                                "parallel_workers": min(
                                    int(st.session_state[recall_workers_key]),
                                    len(recall_items),
                                    MAX_RECALL_PARALLEL_WORKERS,
                                ),
                                "reports": batch_reports,
                            }
                        elif len(recall_items) == 1:
                            item = recall_items[0]
                            report = run_recall_with_fallback(
                                controller,
                                question=item["question"],
                                query=item["query"],
                                who=item["who"],
                                max_rounds=int(st.session_state[recall_rounds_key]),
                                resume_report=resume_report,
                            )
            except Exception as exc:
                st.error(f"模型召回推理初始化失败：{exc}")

            if report is not None:
                st.session_state[recall_report_key] = report

    if recall_report_key in st.session_state:
        stored_report = st.session_state[recall_report_key]
        if isinstance(stored_report, dict) and "reports" in stored_report:
            render_batch_recall_reports(stored_report)
        else:
            render_compliance_recall_report(stored_report)


def render_batch_recall_extension():
    preset_key = st.selectbox(
        "批量默认模板",
        list(SCENARIO_PRESETS.keys()),
        format_func=lambda key: SCENARIO_PRESETS[key]["title"],
        key="batch_recall_preset_key",
    )
    preset = SCENARIO_PRESETS[preset_key]

    last_preset_key = "batch_recall_last_preset_key"
    batch_input_key = "batch_recall_input"
    batch_query_key = "batch_recall_default_query"
    batch_who_key = "batch_recall_default_who"
    batch_rounds_key = "batch_recall_rounds"
    batch_workers_key = "batch_recall_workers"
    batch_report_key = "batch_recall_report_bundle"

    if st.session_state.get(last_preset_key) != preset_key:
        st.session_state[batch_input_key] = preset["question"]
        st.session_state[batch_query_key] = preset.get("query") or preset["title"]
        st.session_state[batch_who_key] = preset.get("recall_who") or (preset["actor_terms"][0] if preset["actor_terms"] else "")
        st.session_state[last_preset_key] = preset_key
    st.session_state.setdefault(batch_rounds_key, 2)
    st.session_state.setdefault(batch_workers_key, DEFAULT_RECALL_PARALLEL_WORKERS)

    st.divider()
    st.subheader("批量问题召回")
    st.caption("每行输入 1 个问题；也支持 `问题 | query | who` 或 `问题<TAB>query<TAB>who`。若行内未写 query / who，则回退到下面默认值。")

    current_model = st.session_state.get("recall_model") or st.session_state.get("extract_reasoning_model") or st.session_state.get("extract_model", "")
    current_base_url = st.session_state.get("recall_base_url") or st.session_state.get("extract_base_url", "")
    if current_model or current_base_url:
        st.caption(f"沿用上方召回配置：model={current_model or '-'} | base_url={current_base_url or '-'}")

    with st.form(key="batch_recall_form"):
        st.text_area("批量问题输入", key=batch_input_key, height=180)
        form_left, form_mid, form_right, form_parallel = st.columns([1.2, 1, 0.8, 0.7])
        with form_left:
            st.text_input("默认业务 query", key=batch_query_key)
        with form_mid:
            st.text_input("默认主体关键词", key=batch_who_key)
        with form_right:
            st.slider("最大推理轮次", min_value=1, max_value=4, key=batch_rounds_key)
        with form_parallel:
            st.slider("并行数", min_value=1, max_value=MAX_RECALL_PARALLEL_WORKERS, key=batch_workers_key)
        batch_submitted = st.form_submit_button("运行批量召回", width="stretch")

    if batch_submitted:
        recall_api_key = st.session_state.get("recall_api_key", "")
        recall_base_url = st.session_state.get("recall_base_url", st.session_state.get("extract_base_url", ""))
        recall_model = st.session_state.get("recall_model") or st.session_state.get("extract_reasoning_model") or st.session_state.get("extract_model", "")
        missing = validate_llm_inputs(recall_api_key, recall_base_url, recall_model, model_label="召回模型")
        if missing:
            st.error(f"请先在上方单问题区域填写：{'、'.join(missing)}")
        else:
            batch_items = parse_batch_recall_inputs(
                st.session_state[batch_input_key],
                default_query=st.session_state[batch_query_key],
                default_who=st.session_state[batch_who_key],
            )
            if not batch_items:
                st.error("当前没有解析出可执行的问题，请至少输入 1 行问题。")
            else:
                try:
                    progress = st.progress(0, text="准备执行批量召回...")
                    with temporary_api_env(recall_api_key, recall_base_url, recall_model, recall_model):
                        controller = get_recall_controller(recall_model)
                        batch_reports = run_recall_items_parallel(
                            controller,
                            batch_items,
                            max_rounds=int(st.session_state[batch_rounds_key]),
                            max_workers=int(st.session_state[batch_workers_key]),
                            progress=progress,
                        )
                    progress.empty()
                    st.session_state[batch_report_key] = {
                        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "mode": "batch_question",
                        "parallel_workers": min(
                            int(st.session_state[batch_workers_key]),
                            len(batch_items),
                            MAX_RECALL_PARALLEL_WORKERS,
                        ),
                        "reports": batch_reports,
                    }
                except Exception as exc:
                    st.error(f"批量召回执行失败：{exc}")

    if batch_report_key in st.session_state:
        render_batch_recall_reports(st.session_state[batch_report_key])


def render_extraction_tab():
    st.subheader("法规抽取与图谱构建")

    default_doc_names = [path.name for path in match_raw_docs(["票据法", "支付结算办法"])]
    if "extract_doc_input_mode" not in st.session_state:
        st.session_state["extract_doc_input_mode"] = "勾选文档"
    if "extract_selected_docs" not in st.session_state:
        st.session_state["extract_selected_docs"] = default_doc_names
    if "extract_doc_keywords" not in st.session_state:
        st.session_state["extract_doc_keywords"] = "票据法\n支付结算办法"
    if "extract_upload_feedback" not in st.session_state:
        st.session_state["extract_upload_feedback"] = ""

    raw_docs = list_raw_docs()
    upload_feedback = st.session_state.pop("extract_upload_feedback", "")
    if upload_feedback:
        st.success(upload_feedback)

    artifacts = get_session_generated_artifacts()
    top1, top2, top3 = st.columns(3)
    top1.metric("原始法规文档", len(raw_docs))
    top2.metric("本次会话产物", len(artifacts))
    top3.metric("现有 UI 能力", "抽取 + 图谱 + 召回")

    config_col, info_col = st.columns([1.4, 1])
    with config_col:
        st.markdown("**API 配置**")
        api_key = st.text_input(
            "API Key",
            key="extract_api_key",
            type="password",
            help="用于抽取与分类阶段的 LLM 调用。",
        )
        base_url = st.text_input(
            "Base URL",
            key="extract_base_url",
            value="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model_col, reasoning_col = st.columns(2)
        with model_col:
            llm_model = st.text_input("抽取模型", key="extract_model", value="qwen-plus")
        with reasoning_col:
            reasoning_model = st.text_input("分类/推理模型", key="extract_reasoning_model", value="qwen-plus")

        st.markdown("**抽取输入**")
        uploaded_files = st.file_uploader(
            "上传 docx",
            type=["docx"],
            accept_multiple_files=True,
            key="extract_doc_uploads",
        )
        if uploaded_files:
            if st.button("导入上传文件", key="extract_import_uploads", width="stretch"):
                saved_names, skipped_names = save_uploaded_docx_files(uploaded_files)
                selected_docs = st.session_state.get("extract_selected_docs", [])
                st.session_state["extract_selected_docs"] = list(dict.fromkeys(selected_docs + saved_names))
                st.session_state["extract_doc_input_mode"] = "勾选文档"

                messages = []
                if saved_names:
                    messages.append(f"已导入 {len(saved_names)} 个文档")
                if skipped_names:
                    messages.append(f"已跳过 {len(skipped_names)} 个非 docx 文件")
                st.session_state["extract_upload_feedback"] = "；".join(messages) or "上传未产生新文档"

                rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
                if rerun:
                    rerun()

        doc_input_mode = st.radio(
            "文档选择方式",
            ["勾选文档", "关键词筛选"],
            key="extract_doc_input_mode",
            horizontal=True,
        )
        if doc_input_mode == "勾选文档":
            st.multiselect(
                "目标文档",
                options=[path.name for path in raw_docs],
                key="extract_selected_docs",
            )
        else:
            st.text_area(
                "文档关键词",
                key="extract_doc_keywords",
                height=90,
            )
        max_chunks = st.number_input(
            "每份文档最多处理块数",
            min_value=0,
            max_value=500,
            value=0,
            step=1,
            help="0 表示不限制。",
        )

        st.markdown("**输出命名**")
        phase1_output_name = st.text_input("Phase1/2 输出", key="extract_phase1_output", value="phase1_entities_checkpoint.xlsx")
        atoms_output_name = st.text_input("原子抽取输出", key="extract_atoms_output", value="legal_atoms_v4_final.xlsx")
        classified_output_name = st.text_input("业务分类输出", key="extract_classified_output", value="legal_atoms_business_taxonomy.xlsx")

        st.markdown("**分类与图谱参数**")
        batch_col, force_col, heuristic_col = st.columns(3)
        with batch_col:
            batch_size = st.number_input("分类批次", min_value=1, max_value=50, value=12, step=1)
        with force_col:
            force_classify = st.checkbox("分类强制重跑", value=False, help="忽略 checkpoint，重新发起分类。")
        with heuristic_col:
            heuristic_only = st.checkbox("分类只用启发式", value=False, help="不调用 LLM，只用本地启发式分类。")

        neo4j_col1, neo4j_col2, neo4j_col3, neo4j_col4 = st.columns([1.4, 0.8, 0.8, 0.8])
        with neo4j_col1:
            neo4j_uri = st.text_input("Neo4j URI", key="extract_neo4j_uri", value=URI)
        with neo4j_col2:
            neo4j_user = st.text_input("Neo4j 用户名", key="extract_neo4j_user", value=AUTH[0])
        with neo4j_col3:
            neo4j_password = st.text_input("Neo4j 密码", key="extract_neo4j_password", value=AUTH[1], type="password")
        with neo4j_col4:
            clear_graph = st.checkbox("导入前清空图谱", value=False)

    if st.session_state["extract_doc_input_mode"] == "勾选文档":
        matched_docs = match_raw_docs_by_names(st.session_state.get("extract_selected_docs", []))
        doc_keywords = [path.name for path in matched_docs]
        doc_panel_title = "已选文档"
        missing_docs_error = "请至少选择一个原始法规文档。"
    else:
        doc_keywords = split_keyword_lines(st.session_state.get("extract_doc_keywords", ""))
        matched_docs = match_raw_docs(doc_keywords)
        doc_panel_title = "命中文档"
        missing_docs_error = "当前文档关键词没有命中任何原始法规文档。"

    with info_col:
        st.markdown(f"**{doc_panel_title}**")
        if matched_docs:
            st.dataframe(
                [{"文档": path.name} for path in matched_docs[:12]],
                width="stretch",
                hide_index=True,
            )
        else:
            st.warning(missing_docs_error)

        st.markdown("**当前产物盘点**")
        if artifacts:
            st.dataframe(artifacts[:12], width="stretch", hide_index=True)
        else:
            st.info("当前会话尚未生成新产物。默认不展示已有项目文件。")

    action_col1, action_col2, action_col3, action_col4 = st.columns(4)
    with action_col1:
        run_phase12_clicked = st.button("运行 Phase1/2", width="stretch")
    with action_col2:
        run_atoms_clicked = st.button("运行原子抽取", width="stretch")
    with action_col3:
        run_classify_clicked = st.button("运行业务分类", width="stretch")
    with action_col4:
        run_graph_clicked = st.button("导入 Neo4j", width="stretch")

    def llm_ready_or_error():
        missing = validate_llm_inputs(api_key, base_url, llm_model, model_label="抽取模型")
        if missing:
            st.error(f"请先填写：{'、'.join(missing)}")
            return False
        return True

    status_placeholder = st.empty()

    if run_phase12_clicked:
        if not matched_docs:
            st.error(missing_docs_error)
        elif llm_ready_or_error():
            try:
                status_placeholder.info("正在执行 Phase1/2 NER...")
                with st.spinner("正在扫描文档并抽取实体..."):
                    with temporary_api_env(api_key, base_url, llm_model, reasoning_model):
                        output_path = run_phase1(
                            doc_keywords=doc_keywords,
                            output_name=phase1_output_name,
                            model=llm_model,
                            max_chunks_per_doc=int(max_chunks),
                        )
                st.session_state["extract_phase1_summary"] = summarize_phase1_file(output_path)
                remember_generated_artifact(output_path)
                status_placeholder.success(f"Phase1/2 已完成：{output_path}")
            except Exception as exc:
                status_placeholder.error(f"Phase1/2 执行失败：{exc}")

    if run_atoms_clicked:
        if not matched_docs:
            st.error(missing_docs_error)
        elif llm_ready_or_error():
            try:
                status_placeholder.info("正在执行三阶段原子抽取...")
                with st.spinner("正在完成 Stage1/2/3 全链路抽取..."):
                    with temporary_api_env(api_key, base_url, llm_model, reasoning_model):
                        output_path = run_pipeline(
                            doc_keywords=doc_keywords,
                            output_name=atoms_output_name,
                            model=llm_model,
                            max_chunks_per_doc=int(max_chunks),
                        )
                st.session_state["extract_atoms_summary"] = summarize_atoms_file(output_path)
                remember_generated_artifact(output_path)
                status_placeholder.success(f"原子抽取已完成：{output_path}")
            except Exception as exc:
                status_placeholder.error(f"原子抽取执行失败：{exc}")

    if run_classify_clicked:
        atoms_path = PROCESSED_DIR / atoms_output_name
        if not atoms_path.exists():
            st.error(f"未找到原子抽取结果：{atoms_path}")
        elif (not heuristic_only) and (not llm_ready_or_error()):
            pass
        else:
            try:
                status_placeholder.info("正在执行业务分类与场景挂接...")
                df = pd.read_excel(atoms_path).fillna("")
                _, entries, _ = parse_taxonomy(resolve_taxonomy_doc())
                with st.spinner("正在将原子映射到业务分类体系..."):
                    if heuristic_only:
                        classified_df = classify_atoms(
                            df,
                            entries,
                            model=reasoning_model or llm_model,
                            batch_size=int(batch_size),
                            force=force_classify,
                            heuristic_only=True,
                        )
                    else:
                        with temporary_api_env(api_key, base_url, llm_model, reasoning_model):
                            classified_df = classify_atoms(
                                df,
                                entries,
                                model=reasoning_model or llm_model,
                                batch_size=int(batch_size),
                                force=force_classify,
                                heuristic_only=False,
                            )
                output_path = PROCESSED_DIR / classified_output_name
                classified_df.to_excel(output_path, index=False)
                st.session_state["extract_classified_summary"] = summarize_classified_file(output_path)
                remember_generated_artifact(output_path)
                status_placeholder.success(f"业务分类已完成：{output_path}")
            except Exception as exc:
                status_placeholder.error(f"业务分类执行失败：{exc}")

    if run_graph_clicked:
        classified_path = PROCESSED_DIR / classified_output_name
        if not classified_path.exists():
            st.error(f"未找到业务分类结果：{classified_path}")
        else:
            try:
                status_placeholder.info("正在导入 Neo4j 业务图谱...")
                classified_summary = st.session_state.get("extract_classified_summary")
                if not classified_summary or classified_summary.get("path") != str(classified_path):
                    classified_summary = summarize_classified_file(classified_path)
                    st.session_state["extract_classified_summary"] = classified_summary
                df = pd.read_excel(classified_path).fillna("")
                with st.spinner("正在构建 BusinessBoard/Scene/Atom 图谱..."):
                    graph_stats = load_business_graph(
                        df,
                        classified_summary["entries"],
                        classified_summary["scenes"],
                        clear_first=clear_graph,
                        uri=neo4j_uri,
                        user=neo4j_user,
                        password=neo4j_password,
                    )
                st.session_state["extract_graph_stats"] = graph_stats
                status_placeholder.success("Neo4j 图谱导入完成。")
            except Exception as exc:
                status_placeholder.error(f"Neo4j 导入失败：{exc}")

    phase1_summary = st.session_state.get("extract_phase1_summary")
    if phase1_summary:
        st.divider()
        st.markdown("**Phase1/2 NER 输出**")
        metric1, metric2, metric3, metric4 = st.columns(4)
        metric1.metric("文档数", phase1_summary["doc_count"])
        metric2.metric("切块数", phase1_summary["chunk_count"])
        metric3.metric("有实体块", phase1_summary["non_empty_chunk_count"])
        metric4.metric("实体总数", phase1_summary["entity_total"])
        st.dataframe(phase1_summary["preview"], width="stretch", hide_index=True)

    atoms_summary = st.session_state.get("extract_atoms_summary")
    if atoms_summary:
        st.divider()
        st.markdown("**三阶段原子抽取输出**")
        metric1, metric2, metric3, metric4 = st.columns(4)
        metric1.metric("原子数", atoms_summary["rows"])
        metric2.metric("来源法规", atoms_summary["doc_count"])
        metric3.metric("规则类型", atoms_summary["rule_type_count"])
        metric4.metric("歧义原子", atoms_summary["ambiguous_count"])
        st.dataframe(atoms_summary["preview"], width="stretch", hide_index=True)

    classified_summary = st.session_state.get("extract_classified_summary")
    if classified_summary:
        st.divider()
        st.markdown("**业务分类与场景挂接输出**")
        metric1, metric2, metric3, metric4, metric5 = st.columns(5)
        metric1.metric("总原子", classified_summary["rows"])
        metric2.metric("已分类原子", classified_summary["labelled_rows"])
        metric3.metric("待分类原子", classified_summary["unclassified_rows"])
        metric4.metric("命中业务模块", classified_summary["module_count"])
        metric5.metric("场景挂接关系", classified_summary["scene_match_count"])
        st.dataframe(classified_summary["preview"], width="stretch", hide_index=True)

    graph_stats = st.session_state.get("extract_graph_stats")
    if graph_stats:
        st.divider()
        st.markdown("**Neo4j 图谱构建结果**")
        metric1, metric2, metric3, metric4, metric5 = st.columns(5)
        metric1.metric("Boards", graph_stats.get("boards", 0))
        metric2.metric("Categories", graph_stats.get("categories", 0))
        metric3.metric("Modules", graph_stats.get("modules", 0))
        metric4.metric("Scenes", graph_stats.get("scenes", 0))
        metric5.metric("Atoms", graph_stats.get("atoms", 0))

        metric6, metric7, metric8, metric9 = st.columns(4)
        metric6.metric("标签关系", graph_stats.get("tags", 0))
        metric7.metric("Actor 节点", graph_stats.get("actors", 0))
        metric8.metric("场景命中", graph_stats.get("scene_matches", 0))
        metric9.metric("场景主体", graph_stats.get("scene_actors", 0))


def main():
    st.set_page_config(page_title="金融法规模型召回推理", layout="wide")
    st.title("金融法规模型召回推理")

    driver = get_driver()
    extraction_tab, checklist_tab, overview_tab, browse_tab, scenario_tab = st.tabs(["抽取与构建", "人工核查清单", "分类概览", "图谱浏览", "模型推理演示"])

    with extraction_tab:
        render_extraction_tab()

    with checklist_tab:
        render_checklist_tab(driver)

    with overview_tab:
        render_category_overview_tab(driver)

    with browse_tab:
        render_browser_tab(driver)

    with scenario_tab:
        render_scenario_tab(driver)
        render_batch_recall_extension()


if __name__ == "__main__":
    main()
