# Context Carry-over

Date: 2026-05-19

## Current Project Baseline

The project is a financial regulation compliance pipeline:

1. extraction
2. manual review
3. MySQL traceability and statistics
4. conflict detection
5. Neo4j graph build
6. recall / reasoning
7. final compliance judgement

Two tracks remain active:

1. improve the compliance pipeline itself
2. prepare the repository for public GitHub publication

Publication risk is still Git history. The previously exposed DashScope key must be rotated / revoked, and history must be cleaned or replaced before public push.

## Stable Implementation Facts

1. `src/mysql_traceability.py` supports MySQL sync for batches, artifacts, chunks, atoms, taxonomy, and recall outputs.
2. `src/business_taxonomy_app.py` exposes extraction/build, manual review, MySQL/statistics, conflict detection, classification overview, graph browsing, and model recall/reasoning.
3. Streamlit LLM flow accepts explicit `api_config`; it should not depend on local `qwen.env`.
4. `src/conflict_detection.py` is still the first heuristic version.

## Graph Baseline

The graph is not only a business taxonomy tree anymore. Treat the current graph as:

1. taxonomy backbone: board / category / module / scene
2. evidence layer: `BusinessAtom`
3. normalized entity layer: `BusinessActor`, `BusinessObject`, `BusinessTimeContext`
4. scene-to-entity aggregation layer

Current aligned graph baseline after the 2026-05-14 refresh:

1. Neo4j and `data/processed/business_taxonomy_graph.json` were aligned.
2. Core graph counts: `3975` nodes, `9375` relationships, zero-degree nodes `0`.
3. Node counts: `3` boards, `18` categories, `85` modules, `336` scenes, `1942` atoms, `514` actors, `341` objects, `736` time contexts.
4. Relationship counts: `2441` `TAGGED_AS`, `296` `MATCHES_SCENE`, `2601` `INVOLVES_ACTOR`, `1279` `TARGETS_OBJECT`, `1800` `HAS_TIME_CONTEXT`, `202` `SCENE_HAS_ACTOR`, `118` `SCENE_HAS_OBJECT`, `199` `SCENE_HAS_TIME`.

Graph interpretation:

1. no isolated free nodes were observed in the aligned baseline
2. every atom had at least one module tag
3. weak point is coverage, not disconnection
4. sparse areas remain: `1709` atoms without scene match, `38` modules without atom evidence, `309` scenes without atom evidence

Do not add direct pairwise atom-to-atom `SHARES_WHO` / `SHARES_WHAT` / `SHARES_WHEN` edges unless a specific retrieval use case justifies the edge explosion.

## Graph UI And Neo4j Demo

1. Streamlit graph browser remains the preferred in-project visualization path.
2. It renders taxonomy nodes, entity nodes, and atom nodes in one graph.
3. Neo4j Browser custom GraSS styling is no longer the preferred workflow.
4. For Neo4j demos, use plain Cypher returning graph paths and rely on Browser default label coloring.

If a Neo4j Browser query only shows `BusinessModule -> BusinessScene`, first check manual query input / line breaks. Local validation previously confirmed `BIZ-02-03-SCENE-01` has actor/object/time entity links.

## Atom Field Interpretation

Repeated atom fields are intentional layers:

1. raw text: `who`, `what`, `when`
2. extracted terms: `who_terms`, `what_terms`, `when_terms`
3. normalized terms: `who_terms_normalized`, `what_terms_normalized`, `when_terms_normalized`

Raw fields are for provenance/readability. Normalized arrays are for stable graph linkage and retrieval. Intermediate `*_terms` can be de-emphasized in UI later, but should not be deleted casually.

## Formal QA Current State

`src/formal_qa.py` is a conservative fast path before the existing LLM recall/reasoning path. `BusinessAtom` remains the answer container; no separate answer nodes are needed.

Strict rule:

1. return `confidence = "formal"` only when evidence is complete and deterministic
2. prefer false negatives over false positives
3. preserve candidate atoms and `fail_reason` for slow path when the fast path refuses

Existing Streamlit integration:

1. single-query scenario recall calls `answer_question_formally(...)` before LLM recall
2. formal hits display directly and skip LLM
3. misses display `fail_reason` / retained evidence and continue to the existing recall controller
4. multi-query submissions intentionally skip the formal fast path for now

## Formal QA Changes On 2026-05-19

The report `docs/formal_qa_query_test_report.md` exposed two issues:

1. Type A rules were still not tight enough; some atoms had extra conditions such as `异地` / `经营地与注册地不在同一行政区域`, while the user question did not state that scope.
2. Type B/C were still untreated, returning `unsupported_question_type`.

Implemented conservative updates in `src/formal_qa.py`:

1. Type A now rejects atoms with unmatched condition-scope markers via `unmatched_condition_scope`.
2. Type B has a narrow actor-answer path for questions such as `哪些机构...` / `谁有权...`; answers come from `BusinessActor`, constrained by object and deterministic rule types.
3. Type C has a narrow definition path for `是什么意思` / `是指` / `定义` / `如何界定`; answers come from definition-style atoms only.
4. Type C condition/trigger questions still go slow path until their evidence completeness rules are defined.

