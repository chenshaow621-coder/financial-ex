import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd

from data_loader import load_and_chunk_docx
from dictionary_builder import build_entity_dictionary
from prompt import build_stage1_prompt, build_stage2_prompt
from qwen_client import call_qwen, get_default_model

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def clean_json_string(raw_str: str) -> str:
    if not raw_str:
        return ""
    cleaned = re.sub(r"```json\s*", "", raw_str)
    cleaned = re.sub(r"```\s*", "", cleaned)
    return cleaned.strip()


def resolve_entity_dict_path() -> Path:
    data_dir = PROJECT_ROOT / "data"
    candidates = list(data_dir.glob("*entity_table_unified_v3.xlsx"))
    if not candidates:
        raise FileNotFoundError("Could not find entity dictionary xlsx under data/.")
    return candidates[0]


def collect_target_docs(doc_keywords):
    docs = []
    for path in RAW_DIR.glob("*.docx"):
        if path.name.startswith("~$"):
            continue
        if any(keyword in path.name for keyword in doc_keywords):
            docs.append(path)
    return sorted(docs)


def extract_entities_only(text_chunk, entity_dict, model=None, max_retries=3):
    model = model or get_default_model()
    for attempt in range(max_retries):
        try:
            stage1_prompt = build_stage1_prompt(text_chunk)
            stage1_response = call_qwen(stage1_prompt, model=model, timeout=300)
            print(f"    [Stage 1] {stage1_response.strip()}")
            if not stage1_response or stage1_response.strip() in {"?", "[]"}:
                return "[]"
            identified_categories = [cat.strip() for cat in stage1_response.split(",") if cat.strip()]
            all_entities = []
            for category in identified_categories:
                reference_words = entity_dict.get(category, [])
                stage2_prompt = build_stage2_prompt(text_chunk, category, reference_words)
                stage2_response = call_qwen(stage2_prompt, model=model, timeout=300)
                json_str = clean_json_string(stage2_response)
                if json_str and json_str != "[]":
                    try:
                        all_entities.extend(json.loads(json_str))
                    except json.JSONDecodeError:
                        print(f"      [Warn] Stage 2 JSON decode failed for category={category}")
            return json.dumps(all_entities, ensure_ascii=False)
        except Exception as exc:
            print(f"    [Retry {attempt + 1}/{max_retries}] {exc}")
            time.sleep(2)
    return "[]"


def run_phase1(doc_keywords=None, output_name="phase1_entities_checkpoint.xlsx", model=None, max_chunks_per_doc=0):
    doc_keywords = doc_keywords or ["票据法", "支付结算办法"]
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    entity_dict = build_entity_dictionary(str(resolve_entity_dict_path()))
    target_docs = collect_target_docs(doc_keywords)
    rows = []
    for path in target_docs:
        print(f"Processing {path.name}")
        chunks = load_and_chunk_docx(str(path))
        if max_chunks_per_doc and max_chunks_per_doc > 0:
            chunks = chunks[:max_chunks_per_doc]
        for index, chunk in enumerate(chunks, 1):
            print(f"  - Chunk {index}/{len(chunks)}")
            rows.append({
                "source_document": path.stem.replace("(1)", ""),
                "chunk_index": index,
                "content_original": chunk,
                "ner_entities_json": extract_entities_only(chunk, entity_dict, model=model),
            })
    df = pd.DataFrame(rows)
    output_path = PROCESSED_DIR / output_name
    df.to_excel(output_path, index=False)
    print(f"Saved phase1 checkpoint to {output_path}")
    return output_path


def build_parser():
    parser = argparse.ArgumentParser(description="Run Phase 1/2 NER with DashScope Qwen.")
    parser.add_argument("--doc-keyword", action="append", dest="doc_keywords", help="Repeatable doc keyword filter.")
    parser.add_argument("--output", default="phase1_entities_checkpoint.xlsx")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-chunks-per-doc", type=int, default=0)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    run_phase1(
        doc_keywords=args.doc_keywords,
        output_name=args.output,
        model=args.model,
        max_chunks_per_doc=args.max_chunks_per_doc,
    )
