import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from main import run_pipeline
from neo4j_config import DEFAULT_NEO4J_URI, DEFAULT_NEO4J_USER, get_neo4j_password
from qwen_client import get_default_model
from reference_rule_pipeline import build_catalog, demo_payload, overlap_score, run_check, save_build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_ATOM_FILE = PROCESSED_DIR / "sample_two_doc_atoms.xlsx"
RULE_FILE = PROCESSED_DIR / "reference_rule_catalog.xlsx"
ALIGN_FILE = PROCESSED_DIR / "reference_rule_alignment.xlsx"


def ensure_reference_outputs():
    if RULE_FILE.exists() and ALIGN_FILE.exists():
        return
    rule_df, align_df, atom_df, kg = build_catalog()
    save_build(rule_df, align_df, atom_df, kg)


def load_or_extract_atoms(model=None, max_chunks_per_doc=2, force=False):
    if SAMPLE_ATOM_FILE.exists() and not force:
        return SAMPLE_ATOM_FILE
    return run_pipeline(
        doc_keywords=["票据法", "支付结算办法"],
        output_name=SAMPLE_ATOM_FILE.name,
        model=model or get_default_model(),
        max_chunks_per_doc=max_chunks_per_doc,
    )


def build_sample_links(atom_df, align_df, threshold=0.45):
    links = []
    for _, atom in atom_df.iterrows():
        source_doc = str(atom.get("source_document", ""))
        content = str(atom.get("content_original", ""))
        if not source_doc or not content:
            continue
        candidates = align_df[align_df["document_name"] == source_doc]
        scored = []
        for _, clause in candidates.iterrows():
            score = overlap_score(content, clause["matched_segment_text"])
            if score >= threshold:
                scored.append((score, clause))
        scored.sort(key=lambda item: item[0], reverse=True)
        for score, clause in scored[:3]:
            links.append({
                "atom_id": str(atom["atom_id"]),
                "rule_id": clause["rule_id"],
                "segment_id": clause["matched_segment_id"],
                "score": round(score, 4),
            })
    return links


def load_to_neo4j(atom_file, clear_first=False, uri=None, user=None, password=None):
    uri = uri or DEFAULT_NEO4J_URI
    user = user or DEFAULT_NEO4J_USER
    password = password if password is not None else get_neo4j_password()
    ensure_reference_outputs()
    atom_df = pd.read_excel(atom_file).fillna("")
    rule_df = pd.read_excel(RULE_FILE).fillna("")
    align_df = pd.read_excel(ALIGN_FILE).fillna("")

    # [💡 优化点 1]：剔除没有 ID 的脏数据，防止 Neo4j 节点塌陷互相覆盖
    atom_df = atom_df[atom_df["atom_id"].astype(str).str.strip() != ""]
    rule_df = rule_df[rule_df["rule_id"].astype(str).str.strip() != ""]
    align_df = align_df[align_df["matched_segment_id"].astype(str).str.strip() != ""]

    links = build_sample_links(atom_df, align_df)

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            if clear_first:
                session.run("MATCH (n) DETACH DELETE n")

            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (a:SampleAtom) REQUIRE a.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (r:ReferenceRule) REQUIRE r.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:ClauseSegment) REQUIRE c.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (x:ComplianceRun) REQUIRE x.id IS UNIQUE")

            # [💡 优化点 2]：将 iterrows 循环改为 UNWIND 批量写入，全量跑时性能可提升百倍

            # --- 1. 批量写入 Atom 节点 ---
            if not atom_df.empty:
                atom_batch = []
                for _, row in atom_df.iterrows():
                    atom_batch.append({
                        "doc_name": str(row.get("source_document", "")),
                        "atom_id": str(row.get("atom_id", "")),
                        "props": {
                            "rule_type": str(row.get("rule_type", "")),
                            "who": str(row.get("who", "")),
                            "when": str(row.get("when", "")),
                            "where": str(row.get("where", "")),
                            "what": str(row.get("what", "")),
                            "how": str(row.get("how", "")),
                            "content_original": str(row.get("content_original", "")),
                        }
                    })
                session.run(
                    """
                    UNWIND $batch AS row
                    MERGE (d:Document {name: row.doc_name})
                    MERGE (a:SampleAtom {id: row.atom_id})
                    SET a += row.props
                    MERGE (d)-[:HAS_ATOM]->(a)
                    """,
                    batch=atom_batch
                )

            # --- 2. 批量写入 ReferenceRule 节点 ---
            if not rule_df.empty:
                rule_batch = []
                for _, row in rule_df.iterrows():
                    rule_batch.append({
                        "rule_id": str(row["rule_id"]),
                        "name": str(row["expert_rule"]),
                        "check_type": str(row["check_type"]),
                        "required_fields": str(row["required_fields"]),
                        "params": str(row["params"]),
                        "entities": str(row["entities"]),
                    })
                session.run(
                    """
                    UNWIND $batch AS row
                    MERGE (r:ReferenceRule {id: row.rule_id})
                    SET r.name = row.name,
                        r.check_type = row.check_type,
                        r.required_fields = row.required_fields,
                        r.params = row.params,
                        r.entities = row.entities
                    """,
                    batch=rule_batch
                )

            # --- 3. 批量写入 ClauseSegment 节点及关系 ---
            if not align_df.empty:
                align_batch = []
                for _, row in align_df.iterrows():
                    align_batch.append({
                        "doc_name": str(row["document_name"]),
                        "segment_id": str(row["matched_segment_id"]),
                        "segment_ref": str(row["matched_segment_ref"]),
                        "segment_type": str(row["matched_segment_type"]),
                        "segment_text": str(row["matched_segment_text"]),
                        "rule_id": str(row["rule_id"]),
                    })
                session.run(
                    """
                    UNWIND $batch AS row
                    MERGE (d:Document {name: row.doc_name})
                    MERGE (c:ClauseSegment {id: row.segment_id})
                    SET c.ref = row.segment_ref,
                        c.segment_type = row.segment_type,
                        c.text = row.segment_text
                    MERGE (r:ReferenceRule {id: row.rule_id})
                    MERGE (r)-[:SUPPORTED_BY]->(c)
                    MERGE (c)-[:FROM_DOCUMENT]->(d)
                    """,
                    batch=align_batch
                )

            # --- 4. 批量写入 Links 关系 ---
            if links:
                # links 本身已经是字典列表，直接喂给 UNWIND 即可
                session.run(
                    """
                    UNWIND $batch AS link
                    MATCH (a:SampleAtom {id: link.atom_id})
                    MATCH (r:ReferenceRule {id: link.rule_id})
                    MATCH (c:ClauseSegment {id: link.segment_id})
                    MERGE (c)-[m:SUGGESTS_ATOM]->(a)
                    SET m.score = link.score
                    MERGE (r)-[n:CHECKS_ATOM]->(a)
                    SET n.score = link.score
                    """,
                    batch=links
                )
    finally:
        driver.close()

    return {"atom_count": len(atom_df), "rule_count": len(rule_df), "link_count": len(links)}


