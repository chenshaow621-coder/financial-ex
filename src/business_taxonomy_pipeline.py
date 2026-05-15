import argparse
import ast
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from data_loader import clean_text, iter_block_items, load_and_chunk_document
from dictionary_builder import build_entity_dictionary
from entity_normalization import (
    extract_normalized_actor_names,
    extract_normalized_actor_records,
    extract_normalized_object_names,
    extract_normalized_object_records,
    extract_normalized_time_names,
    extract_normalized_time_records,
    load_reference_terms,
)
from main import extract_3stage_with_retry, flatten_atom_rows, resolve_entity_dict_path
from mysql_traceability import add_mysql_sync_args, maybe_sync_artifacts_from_args
from neo4j_config import DEFAULT_NEO4J_URI, DEFAULT_NEO4J_USER, get_neo4j_password
from qwen_client import call_qwen, get_reasoning_model


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_ATOMS_FILE = PROCESSED_DIR / "legal_atoms_v4_final.xlsx"
DEFAULT_CLASSIFIED_FILE = PROCESSED_DIR / "legal_atoms_business_taxonomy.xlsx"
DEFAULT_TAXONOMY_XLSX = PROCESSED_DIR / "business_taxonomy_catalog.xlsx"
DEFAULT_TAXONOMY_JSON = PROCESSED_DIR / "business_taxonomy_catalog.json"
DEFAULT_GRAPH_JSON = PROCESSED_DIR / "business_taxonomy_graph.json"
DEFAULT_RECALL_JSON = PROCESSED_DIR / "business_taxonomy_recall_report.json"
DEFAULT_CHECKPOINT_JSON = PROCESSED_DIR / "business_taxonomy_checkpoint.json"

UNCLASSIFIED_CODE = "UNCAT-00-00"
UNCLASSIFIED_PATH = "待分类 > 待分类 > 待分类"
GENERIC_TERMS = {
    "业务", "管理", "办理", "处理", "核查", "核对", "报告", "报送", "流程", "资料",
    "档案", "信息", "系统", "台账", "运营", "监控", "检查", "管理类", "业务管理类", "基础管理类",
}


def clean_json_string(raw_str: str) -> str:
    if not raw_str:
        return ""
    cleaned = re.sub(r"```json\s*", "", raw_str)
    cleaned = re.sub(r"```\s*", "", cleaned)
    return cleaned.strip()


def parse_multiline_items(text: str) -> list[str]:
    items = []
    for chunk in re.split(r"[\r\n]+", str(text or "")):
        cleaned = clean_text(chunk)
        if cleaned:
            items.append(cleaned)
    return items


def safe_literal_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    parts = re.split(r"[，,;；/|]", text)
    return [part.strip() for part in parts if part.strip()]


