import argparse
import json
from pathlib import Path

import pandas as pd

from business_taxonomy_pipeline import (
    DEFAULT_CLASSIFIED_FILE,
    PROCESSED_DIR,
    extract_actor_graph_records,
    extract_object_graph_records,
    extract_time_graph_records,
    build_scene_match_rows,
    build_scene_actor_rows,
    build_scene_object_rows,
    build_scene_time_rows,
    extract_what_terms,
    extract_when_terms,
    extract_who_terms,
    parse_taxonomy,
    read_atoms,
    resolve_taxonomy_doc,
    safe_literal_list,
)


DEFAULT_OUTPUT = PROCESSED_DIR / "business_taxonomy_graph.cypher"


def cypher_value(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(cypher_value(item) for item in value) + "]"
    text = str(value)
    text = text.replace("\\'", "'").replace('\\"', '"')
    text = text.replace("\\", "\\\\").replace("'", "''")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return f"'{text}'"


def props(mapping: dict) -> str:
    return ", ".join(f"{key}: {cypher_value(value)}" for key, value in mapping.items())


def export_cypher(atoms_file: Path, output_file: Path, clear_first: bool = False) -> Path:
    taxonomy_doc = resolve_taxonomy_doc()
    _, entries, scenes = parse_taxonomy(taxonomy_doc)
    df = read_atoms(atoms_file).fillna("")
    scene_match_rows = build_scene_match_rows(df, scenes)
    scene_actor_rows = build_scene_actor_rows(df, scene_match_rows)
    scene_object_rows = build_scene_object_rows(df, scene_match_rows)
    scene_time_rows = build_scene_time_rows(df, scene_match_rows)

    lines = []
    lines.append("// Business taxonomy graph export")
    lines.append("// Generated from processed taxonomy + classified atoms")
    if clear_first:
        lines.append("MATCH (n) DETACH DELETE n;")

    lines.extend(
        [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (b:BusinessBoard) REQUIRE b.name IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:BusinessCategory) REQUIRE c.key IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:BusinessModule) REQUIRE m.code IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:BusinessScene) REQUIRE s.key IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:BusinessAtom) REQUIRE a.id IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:BusinessDocument) REQUIRE d.name IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (w:BusinessActor) REQUIRE w.name IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (o:BusinessObject) REQUIRE o.name IS UNIQUE;",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:BusinessTimeContext) REQUIRE t.name IS UNIQUE;",
            "",
        ]
    )

    seen_boards = set()
    seen_categories = set()
    for entry in entries:
        if entry["section"] not in seen_boards:
            lines.append(
                f"MERGE (:BusinessBoard {{{props({'name': entry['section']})}}});"
            )
            seen_boards.add(entry["section"])

        category_key = f"{entry['section']}::{entry['category']}"
        if category_key not in seen_categories:
            lines.append(
                "MERGE (c:BusinessCategory {"
                + props({"key": category_key})
                + "}) "
                + "SET c += {"
                + props({"name": entry["category"], "section": entry["section"]})
                + "};"
            )
            lines.append(
                "MATCH (b:BusinessBoard {"
                + props({"name": entry["section"]})
                + "}), (c:BusinessCategory {"
                + props({"key": category_key})
                + "}) "
                + "MERGE (b)-[:HAS_CATEGORY]->(c);"
            )
            seen_categories.add(category_key)

        lines.append(
            "MERGE (m:BusinessModule {"
            + props({"code": entry["code"]})
            + "}) SET m += {"
            + props(
                {
                    "name": entry["module"],
                    "section": entry["section"],
                    "category": entry["category"],
                    "label_path": entry["label_path"],
                    "projects_text": entry["projects_text"],
                    "remark": entry["remark"],
                }
            )
            + "};"
        )
        lines.append(
            "MATCH (c:BusinessCategory {"
            + props({"key": category_key})
            + "}), (m:BusinessModule {"
            + props({"code": entry["code"]})
            + "}) MERGE (c)-[:HAS_MODULE]->(m);"
        )

    for scene in scenes:
        lines.append(
            "MERGE (s:BusinessScene {"
            + props({"key": scene["scene_key"]})
            + "}) SET s += {"
            + props(
                {
                    "name": scene["scene_name"],
                    "section": scene["section"],
                    "category": scene["category"],
                    "module": scene["module"],
                    "label_path": scene["label_path"],
                }
            )
            + "};"
        )
        lines.append(
            "MATCH (m:BusinessModule {"
            + props({"code": scene["module_code"]})
            + "}), (s:BusinessScene {"
            + props({"key": scene["scene_key"]})
            + "}) MERGE (m)-[:HAS_SCENE]->(s);"
        )

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
        source_document = str(row.get("source_document", "")).strip()
        who_terms = extract_who_terms(row.get("who", ""))
        actor_records = extract_actor_graph_records(row.get("who", ""))
        what_terms = extract_what_terms(row.get("what", ""))
        object_records = extract_object_graph_records(row.get("what", ""))
        when_terms = extract_when_terms(row.get("when", ""))
        time_records = extract_time_graph_records(row.get("when", ""))
        lines.append(
            "MERGE (d:BusinessDocument {"
            + props({"name": source_document})
            + "});"
        )
        lines.append(
            "MERGE (a:BusinessAtom {"
            + props({"id": atom_id})
            + "}) SET a += {"
            + props(
                {
                    "source_document": source_document,
                    "rule_type": str(row.get("rule_type", "")),
                    "article_reference": str(row.get("article_reference", "")),
                    "who": str(row.get("who", "")),
                    "who_terms": who_terms,
                    "who_terms_normalized": [item["name"] for item in actor_records],
                    "when": str(row.get("when", "")),
                    "when_terms": when_terms,
                    "when_terms_normalized": [item["name"] for item in time_records],
                    "what": str(row.get("what", "")),
                    "what_terms": what_terms,
                    "what_terms_normalized": [item["name"] for item in object_records],
                    "how": str(row.get("how", "")),
                    "where": str(row.get("where", "")),
                    "content_original": str(row.get("content_original", "")),
                    "legacy_related_scenarios": safe_literal_list(row.get("related_scenarios", "")),
                    "legacy_business_categories": safe_literal_list(row.get("business_categories", "")),
                }
            )
            + "};"
        )
        lines.append(
            "MATCH (d:BusinessDocument {"
            + props({"name": source_document})
            + "}), (a:BusinessAtom {"
            + props({"id": atom_id})
            + "}) MERGE (d)-[:HAS_ATOM]->(a);"
        )
        for code in json.loads(str(row.get("business_taxonomy_label_codes", "[]"))):
            lines.append(
                "MATCH (a:BusinessAtom {"
                + props({"id": atom_id})
                + "}), (m:BusinessModule {"
                + props({"code": code})
                + "}) MERGE (a)-[:TAGGED_AS]->(m);"
            )
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

    for row in actor_rows:
        lines.append(
            "MERGE (w:BusinessActor {"
            + props({"name": row["name"]})
            + "}) SET w += {"
            + props(
                {
                    "normalized_name": row["normalized_name"],
                    "aliases": row["aliases"],
                    "matched_aliases": row["matched_aliases"],
                    "source_categories": row["source_categories"],
                    "is_normalized": row["is_normalized"],
                }
            )
            + "};"
        )
    for row in object_rows:
        lines.append(
            "MERGE (o:BusinessObject {"
            + props({"name": row["name"]})
            + "}) SET o += {"
            + props(
                {
                    "normalized_name": row["normalized_name"],
                    "aliases": row["aliases"],
                    "matched_aliases": row["matched_aliases"],
                    "source_categories": row["source_categories"],
                    "is_normalized": row["is_normalized"],
                }
            )
            + "};"
        )
    for row in time_rows:
        lines.append(
            "MERGE (t:BusinessTimeContext {"
            + props({"name": row["name"]})
            + "}) SET t += {"
            + props(
                {
                    "normalized_name": row["normalized_name"],
                    "aliases": row["aliases"],
                    "matched_aliases": row["matched_aliases"],
                    "source_categories": row["source_categories"],
                    "is_normalized": row["is_normalized"],
                }
            )
            + "};"
        )

    for row in actor_link_rows:
        lines.append(
            "MATCH (a:BusinessAtom {"
            + props({"id": row["atom_id"]})
            + "}), (w:BusinessActor {"
            + props({"name": row["actor_name"]})
            + "}) MERGE (a)-[:INVOLVES_ACTOR]->(w);"
        )
    for row in object_link_rows:
        lines.append(
            "MATCH (a:BusinessAtom {"
            + props({"id": row["atom_id"]})
            + "}), (o:BusinessObject {"
            + props({"name": row["object_name"]})
            + "}) MERGE (a)-[:TARGETS_OBJECT]->(o);"
        )
    for row in time_link_rows:
        lines.append(
            "MATCH (a:BusinessAtom {"
            + props({"id": row["atom_id"]})
            + "}), (t:BusinessTimeContext {"
            + props({"name": row["time_name"]})
            + "}) MERGE (a)-[:HAS_TIME_CONTEXT]->(t);"
        )

    for row in scene_match_rows:
        lines.append(
            "MATCH (a:BusinessAtom {"
            + props({"id": row["atom_id"]})
            + "}), (s:BusinessScene {"
            + props({"key": row["scene_key"]})
            + "}) MERGE (a)-[r:MATCHES_SCENE]->(s) SET r += {"
            + props(
                {
                    "score": row["score"],
                    "matched_terms": row["matched_terms"],
                    "module_code": row["module_code"],
                }
            )
            + "};"
        )

    for row in scene_actor_rows:
        lines.append(
            "MATCH (s:BusinessScene {"
            + props({"key": row["scene_key"]})
            + "}), (w:BusinessActor {"
            + props({"name": row["actor_name"]})
            + "}) MERGE (s)-[r:SCENE_HAS_ACTOR]->(w) SET r.atom_count = "
            + cypher_value(row["atom_count"])
            + ";"
        )
    for row in scene_object_rows:
        lines.append(
            "MATCH (s:BusinessScene {"
            + props({"key": row["scene_key"]})
            + "}), (o:BusinessObject {"
            + props({"name": row["object_name"]})
            + "}) MERGE (s)-[r:SCENE_HAS_OBJECT]->(o) SET r.atom_count = "
            + cypher_value(row["atom_count"])
            + ";"
        )
    for row in scene_time_rows:
        lines.append(
            "MATCH (s:BusinessScene {"
            + props({"key": row["scene_key"]})
            + "}), (t:BusinessTimeContext {"
            + props({"name": row["time_name"]})
            + "}) MERGE (s)-[r:SCENE_HAS_TIME]->(t) SET r.atom_count = "
            + cypher_value(row["atom_count"])
            + ";"
        )

    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_file


def build_parser():
    parser = argparse.ArgumentParser(description="Export business taxonomy graph into a Cypher file.")
    parser.add_argument("--atoms-file", default=str(DEFAULT_CLASSIFIED_FILE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--clear-first", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    out = export_cypher(Path(args.atoms_file), Path(args.output), clear_first=args.clear_first)
    print(out)


if __name__ == "__main__":
    main()
