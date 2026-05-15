from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from data_loader import clean_text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_PHASE1_FILE = DEFAULT_PROCESSED_DIR / "phase1_entities_checkpoint.xlsx"
DEFAULT_ATOMS_FILE = DEFAULT_PROCESSED_DIR / "legal_atoms_v4_final.xlsx"
DEFAULT_CLASSIFIED_FILE = DEFAULT_PROCESSED_DIR / "legal_atoms_business_taxonomy.xlsx"
DEFAULT_TAXONOMY_CATALOG_FILE = DEFAULT_PROCESSED_DIR / "business_taxonomy_catalog.xlsx"
DEFAULT_TAXONOMY_RECALL_FILE = DEFAULT_PROCESSED_DIR / "business_taxonomy_recall_report.json"


TRACE_BATCHES = "trace_batches"
TRACE_ARTIFACTS = "trace_artifacts"
PHASE1_CHUNKS = "phase1_chunks"
LEGAL_ATOMS = "legal_atoms"
TAXONOMY_MODULES = "taxonomy_modules"
TAXONOMY_SCENES = "taxonomy_scenes"
SCENE_MATCHES = "scene_matches"
TAXONOMY_RECALL_QUERIES = "taxonomy_recall_queries"
COMPLIANCE_RECALL_REPORTS = "compliance_recall_reports"
COMPLIANCE_RECALL_ROUNDS = "compliance_recall_rounds"
SAMPLE_REVIEW_ROWS = "sample_review_rows"


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def split_label_terms(text: Any) -> list[str]:
    value = clean_text(str(text or "")).strip()
    if not value:
        return []
    parts = re.split(r"[、，,；;|/]+|\s+|和|及|与|或", value)
    return [part.strip(":： ").strip() for part in parts if part.strip(":： ").strip()]


def safe_literal_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False)]
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
    parts = re.split(r"[，,;；/|]", text)
    return [part.strip() for part in parts if part.strip()]


def safe_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    text = str(value).strip()
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
        return parsed
    return safe_literal_list(value)


def extract_who_terms(who_text: Any) -> list[str]:
    normalized = clean_text(str(who_text or "")).strip()
    if not normalized or normalized.lower() == "nan" or normalized in {"未指定", "None", "null"}:
        return []
    fragments = split_label_terms(normalized)
    if normalized not in fragments:
        fragments.insert(0, normalized)
    return dedupe_keep_order(fragments)[:20]


def tokenize_scene_name(scene_name: Any) -> list[str]:
    text = clean_text(str(scene_name or "")).strip()
    if not text:
        return []
    tokens = [text]
    for token in re.split(r"[（()）/、，,；;\s]+", text):
        cleaned = token.strip()
        if len(cleaned) >= 2:
            tokens.append(cleaned)
    return dedupe_keep_order(tokens)


def normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (list, tuple, dict, set)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_row_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {key: normalize_scalar(value) for key, value in row.items()}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是"}


def parse_datetime(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts)


def compute_sha1(path: Path) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def discover_named_file(processed_dir: Path, filename: str) -> Path | None:
    candidate = processed_dir / filename
    return candidate if candidate.exists() else None


def load_excel_rows(path: Path, sheet_name: str | None = None) -> list[dict[str, Any]]:
    excel_sheet = 0 if sheet_name is None else sheet_name
    df = pd.read_excel(path, sheet_name=excel_sheet).fillna("")
    if isinstance(df, dict):
        raise ValueError("Expected a single worksheet DataFrame.")
    return [normalize_row_dict(row) for row in df.to_dict(orient="records")]