def normalize_code(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def normalize_path(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("＞", ">").replace("->", ">")


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def split_label_terms(text: str) -> list[str]:
    value = clean_text(str(text or "")).strip()
    if not value:
        return []
    parts = re.split(r"[、，,；;|/]+|\s+|和|及|与|或", value)
    return [part.strip(":： ").strip() for part in parts if part.strip(":： ").strip()]


@lru_cache(maxsize=1)
def load_who_dictionary_terms() -> tuple[str, ...]:
    try:
        entity_dict = build_entity_dictionary(str(resolve_entity_dict_path()))
    except Exception:
        return tuple()

    raw_terms = []
    for key in ("法律主体(WHO)", "法律主体", "LEGAL_ACTORS"):
        raw_terms.extend(entity_dict.get(key, []))

    cleaned = [
        clean_text(str(term or "")).strip()
        for term in raw_terms
        if clean_text(str(term or "")).strip()
    ]
    cleaned = dedupe_keep_order(sorted(cleaned, key=lambda item: (-len(item), item)))
    return tuple(cleaned)


@lru_cache(maxsize=1)
def load_what_dictionary_terms() -> tuple[str, ...]:
    try:
        return load_reference_terms(str(resolve_entity_dict_path()), group_key="WHAT")
    except Exception:
        return tuple()


@lru_cache(maxsize=1)
def load_when_dictionary_terms() -> tuple[str, ...]:
    try:
        return load_reference_terms(str(resolve_entity_dict_path()), group_key="WHEN")
    except Exception:
        return tuple()


def extract_who_terms(who_text: str) -> list[str]:
    normalized = clean_text(str(who_text or "")).strip()
    if not normalized or normalized.lower() == "nan" or normalized in {"未指定", "None", "null"}:
        return []

    fragments = split_label_terms(normalized)
    if normalized not in fragments:
        fragments.insert(0, normalized)

    dictionary_matches = []
    for term in load_who_dictionary_terms():
        if term and term in normalized:
            dictionary_matches.append(term)

    return dedupe_keep_order(fragments + dictionary_matches)[:20]


def extract_what_terms(what_text: str) -> list[str]:
    normalized = clean_text(str(what_text or "")).strip()
    if not normalized or normalized.lower() == "nan" or normalized in {"未指定", "None", "null"}:
        return []

    fragments = split_label_terms(normalized)
    if normalized not in fragments and len(normalized) <= 30 and not any(sep in normalized for sep in ("；", ";", "、")):
        fragments.insert(0, normalized)

    dictionary_matches = []
    for term in load_what_dictionary_terms():
        if term and term in normalized:
            dictionary_matches.append(term)

    filtered = []
    for term in dedupe_keep_order(dictionary_matches + fragments):
        value = clean_text(str(term or "")).strip()
        if not value:
            continue
        if len(value) <= 1:
            continue
        if len(value) == 2 and value not in {"支票", "本票", "汇票", "票据", "现金"}:
            continue
        if any(sep in value for sep in ("；", ";")):
            continue
        filtered.append(value)
    return filtered[:20]


def extract_when_terms(when_text: str) -> list[str]:
    normalized = clean_text(str(when_text or "")).strip()
    if not normalized or normalized.lower() == "nan" or normalized in {"未指定", "None", "null"}:
        return []

    fragments = split_label_terms(normalized)
    if normalized not in fragments and len(normalized) <= 28 and not any(sep in normalized for sep in ("；", ";", "、", "，", ",")):
        fragments.insert(0, normalized)

    dictionary_matches = []
    for term in load_when_dictionary_terms():
        if term and term in normalized:
            dictionary_matches.append(term)

    filtered = []
    for term in dedupe_keep_order(dictionary_matches + fragments):
        value = clean_text(str(term or "")).strip()
        if not value:
            continue
        if any(sep in value for sep in ("；", ";", "、", "，", ",")):
            continue
        if len(value) <= 2 and value not in {"当日", "次日", "每日", "每月", "每年"}:
            continue
        if not (
            value.endswith(("时", "前", "后", "期间", "期内", "以内", "之内", "之前", "之后"))
            or re.search(r"\d", value)
            or "日起" in value
            or "期限" in value
            or "当日" in value
            or "次日" in value
            or value.startswith("每")
        ):
            continue
        filtered.append(value)
    return filtered[:15]


def extract_actor_graph_records(who_text: str) -> list[dict]:
    raw_terms = extract_who_terms(who_text)
    normalized = clean_text(str(who_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_actor_records(who_text, raw_terms=raw_terms)


def extract_object_graph_records(what_text: str) -> list[dict]:
    raw_terms = extract_what_terms(what_text)
    normalized = clean_text(str(what_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_object_records(what_text, raw_terms=raw_terms)


def extract_time_graph_records(when_text: str) -> list[dict]:
    raw_terms = extract_when_terms(when_text)
    normalized = clean_text(str(when_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_time_records(when_text, raw_terms=raw_terms)


def extract_normalized_who_terms(who_text: str) -> list[str]:
    raw_terms = extract_who_terms(who_text)
    normalized = clean_text(str(who_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_actor_names(who_text, raw_terms=raw_terms)


def extract_normalized_what_terms(what_text: str) -> list[str]:
    raw_terms = extract_what_terms(what_text)
    normalized = clean_text(str(what_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_object_names(what_text, raw_terms=raw_terms)


def extract_normalized_when_terms(when_text: str) -> list[str]:
    raw_terms = extract_when_terms(when_text)
    normalized = clean_text(str(when_text or "")).strip()
    if len(raw_terms) > 1 and raw_terms and raw_terms[0] == normalized:
        raw_terms = raw_terms[1:]
    return extract_normalized_time_names(when_text, raw_terms=raw_terms)


def strip_category_prefix(text: str) -> str:
    return re.sub(r"^[一二三四五六七八九十]+、", "", str(text or "")).strip()


def tokenize_scene_name(scene_name: str) -> list[str]:
    scene_name = str(scene_name or "").strip()
    if not scene_name:
        return []

    tokens = [scene_name]
    parts = re.split(r"[（()）/、，,；;]+", scene_name)
    for part in parts:
        part = part.strip()
        if len(part) >= 3 and part not in GENERIC_TERMS:
            tokens.append(part)
    return dedupe_keep_order(tokens)


def resolve_taxonomy_doc(path: str | None = None) -> Path:
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        if not candidate.exists():
            raise FileNotFoundError(f"Taxonomy doc not found: {candidate}")
        return candidate

    matches = [item for item in RAW_DIR.glob("*.docx") if "业务分类体系" in item.name]
    if not matches:
        raise FileNotFoundError("Could not find `业务分类体系` docx under data/raw.")
    if len(matches) > 1:
        raise RuntimeError(f"Expected one taxonomy docx, found: {[item.name for item in matches]}")
    return matches[0]


def read_taxonomy_doc(path: Path) -> dict:
    doc = Document(str(path))
    paragraphs = []
    rows = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = clean_text(block.text)
            if text:
                paragraphs.append(text)
        elif isinstance(block, Table):
            for row in block.rows:
                cells = [clean_text(cell.text) for cell in row.cells]
                if any(cells):
                    rows.append(cells)
    return {"paragraphs": paragraphs, "rows": rows}


def parse_taxonomy(path: Path) -> tuple[dict, list[dict], list[dict]]:
    raw = read_taxonomy_doc(path)
    rows = raw["rows"]
    if not rows:
        raise RuntimeError(f"No taxonomy rows found in `{path}`.")

    current_section = None
    entries = []
    category_order = defaultdict(list)
    module_order = defaultdict(list)

    for row in rows[1:]:
        if len(row) < 4:
            continue
        category, module, projects, remark = row[:4]
        if not any([category, module, projects, remark]):
            continue
        if category.startswith("【") and category == module:
            current_section = category.strip("【】").strip()
            continue
        if not current_section:
            continue

        category = category.strip()
        module = module.strip()
        project_items = parse_multiline_items(projects)
        remark = remark.strip()

        if category not in category_order[current_section]:
            category_order[current_section].append(category)
        category_idx = category_order[current_section].index(category) + 1

        module_key = (current_section, category)
        if module not in module_order[module_key]:
            module_order[module_key].append(module)
        module_idx = module_order[module_key].index(module) + 1

        prefix = "BIZ" if current_section == "业务管理类" else "BASE"
        code = f"{prefix}-{category_idx:02d}-{module_idx:02d}"
        label_path = f"{current_section} > {category} > {module}"

        entries.append(
            {
                "section": current_section,
                "category": category,
                "module": module,
                "projects": project_items,
                "projects_text": "；".join(project_items),
                "remark": remark,
                "code": code,
                "label_path": label_path,
                "category_index": category_idx,
                "module_index": module_idx,
            }
        )

    entries.append(
        {
            "section": "待分类",
            "category": "待分类",
            "module": "待分类",
            "projects": [],
            "projects_text": "",
            "remark": "无法归入现有分类的原子知识统一打此标签。",
            "code": UNCLASSIFIED_CODE,
            "label_path": UNCLASSIFIED_PATH,
            "category_index": 0,
            "module_index": 0,
        }
    )

    scenes = []
    for entry in entries:
        term_pool = [entry["module"], strip_category_prefix(entry["category"])]
        term_pool.extend(entry["projects"])
        terms = []
        for phrase in term_pool:
            phrase = str(phrase or "").strip()
            if not phrase:
                continue
            terms.append(phrase)
            for token in re.split(r"[（()）/、，,；;\s]+", phrase):
                token = token.strip()
                if len(token) >= 3 and token not in GENERIC_TERMS:
                    terms.append(token)
        entry["terms"] = dedupe_keep_order(terms)
        for idx, scene_name in enumerate(entry["projects"], 1):
            scenes.append(
                {
                    "scene_key": f"{entry['code']}-SCENE-{idx:02d}",
                    "scene_name": scene_name,
                    "scene_terms": tokenize_scene_name(scene_name),
                    "module_code": entry["code"],
                    "module": entry["module"],
                    "category": entry["category"],
                    "section": entry["section"],
                    "label_path": entry["label_path"],
                }
            )

    metadata = {
        "source_file": path.name,
        "title": raw["paragraphs"][0] if raw["paragraphs"] else "",
        "usage_notes": raw["paragraphs"][1:],
        "entry_count": len(entries),
        "scene_count": len(scenes),
    }
    return metadata, entries, scenes


def save_taxonomy_outputs(metadata: dict, entries: list[dict], scenes: list[dict]) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(DEFAULT_TAXONOMY_XLSX) as writer:
        pd.DataFrame(entries).to_excel(writer, sheet_name="modules", index=False)
        pd.DataFrame(scenes).to_excel(writer, sheet_name="scenes", index=False)
        pd.DataFrame(
            [{"key": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value}
             for key, value in metadata.items()]
        ).to_excel(writer, sheet_name="metadata", index=False)
    DEFAULT_TAXONOMY_JSON.write_text(
        json.dumps({"metadata": metadata, "entries": entries, "scenes": scenes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def taxonomy_prompt_text(entries: list[dict]) -> str:
    grouped = defaultdict(list)
    for entry in entries:
        grouped[(entry["section"], entry["category"])].append(entry)

    lines = []
    for (section, category), modules in grouped.items():
        lines.append(f"{section} / {category}")
        for entry in modules:
            projects = entry["projects_text"] or "无"
            remark = entry["remark"] or "无"
            lines.append(
                f"- {entry['code']} | {entry['module']} | 业务项目: {projects} | 备注: {remark}"
            )
    return "\n".join(lines)


def read_atoms(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Atom file not found: {path}")
    return pd.read_excel(path).fillna("")


def collect_legal_docs_for_full_extract(taxonomy_doc: Path) -> list[Path]:
    docs = []
    for pattern in ("*.docx", "*.pdf"):
        for path in RAW_DIR.glob(pattern):
            if path.name.startswith("~$"):
                continue
            if path == taxonomy_doc:
                continue
            docs.append(path)
    return sorted(docs)


def run_full_extract(output_path: Path, taxonomy_doc: Path, model: str | None = None, max_chunks_per_doc: int = 0) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    entity_dict = build_entity_dictionary(str(resolve_entity_dict_path()))
    all_rows = []
    global_counter = 1
    docs = collect_legal_docs_for_full_extract(taxonomy_doc)

    for path in docs:
        print(f"Processing {path.name}")
        chunks = load_and_chunk_document(str(path))
        if max_chunks_per_doc and max_chunks_per_doc > 0:
            chunks = chunks[:max_chunks_per_doc]
        for index, chunk in enumerate(chunks, 1):
            print(f"  - Chunk {index}/{len(chunks)}")
            atoms = extract_3stage_with_retry(chunk, path.name, entity_dict, model=model)
            batch_rows, global_counter = flatten_atom_rows(atoms, global_counter)
            all_rows.extend(batch_rows)
            print(f"    -> atoms: {len(batch_rows)}")

    pd.DataFrame(all_rows).to_excel(output_path, index=False)
    print(f"Saved extracted atoms to {output_path}")
    return output_path


def build_classification_prompt(batch: list[dict], taxonomy_text: str) -> str:
    return f"""你是银行运营管理部知识图谱的分类专家。

任务：
根据给定的“原子知识”内容，从下列业务分类体系中，为每条原子知识选择 1-3 个最贴切的二级标签代码。
二级标签的含义是：业务板块 > 业务大类 > 业务模块。

强约束：
1. 只能从下方标签代码清单里选择，不能自造标签。
2. 只有在原子知识确实横跨多个业务模块时，才允许多标签。
3. 如果无法准确归类，返回 ["{UNCLASSIFIED_CODE}"]。
4. 优先根据具体业务动作、流程对象、操作场景来选模块，不要只看法规名称。
5. 对 legacy_business_categories 和 legacy_related_scenarios 只能作为辅助参考，不能机械照抄。

标签代码清单：
{taxonomy_text}

待分类原子知识：
{json.dumps(batch, ensure_ascii=False, indent=2)}

请严格输出 JSON，不要加 Markdown 代码块，格式如下：
{{
  "results": [
    {{
      "atom_id": "YZ-XXX",
      "label_codes": ["BIZ-01-01"],
      "reason": "一句话说明分类依据"
    }}
  ]
}}
"""


def extract_result_items(payload) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload, list):
        return payload
    return []


def resolve_codes(raw_item: dict, code_to_entry: dict[str, dict], path_to_code: dict[str, str]) -> list[str]:
    raw_codes = []
    for key in ("label_codes", "codes", "label_code"):
        value = raw_item.get(key)
        if isinstance(value, list):
            raw_codes.extend(value)
        elif value:
            raw_codes.append(value)

    if not raw_codes and isinstance(raw_item.get("labels"), list):
        for value in raw_item["labels"]:
            if isinstance(value, str):
                raw_codes.append(value)
            elif isinstance(value, dict):
                if value.get("code"):
                    raw_codes.append(value["code"])
                elif value.get("label_path"):
                    raw_codes.append(value["label_path"])

    resolved = []
    for value in raw_codes:
        if not value:
            continue
        code = normalize_code(value)
        if code in code_to_entry:
            resolved.append(code)
            continue
        path_key = normalize_path(value)
        if path_key in path_to_code:
            resolved.append(path_to_code[path_key])

    resolved = dedupe_keep_order(resolved)
    return resolved[:3] if resolved else [UNCLASSIFIED_CODE]


def classify_batch(
    batch_rows: list[dict],
    taxonomy_text: str,
    code_to_entry: dict[str, dict],
    model: str,
    max_retries: int = 3,
    api_config: dict | None = None,
) -> dict:
    atom_ids = [row["atom_id"] for row in batch_rows]
    path_to_code = {normalize_path(entry["label_path"]): entry["code"] for entry in code_to_entry.values()}

    for attempt in range(1, max_retries + 1):
        try:
            prompt = build_classification_prompt(batch_rows, taxonomy_text)
            response = call_qwen(prompt, model=model, timeout=600, api_config=api_config)
            payload = json.loads(clean_json_string(response))
            items = extract_result_items(payload)
            result_map = {}
            for item in items:
                atom_id = str(item.get("atom_id", "")).strip()
                if not atom_id:
                    continue
                result_map[atom_id] = {
                    "label_codes": resolve_codes(item, code_to_entry, path_to_code),
                    "reason": str(item.get("reason", "")).strip(),
                }
            for atom_id in atom_ids:
                result_map.setdefault(atom_id, {"label_codes": [UNCLASSIFIED_CODE], "reason": "LLM未返回有效分类，已回退待分类"})
            return result_map
        except Exception as exc:
            print(f"    [Classification Retry {attempt}/{max_retries}] {exc}")
            time.sleep(2)

    return {
        atom_id: {"label_codes": [UNCLASSIFIED_CODE], "reason": "LLM分类失败，已回退待分类"}
        for atom_id in atom_ids
    }


def build_batch_rows(df_slice: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in df_slice.iterrows():
        rows.append(
            {
                "atom_id": str(row.get("atom_id", "")).strip(),
                "source_document": str(row.get("source_document", "")).strip(),
                "rule_type": str(row.get("rule_type", "")).strip(),
                "article_reference": str(row.get("article_reference", "")).strip(),
                "who": str(row.get("who", "")).strip(),
                "what": str(row.get("what", "")).strip(),
                "how": str(row.get("how", "")).strip(),
                "where": str(row.get("where", "")).strip(),
                "content_original": str(row.get("content_original", "")).strip(),
                "legacy_business_categories": safe_literal_list(row.get("business_categories", "")),
                "legacy_related_scenarios": safe_literal_list(row.get("related_scenarios", "")),
            }
        )
    return rows


def load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_checkpoint(path: Path, checkpoint: dict) -> None:
    path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")


def heuristic_classify_row(row: pd.Series, entries: list[dict]) -> dict:
    legacy_categories = safe_literal_list(row.get("business_categories", ""))
    legacy_scenarios = safe_literal_list(row.get("related_scenarios", ""))
    text_parts = [
        row.get("source_document", ""),
        row.get("what", ""),
        row.get("how", ""),
        row.get("where", ""),
        row.get("content_original", ""),
        " ".join(legacy_categories),
        " ".join(legacy_scenarios),
    ]
    haystack = "".join(str(part or "") for part in text_parts)

    scored = []
    for entry in entries:
        if entry["code"] == UNCLASSIFIED_CODE:
            continue
        score = 0
        module = entry["module"]
        category_core = strip_category_prefix(entry["category"])

        if module and module in haystack:
            score += 24
        if category_core and category_core in haystack:
            score += 6

        for project in entry["projects"]:
            if project and project in haystack:
                score += 32

        for legacy in legacy_scenarios:
            if module and (module in legacy or legacy in module):
                score += 14
            if category_core and (category_core in legacy or legacy in category_core):
                score += 4
            for project in entry["projects"]:
                if project and (project in legacy or legacy in project):
                    score += 18

        for legacy in legacy_categories:
            if module and (module in legacy or legacy in module):
                score += 8
            if category_core and (category_core in legacy or legacy in category_core):
                score += 3

        for term in entry.get("terms", []):
            if term and term in haystack:
                score += 5 if len(term) >= 4 else 3

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 12:
        return {"label_codes": [UNCLASSIFIED_CODE], "reason": "Heuristic fallback: no strong taxonomy match"}

    winner_score = scored[0][0]
    chosen = [scored[0][1]["code"]]
    for score, entry in scored[1:3]:
        if score >= max(24, winner_score - 4):
            chosen.append(entry["code"])
    chosen = dedupe_keep_order(chosen)[:3] or [UNCLASSIFIED_CODE]
    return {"label_codes": chosen, "reason": f"Heuristic fallback: matched taxonomy terms, top score={winner_score}"}


def classify_atoms(
    df: pd.DataFrame,
    entries: list[dict],
    model: str | None = None,
    batch_size: int = 12,
    force: bool = False,
    heuristic_only: bool = False,
    api_config: dict | None = None,
) -> pd.DataFrame:
    code_to_entry = {entry["code"]: entry for entry in entries}
    taxonomy_text = taxonomy_prompt_text(entries)
    checkpoint = {} if force else load_checkpoint(DEFAULT_CHECKPOINT_JSON)
    result_map = checkpoint.get("results", {})
    model_name = model or get_reasoning_model()

    atom_ids = [str(atom_id).strip() for atom_id in df["atom_id"].tolist()]
    pending_ids = [atom_id for atom_id in atom_ids if atom_id and atom_id not in result_map]
    if pending_ids:
        print(f"Business classification pending atoms: {len(pending_ids)} / {len(atom_ids)}")

    if pending_ids and not heuristic_only:
        pending_df = df[df["atom_id"].astype(str).isin(pending_ids)].copy()
        batches = [
            pending_df.iloc[start:start + batch_size]
            for start in range(0, len(pending_df), batch_size)
        ]
        for idx, batch_df in enumerate(batches, 1):
            batch_rows = build_batch_rows(batch_df)
            print(f"  - Classifying batch {idx}/{len(batches)} ({len(batch_rows)} atoms)")
            batch_result = classify_batch(batch_rows, taxonomy_text, code_to_entry, model_name, api_config=api_config)
            result_map.update(batch_result)
            save_checkpoint(
                DEFAULT_CHECKPOINT_JSON,
                {
                    "model": model_name,
                    "batch_size": batch_size,
                    "results": result_map,
                },
            )

    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        current = result_map.get(atom_id)
        should_fallback = heuristic_only or not current or (
            current.get("label_codes") == [UNCLASSIFIED_CODE] and "LLM" in str(current.get("reason", ""))
        )
        if atom_id and should_fallback:
            result_map[atom_id] = heuristic_classify_row(row, entries)

    label_codes_col = []
    label_paths_col = []
    sections_col = []
    categories_col = []
    modules_col = []
    reason_col = []

    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        item = result_map.get(atom_id, {"label_codes": [UNCLASSIFIED_CODE], "reason": "无分类结果，已回退待分类"})
        codes = dedupe_keep_order([code for code in item.get("label_codes", []) if code in code_to_entry])
        if not codes:
            codes = [UNCLASSIFIED_CODE]
        labels = [code_to_entry[code] for code in codes]

        label_codes_col.append(json.dumps(codes, ensure_ascii=False))
        label_paths_col.append(json.dumps([label["label_path"] for label in labels], ensure_ascii=False))
        sections_col.append(json.dumps(dedupe_keep_order([label["section"] for label in labels]), ensure_ascii=False))
        categories_col.append(json.dumps(dedupe_keep_order([label["category"] for label in labels]), ensure_ascii=False))
        modules_col.append(json.dumps(dedupe_keep_order([label["module"] for label in labels]), ensure_ascii=False))
        reason_col.append(item.get("reason", ""))

    out = df.copy()
    out["business_taxonomy_label_codes"] = label_codes_col
    out["business_taxonomy_label_paths"] = label_paths_col
    out["business_sections_v2"] = sections_col
    out["business_categories_v2"] = categories_col
    out["business_modules_v2"] = modules_col
    out["business_classification_reason"] = reason_col
    return out


def taxonomy_dimension_batches(entries: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    boards = []
    categories = []
    modules = []
    seen_boards = set()
    seen_categories = set()

    for entry in entries:
        if entry["section"] not in seen_boards:
            boards.append({"name": entry["section"]})
            seen_boards.add(entry["section"])

        category_key = f"{entry['section']}::{entry['category']}"
        if category_key not in seen_categories:
            categories.append(
                {
                    "key": category_key,
                    "name": entry["category"],
                    "section": entry["section"],
                    "section_name": entry["section"],
                }
            )
            seen_categories.add(category_key)

        modules.append(
            {
                "code": entry["code"],
                "name": entry["module"],
                "section": entry["section"],
                "category": entry["category"],
                "category_key": category_key,
                "label_path": entry["label_path"],
                "projects_text": entry["projects_text"],
                "remark": entry["remark"],
            }
        )

    return boards, categories, modules


def build_scene_match_rows(df: pd.DataFrame, scenes: list[dict]) -> list[dict]:
    scenes_by_module = defaultdict(list)
    for scene in scenes:
        scene_copy = dict(scene)
        scene_copy["scene_terms"] = dedupe_keep_order(scene_copy.get("scene_terms", []) or tokenize_scene_name(scene_copy["scene_name"]))
        scenes_by_module[scene_copy["module_code"]].append(scene_copy)

    match_map = {}
    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        if not atom_id:
            continue

        codes = json.loads(str(row.get("business_taxonomy_label_codes", "[]")))
        legacy_scenarios = safe_literal_list(row.get("related_scenarios", ""))
        text_parts = [
            str(row.get("source_document", "")),
            str(row.get("what", "")),
            str(row.get("how", "")),
            str(row.get("where", "")),
            str(row.get("content_original", "")),
            " ".join(legacy_scenarios),
        ]
        haystack = "\n".join(part for part in text_parts if part)

        for code in codes:
            for scene in scenes_by_module.get(code, []):
                score = 0
                matched_terms = []
                scene_name = scene["scene_name"]

                if scene_name and scene_name in haystack:
                    score += 20
                    matched_terms.append(scene_name)

                for term in scene["scene_terms"]:
                    if term == scene_name:
                        continue
                    if term and term in haystack:
                        score += 8
                        matched_terms.append(term)

                for legacy in legacy_scenarios:
                    if scene_name and (scene_name in legacy or legacy in scene_name):
                        score += 12
                        matched_terms.append(legacy)

                if score < 8:
                    continue

                key = (atom_id, scene["scene_key"])
                existing = match_map.get(key)
                current_terms = dedupe_keep_order(matched_terms)
                if existing is None or score > existing["score"]:
                    match_map[key] = {
                        "atom_id": atom_id,
                        "scene_key": scene["scene_key"],
                        "module_code": scene["module_code"],
                        "score": score,
                        "matched_terms": current_terms,
                    }
                elif score == existing["score"]:
                    existing["matched_terms"] = dedupe_keep_order(existing["matched_terms"] + current_terms)

    return list(match_map.values())


def build_scene_actor_rows(df: pd.DataFrame, scene_match_rows: list[dict]) -> list[dict]:
    who_terms_by_atom = {}
    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        if atom_id:
            who_terms_by_atom[atom_id] = extract_normalized_who_terms(row.get("who", ""))

    counter = defaultdict(int)
    for row in scene_match_rows:
        atom_id = row["atom_id"]
        scene_key = row["scene_key"]
        for actor_name in who_terms_by_atom.get(atom_id, []):
            counter[(scene_key, actor_name)] += 1

    return [
        {
            "scene_key": scene_key,
            "actor_name": actor_name,
            "atom_count": atom_count,
        }
        for (scene_key, actor_name), atom_count in sorted(counter.items())
    ]


def build_scene_object_rows(df: pd.DataFrame, scene_match_rows: list[dict]) -> list[dict]:
    object_terms_by_atom = {}
    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        if atom_id:
            object_terms_by_atom[atom_id] = extract_normalized_what_terms(row.get("what", ""))

    counter = defaultdict(int)
    for row in scene_match_rows:
        atom_id = row["atom_id"]
        scene_key = row["scene_key"]
        for object_name in object_terms_by_atom.get(atom_id, []):
            counter[(scene_key, object_name)] += 1

    return [
        {
            "scene_key": scene_key,
            "object_name": object_name,
            "atom_count": atom_count,
        }
        for (scene_key, object_name), atom_count in sorted(counter.items())
    ]


def build_scene_time_rows(df: pd.DataFrame, scene_match_rows: list[dict]) -> list[dict]:
    time_terms_by_atom = {}
    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        if atom_id:
            time_terms_by_atom[atom_id] = extract_normalized_when_terms(row.get("when", ""))

    counter = defaultdict(int)
    for row in scene_match_rows:
        atom_id = row["atom_id"]
        scene_key = row["scene_key"]
        for time_name in time_terms_by_atom.get(atom_id, []):
            counter[(scene_key, time_name)] += 1

    return [
        {
            "scene_key": scene_key,
            "time_name": time_name,
            "atom_count": atom_count,
        }
        for (scene_key, time_name), atom_count in sorted(counter.items())
    ]


def load_business_graph(df: pd.DataFrame, entries: list[dict], scenes: list[dict], clear_first: bool, uri: str, user: str, password: str) -> dict:
    boards, categories, modules = taxonomy_dimension_batches(entries)
    scene_match_rows = build_scene_match_rows(df, scenes)
    scene_actor_rows = build_scene_actor_rows(df, scene_match_rows)
    scene_object_rows = build_scene_object_rows(df, scene_match_rows)
    scene_time_rows = build_scene_time_rows(df, scene_match_rows)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            if clear_first:
                session.run("MATCH (n) DETACH DELETE n")

            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (b:BusinessBoard) REQUIRE b.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:BusinessCategory) REQUIRE c.key IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:BusinessModule) REQUIRE m.code IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:BusinessScene) REQUIRE s.key IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (a:BusinessAtom) REQUIRE a.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (d:BusinessDocument) REQUIRE d.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (w:BusinessActor) REQUIRE w.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (o:BusinessObject) REQUIRE o.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (t:BusinessTimeContext) REQUIRE t.name IS UNIQUE")

            session.run("UNWIND $rows AS row MERGE (b:BusinessBoard {name: row.name})", rows=boards)
            session.run(
                """
                UNWIND $rows AS row
                MATCH (b:BusinessBoard {name: row.section_name})
                MERGE (c:BusinessCategory {key: row.key})
                SET c.name = row.name, c.section = row.section
                MERGE (b)-[:HAS_CATEGORY]->(c)
                """,
                rows=categories,
            )
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:BusinessCategory {key: row.category_key})
                MERGE (m:BusinessModule {code: row.code})
                SET m.name = row.name,
                    m.section = row.section,
                    m.category = row.category,
                    m.label_path = row.label_path,
                    m.projects_text = row.projects_text,
                    m.remark = row.remark
                MERGE (c)-[:HAS_MODULE]->(m)
                """,
                rows=modules,
            )
            if scenes:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (m:BusinessModule {code: row.module_code})
                    MERGE (s:BusinessScene {key: row.scene_key})
                    SET s.name = row.scene_name,
                        s.section = row.section,
                        s.category = row.category,
                        s.module = row.module,
                        s.label_path = row.label_path
                    MERGE (m)-[:HAS_SCENE]->(s)
                    """,
                    rows=scenes,
                )

            atom_rows = []
            tag_rows = []
            actor_rows = []
            actor_link_rows = []
            object_rows = []
            object_link_rows = []
            time_rows = []
            time_link_rows = []
            seen_actors = set()
            seen_actor_links = set()
            seen_objects = set()
            seen_object_links = set()
            seen_times = set()
            seen_time_links = set()
            for _, row in df.iterrows():
                atom_id = str(row.get("atom_id", "")).strip()
                if not atom_id:
                    continue
                who_terms = extract_who_terms(row.get("who", ""))
                actor_records = extract_actor_graph_records(row.get("who", ""))
                who_terms_normalized = [item["name"] for item in actor_records]
                what_terms = extract_what_terms(row.get("what", ""))
                object_records = extract_object_graph_records(row.get("what", ""))
                what_terms_normalized = [item["name"] for item in object_records]
                when_terms = extract_when_terms(row.get("when", ""))
                time_records = extract_time_graph_records(row.get("when", ""))
                when_terms_normalized = [item["name"] for item in time_records]
                atom_rows.append(
                    {
                        "atom_id": atom_id,
                        "source_document": str(row.get("source_document", "")).strip(),
                        "rule_type": str(row.get("rule_type", "")).strip(),
                        "article_reference": str(row.get("article_reference", "")).strip(),
                        "who": str(row.get("who", "")).strip(),
                        "who_terms": who_terms,
                        "who_terms_normalized": who_terms_normalized,
                        "when": str(row.get("when", "")).strip(),
                        "when_terms": when_terms,
                        "when_terms_normalized": when_terms_normalized,
                        "what": str(row.get("what", "")).strip(),
                        "what_terms": what_terms,
                        "what_terms_normalized": what_terms_normalized,
                        "how": str(row.get("how", "")).strip(),
                        "where": str(row.get("where", "")).strip(),
                        "content_original": str(row.get("content_original", "")).strip(),
                        "legacy_related_scenarios": json.dumps(safe_literal_list(row.get("related_scenarios", "")), ensure_ascii=False),
                        "legacy_business_categories": json.dumps(safe_literal_list(row.get("business_categories", "")), ensure_ascii=False),
                    }
                )
                for code in json.loads(str(row.get("business_taxonomy_label_codes", "[]"))):
                    tag_rows.append({"atom_id": atom_id, "module_code": code})
                for actor in actor_records:
                    actor_name = actor["name"]
                    if actor_name not in seen_actors:
                        actor_rows.append(
                            {
                                "name": actor_name,
                                "normalized_name": actor["normalized_name"],
                                "aliases": actor["aliases"],
                                "matched_aliases": actor["matched_aliases"],
                                "source_categories": actor["source_categories"],
                                "is_normalized": actor["is_normalized"],
                            }
                        )
                        seen_actors.add(actor_name)
                    actor_key = (atom_id, actor_name)
                    if actor_key not in seen_actor_links:
                        actor_link_rows.append({"atom_id": atom_id, "actor_name": actor_name})
                        seen_actor_links.add(actor_key)
                for obj in object_records:
                    object_name = obj["name"]
                    if object_name not in seen_objects:
                        object_rows.append(
                            {
                                "name": object_name,
                                "normalized_name": obj["normalized_name"],
                                "aliases": obj["aliases"],
                                "matched_aliases": obj["matched_aliases"],
                                "source_categories": obj["source_categories"],
                                "is_normalized": obj["is_normalized"],
                            }
                        )
                        seen_objects.add(object_name)
                    object_key = (atom_id, object_name)
                    if object_key not in seen_object_links:
                        object_link_rows.append({"atom_id": atom_id, "object_name": object_name})
                        seen_object_links.add(object_key)
                for time_record in time_records:
                    time_name = time_record["name"]
                    if time_name not in seen_times:
                        time_rows.append(
                            {
                                "name": time_name,
                                "normalized_name": time_record["normalized_name"],
                                "aliases": time_record["aliases"],
                                "matched_aliases": time_record["matched_aliases"],
                                "source_categories": time_record["source_categories"],
                                "is_normalized": time_record["is_normalized"],
                            }
                        )
                        seen_times.add(time_name)
                    time_key = (atom_id, time_name)
                    if time_key not in seen_time_links:
                        time_link_rows.append({"atom_id": atom_id, "time_name": time_name})
                        seen_time_links.add(time_key)

            session.run(
                """
                UNWIND $rows AS row
                MERGE (d:BusinessDocument {name: row.source_document})
                MERGE (a:BusinessAtom {id: row.atom_id})
                SET a.source_document = row.source_document,
                    a.rule_type = row.rule_type,
                    a.article_reference = row.article_reference,
                    a.who = row.who,
                    a.who_terms = row.who_terms,
                    a.who_terms_normalized = row.who_terms_normalized,
                    a.when = row.when,
                    a.when_terms = row.when_terms,
                    a.when_terms_normalized = row.when_terms_normalized,
                    a.what = row.what,
                    a.what_terms = row.what_terms,
                    a.what_terms_normalized = row.what_terms_normalized,
                    a.how = row.how,
                    a.where = row.where,
                    a.content_original = row.content_original,
                    a.legacy_related_scenarios = row.legacy_related_scenarios,
                    a.legacy_business_categories = row.legacy_business_categories
                MERGE (d)-[:HAS_ATOM]->(a)
                """,
                rows=atom_rows,
            )
            if actor_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (w:BusinessActor {name: row.name})
                    SET w.normalized_name = row.normalized_name,
                        w.aliases = row.aliases,
                        w.matched_aliases = row.matched_aliases,
                        w.source_categories = row.source_categories,
                        w.is_normalized = row.is_normalized
                    """,
                    rows=actor_rows,
                )
            if object_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (o:BusinessObject {name: row.name})
                    SET o.normalized_name = row.normalized_name,
                        o.aliases = row.aliases,
                        o.matched_aliases = row.matched_aliases,
                        o.source_categories = row.source_categories,
                        o.is_normalized = row.is_normalized
                    """,
                    rows=object_rows,
                )
            if time_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (t:BusinessTimeContext {name: row.name})
                    SET t.normalized_name = row.normalized_name,
                        t.aliases = row.aliases,
                        t.matched_aliases = row.matched_aliases,
                        t.source_categories = row.source_categories,
                        t.is_normalized = row.is_normalized
                    """,
                    rows=time_rows,
                )
            if actor_link_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:BusinessAtom {id: row.atom_id})
                    MATCH (w:BusinessActor {name: row.actor_name})
                    MERGE (a)-[:INVOLVES_ACTOR]->(w)
                    """,
                    rows=actor_link_rows,
                )
            if object_link_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:BusinessAtom {id: row.atom_id})
                    MATCH (o:BusinessObject {name: row.object_name})
                    MERGE (a)-[:TARGETS_OBJECT]->(o)
                    """,
                    rows=object_link_rows,
                )
            if time_link_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:BusinessAtom {id: row.atom_id})
                    MATCH (t:BusinessTimeContext {name: row.time_name})
                    MERGE (a)-[:HAS_TIME_CONTEXT]->(t)
                    """,
                    rows=time_link_rows,
                )
            if tag_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:BusinessAtom {id: row.atom_id})
                    MATCH (m:BusinessModule {code: row.module_code})
                    MERGE (a)-[:TAGGED_AS]->(m)
                    """,
                    rows=tag_rows,
                )
            if scene_match_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:BusinessAtom {id: row.atom_id})
                    MATCH (s:BusinessScene {key: row.scene_key})
                    MERGE (a)-[r:MATCHES_SCENE]->(s)
                    SET r.score = row.score,
                        r.matched_terms = row.matched_terms,
                        r.module_code = row.module_code
                    """,
                    rows=scene_match_rows,
                )
            if scene_actor_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (s:BusinessScene {key: row.scene_key})
                    MATCH (w:BusinessActor {name: row.actor_name})
                    MERGE (s)-[r:SCENE_HAS_ACTOR]->(w)
                    SET r.atom_count = row.atom_count
                    """,
                    rows=scene_actor_rows,
                )
            if scene_object_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (s:BusinessScene {key: row.scene_key})
                    MATCH (o:BusinessObject {name: row.object_name})
                    MERGE (s)-[r:SCENE_HAS_OBJECT]->(o)
                    SET r.atom_count = row.atom_count
                    """,
                    rows=scene_object_rows,
                )
            if scene_time_rows:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (s:BusinessScene {key: row.scene_key})
                    MATCH (t:BusinessTimeContext {name: row.time_name})
                    MERGE (s)-[r:SCENE_HAS_TIME]->(t)
                    SET r.atom_count = row.atom_count
                    """,
                    rows=scene_time_rows,
                )
    finally:
        driver.close()

    return {
        "boards": len(boards),
        "categories": len(categories),
        "modules": len(modules),
        "scenes": len(scenes),
        "atoms": len(atom_rows),
        "tags": len(tag_rows),
        "actors": len(actor_rows),
        "actor_links": len(actor_link_rows),
        "objects": len(object_rows),
        "object_links": len(object_link_rows),
        "time_contexts": len(time_rows),
        "time_links": len(time_link_rows),
        "scene_matches": len(scene_match_rows),
        "scene_actors": len(scene_actor_rows),
        "scene_objects": len(scene_object_rows),
        "scene_times": len(scene_time_rows),
    }


def save_graph_json(df: pd.DataFrame, entries: list[dict], scenes: list[dict]) -> None:
    nodes = []
    edges = []
    scene_match_rows = build_scene_match_rows(df, scenes)
    scene_actor_rows = build_scene_actor_rows(df, scene_match_rows)
    scene_object_rows = build_scene_object_rows(df, scene_match_rows)
    scene_time_rows = build_scene_time_rows(df, scene_match_rows)

    for entry in entries:
        board_id = f"BOARD::{entry['section']}"
        category_id = f"CATEGORY::{entry['section']}::{entry['category']}"
        module_id = f"MODULE::{entry['code']}"

        nodes.append({"id": board_id, "type": "board", "name": entry["section"]})
        nodes.append({"id": category_id, "type": "category", "name": entry["category"], "section": entry["section"]})
        nodes.append(
            {
                "id": module_id,
                "type": "module",
                "name": entry["module"],
                "code": entry["code"],
                "label_path": entry["label_path"],
                "projects_text": entry["projects_text"],
                "remark": entry["remark"],
            }
        )
        edges.append({"source": board_id, "target": category_id, "type": "HAS_CATEGORY"})
        edges.append({"source": category_id, "target": module_id, "type": "HAS_MODULE"})

    for scene in scenes:
        scene_id = f"SCENE::{scene['scene_key']}"
        module_id = f"MODULE::{scene['module_code']}"
        nodes.append(
            {
                "id": scene_id,
                "type": "scene",
                "name": scene["scene_name"],
                "module_code": scene["module_code"],
                "label_path": scene["label_path"],
            }
        )
        edges.append({"source": module_id, "target": scene_id, "type": "HAS_SCENE"})

    for _, row in df.iterrows():
        atom_id = str(row.get("atom_id", "")).strip()
        if not atom_id:
            continue
        who_terms = extract_who_terms(row.get("who", ""))
        actor_records = extract_actor_graph_records(row.get("who", ""))
        what_terms = extract_what_terms(row.get("what", ""))
        object_records = extract_object_graph_records(row.get("what", ""))
        when_terms = extract_when_terms(row.get("when", ""))
        time_records = extract_time_graph_records(row.get("when", ""))
        atom_node_id = f"ATOM::{atom_id}"
        nodes.append(
            {
                "id": atom_node_id,
                "type": "atom",
                "atom_id": atom_id,
                "source_document": str(row.get("source_document", "")),
                "who": str(row.get("who", "")),
                "who_terms": who_terms,
                "who_terms_normalized": [item["name"] for item in actor_records],
                "when": str(row.get("when", "")),
                "when_terms": when_terms,
                "when_terms_normalized": [item["name"] for item in time_records],
                "rule_type": str(row.get("rule_type", "")),
                "what": str(row.get("what", "")),
                "what_terms": what_terms,
                "what_terms_normalized": [item["name"] for item in object_records],
                "how": str(row.get("how", "")),
                "content_original": str(row.get("content_original", "")),
            }
        )
        for code in json.loads(str(row.get("business_taxonomy_label_codes", "[]"))):
            edges.append(
                {
                    "source": atom_node_id,
                    "target": f"MODULE::{code}",
                    "type": "TAGGED_AS",
                }
            )
        for actor in actor_records:
            actor_name = actor["name"]
            actor_node_id = f"ACTOR::{actor_name}"
            nodes.append(
                {
                    "id": actor_node_id,
                    "type": "actor",
                    "name": actor_name,
                    "normalized_name": actor["normalized_name"],
                    "aliases": actor["aliases"],
                    "matched_aliases": actor["matched_aliases"],
                    "source_categories": actor["source_categories"],
                    "is_normalized": actor["is_normalized"],
                }
            )
            edges.append(
                {
                    "source": atom_node_id,
                    "target": actor_node_id,
                    "type": "INVOLVES_ACTOR",
                }
            )
        for obj in object_records:
            object_name = obj["name"]
            object_node_id = f"OBJECT::{object_name}"
            nodes.append(
                {
                    "id": object_node_id,
                    "type": "object",
                    "name": object_name,
                    "normalized_name": obj["normalized_name"],
                    "aliases": obj["aliases"],
                    "matched_aliases": obj["matched_aliases"],
                    "source_categories": obj["source_categories"],
                    "is_normalized": obj["is_normalized"],
                }
            )
            edges.append(
                {
                    "source": atom_node_id,
                    "target": object_node_id,
                    "type": "TARGETS_OBJECT",
                }
            )
        for time_record in time_records:
            time_name = time_record["name"]
            time_node_id = f"TIME::{time_name}"
            nodes.append(
                {
                    "id": time_node_id,
                    "type": "time_context",
                    "name": time_name,
                    "normalized_name": time_record["normalized_name"],
                    "aliases": time_record["aliases"],
                    "matched_aliases": time_record["matched_aliases"],
                    "source_categories": time_record["source_categories"],
                    "is_normalized": time_record["is_normalized"],
                }
            )
            edges.append(
                {
                    "source": atom_node_id,
                    "target": time_node_id,
                    "type": "HAS_TIME_CONTEXT",
                }
            )

    for row in scene_match_rows:
        edges.append(
            {
                "source": f"ATOM::{row['atom_id']}",
                "target": f"SCENE::{row['scene_key']}",
                "type": "MATCHES_SCENE",
                "score": row["score"],
                "matched_terms": row["matched_terms"],
            }
        )

    for row in scene_actor_rows:
        edges.append(
            {
                "source": f"SCENE::{row['scene_key']}",
                "target": f"ACTOR::{row['actor_name']}",
                "type": "SCENE_HAS_ACTOR",
                "atom_count": row["atom_count"],
            }
        )
    for row in scene_object_rows:
        edges.append(
            {
                "source": f"SCENE::{row['scene_key']}",
                "target": f"OBJECT::{row['object_name']}",
                "type": "SCENE_HAS_OBJECT",
                "atom_count": row["atom_count"],
            }
        )
    for row in scene_time_rows:
        edges.append(
            {
                "source": f"SCENE::{row['scene_key']}",
                "target": f"TIME::{row['time_name']}",
                "type": "SCENE_HAS_TIME",
                "atom_count": row["atom_count"],
            }
        )

    dedup_nodes = {node["id"]: node for node in nodes}
    dedup_edges = {(edge["source"], edge["target"], edge["type"]): edge for edge in edges}
    DEFAULT_GRAPH_JSON.write_text(
        json.dumps({"nodes": list(dedup_nodes.values()), "edges": list(dedup_edges.values())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def text_hit(term: str, *values: str) -> bool:
    needle = str(term or "").strip()
    if not needle:
        return False
    haystack = " ".join(str(value or "") for value in values)
    return needle in haystack


def parse_query_spec(raw_query: str) -> dict:
    text = clean_text(str(raw_query or "")).strip()
    if not text:
        return {"raw_query": "", "query": "", "who_terms": []}

    query = text
    who_text = ""
    for separator in ("::", "@@"):
        if separator in text:
            query, who_text = text.split(separator, 1)
            break

    who_terms = extract_who_terms(who_text)
    if not who_terms and who_text.strip():
        who_terms = split_label_terms(who_text)

    return {
        "raw_query": text,
        "query": query.strip(),
        "who_terms": dedupe_keep_order([term for term in who_terms if term]),
    }


def match_row_who_terms(row: pd.Series, who_terms: list[str]) -> list[str]:
    if not who_terms:
        return []

    who_text = clean_text(str(row.get("who", ""))).strip()
    atom_terms = extract_who_terms(who_text)
    matched = []
    for term in who_terms:
        if term in who_text or term in atom_terms:
            matched.append(term)
    return dedupe_keep_order(matched)


def build_recall_report(df: pd.DataFrame, entries: list[dict], scenes: list[dict], queries: list[str]) -> dict:
    code_to_entry = {entry["code"]: entry for entry in entries}
    scene_match_rows = build_scene_match_rows(df, scenes)
    matched_atom_ids_by_scene = defaultdict(set)
    for row in scene_match_rows:
        matched_atom_ids_by_scene[row["scene_key"]].add(row["atom_id"])
    results = []

    for raw_query in queries:
        query_spec = parse_query_spec(raw_query)
        query = query_spec["query"]
        who_terms = query_spec["who_terms"]
        matched_scene_rows = [scene for scene in scenes if query in scene["scene_name"]]
        matched_scene_names = [scene["scene_name"] for scene in matched_scene_rows]
        matched_module_codes = dedupe_keep_order([scene["module_code"] for scene in matched_scene_rows])
        matched_scene_keys = [scene["scene_key"] for scene in matched_scene_rows]
        precise_atom_ids = set()
        for scene_key in matched_scene_keys:
            precise_atom_ids.update(matched_atom_ids_by_scene.get(scene_key, set()))

        hit_rows = []
        for _, row in df.iterrows():
            label_codes = json.loads(str(row.get("business_taxonomy_label_codes", "[]")))
            text_match = text_hit(
                query,
                row.get("content_original", ""),
                row.get("what", ""),
                row.get("how", ""),
                row.get("where", ""),
                row.get("business_modules_v2", ""),
                row.get("business_categories_v2", ""),
            )
            module_match = any(code in matched_module_codes for code in label_codes)
            if text_match or module_match:
                hit_rows.append(row)

        module_counter = defaultdict(int)
        category_counter = defaultdict(int)
        precise_who_counter = defaultdict(int)
        refined_who_counter = defaultdict(int)
        sample_atoms = []
        for row in hit_rows[:10]:
            for code in json.loads(str(row.get("business_taxonomy_label_codes", "[]"))):
                entry = code_to_entry.get(code)
                if not entry:
                    continue
                module_counter[entry["label_path"]] += 1
                category_counter[f"{entry['section']} > {entry['category']}"] += 1
            sample_atoms.append(
                {
                    "atom_id": str(row.get("atom_id", "")),
                    "source_document": str(row.get("source_document", "")),
                    "what": str(row.get("what", "")),
                    "how": str(row.get("how", ""))[:120],
                    "content_original": str(row.get("content_original", ""))[:160],
                }
            )

        precise_rows = []
        for _, row in df.iterrows():
            atom_id = str(row.get("atom_id", "")).strip()
            if atom_id in precise_atom_ids:
                precise_rows.append(row)
                for term in extract_who_terms(row.get("who", "")):
                    precise_who_counter[term] += 1

        who_refined_atom_ids = set()
        who_refined_samples = []
        if who_terms:
            for row in precise_rows:
                atom_id = str(row.get("atom_id", "")).strip()
                matched_who_terms = match_row_who_terms(row, who_terms)
                if not matched_who_terms:
                    continue
                who_refined_atom_ids.add(atom_id)
                for term in extract_who_terms(row.get("who", "")):
                    refined_who_counter[term] += 1
                if len(who_refined_samples) < 10:
                    who_refined_samples.append(
                        {
                            "atom_id": atom_id,
                            "who": str(row.get("who", "")),
                            "what": str(row.get("what", "")),
                            "how": str(row.get("how", ""))[:120],
                            "matched_who_terms": matched_who_terms,
                        }
                    )

        results.append(
            {
                "query": query,
                "raw_query": query_spec["raw_query"],
                "matched_scene_count": len(matched_scene_names),
                "matched_scenes": matched_scene_names[:20],
                "retrieved_atom_count": len(hit_rows),
                "broad_recall_count": len(hit_rows),
                "precise_recall_count": len(precise_atom_ids),
                "who_terms": who_terms,
                "who_refined_count": len(who_refined_atom_ids) if who_terms else None,
                "top_modules": sorted(module_counter.items(), key=lambda item: item[1], reverse=True)[:10],
                "top_categories": sorted(category_counter.items(), key=lambda item: item[1], reverse=True)[:10],
                "top_precise_who_terms": sorted(precise_who_counter.items(), key=lambda item: item[1], reverse=True)[:10],
                "top_refined_who_terms": sorted(refined_who_counter.items(), key=lambda item: item[1], reverse=True)[:10] if who_terms else [],
                "sample_atoms": sample_atoms,
                "who_refined_sample_atoms": who_refined_samples,
            }
        )

    return {"queries": queries, "results": results}


def default_queries() -> list[str]:
    return ["个人账户开立", "银行汇票", "大额交易", "可疑交易", "投诉受理", "征信查询"]


def print_taxonomy_for_user(metadata: dict, entries: list[dict]) -> None:
    print(metadata["title"])
    for line in metadata["usage_notes"]:
        print(line)

    current_section = None
    current_category = None
    for entry in entries:
        if entry["code"] == UNCLASSIFIED_CODE:
            continue
        if entry["section"] != current_section:
            current_section = entry["section"]
            current_category = None
            print(f"\n[{current_section}]")
        if entry["category"] != current_category:
            current_category = entry["category"]
            print(f"  {current_category}")
        print(f"    - {entry['module']}: {entry['projects_text']}")
        if entry["remark"]:
            print(f"      备注: {entry['remark']}")


def build_parser():
    parser = argparse.ArgumentParser(description="Build a business-taxonomy view of the compliance graph.")
    parser.add_argument("--taxonomy-doc", help="Optional path to the taxonomy docx.")
    parser.add_argument("--atoms-file", help="Optional existing atom xlsx path.")
    parser.add_argument("--force-extract", action="store_true", help="Re-run full atom extraction from raw docs.")
    parser.add_argument("--force-classify", action="store_true", help="Ignore checkpoint and re-run business classification.")
    parser.add_argument("--heuristic-only", action="store_true", help="Skip Qwen and classify using local fallback rules only.")
    parser.add_argument("--model", default=None, help="Optional Qwen model override for business classification.")
    parser.add_argument("--max-chunks-per-doc", type=int, default=0, help="Use 0 for all chunks when force extracting.")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--clear-neo4j", action="store_true")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=get_neo4j_password())
    parser.add_argument("--query", action="append", dest="queries", help="Repeatable retrieval-inspection query.")
    parser.add_argument("--print-taxonomy", action="store_true", help="Print the taxonomy doc content in a readable structure.")
    return add_mysql_sync_args(parser)


def main():
    args = build_parser().parse_args()
    taxonomy_doc = resolve_taxonomy_doc(args.taxonomy_doc)
    metadata, entries, scenes = parse_taxonomy(taxonomy_doc)
    save_taxonomy_outputs(metadata, entries, scenes)

    if args.print_taxonomy:
        print_taxonomy_for_user(metadata, entries)

    if args.force_extract:
        atoms_path = run_full_extract(
            DEFAULT_ATOMS_FILE,
            taxonomy_doc,
            model=args.model,
            max_chunks_per_doc=args.max_chunks_per_doc,
        )
    elif args.atoms_file:
        atoms_path = Path(args.atoms_file)
    else:
        atoms_path = DEFAULT_ATOMS_FILE

    df = read_atoms(atoms_path)
    classified_df = classify_atoms(
        df,
        entries,
        model=args.model,
        batch_size=args.batch_size,
        force=args.force_classify,
        heuristic_only=args.heuristic_only,
    )
    classified_df.to_excel(DEFAULT_CLASSIFIED_FILE, index=False)
    save_graph_json(classified_df, entries, scenes)

    graph_stats = None
    if not args.skip_neo4j:
        try:
            graph_stats = load_business_graph(
                classified_df,
                entries,
                scenes,
                clear_first=args.clear_neo4j,
                uri=args.neo4j_uri,
                user=args.neo4j_user,
                password=args.neo4j_password,
            )
        except ServiceUnavailable as exc:
            raise RuntimeError(
                f"Cannot connect to Neo4j at `{args.neo4j_uri}`. Re-run with `--skip-neo4j` if you only need classification files."
            ) from exc

    queries = args.queries or default_queries()
    recall_report = build_recall_report(classified_df, entries, scenes, queries)
    DEFAULT_RECALL_JSON.write_text(json.dumps(recall_report, ensure_ascii=False, indent=2), encoding="utf-8")

    mysql_items = [
        ("taxonomy_catalog", DEFAULT_TAXONOMY_XLSX),
        ("classified_atoms", DEFAULT_CLASSIFIED_FILE),
        ("taxonomy_recall_report", DEFAULT_RECALL_JSON),
    ]
    if args.force_extract:
        mysql_items.insert(0, ("legal_atoms", atoms_path))

    sync_results = maybe_sync_artifacts_from_args(
        args,
        items=mysql_items,
        default_batch_label=f"business-taxonomy-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        taxonomy_catalog_path=DEFAULT_TAXONOMY_XLSX,
        source_dir=PROCESSED_DIR,
        batch_extra_json={
            "pipeline_step": "business_taxonomy_pipeline",
            "force_extract": args.force_extract,
            "force_classify": args.force_classify,
            "heuristic_only": args.heuristic_only,
            "skip_neo4j": args.skip_neo4j,
        },
    )

    print(f"Taxonomy catalog: {DEFAULT_TAXONOMY_XLSX}")
    print(f"Classified atoms: {DEFAULT_CLASSIFIED_FILE}")
    print(f"Graph JSON: {DEFAULT_GRAPH_JSON}")
    print(f"Recall report: {DEFAULT_RECALL_JSON}")
    if graph_stats:
        print(
            "Neo4j graph loaded: "
            f"boards={graph_stats['boards']} "
            f"categories={graph_stats['categories']} "
            f"modules={graph_stats['modules']} "
            f"scenes={graph_stats['scenes']} "
            f"atoms={graph_stats['atoms']} "
            f"tags={graph_stats['tags']} "
            f"scene_matches={graph_stats['scene_matches']}"
        )
    for item in sync_results:
        print(f"MySQL sync [{item['status']}] {item['artifact_type']}: {item['path']}")


if __name__ == "__main__":
    main()
