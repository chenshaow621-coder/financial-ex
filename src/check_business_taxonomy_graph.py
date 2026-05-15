import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    from neo4j import GraphDatabase
except ImportError as exc:
    raise SystemExit(
        "Missing dependency `neo4j`. Run this script with the project virtualenv, for example "
        r"`.\venv\Scripts\python.exe .\src\check_business_taxonomy_graph.py`."
    ) from exc

from neo4j_config import DEFAULT_NEO4J_URI, DEFAULT_NEO4J_USER, get_neo4j_password

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH_JSON = PROJECT_ROOT / "data" / "processed" / "business_taxonomy_graph.json"

NODE_TYPE_LABELS = {
    "board": "BusinessBoard",
    "category": "BusinessCategory",
    "module": "BusinessModule",
    "scene": "BusinessScene",
    "atom": "BusinessAtom",
    "actor": "BusinessActor",
    "object": "BusinessObject",
    "time_context": "BusinessTimeContext",
}

EDGE_TYPE_RELATIONS = {
    "HAS_CATEGORY": "HAS_CATEGORY",
    "HAS_MODULE": "HAS_MODULE",
    "HAS_SCENE": "HAS_SCENE",
    "TAGGED_AS": "TAGGED_AS",
    "MATCHES_SCENE": "MATCHES_SCENE",
    "INVOLVES_ACTOR": "INVOLVES_ACTOR",
    "TARGETS_OBJECT": "TARGETS_OBJECT",
    "HAS_TIME_CONTEXT": "HAS_TIME_CONTEXT",
    "SCENE_HAS_ACTOR": "SCENE_HAS_ACTOR",
    "SCENE_HAS_OBJECT": "SCENE_HAS_OBJECT",
    "SCENE_HAS_TIME": "SCENE_HAS_TIME",
}