def load_taxonomy_catalog(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    modules = load_excel_rows(path, sheet_name="modules")
    scenes = load_excel_rows(path, sheet_name="scenes")
    metadata_rows = load_excel_rows(path, sheet_name="metadata")
    for scene in scenes:
        scene["scene_terms"] = safe_json_list(scene.get("scene_terms")) or tokenize_scene_name(scene.get("scene_name"))
    return modules, scenes, metadata_rows


def build_scene_match_rows(
    classified_rows: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scenes_by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scene in scenes:
        scene_copy = dict(scene)
        scene_copy["scene_terms"] = dedupe_keep_order(
            [str(term).strip() for term in safe_json_list(scene_copy.get("scene_terms")) if str(term).strip()]
            or tokenize_scene_name(scene_copy.get("scene_name"))
        )
        scenes_by_module[str(scene_copy.get("module_code", "")).strip()].append(scene_copy)

    match_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in classified_rows:
        atom_id = str(row.get("atom_id", "")).strip()
        if not atom_id:
            continue
        codes = [str(code).strip() for code in safe_json_list(row.get("business_taxonomy_label_codes")) if str(code).strip()]
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
                matched_terms: list[str] = []
                scene_name = str(scene.get("scene_name", "")).strip()
                if scene_name and scene_name in haystack:
                    score += 20
                    matched_terms.append(scene_name)
                for term in scene.get("scene_terms", []):
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
                key = (atom_id, str(scene.get("scene_key", "")).strip())
                current_terms = dedupe_keep_order(matched_terms)
                existing = match_map.get(key)
                if existing is None or score > existing["score"]:
                    match_map[key] = {
                        "atom_id": atom_id,
                        "scene_key": str(scene.get("scene_key", "")).strip(),
                        "scene_name": scene_name,
                        "module_code": str(scene.get("module_code", "")).strip(),
                        "score": score,
                        "matched_terms": current_terms,
                    }
                elif score == existing["score"]:
                    existing["matched_terms"] = dedupe_keep_order(existing["matched_terms"] + current_terms)
    return list(match_map.values())


def auto_discover_files(processed_dir: Path) -> dict[str, list[Path] | Path | None]:
    sample_review_files = sorted(processed_dir.glob("sample_review_checklist_*.xlsx"))
    compliance_reports = sorted(processed_dir.glob("compliance_recall*.json"))
    return {
        "phase1_file": discover_named_file(processed_dir, DEFAULT_PHASE1_FILE.name),
        "atoms_file": discover_named_file(processed_dir, DEFAULT_ATOMS_FILE.name),
        "classified_file": discover_named_file(processed_dir, DEFAULT_CLASSIFIED_FILE.name),
        "taxonomy_catalog_file": discover_named_file(processed_dir, DEFAULT_TAXONOMY_CATALOG_FILE.name),
        "taxonomy_recall_file": discover_named_file(processed_dir, DEFAULT_TAXONOMY_RECALL_FILE.name),
        "sample_review_files": sample_review_files[-1:] if sample_review_files else [],
        "compliance_reports": compliance_reports,
    }


class MySQLTraceabilityStore:
    def __init__(self, mysql_url: str) -> None:
        self.engine = create_engine(mysql_url, future=True)
        self.metadata = MetaData()
        self._define_tables()

    def _define_tables(self) -> None:
        self.trace_batches = Table(
            TRACE_BATCHES,
            self.metadata,
            Column("batch_id", String(64), primary_key=True),
            Column("batch_label", String(255), nullable=False, index=True),
            Column("source_dir", Text),
            Column("notes", Text),
            Column("extra_json", JSON),
            Column("created_at", DateTime, nullable=False),
        )
        self.trace_artifacts = Table(
            TRACE_ARTIFACTS,
            self.metadata,
            Column("artifact_id", String(64), primary_key=True),
            Column("batch_id", String(64), ForeignKey(f"{TRACE_BATCHES}.batch_id"), nullable=False, index=True),
            Column("artifact_type", String(64), nullable=False, index=True),
            Column("artifact_name", String(255), nullable=False),
            Column("artifact_path", Text, nullable=False),
            Column("file_mtime", DateTime),
            Column("file_size_bytes", BigInteger),
            Column("file_sha1", String(40), nullable=False, index=True),
            Column("row_count", Integer),
            Column("extra_json", JSON),
            Column("created_at", DateTime, nullable=False),
        )
        self.phase1_chunks = Table(
            PHASE1_CHUNKS,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("source_document", String(255), index=True),
            Column("chunk_index", Integer),
            Column("entity_count", Integer),
            Column("content_original", Text),
            Column("ner_entities_json", JSON),
            Column("row_json", JSON),
        )
        self.legal_atoms = Table(
            LEGAL_ATOMS,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("atom_id", String(64), index=True),
            Column("source_document", String(255), index=True),
            Column("article_reference", String(255)),
            Column("rule_type", String(64), index=True),
            Column("relation_type", String(64)),
            Column("parent_atom_id", String(64)),
            Column("who_text", Text),
            Column("when_text", Text),
            Column("where_text", Text),
            Column("what_text", Text),
            Column("how_text", Text),
            Column("content_original", Text),
            Column("is_ambiguous", Boolean),
            Column("review_reason", String(64)),
            Column("business_taxonomy_label_codes", JSON),
            Column("business_taxonomy_label_paths", JSON),
            Column("business_sections_v2", JSON),
            Column("business_categories_v2", JSON),
            Column("business_modules_v2", JSON),
            Column("related_scenarios", JSON),
            Column("business_classification_reason", Text),
            Column("row_json", JSON),
        )
        self.taxonomy_modules = Table(
            TAXONOMY_MODULES,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("code", String(64), index=True),
            Column("section", String(255)),
            Column("category", String(255)),
            Column("module", String(255)),
            Column("label_path", Text),
            Column("projects_text", Text),
            Column("remark", Text),
            Column("row_json", JSON),
        )
        self.taxonomy_scenes = Table(
            TAXONOMY_SCENES,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("scene_key", String(128), index=True),
            Column("scene_name", String(255), index=True),
            Column("module_code", String(64), index=True),
            Column("module", String(255)),
            Column("category", String(255)),
            Column("section", String(255)),
            Column("label_path", Text),
            Column("scene_terms", JSON),
            Column("row_json", JSON),
        )
        self.scene_matches = Table(
            SCENE_MATCHES,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("atom_id", String(64), index=True),
            Column("scene_key", String(128), index=True),
            Column("scene_name", String(255)),
            Column("module_code", String(64)),
            Column("score", Integer),
            Column("matched_terms", JSON),
        )
        self.taxonomy_recall_queries = Table(
            TAXONOMY_RECALL_QUERIES,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("query_text", String(255), index=True),
            Column("raw_query", Text),
            Column("matched_scene_count", Integer),
            Column("retrieved_atom_count", Integer),
            Column("broad_recall_count", Integer),
            Column("precise_recall_count", Integer),
            Column("who_refined_count", Integer),
            Column("result_json", JSON),
        )
        self.compliance_recall_reports = Table(
            COMPLIANCE_RECALL_REPORTS,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("question", Text),
            Column("raw_query", Text),
            Column("final_decision", String(64), index=True),
            Column("judge_final_decision", String(64)),
            Column("stop_reason", String(64)),
            Column("can_make_final_compliance_judgement", Boolean),
            Column("final_recall_atom_count", Integer),
            Column("report_json", JSON),
        )
        self.compliance_recall_rounds = Table(
            COMPLIANCE_RECALL_ROUNDS,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("round_index", Integer),
            Column("input_atom_count", Integer),
            Column("output_atom_count", Integer),
            Column("new_atom_count", Integer),
            Column("round_json", JSON),
        )
        self.sample_review_rows = Table(
            SAMPLE_REVIEW_ROWS,
            self.metadata,
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("artifact_id", String(64), ForeignKey(f"{TRACE_ARTIFACTS}.artifact_id"), nullable=False, index=True),
            Column("sheet_name", String(64)),
            Column("sample_group", String(128)),
            Column("sample_label", String(128)),
            Column("sample_id", String(255), index=True),
            Column("judgement", String(64), index=True),
            Column("notes", Text),
            Column("row_json", JSON),
        )

    def ensure_schema(self) -> None:
        self.metadata.create_all(self.engine, checkfirst=True)

    def _lookup_existing_artifact_id(self, path: Path, sha1: str) -> str | None:
        stmt = select(self.trace_artifacts.c.artifact_id).where(
            self.trace_artifacts.c.artifact_path == str(path.resolve()),
            self.trace_artifacts.c.file_sha1 == sha1,
        )
        with self.engine.connect() as conn:
            return conn.execute(stmt).scalar_one_or_none()

    def _create_batch(
        self,
        batch_label: str,
        source_dir: Path | None = None,
        notes: str = "",
        extra_json: dict[str, Any] | None = None,
    ) -> str:
        batch_id = uuid.uuid4().hex
        payload = {
            "batch_id": batch_id,
            "batch_label": batch_label,
            "source_dir": str(source_dir.resolve()) if source_dir else None,
            "notes": notes or None,
            "extra_json": extra_json or {},
            "created_at": datetime.utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(self.trace_batches.insert(), [payload])
        return batch_id

    def create_batch(
        self,
        batch_label: str,
        source_dir: Path | None = None,
        notes: str = "",
        extra_json: dict[str, Any] | None = None,
    ) -> str:
        return self._create_batch(
            batch_label=batch_label,
            source_dir=source_dir,
            notes=notes,
            extra_json=extra_json,
        )

    def _register_artifact(
        self,
        batch_id: str,
        artifact_type: str,
        path: Path,
        row_count: int,
        extra_json: dict[str, Any] | None = None,
    ) -> str:
        stat = path.stat()
        artifact_id = uuid.uuid4().hex
        payload = {
            "artifact_id": artifact_id,
            "batch_id": batch_id,
            "artifact_type": artifact_type,
            "artifact_name": path.name,
            "artifact_path": str(path.resolve()),
            "file_mtime": parse_datetime(stat.st_mtime),
            "file_size_bytes": int(stat.st_size),
            "file_sha1": compute_sha1(path),
            "row_count": row_count,
            "extra_json": extra_json or {},
            "created_at": datetime.utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(self.trace_artifacts.insert(), [payload])
        return artifact_id

    def _prepare_batch_and_artifact(
        self,
        artifact_type: str,
        path: Path,
        row_count: int,
        batch_label: str | None = None,
        notes: str = "",
        source_dir: Path | None = None,
        extra_json: dict[str, Any] | None = None,
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        sha1 = compute_sha1(path)
        existing_artifact_id = self._lookup_existing_artifact_id(path, sha1)
        if skip_existing and existing_artifact_id:
            return None, existing_artifact_id
        if batch_id is None:
            if not batch_label:
                raise ValueError("batch_label is required when batch_id is not provided.")
            batch_id = self._create_batch(batch_label=batch_label, source_dir=source_dir, notes=notes)
        artifact_id = self._register_artifact(
            batch_id=batch_id,
            artifact_type=artifact_type,
            path=path,
            row_count=row_count,
            extra_json=extra_json,
        )
        return artifact_id, None

    def _build_legal_atom_payload_rows(
        self,
        artifact_id: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payload_rows = []
        for row in rows:
            payload_rows.append(
                {
                    "artifact_id": artifact_id,
                    "atom_id": str(row.get("atom_id", "")).strip() or None,
                    "source_document": str(row.get("source_document", "")).strip() or None,
                    "article_reference": str(row.get("article_reference", "")).strip() or None,
                    "rule_type": str(row.get("rule_type", "")).strip() or None,
                    "relation_type": str(row.get("relation_type", "")).strip() or None,
                    "parent_atom_id": str(row.get("parent_atom_id", "")).strip() or None,
                    "who_text": row.get("who"),
                    "when_text": row.get("when"),
                    "where_text": row.get("where"),
                    "what_text": row.get("what"),
                    "how_text": row.get("how"),
                    "content_original": row.get("content_original"),
                    "is_ambiguous": parse_bool(row.get("is_ambiguous")),
                    "review_reason": str(row.get("review_reason", "")).strip() or None,
                    "business_taxonomy_label_codes": safe_json_list(row.get("business_taxonomy_label_codes")),
                    "business_taxonomy_label_paths": safe_json_list(row.get("business_taxonomy_label_paths")),
                    "business_sections_v2": safe_json_list(row.get("business_sections_v2")),
                    "business_categories_v2": safe_json_list(row.get("business_categories_v2")),
                    "business_modules_v2": safe_json_list(row.get("business_modules_v2")),
                    "related_scenarios": safe_literal_list(row.get("related_scenarios")),
                    "business_classification_reason": row.get("business_classification_reason"),
                    "row_json": row,
                }
            )
        return payload_rows

    def import_phase1_file(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        rows = load_excel_rows(path)
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="phase1_entities",
            path=path,
            row_count=len(rows),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "phase1_entities",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        payload_rows = []
        for row in rows:
            entities = safe_json_list(row.get("ner_entities_json"))
            payload_rows.append(
                {
                    "artifact_id": artifact_id,
                    "source_document": str(row.get("source_document", "")).strip() or None,
                    "chunk_index": int(row.get("chunk_index") or 0) or None,
                    "entity_count": len(entities),
                    "content_original": row.get("content_original"),
                    "ner_entities_json": entities,
                    "row_json": row,
                }
            )
        if payload_rows:
            with self.engine.begin() as conn:
                conn.execute(self.phase1_chunks.insert(), payload_rows)
        return {
            "status": "imported",
            "artifact_type": "phase1_entities",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": len(payload_rows),
            "table_counts": {
                PHASE1_CHUNKS: len(payload_rows),
            },
        }

    def import_atoms_file(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        artifact_type: str = "legal_atoms",
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        rows = load_excel_rows(path)
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type=artifact_type,
            path=path,
            row_count=len(rows),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": artifact_type,
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        payload_rows = self._build_legal_atom_payload_rows(artifact_id=artifact_id, rows=rows)
        if payload_rows:
            with self.engine.begin() as conn:
                conn.execute(self.legal_atoms.insert(), payload_rows)
        return {
            "status": "imported",
            "artifact_type": artifact_type,
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": len(payload_rows),
            "table_counts": {
                LEGAL_ATOMS: len(payload_rows),
            },
        }

    def import_taxonomy_catalog(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        modules, scenes, metadata_rows = load_taxonomy_catalog(path)
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="taxonomy_catalog",
            path=path,
            row_count=len(modules) + len(scenes),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            extra_json={"metadata_rows": metadata_rows},
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "taxonomy_catalog",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        module_rows = []
        for row in modules:
            module_rows.append(
                {
                    "artifact_id": artifact_id,
                    "code": str(row.get("code", "")).strip() or None,
                    "section": row.get("section"),
                    "category": row.get("category"),
                    "module": row.get("module"),
                    "label_path": row.get("label_path"),
                    "projects_text": row.get("projects_text"),
                    "remark": row.get("remark"),
                    "row_json": row,
                }
            )
        scene_rows = []
        for row in scenes:
            scene_rows.append(
                {
                    "artifact_id": artifact_id,
                    "scene_key": str(row.get("scene_key", "")).strip() or None,
                    "scene_name": row.get("scene_name"),
                    "module_code": row.get("module_code"),
                    "module": row.get("module"),
                    "category": row.get("category"),
                    "section": row.get("section"),
                    "label_path": row.get("label_path"),
                    "scene_terms": row.get("scene_terms"),
                    "row_json": row,
                }
            )
        with self.engine.begin() as conn:
            if module_rows:
                conn.execute(self.taxonomy_modules.insert(), module_rows)
            if scene_rows:
                conn.execute(self.taxonomy_scenes.insert(), scene_rows)
        return {
            "status": "imported",
            "artifact_type": "taxonomy_catalog",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": len(module_rows) + len(scene_rows),
            "table_counts": {
                TAXONOMY_MODULES: len(module_rows),
                TAXONOMY_SCENES: len(scene_rows),
            },
        }

    def import_classified_file(
        self,
        path: Path,
        batch_label: str | None = None,
        taxonomy_catalog_path: Path | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        rows = load_excel_rows(path)
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="classified_atoms",
            path=path,
            row_count=len(rows),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            extra_json={"taxonomy_catalog_path": str(taxonomy_catalog_path.resolve()) if taxonomy_catalog_path and taxonomy_catalog_path.exists() else None},
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "classified_atoms",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }

        atom_rows = self._build_legal_atom_payload_rows(artifact_id=artifact_id, rows=rows)

        scene_row_count = 0
        scene_rows = []
        if taxonomy_catalog_path and taxonomy_catalog_path.exists():
            _modules, scenes, _metadata_rows = load_taxonomy_catalog(taxonomy_catalog_path)
            scene_matches = build_scene_match_rows(rows, scenes)
            scene_rows = [
                {
                    "artifact_id": artifact_id,
                    "atom_id": row["atom_id"],
                    "scene_key": row["scene_key"],
                    "scene_name": row["scene_name"],
                    "module_code": row["module_code"],
                    "score": int(row["score"]),
                    "matched_terms": row["matched_terms"],
                }
                for row in scene_matches
            ]
            scene_row_count = len(scene_rows)
        with self.engine.begin() as conn:
            if atom_rows:
                conn.execute(self.legal_atoms.insert(), atom_rows)
            if scene_rows:
                conn.execute(self.scene_matches.insert(), scene_rows)
        return {
            "status": "imported",
            "artifact_type": "classified_atoms",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": len(atom_rows),
            "scene_match_count": scene_row_count,
            "table_counts": {
                LEGAL_ATOMS: len(atom_rows),
                SCENE_MATCHES: scene_row_count,
            },
        }

    def import_taxonomy_recall_report(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        results = payload.get("results", []) if isinstance(payload, dict) else []
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="taxonomy_recall_report",
            path=path,
            row_count=len(results),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            extra_json={"queries": payload.get("queries", []) if isinstance(payload, dict) else []},
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "taxonomy_recall_report",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        rows = []
        for item in results:
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "query_text": str(item.get("query", "")).strip() or None,
                    "raw_query": item.get("raw_query"),
                    "matched_scene_count": item.get("matched_scene_count"),
                    "retrieved_atom_count": item.get("retrieved_atom_count"),
                    "broad_recall_count": item.get("broad_recall_count"),
                    "precise_recall_count": item.get("precise_recall_count"),
                    "who_refined_count": item.get("who_refined_count"),
                    "result_json": item,
                }
            )
        if rows:
            with self.engine.begin() as conn:
                conn.execute(self.taxonomy_recall_queries.insert(), rows)
        return {
            "status": "imported",
            "artifact_type": "taxonomy_recall_report",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": len(rows),
            "table_counts": {
                TAXONOMY_RECALL_QUERIES: len(rows),
            },
        }

    def import_compliance_recall_report(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rounds = payload.get("rounds", []) if isinstance(payload, dict) else []
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="compliance_recall_report",
            path=path,
            row_count=len(rounds),
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            extra_json={"final_decision": payload.get("final_decision") if isinstance(payload, dict) else None},
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "compliance_recall_report",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        report_row = {
            "artifact_id": artifact_id,
            "question": payload.get("question"),
            "raw_query": payload.get("raw_query"),
            "final_decision": payload.get("final_decision"),
            "judge_final_decision": payload.get("judge_final_decision"),
            "stop_reason": payload.get("stop_reason"),
            "can_make_final_compliance_judgement": parse_bool(payload.get("can_make_final_compliance_judgement")),
            "final_recall_atom_count": payload.get("final_recall_atom_count"),
            "report_json": payload,
        }
        round_rows = []
        for item in rounds:
            round_rows.append(
                {
                    "artifact_id": artifact_id,
                    "round_index": int(item.get("round") or 0) or None,
                    "input_atom_count": item.get("input_atom_count"),
                    "output_atom_count": item.get("output_atom_count"),
                    "new_atom_count": item.get("new_atom_count"),
                    "round_json": item,
                }
            )
        with self.engine.begin() as conn:
            conn.execute(self.compliance_recall_reports.insert(), [report_row])
            if round_rows:
                conn.execute(self.compliance_recall_rounds.insert(), round_rows)
        return {
            "status": "imported",
            "artifact_type": "compliance_recall_report",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": 1,
            "round_count": len(round_rows),
            "table_counts": {
                COMPLIANCE_RECALL_REPORTS: 1,
                COMPLIANCE_RECALL_ROUNDS: len(round_rows),
            },
        }

    def import_sample_review_file(
        self,
        path: Path,
        batch_label: str | None = None,
        notes: str = "",
        skip_existing: bool = True,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        xls = pd.ExcelFile(path)
        row_count = 0
        payload_rows = []
        for sheet_name in xls.sheet_names:
            if sheet_name in {"summary", "notes"}:
                continue
            df = pd.read_excel(path, sheet_name=sheet_name).fillna("")
            for row in df.to_dict(orient="records"):
                normalized = normalize_row_dict(row)
                row_count += 1
                payload_rows.append(
                    {
                        "sheet_name": sheet_name,
                        "sample_group": str(normalized.get("样本组", "")).strip() or None,
                        "sample_label": str(normalized.get("样本标签", "")).strip() or None,
                        "sample_id": str(normalized.get("样本ID", "")).strip() or None,
                        "judgement": str(normalized.get("判定", "")).strip() or None,
                        "notes": normalized.get("备注"),
                        "row_json": normalized,
                    }
                )
        artifact_id, skipped_artifact_id = self._prepare_batch_and_artifact(
            artifact_type="sample_review_checklist",
            path=path,
            row_count=row_count,
            batch_label=batch_label,
            notes=notes,
            source_dir=path.parent,
            skip_existing=skip_existing,
            batch_id=batch_id,
        )
        if skipped_artifact_id:
            return {
                "status": "skipped",
                "artifact_type": "sample_review_checklist",
                "path": str(path),
                "artifact_id": skipped_artifact_id,
                "table_counts": {},
            }
        if payload_rows:
            for row in payload_rows:
                row["artifact_id"] = artifact_id
            with self.engine.begin() as conn:
                conn.execute(self.sample_review_rows.insert(), payload_rows)
        return {
            "status": "imported",
            "artifact_type": "sample_review_checklist",
            "path": str(path),
            "artifact_id": artifact_id,
            "row_count": row_count,
            "table_counts": {
                SAMPLE_REVIEW_ROWS: row_count,
            },
        }


def build_mysql_url_from_parts(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> str:
    quoted_password = quote_plus(password)
    return f"mysql+pymysql://{user}:{quoted_password}@{host}:{port}/{database}?charset=utf8mb4"


def resolve_mysql_url(args: argparse.Namespace) -> str:
    if args.mysql_url:
        return args.mysql_url
    env_url = os.getenv("MYSQL_URL")
    if env_url:
        return env_url
    host = args.mysql_host or os.getenv("MYSQL_HOST") or "127.0.0.1"
    port = int(args.mysql_port or os.getenv("MYSQL_PORT") or 3306)
    user = args.mysql_user or os.getenv("MYSQL_USER") or "root"
    password = args.mysql_password or os.getenv("MYSQL_PASSWORD") or ""
    database = args.mysql_database or os.getenv("MYSQL_DATABASE") or ""
    if not database:
        raise ValueError("MySQL database name is required. Pass --mysql-database or set MYSQL_DATABASE.")
    return build_mysql_url_from_parts(host=host, port=port, user=user, password=password, database=database)


def build_discovered_artifact_items(
    processed_dir: Path,
) -> tuple[list[tuple[str, Path]], Path | None]:
    discovered = auto_discover_files(processed_dir)
    taxonomy_catalog_path = discovered["taxonomy_catalog_file"]
    ordered_items: list[tuple[str, Path]] = []

    for artifact_type, key in [
        ("phase1_entities", "phase1_file"),
        ("legal_atoms", "atoms_file"),
        ("taxonomy_catalog", "taxonomy_catalog_file"),
        ("classified_atoms", "classified_file"),
        ("taxonomy_recall_report", "taxonomy_recall_file"),
    ]:
        item_path = discovered.get(key)
        if isinstance(item_path, Path) and item_path.exists():
            ordered_items.append((artifact_type, item_path))

    for report_path in discovered.get("compliance_reports", []):
        ordered_items.append(("compliance_recall_report", report_path))
    for review_path in discovered.get("sample_review_files", []):
        ordered_items.append(("sample_review_checklist", review_path))

    return ordered_items, taxonomy_catalog_path if isinstance(taxonomy_catalog_path, Path) else None


def dedupe_artifact_items(items: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, Path]] = []
    for artifact_type, path in items:
        key = (artifact_type, str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        result.append((artifact_type, path))
    return result


def sync_artifact_items(
    store: MySQLTraceabilityStore,
    items: list[tuple[str, Path]],
    batch_label: str,
    notes: str = "",
    taxonomy_catalog_path: Path | None = None,
    skip_existing: bool = True,
    source_dir: Path | None = None,
    batch_extra_json: dict[str, Any] | None = None,
    continue_on_error: bool = False,
) -> list[dict[str, Any]]:
    if not items:
        return []

    batch_id = store.create_batch(
        batch_label=batch_label,
        source_dir=source_dir,
        notes=notes,
        extra_json=batch_extra_json,
    )
    results: list[dict[str, Any]] = []
    for artifact_type, path in items:
        try:
            if artifact_type == "phase1_entities":
                result = store.import_phase1_file(path=path, batch_id=batch_id, notes=notes, skip_existing=skip_existing)
            elif artifact_type == "legal_atoms":
                result = store.import_atoms_file(
                    path=path,
                    batch_id=batch_id,
                    notes=notes,
                    skip_existing=skip_existing,
                    artifact_type="legal_atoms",
                )
            elif artifact_type == "taxonomy_catalog":
                result = store.import_taxonomy_catalog(path=path, batch_id=batch_id, notes=notes, skip_existing=skip_existing)
            elif artifact_type == "classified_atoms":
                result = store.import_classified_file(
                    path=path,
                    batch_id=batch_id,
                    taxonomy_catalog_path=taxonomy_catalog_path,
                    notes=notes,
                    skip_existing=skip_existing,
                )
            elif artifact_type == "taxonomy_recall_report":
                result = store.import_taxonomy_recall_report(path=path, batch_id=batch_id, notes=notes, skip_existing=skip_existing)
            elif artifact_type == "compliance_recall_report":
                result = store.import_compliance_recall_report(path=path, batch_id=batch_id, notes=notes, skip_existing=skip_existing)
            elif artifact_type == "sample_review_checklist":
                result = store.import_sample_review_file(path=path, batch_id=batch_id, notes=notes, skip_existing=skip_existing)
            else:
                raise ValueError(f"Unsupported artifact_type: {artifact_type}")
        except Exception as exc:
            if not continue_on_error:
                raise
            result = {
                "status": "failed",
                "artifact_type": artifact_type,
                "path": str(path),
                "artifact_id": None,
                "error": str(exc),
                "table_counts": {},
            }
        result["batch_id"] = batch_id
        results.append(result)
    return results


def add_mysql_sync_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--mysql-sync", action="store_true", help="Sync the generated artifact(s) into MySQL after the pipeline step finishes.")
    parser.add_argument("--mysql-url", default=None, help="Full SQLAlchemy MySQL URL, e.g. mysql+pymysql://user:pass@host:3306/db")
    parser.add_argument("--mysql-host", default=None)
    parser.add_argument("--mysql-port", type=int, default=None)
    parser.add_argument("--mysql-user", default=None)
    parser.add_argument("--mysql-password", default=None)
    parser.add_argument("--mysql-database", default=None)
    parser.add_argument("--mysql-batch-label", default="", help="Optional trace batch label. Defaults to a step-based label.")
    parser.add_argument("--mysql-notes", default="", help="Optional trace batch notes.")
    parser.add_argument("--mysql-force-reimport", dest="mysql_skip_existing", action="store_false", default=True, help="Re-import even if the same file path + checksum already exists.")
    return parser


def maybe_sync_artifacts_from_args(
    args: argparse.Namespace,
    items: list[tuple[str, Path]],
    default_batch_label: str,
    taxonomy_catalog_path: Path | None = None,
    source_dir: Path | None = None,
    batch_extra_json: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not getattr(args, "mysql_sync", False):
        return []

    mysql_url = resolve_mysql_url(args)
    store = MySQLTraceabilityStore(mysql_url)
    store.ensure_schema()
    return sync_artifact_items(
        store=store,
        items=dedupe_artifact_items(items),
        batch_label=(getattr(args, "mysql_batch_label", "") or "").strip() or default_batch_label,
        notes=getattr(args, "mysql_notes", "") or "",
        taxonomy_catalog_path=taxonomy_catalog_path,
        skip_existing=getattr(args, "mysql_skip_existing", True),
        source_dir=source_dir,
        batch_extra_json=batch_extra_json,
    )


def sync_single_artifact(
    artifact_type: str,
    path: Path,
    mysql_url: str,
    batch_label: str,
    notes: str = "",
    taxonomy_catalog_path: Path | None = None,
    skip_existing: bool = True,
) -> dict[str, Any]:
    store = MySQLTraceabilityStore(mysql_url)
    store.ensure_schema()
    results = sync_artifact_items(
        store=store,
        items=[(artifact_type, path)],
        batch_label=batch_label,
        notes=notes,
        taxonomy_catalog_path=taxonomy_catalog_path,
        skip_existing=skip_existing,
        source_dir=path.parent,
    )
    return results[0]


def sync_discovered_bundle(
    mysql_url: str,
    processed_dir: Path,
    batch_label: str,
    notes: str = "",
    skip_existing: bool = True,
) -> list[dict[str, Any]]:
    store = MySQLTraceabilityStore(mysql_url)
    store.ensure_schema()
    ordered_items, taxonomy_catalog_path = build_discovered_artifact_items(processed_dir)
    return sync_artifact_items(
        store=store,
        items=ordered_items,
        batch_label=batch_label,
        notes=notes,
        taxonomy_catalog_path=taxonomy_catalog_path,
        skip_existing=skip_existing,
        source_dir=processed_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync extraction/classification/recall artifacts into MySQL for traceability.")
    parser.add_argument("--mysql-url", default=None, help="Full SQLAlchemy MySQL URL, e.g. mysql+pymysql://user:pass@host:3306/db")
    parser.add_argument("--mysql-host", default=None)
    parser.add_argument("--mysql-port", type=int, default=None)
    parser.add_argument("--mysql-user", default=None)
    parser.add_argument("--mysql-password", default=None)
    parser.add_argument("--mysql-database", default=None)
    parser.add_argument("--processed-dir", default=str(DEFAULT_PROCESSED_DIR))
    parser.add_argument("--batch-label", default=f"backfill-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--notes", default="")
    parser.add_argument("--phase1-file", default="")
    parser.add_argument("--atoms-file", default="")
    parser.add_argument("--classified-file", default="")
    parser.add_argument("--taxonomy-catalog-file", default="")
    parser.add_argument("--taxonomy-recall-file", default="")
    parser.add_argument("--compliance-report", action="append", dest="compliance_reports", default=[])
    parser.add_argument("--sample-review-file", action="append", dest="sample_review_files", default=[])
    parser.add_argument("--auto-discover", action="store_true", help="Auto import the current default processed outputs.")
    parser.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True, help="Skip files already imported with the same path + checksum. Enabled by default.")
    parser.add_argument("--force-reimport", dest="skip_existing", action="store_false", help="Re-import even if the same file path + checksum already exists.")
    parser.add_argument("--init-only", action="store_true", help="Create tables only, do not import any artifact.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    mysql_url = resolve_mysql_url(args)
    store = MySQLTraceabilityStore(mysql_url)
    store.ensure_schema()

    if args.init_only:
        print("MySQL traceability schema initialized.")
        return

    processed_dir = Path(args.processed_dir)
    artifact_items: list[tuple[str, Path]] = []
    taxonomy_catalog_path = Path(args.taxonomy_catalog_file) if args.taxonomy_catalog_file else None

    if args.auto_discover:
        discovered_items, discovered_taxonomy_catalog = build_discovered_artifact_items(processed_dir)
        artifact_items.extend(discovered_items)
        if taxonomy_catalog_path is None:
            taxonomy_catalog_path = discovered_taxonomy_catalog

    if args.phase1_file:
        artifact_items.append(("phase1_entities", Path(args.phase1_file)))
    if args.atoms_file:
        artifact_items.append(("legal_atoms", Path(args.atoms_file)))
    if args.taxonomy_catalog_file:
        artifact_items.append(("taxonomy_catalog", Path(args.taxonomy_catalog_file)))
    if args.classified_file:
        artifact_items.append(("classified_atoms", Path(args.classified_file)))
    if args.taxonomy_recall_file:
        artifact_items.append(("taxonomy_recall_report", Path(args.taxonomy_recall_file)))
    for path_text in args.compliance_reports:
        artifact_items.append(("compliance_recall_report", Path(path_text)))
    for path_text in args.sample_review_files:
        artifact_items.append(("sample_review_checklist", Path(path_text)))

    artifact_items = dedupe_artifact_items(artifact_items)
    results = sync_artifact_items(
        store=store,
        items=artifact_items,
        batch_label=args.batch_label,
        notes=args.notes,
        taxonomy_catalog_path=taxonomy_catalog_path,
        skip_existing=args.skip_existing,
        source_dir=processed_dir,
    )
    if not results:
        print("No artifact imported. Pass --auto-discover or explicit file arguments.")
        return

    for item in results:
        status = item.get("status", "unknown")
        artifact_type = item.get("artifact_type", "unknown")
        path = item.get("path", "")
        row_count = item.get("row_count")
        suffix = f" rows={row_count}" if row_count is not None else ""
        print(f"[{status}] {artifact_type} {path}{suffix}")


if __name__ == "__main__":
    main()
