import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd

from data_loader import load_and_chunk_docx
from dictionary_builder import build_entity_dictionary
from prompt import build_stage1_prompt, build_stage2_prompt, build_stage3_ee_prompt
from qwen_client import call_qwen, get_default_model
from schema import EventAssemblyResult, generate_atom_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def clean_json_string(raw_str: str) -> str:
    if not raw_str:
        return ""
    cleaned = re.sub(r"```json\s*", "", raw_str)
    cleaned = re.sub(r"```\s*", "", cleaned)
    return cleaned.strip()


def extract_3stage_with_retry(text_chunk, source_filename, entity_dict, model=None, max_retries=3):
    model = model or get_default_model()
    for attempt in range(max_retries):
        try:
            stage1_prompt = build_stage1_prompt(text_chunk)
            stage1_response = call_qwen(stage1_prompt, model=model, timeout=600)
            print(f"    [Stage 1] {stage1_response.strip()}")
            if not stage1_response or stage1_response.strip() in {"?", "[]"}:
                return []

            identified_categories = [cat.strip() for cat in stage1_response.split(",") if cat.strip()]
            all_extracted_entities = []
            for category in identified_categories:
                reference_words = entity_dict.get(category, [])
                stage2_prompt = build_stage2_prompt(text_chunk, category, reference_words)
                stage2_response = call_qwen(stage2_prompt, model=model, timeout=600)
                json_str = clean_json_string(stage2_response)
                if json_str and json_str != "[]":
                    try:
                        all_extracted_entities.extend(json.loads(json_str))
                    except json.JSONDecodeError:
                        print(f"      [Warn] Stage 2 JSON decode failed for category={category}")

            if not all_extracted_entities:
                return []

            ner_entities_json = json.dumps(all_extracted_entities, ensure_ascii=False)
            stage3_prompt = build_stage3_ee_prompt(text_chunk, ner_entities_json)
            stage3_response = call_qwen(stage3_prompt, model=model, timeout=600)
            json_str = clean_json_string(stage3_response)
            final_data = json.loads(json_str)
            result = EventAssemblyResult.model_validate(final_data)

            clean_source = source_filename.replace(".docx", "").replace("(1)", "")
            atoms = []
            for atom in result.atoms:
                atom_dict = atom.model_dump()
                atom_dict["source_document"] = clean_source
                atoms.append(atom_dict)
            return atoms
        except Exception as exc:
            print(f"    [Retry {attempt + 1}/{max_retries}] {exc}")
            time.sleep(2)
    return []


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


def flatten_atom_rows(atom_rows, counter_start=1):
    rows = []
    counter = counter_start
    for row in atom_rows:
        item = dict(row)
        behavior = item.pop("behavior_struct", None)
        if isinstance(behavior, dict):
            item["who"] = behavior.get("who")
            item["when"] = behavior.get("when")
            item["where"] = behavior.get("where")
            item["what"] = behavior.get("what")
            item["how"] = behavior.get("how")
        source = item.get("source_document", "UNKNOWN_SOURCE")
        rule_type = item.get("rule_type", "UNKNOWN")
        item["atom_id"] = generate_atom_id(source, rule_type, counter)
        rows.append(item)
        counter += 1
    return rows, counter


def run_pipeline(doc_keywords=None, output_name="legal_atoms_qwen_sample.xlsx", model=None, max_chunks_per_doc=0):
    doc_keywords = doc_keywords or ["票据法", "支付结算办法"]
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    entity_dict = build_entity_dictionary(str(resolve_entity_dict_path()))
    target_docs = collect_target_docs(doc_keywords)
    if not target_docs:
        raise FileNotFoundError(f"No docx files matched keywords: {doc_keywords}")

    all_rows = []
    global_counter = 1
    for path in target_docs:
        print(f"Processing {path.name}")
        chunks = load_and_chunk_docx(str(path))
        if max_chunks_per_doc and max_chunks_per_doc > 0:
            chunks = chunks[:max_chunks_per_doc]
        for index, chunk in enumerate(chunks, 1):
            print(f"  - Chunk {index}/{len(chunks)}")
            atoms = extract_3stage_with_retry(chunk, path.name, entity_dict, model=model)
            batch_rows, global_counter = flatten_atom_rows(atoms, global_counter)
            all_rows.extend(batch_rows)
            print(f"    -> atoms: {len(batch_rows)}")

    df = pd.DataFrame(all_rows)
    preferred = [
        "atom_id", "source_document", "rule_type", "relation_type", "parent_atom_id",
        "who", "how", "when", "what", "where", "content_original",
        "is_ambiguous", "review_reason",
    ]
    ordered = [col for col in preferred if col in df.columns] + [col for col in df.columns if col not in preferred]
    df = df[ordered] if not df.empty else df
    output_path = PROCESSED_DIR / output_name
    df.to_excel(output_path, index=False)
    print(f"Saved atoms to {output_path}")
    return output_path


def build_parser():
    parser = argparse.ArgumentParser(description="Run 3-stage legal atom extraction with DashScope Qwen.")
    parser.add_argument("--doc-keyword", action="append", dest="doc_keywords", help="Repeatable doc keyword filter.")
    parser.add_argument("--output", default="legal_atoms_qwen_sample.xlsx")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-chunks-per-doc", type=int, default=0)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    run_pipeline(
        doc_keywords=args.doc_keywords,
        output_name=args.output,
        model=args.model,
        max_chunks_per_doc=args.max_chunks_per_doc,
    )