def safe_ratio(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def load_graph_json_stats(graph_json_path: Path) -> dict:
    if not graph_json_path.exists():
        raise FileNotFoundError(f"Graph JSON not found: {graph_json_path}")

    payload = json.loads(graph_json_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    node_type_counts = Counter(str(node.get("type", "")).strip() for node in nodes)
    edge_type_counts = Counter(str(edge.get("type", "")).strip() for edge in edges)

    degree_by_node = defaultdict(int)
    for edge in edges:
        source = str(edge.get("source", "")).strip()
        target = str(edge.get("target", "")).strip()
        if source:
            degree_by_node[source] += 1
        if target:
            degree_by_node[target] += 1

    zero_degree_nodes = [node for node in nodes if degree_by_node.get(str(node.get("id", "")).strip(), 0) == 0]
    zero_degree_by_type = Counter(str(node.get("type", "")).strip() for node in zero_degree_nodes)

    return {
        "path": str(graph_json_path),
        "node_total": len(nodes),
        "edge_total": len(edges),
        "node_type_counts": dict(sorted(node_type_counts.items())),
        "edge_type_counts": dict(sorted(edge_type_counts.items())),
        "zero_degree_total": len(zero_degree_nodes),
        "zero_degree_by_type": dict(sorted(zero_degree_by_type.items())),
    }


def fetch_scalar(session, query: str, **params) -> int:
    row = session.run(query, **params).single()
    return int(row["c"] if row is not None else 0)


def fetch_named_rows(session, query: str, limit: int) -> list[dict]:
    return [dict(row) for row in session.run(query, limit=limit)]


def load_neo4j_stats(uri: str, user: str, password: str, sample_limit: int) -> dict:
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            node_counts = {
                "board": fetch_scalar(session, "MATCH (n:BusinessBoard) RETURN count(n) AS c"),
                "category": fetch_scalar(session, "MATCH (n:BusinessCategory) RETURN count(n) AS c"),
                "module": fetch_scalar(session, "MATCH (n:BusinessModule) RETURN count(n) AS c"),
                "scene": fetch_scalar(session, "MATCH (n:BusinessScene) RETURN count(n) AS c"),
                "atom": fetch_scalar(session, "MATCH (n:BusinessAtom) RETURN count(n) AS c"),
                "document": fetch_scalar(session, "MATCH (n:BusinessDocument) RETURN count(n) AS c"),
                "actor": fetch_scalar(session, "MATCH (n:BusinessActor) RETURN count(n) AS c"),
                "object": fetch_scalar(session, "MATCH (n:BusinessObject) RETURN count(n) AS c"),
                "time_context": fetch_scalar(session, "MATCH (n:BusinessTimeContext) RETURN count(n) AS c"),
            }
            edge_counts = {
                "HAS_CATEGORY": fetch_scalar(session, "MATCH ()-[r:HAS_CATEGORY]->() RETURN count(r) AS c"),
                "HAS_MODULE": fetch_scalar(session, "MATCH ()-[r:HAS_MODULE]->() RETURN count(r) AS c"),
                "HAS_SCENE": fetch_scalar(session, "MATCH ()-[r:HAS_SCENE]->() RETURN count(r) AS c"),
                "HAS_ATOM": fetch_scalar(session, "MATCH ()-[r:HAS_ATOM]->() RETURN count(r) AS c"),
                "TAGGED_AS": fetch_scalar(session, "MATCH ()-[r:TAGGED_AS]->() RETURN count(r) AS c"),
                "MATCHES_SCENE": fetch_scalar(session, "MATCH ()-[r:MATCHES_SCENE]->() RETURN count(r) AS c"),
                "INVOLVES_ACTOR": fetch_scalar(session, "MATCH ()-[r:INVOLVES_ACTOR]->() RETURN count(r) AS c"),
                "TARGETS_OBJECT": fetch_scalar(session, "MATCH ()-[r:TARGETS_OBJECT]->() RETURN count(r) AS c"),
                "HAS_TIME_CONTEXT": fetch_scalar(session, "MATCH ()-[r:HAS_TIME_CONTEXT]->() RETURN count(r) AS c"),
                "SCENE_HAS_ACTOR": fetch_scalar(session, "MATCH ()-[r:SCENE_HAS_ACTOR]->() RETURN count(r) AS c"),
                "SCENE_HAS_OBJECT": fetch_scalar(session, "MATCH ()-[r:SCENE_HAS_OBJECT]->() RETURN count(r) AS c"),
                "SCENE_HAS_TIME": fetch_scalar(session, "MATCH ()-[r:SCENE_HAS_TIME]->() RETURN count(r) AS c"),
            }
            zero_degree_total = fetch_scalar(session, "MATCH (n) WHERE NOT (n)--() RETURN count(n) AS c")
            zero_degree_by_label = fetch_named_rows(
                session,
                """
                MATCH (n)
                WHERE NOT (n)--()
                UNWIND labels(n) AS label
                RETURN label, count(*) AS c
                ORDER BY c DESC, label
                LIMIT $limit
                """,
                limit=max(sample_limit, 20),
            )
            coverage = {
                "atoms_without_module": fetch_scalar(
                    session,
                    "MATCH (a:BusinessAtom) WHERE NOT (a)-[:TAGGED_AS]->(:BusinessModule) RETURN count(a) AS c",
                ),
                "atoms_without_scene": fetch_scalar(
                    session,
                    "MATCH (a:BusinessAtom) WHERE NOT (a)-[:MATCHES_SCENE]->(:BusinessScene) RETURN count(a) AS c",
                ),
                "atoms_without_module_and_scene": fetch_scalar(
                    session,
                    """
                    MATCH (a:BusinessAtom)
                    WHERE NOT (a)-[:TAGGED_AS]->(:BusinessModule)
                      AND NOT (a)-[:MATCHES_SCENE]->(:BusinessScene)
                    RETURN count(a) AS c
                    """,
                ),
                "modules_without_atoms": fetch_scalar(
                    session,
                    "MATCH (m:BusinessModule) WHERE NOT (:BusinessAtom)-[:TAGGED_AS]->(m) RETURN count(m) AS c",
                ),
                "scenes_without_atoms": fetch_scalar(
                    session,
                    "MATCH (s:BusinessScene) WHERE NOT (:BusinessAtom)-[:MATCHES_SCENE]->(s) RETURN count(s) AS c",
                ),
                "actors_without_atom": fetch_scalar(
                    session,
                    "MATCH (w:BusinessActor) WHERE NOT (:BusinessAtom)-[:INVOLVES_ACTOR]->(w) RETURN count(w) AS c",
                ),
                "objects_without_atom": fetch_scalar(
                    session,
                    "MATCH (o:BusinessObject) WHERE NOT (:BusinessAtom)-[:TARGETS_OBJECT]->(o) RETURN count(o) AS c",
                ),
                "times_without_atom": fetch_scalar(
                    session,
                    "MATCH (t:BusinessTimeContext) WHERE NOT (:BusinessAtom)-[:HAS_TIME_CONTEXT]->(t) RETURN count(t) AS c",
                ),
            }
            top_unhit_modules = fetch_named_rows(
                session,
                """
                MATCH (m:BusinessModule)
                WHERE NOT (:BusinessAtom)-[:TAGGED_AS]->(m)
                RETURN m.code AS code, m.name AS name
                ORDER BY m.code
                LIMIT $limit
                """,
                limit=sample_limit,
            )
            top_unhit_scenes = fetch_named_rows(
                session,
                """
                MATCH (s:BusinessScene)
                WHERE NOT (:BusinessAtom)-[:MATCHES_SCENE]->(s)
                RETURN s.key AS key, s.name AS name
                ORDER BY s.key
                LIMIT $limit
                """,
                limit=sample_limit,
            )
    finally:
        driver.close()

    core_node_total = sum(node_counts[key] for key in NODE_TYPE_LABELS)
    core_edge_total = sum(edge_counts[key] for key in EDGE_TYPE_RELATIONS)

    return {
        "uri": uri,
        "user": user,
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "core_node_total": core_node_total,
        "core_edge_total": core_edge_total,
        "zero_degree_total": zero_degree_total,
        "zero_degree_by_label": zero_degree_by_label,
        "coverage": coverage,
        "top_unhit_modules": top_unhit_modules,
        "top_unhit_scenes": top_unhit_scenes,
    }


def compare_stats(json_stats: dict, neo4j_stats: dict) -> list[str]:
    mismatches = []
    if json_stats["node_total"] != neo4j_stats["core_node_total"]:
        mismatches.append(
            f"Core node total mismatch: graph.json={json_stats['node_total']} neo4j={neo4j_stats['core_node_total']}"
        )
    if json_stats["edge_total"] != neo4j_stats["core_edge_total"]:
        mismatches.append(
            f"Core edge total mismatch: graph.json={json_stats['edge_total']} neo4j={neo4j_stats['core_edge_total']}"
        )

    for node_type in NODE_TYPE_LABELS:
        json_count = int(json_stats["node_type_counts"].get(node_type, 0))
        neo4j_count = int(neo4j_stats["node_counts"].get(node_type, 0))
        if json_count != neo4j_count:
            mismatches.append(f"Node count mismatch for `{node_type}`: graph.json={json_count} neo4j={neo4j_count}")

    for edge_type in EDGE_TYPE_RELATIONS:
        json_count = int(json_stats["edge_type_counts"].get(edge_type, 0))
        neo4j_count = int(neo4j_stats["edge_counts"].get(edge_type, 0))
        if json_count != neo4j_count:
            mismatches.append(f"Edge count mismatch for `{edge_type}`: graph.json={json_count} neo4j={neo4j_count}")

    return mismatches


def evaluate_failures(neo4j_stats: dict, mismatches: list[str], args: argparse.Namespace) -> list[str]:
    failures = list(mismatches)
    if neo4j_stats["zero_degree_total"] > args.max_zero_degree:
        failures.append(
            f"Zero-degree node count {neo4j_stats['zero_degree_total']} exceeds threshold {args.max_zero_degree}"
        )

    threshold_pairs = [
        ("atoms_without_scene", args.max_atoms_without_scene),
        ("modules_without_atoms", args.max_modules_without_atoms),
        ("scenes_without_atoms", args.max_scenes_without_atoms),
    ]
    for key, threshold in threshold_pairs:
        if threshold is None:
            continue
        value = int(neo4j_stats["coverage"][key])
        if value > threshold:
            failures.append(f"{key}={value} exceeds threshold {threshold}")

    return failures


def build_warnings(neo4j_stats: dict) -> list[str]:
    warnings = []
    coverage = neo4j_stats["coverage"]
    node_counts = neo4j_stats["node_counts"]

    atoms = int(node_counts["atom"])
    modules = int(node_counts["module"])
    scenes = int(node_counts["scene"])

    if coverage["atoms_without_scene"] > 0:
        ratio = safe_ratio(coverage["atoms_without_scene"], atoms)
        warnings.append(
            f"High atom-to-scene gap: {coverage['atoms_without_scene']} / {atoms} atoms have no scene match ({format_percent(ratio)})"
        )
    if coverage["modules_without_atoms"] > 0:
        ratio = safe_ratio(coverage["modules_without_atoms"], modules)
        warnings.append(
            f"Unhit modules remain: {coverage['modules_without_atoms']} / {modules} modules have no atom evidence ({format_percent(ratio)})"
        )
    if coverage["scenes_without_atoms"] > 0:
        ratio = safe_ratio(coverage["scenes_without_atoms"], scenes)
        warnings.append(
            f"Unhit scenes remain: {coverage['scenes_without_atoms']} / {scenes} scenes have no atom evidence ({format_percent(ratio)})"
        )
    return warnings


def build_report(json_stats: dict, neo4j_stats: dict, mismatches: list[str], failures: list[str], warnings: list[str]) -> dict:
    atoms = int(neo4j_stats["node_counts"]["atom"])
    modules = int(neo4j_stats["node_counts"]["module"])
    scenes = int(neo4j_stats["node_counts"]["scene"])
    coverage = neo4j_stats["coverage"]

    atoms_with_scene = atoms - int(coverage["atoms_without_scene"])
    modules_with_atoms = modules - int(coverage["modules_without_atoms"])
    scenes_with_atoms = scenes - int(coverage["scenes_without_atoms"])

    if failures:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "graph_json": json_stats,
        "neo4j": neo4j_stats,
        "mismatches": mismatches,
        "failures": failures,
        "warnings": warnings,
        "coverage_summary": {
            "atoms_with_scene": atoms_with_scene,
            "atoms_with_scene_ratio": safe_ratio(atoms_with_scene, atoms),
            "modules_with_atoms": modules_with_atoms,
            "modules_with_atoms_ratio": safe_ratio(modules_with_atoms, modules),
            "scenes_with_atoms": scenes_with_atoms,
            "scenes_with_atoms_ratio": safe_ratio(scenes_with_atoms, scenes),
        },
    }


def print_report(report: dict) -> None:
    graph_json = report["graph_json"]
    neo4j = report["neo4j"]
    coverage = neo4j["coverage"]
    coverage_summary = report["coverage_summary"]

    print(f"Status: {report['status']}")
    print(f"Checked at: {report['checked_at']}")
    print(f"Graph JSON: {graph_json['path']}")
    print(f"Neo4j: {neo4j['uri']} ({neo4j['user']})")
    print()

    print("Shared graph consistency")
    print(f"- Core nodes: graph.json={graph_json['node_total']} neo4j={neo4j['core_node_total']}")
    print(f"- Core edges: graph.json={graph_json['edge_total']} neo4j={neo4j['core_edge_total']}")
    print(f"- JSON zero-degree nodes: {graph_json['zero_degree_total']}")
    print(f"- Neo4j zero-degree nodes: {neo4j['zero_degree_total']}")
    if neo4j["node_counts"]["document"] or neo4j["edge_counts"]["HAS_ATOM"]:
        print(
            f"- Neo4j-only layer: documents={neo4j['node_counts']['document']} "
            f"HAS_ATOM={neo4j['edge_counts']['HAS_ATOM']}"
        )
    print()

    print("Node counts")
    for key in NODE_TYPE_LABELS:
        print(f"- {key}: json={graph_json['node_type_counts'].get(key, 0)} neo4j={neo4j['node_counts'][key]}")
    print()

    print("Edge counts")
    for key in EDGE_TYPE_RELATIONS:
        print(f"- {key}: json={graph_json['edge_type_counts'].get(key, 0)} neo4j={neo4j['edge_counts'][key]}")
    print()

    print("Coverage")
    print(
        f"- Atoms with scene: {coverage_summary['atoms_with_scene']} / {neo4j['node_counts']['atom']} "
        f"({format_percent(coverage_summary['atoms_with_scene_ratio'])})"
    )
    print(
        f"- Modules with atoms: {coverage_summary['modules_with_atoms']} / {neo4j['node_counts']['module']} "
        f"({format_percent(coverage_summary['modules_with_atoms_ratio'])})"
    )
    print(
        f"- Scenes with atoms: {coverage_summary['scenes_with_atoms']} / {neo4j['node_counts']['scene']} "
        f"({format_percent(coverage_summary['scenes_with_atoms_ratio'])})"
    )
    print(f"- atoms_without_module: {coverage['atoms_without_module']}")
    print(f"- atoms_without_scene: {coverage['atoms_without_scene']}")
    print(f"- atoms_without_module_and_scene: {coverage['atoms_without_module_and_scene']}")
    print(f"- modules_without_atoms: {coverage['modules_without_atoms']}")
    print(f"- scenes_without_atoms: {coverage['scenes_without_atoms']}")
    print(f"- actors_without_atom: {coverage['actors_without_atom']}")
    print(f"- objects_without_atom: {coverage['objects_without_atom']}")
    print(f"- times_without_atom: {coverage['times_without_atom']}")
    print()

    if neo4j["top_unhit_modules"]:
        print("Sample unhit modules")
        for row in neo4j["top_unhit_modules"]:
            print(f"- {row['code']} | {row['name']}")
        print()

    if neo4j["top_unhit_scenes"]:
        print("Sample unhit scenes")
        for row in neo4j["top_unhit_scenes"]:
            print(f"- {row['key']} | {row['name']}")
        print()

    if report["mismatches"]:
        print("Mismatches")
        for item in report["mismatches"]:
            print(f"- {item}")
        print()

    if report["warnings"]:
        print("Warnings")
        for item in report["warnings"]:
            print(f"- {item}")
        print()

    if report["failures"]:
        print("Failures")
        for item in report["failures"]:
            print(f"- {item}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check business taxonomy graph consistency and coverage.")
    parser.add_argument("--graph-json", default=str(DEFAULT_GRAPH_JSON), help="Path to exported graph JSON.")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=get_neo4j_password())
    parser.add_argument("--sample-limit", type=int, default=5, help="Number of sample unhit modules/scenes to print.")
    parser.add_argument("--max-zero-degree", type=int, default=0, help="Fail if Neo4j zero-degree nodes exceed this value.")
    parser.add_argument("--max-atoms-without-scene", type=int, default=None, help="Optional failure threshold.")
    parser.add_argument("--max-modules-without-atoms", type=int, default=None, help="Optional failure threshold.")
    parser.add_argument("--max-scenes-without-atoms", type=int, default=None, help="Optional failure threshold.")
    parser.add_argument("--output-json", help="Optional path to save the full health report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    json_stats = load_graph_json_stats(Path(args.graph_json))
    neo4j_stats = load_neo4j_stats(args.neo4j_uri, args.neo4j_user, args.neo4j_password, max(args.sample_limit, 0))
    mismatches = compare_stats(json_stats, neo4j_stats)
    failures = evaluate_failures(neo4j_stats, mismatches, args)
    warnings = build_warnings(neo4j_stats)
    report = build_report(json_stats, neo4j_stats, mismatches, failures, warnings)

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print_report(report)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