def save_report_to_neo4j(report, uri=None, user=None, password=None):
    uri = uri or DEFAULT_NEO4J_URI
    user = user or DEFAULT_NEO4J_USER
    password = password if password is not None else get_neo4j_password()
    run_id = datetime.now().strftime("RUN-%Y%m%d-%H%M%S")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            session.run(
                """
                MERGE (r:ComplianceRun {id: $run_id})
                SET r.status = $status,
                    r.summary = $summary,
                    r.created_at = $created_at,
                    r.report_json = $report_json
                """,
                run_id=run_id,
                status=report["overall_status"],
                summary=json.dumps(report["summary"], ensure_ascii=False),
                created_at=datetime.now().isoformat(timespec="seconds"),
                report_json=json.dumps(report, ensure_ascii=False),
            )
            for item in report["results"]:
                session.run(
                    """
                    MATCH (run:ComplianceRun {id: $run_id})
                    MATCH (rule:ReferenceRule {id: $rule_id})
                    MERGE (run)-[r:HAS_RESULT]->(rule)
                    SET r.status = $status,
                        r.reason = $reason
                    """,
                    run_id=run_id,
                    rule_id=item["rule_id"],
                    status=item["status"],
                    reason=item["reason"],
                )
    finally:
        driver.close()
    return run_id


def run_sample_pipeline(args):
    atom_file = Path(args.atoms_file) if args.atoms_file else load_or_extract_atoms(
        model=args.model,
        max_chunks_per_doc=args.max_chunks_per_doc,
        force=args.force_extract,
    )
    neo4j_stats = None
    if not args.skip_neo4j:
        try:
            neo4j_stats = load_to_neo4j(
                atom_file,
                clear_first=args.clear_neo4j,
                uri=args.neo4j_uri,
                user=args.neo4j_user,
                password=args.neo4j_password,
            )
        except ServiceUnavailable as exc:
            raise RuntimeError(
                f"Cannot connect to Neo4j at `{args.neo4j_uri}`. Start the local Neo4j server, "
                "or rerun with `--skip-neo4j` to validate extraction/checking only."
            ) from exc

    ensure_reference_outputs()
    rule_df = pd.read_excel(RULE_FILE).fillna("")
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8")) if args.payload else demo_payload()
    report = run_check(payload, rule_df)
    report_path = PROCESSED_DIR / "sample_two_doc_compliance_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    run_id = None
    if not args.skip_neo4j:
        try:
            run_id = save_report_to_neo4j(report, uri=args.neo4j_uri, user=args.neo4j_user,
                                          password=args.neo4j_password)
        except ServiceUnavailable as exc:
            raise RuntimeError(
                f"Cannot write compliance report to Neo4j at `{args.neo4j_uri}`. "
                "Extraction and checking succeeded, but Neo4j is unavailable."
            ) from exc

    print(f"Atoms: {atom_file}")
    if neo4j_stats:
        print(
            f"Neo4j loaded atoms={neo4j_stats['atom_count']} rules={neo4j_stats['rule_count']} links={neo4j_stats['link_count']}")
    print(f"Compliance report: {report_path}")
    print(f"Overall status: {report['overall_status']}")
    if run_id:
        print(f"Compliance run node: {run_id}")


def build_parser():
    parser = argparse.ArgumentParser(description="Small-sample pipeline for Bill Law + Payment Settlement Measures.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-chunks-per-doc", type=int, default=2,
                        help="Use 0 for all chunks; keep small for smoke tests.")
    parser.add_argument("--force-extract", action="store_true")
    parser.add_argument("--atoms-file", help="Reuse an existing atom xlsx instead of calling the model.")
    parser.add_argument("--payload", help="Optional compliance payload JSON path.")
    parser.add_argument("--skip-neo4j", action="store_true")
    parser.add_argument("--clear-neo4j", action="store_true")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=get_neo4j_password())
    return parser


if __name__ == "__main__":
    try:
        run_sample_pipeline(build_parser().parse_args())
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1)