Additional tightening after the first 2026-05-19 pass:

1. Type B now filters candidate atoms by question action terms and precise object terms before answerability checks. This prevents subjects connected only through broad objects such as `基本存款账户` from answering precise questions such as `开户登记证`.
2. Type C definition answers now only accept `DEF_SCOPE`; `VAL_THRESHOLD` no longer leaks into definition answers such as `临时存款账户是什么意思`.
3. Definition answer text now prefers the atom raw `what` over normalized object labels, so `空头支票是什么意思` displays `空头支票` rather than the broader normalized `支票`.
4. Type A/B/C share a precise-object guard for terms such as `开户登记证`, `本票出票人资格`, `票据凭证的格式和印制管理办法`, `农民工工资专用账户`, `临时存款账户`, `空头支票`, `只收不付`, `客户身份识别义务`, `客户身份资料`, `交易记录`, and `银行汇票丧失`.

Regression tests added:

1. `tests/test_formal_qa.py::test_type_a_rejects_unmatched_condition_scope`
2. `tests/test_formal_qa.py::test_type_b_actor_query_can_return_formal_actor_answer`
3. `tests/test_formal_qa.py::test_type_c_definition_query_can_return_formal_definition_answer`
4. `tests/test_formal_qa.py::test_type_a_rejects_broad_permission_when_precise_object_mismatches`
5. `tests/test_formal_qa.py::test_type_a_requires_all_precise_object_terms_to_match`
6. `tests/test_formal_qa.py::test_type_b_actor_query_filters_to_matching_action`
7. `tests/test_formal_qa.py::test_type_b_actor_query_rejects_precise_object_mismatch`
8. `tests/test_formal_qa.py::test_type_b_actor_query_rejects_unmatched_action`
9. `tests/test_formal_qa.py::test_type_c_definition_query_filters_non_definition_atoms`
10. `tests/test_formal_qa.py::test_type_c_definition_answer_prefers_atom_what_over_normalized_object`
11. `tests/test_formal_qa_query_smoke.py::test_live_query_tightness_cases`

Verified commands:

1. `python -m unittest tests.test_formal_qa -v`
2. `NEO4J_PASSWORD=123456 python -m unittest tests.test_formal_qa_query_smoke -v`
3. `NEO4J_PASSWORD=123456 python -m unittest tests.test_formal_qa tests.test_formal_qa_query_smoke -v`
4. `python -m py_compile src/formal_qa.py src/business_taxonomy_app.py tests/test_formal_qa.py tests/test_formal_qa_query_smoke.py`

`docs/formal_qa_query_test_report.md` has been regenerated against local Neo4j with 40 queries. Current results: `40` total queries, `16` formal hits, `24` slow-path results, `40.0%` formal hit rate. The expanded suite keeps the original 24 queries and adds 16 queries covering Type B action/object alignment, Type C pure definitions, precise-object guardrails, conditions/triggers, and account/bill/customer-identity/wage-account graph areas.

## Next Best Steps

1. Improve entity/object extraction for known false negatives surfaced by the 40-query suite, especially `票据凭证的格式和印制管理办法`, `银行汇票丧失`, and `客户身份识别义务`.
2. Review remaining Type A formal hits for hidden scope conditions and convert findings into `CONDITION_SCOPE_GROUPS` or precise-object terms.
3. Define a strict Type C condition/trigger answerability rule before enabling formal answers for `什么情况下...`.
4. Add structured fast-path metrics in the Streamlit UI: hit count, fail reason distribution, candidate count, avoided LLM calls.
5. Continue improving scene matching and entity normalization, especially object/time over-fragmentation.

## Ready-to-Paste Short Carry-over

Continue the financial regulation compliance project. The graph baseline is aligned between Neo4j and `data/processed/business_taxonomy_graph.json` at `3975` nodes, `9375` relationships, and zero zero-degree nodes. The graph already includes taxonomy, atoms, normalized `who/what/when` entities, and scene-to-entity aggregation; do not describe it as only a taxonomy tree. Current graph weakness is sparse scene/evidence coverage, not graph disconnection. Formal QA is now a conservative pre-LLM fast path in `src/formal_qa.py`: Type A rejects hidden unmatched condition scopes and precise-object mismatches; Type B has a narrow actor-answer path with action/object alignment; Type C only answers pure `DEF_SCOPE` definition questions and uses raw atom `what` for display. `docs/formal_qa_query_test_report.md` has been regenerated to 40 queries with `16` formal and `24` slow-path results; `tests/test_formal_qa_query_smoke.py` now contains the 40-query live smoke suite. Remaining gaps are mainly object/entity extraction for `票据凭证的格式和印制管理办法`, `银行汇票丧失`, and `客户身份识别义务`. Before public GitHub push, rotate/revoke the exposed key and clean or replace Git history.
