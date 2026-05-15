# Context Carry-over

Date: 2026-05-14

## Project Stage

Current pipeline:

1. Financial regulation extraction
2. Manual review
3. MySQL traceability storage and statistics
4. Conflict detection
5. Neo4j graph build
6. Recall / reasoning
7. Final compliance judgement

The project is no longer only exporting Excel / JSON intermediates. It now has two parallel tracks:

1. Continue improving the compliance pipeline itself
2. Prepare the repository for public GitHub publication without leaking local secrets or local-only artifacts

## What Remains Stable From The Previous Round

The following status from the previous carry-over remains valid:

1. `src/mysql_traceability.py` already supports MySQL traceability sync for batch / artifact / chunk / atom / taxonomy / recall outputs
2. `src/business_taxonomy_app.py` already exposes:
   - extraction / build
   - manual review checklist
   - MySQL / statistics
   - conflict detection
   - classification overview
   - graph browsing
   - model reasoning demo
3. `src/conflict_detection.py` is still the first heuristic version
4. Streamlit LLM flow already accepts explicit `api_config` instead of depending on local `qwen.env`
5. Repository secret cleanup is still incomplete at Git history level even though tracked files are now cleaner

## Main Correction To The Previous Carry-over

The previous `docs/context_carry_over_2026-05-12.md` described the graph roadmap partly as a future direction. That description is now outdated in one important respect:

1. the graph is **no longer only business taxonomy / module structure**
2. entity-layer graph expansion for `who / what / when` has already been implemented
3. the current graph already uses normalized shared entity nodes rather than direct atom-to-atom `SHARES_*` edges

This means future discussion should treat the current graph as:

1. taxonomy backbone
2. atom layer
3. normalized entity layer for `who / what / when`
4. scene-to-entity aggregation layer

## Current Graph Model Confirmed In Code

Confirmed implementation in `src/business_taxonomy_pipeline.py`:

1. entity extraction helpers already exist for all three dimensions:
   - `extract_actor_graph_records(...)`
   - `extract_object_graph_records(...)`
   - `extract_time_graph_records(...)`
2. graph constraints already exist for:
   - `BusinessActor`
   - `BusinessObject`
   - `BusinessTimeContext`
3. atom-to-entity edges already exist:
   - `(:BusinessAtom)-[:INVOLVES_ACTOR]->(:BusinessActor)`
   - `(:BusinessAtom)-[:TARGETS_OBJECT]->(:BusinessObject)`
   - `(:BusinessAtom)-[:HAS_TIME_CONTEXT]->(:BusinessTimeContext)`
4. scene-to-entity aggregation edges already exist:
   - `(:BusinessScene)-[:SCENE_HAS_ACTOR]->(:BusinessActor)`
   - `(:BusinessScene)-[:SCENE_HAS_OBJECT]->(:BusinessObject)`
   - `(:BusinessScene)-[:SCENE_HAS_TIME]->(:BusinessTimeContext)`
5. there is still **no** direct atom-to-atom shared-entity edge family such as:
   - `SHARES_WHO`
   - `SHARES_WHAT`
   - `SHARES_WHEN`

This matches the intended design direction better than the old “tree-like graph only” description.

## Current Normalization Status

The normalization layer is already present in code:

1. `src/entity_normalization.py` exists
2. it uses:
   - `REFERENCE_SHEET = "完整实体参考表"`
   - `NORMALIZATION_SHEET = "实体规范化对照表"`
3. `src/business_taxonomy_pipeline.py` already imports and uses:
   - `extract_normalized_actor_records`
   - `extract_normalized_object_records`
   - `extract_normalized_time_records`
4. `src/schema.py` already has `normalized_name`

So the earlier note “likely next step is adding `src/entity_normalization.py`” is no longer accurate. That module has already landed and is wired into graph expansion.

## Graph Validation Done In This Round

Graph validation was run against the local Neo4j configured by environment variables:

- `NEO4J_URI`
- `NEO4J_USER`

Observed graph counts:

1. `BusinessBoard`: `3`
2. `BusinessCategory`: `18`
3. `BusinessModule`: `85`
4. `BusinessScene`: `336`
5. `BusinessAtom`: `1942`
6. `BusinessDocument`: `28`
7. `BusinessActor`: `914`
8. `BusinessObject`: `2017`
9. `BusinessTimeContext`: `1943`

Observed relationship counts:

1. `TAGGED_AS`: `2441`
2. `MATCHES_SCENE`: `296`
3. `INVOLVES_ACTOR`: `3621`
4. `TARGETS_OBJECT`: `3155`
5. `HAS_TIME_CONTEXT`: `2409`
6. `SCENE_HAS_ACTOR`: `291`
7. `SCENE_HAS_OBJECT`: `312`
8. `SCENE_HAS_TIME`: `333`

## Important Validation Result About “Free / Unassigned Nodes”

The remembered issue “many free nodes remained unassigned in the graph” does **not** match the current Neo4j state if “free node” means a zero-degree / isolated node.

Validation result:

1. total nodes: `7286`
2. total relationships: `15239`
3. zero-degree nodes: `0`
4. zero-degree nodes by label: none
5. actors without atom links: `0`
6. objects without atom links: `0`
7. time contexts without atom links: `0`
8. documents with only fully unassigned atoms: `0`

So in the current Neo4j graph:

1. there are **no isolated free nodes**
2. entity nodes are attached
3. atom nodes are attached
4. document nodes are attached

## What Still Looks “Unassigned” In A Weaker Sense

Although there are no isolated nodes, there are still many nodes that are structurally connected but not yet hit by the current corpus at the expected semantic layer.

Current counts:

1. atoms without `TAGGED_AS` module link: `0`
2. atoms without `MATCHES_SCENE` link: `1709`
3. atoms without both module and scene links: `0`
4. modules without atom tags: `38`
5. scenes without atom matches: `309`

This means:

1. every atom currently belongs to at least one module
2. most atoms are still **not** linked to a scene
3. many catalog modules and scenes still exist only as taxonomy coverage targets, not as matched evidence from current regulation atoms

So if someone informally says “there are many unassigned nodes”, the more precise statement is:

1. there are not many isolated nodes
2. there **are** many modules / scenes with no current atom evidence
3. there **are** many atoms with module coverage but no scene coverage yet

## Example Unhit Taxonomy Areas

Examples of modules currently without atom hits:

1. `BASE-01-03` `资质认定`
2. `BASE-01-04` `角色权限`
3. `BASE-02-02` `机构退出`
4. `BASE-02-03` `机构筹建`
5. `BASE-02-04` `机构合并`

Examples of scenes currently without atom hits:

1. `BASE-01-01-SCENE-01` `岗前培训`
2. `BASE-01-01-SCENE-02` `业务技能培训`
3. `BASE-01-01-SCENE-03` `考核组织与评定`
4. `BASE-01-01-SCENE-04` `培训档案管理`
5. `BASE-01-02-SCENE-01` `岗位轮换管理`

This looks like coverage sparsity, not graph breakage.

## Graph Export Consistency Status

During validation on 2026-05-14, a mismatch was found between local Neo4j and `data/processed/business_taxonomy_graph.json`.

That inconsistency has now been resolved by:

1. regenerating `data/processed/business_taxonomy_graph.json`
2. clearing and rebuilding Neo4j from the current classified file and taxonomy catalog

Current aligned counts for both Neo4j and `graph.json` are:

1. nodes: `3975`
2. edges / relationships: `9375`
3. zero-degree nodes: `0`

Current aligned node counts:

1. boards: `3`
2. categories: `18`
3. modules: `85`
4. scenes: `336`
5. atoms: `1942`
6. actors: `514`
7. objects: `341`
8. time contexts: `736`

Current aligned relationship counts:

1. `TAGGED_AS`: `2441`
2. `MATCHES_SCENE`: `296`
3. `INVOLVES_ACTOR`: `2601`
4. `TARGETS_OBJECT`: `1279`
5. `HAS_TIME_CONTEXT`: `1800`
6. `SCENE_HAS_ACTOR`: `202`
7. `SCENE_HAS_OBJECT`: `118`
8. `SCENE_HAS_TIME`: `199`

Operational implication:

1. Neo4j and `data/processed/business_taxonomy_graph.json` are now consistent
2. this aligned state should be treated as the current project baseline

## Current Graph Interpretation

The current graph should now be understood as:

1. a taxonomy scaffold of boards / categories / modules / scenes
2. an evidence layer of `BusinessAtom`
3. a normalized actor / object / time layer
4. a scene-entity summary layer

The current main weakness is not “free nodes everywhere”. The real weakness is:

1. sparse scene matching
2. incomplete evidence coverage for many modules / scenes
3. very high cardinality in object / time nodes, which suggests normalization quality should continue to improve

## Current Best Next Steps For Graph Work

The natural next graph improvements are now:

1. improve scene recall / matching so fewer atoms remain scene-unmatched
2. strengthen normalization quality for `what` and especially `when`
3. review whether `BusinessObject` and `BusinessTimeContext` nodes are over-fragmented by raw phrase variation
4. optionally add health metrics for:
   - scene match ratio
   - module hit ratio
   - top unmatched atom patterns
   - top sparse object/time aliases
5. keep avoiding pairwise atom-to-atom `SHARES_*` edge explosion unless a specific retrieval use case justifies it

## Publication / Safety Status

Repository publication safety status is unchanged in principle:

1. tracked source no longer contains the previously exposed DashScope key
2. `qwen.env` has been removed
3. `.gitignore` and `qwen.env.example` are present
4. the remaining real publication risk is still Git history

Required actions before public push still include:

1. rotate / revoke the previously exposed key
2. rewrite Git history or recreate a clean repository history

## Notes For The Next Thread

1. do not describe the graph as “only business taxonomy nodes” anymore
2. do not describe `src/entity_normalization.py` as future work; it already exists and is wired in
3. when discussing “free nodes”, distinguish:
   - isolated graph nodes = currently `0`
   - taxonomy nodes with no atom evidence = still many
   - atoms with no scene match = still many
4. current graph health is better than previously remembered, but scene coverage is still weak
5. Neo4j and `data/processed/business_taxonomy_graph.json` are currently aligned after a full refresh on 2026-05-14

## Ready-to-Paste Carry-over

> Continue the current financial regulation project. The main pipeline remains extraction -> manual review -> MySQL traceability/statistics -> conflict detection -> Neo4j graph -> recall/reasoning -> final compliance judgement. MySQL traceability and Streamlit MySQL browsing remain active. Conflict detection is still the first heuristic version. The important graph update is that the project is no longer only a business taxonomy tree: the current graph already includes normalized entity expansion for `who / what / when`. In `src/business_taxonomy_pipeline.py`, atoms already connect to `BusinessActor`, `BusinessObject`, and `BusinessTimeContext` via `INVOLVES_ACTOR`, `TARGETS_OBJECT`, and `HAS_TIME_CONTEXT`, and scenes already aggregate into those entities via `SCENE_HAS_ACTOR`, `SCENE_HAS_OBJECT`, and `SCENE_HAS_TIME`. `src/entity_normalization.py` already exists and is wired into graph expansion, so normalization is not just a future design note anymore. Direct atom-to-atom `SHARES_WHO` / `SHARES_WHAT` / `SHARES_WHEN` edges are still not present, which remains the preferred design choice for now. Local validation on 2026-05-14 confirmed no isolated free nodes in the current aligned graph baseline: Neo4j and `data/processed/business_taxonomy_graph.json` were both refreshed and now match at `3975` nodes, `9375` relationships, and zero-degree nodes `0`. Current aligned graph counts are `3` boards, `18` categories, `85` modules, `336` scenes, `1942` atoms, `514` actors, `341` objects, and `736` time contexts; relations include `2441` `TAGGED_AS`, `296` `MATCHES_SCENE`, `2601` `INVOLVES_ACTOR`, `1279` `TARGETS_OBJECT`, `1800` `HAS_TIME_CONTEXT`, `202` `SCENE_HAS_ACTOR`, `118` `SCENE_HAS_OBJECT`, and `199` `SCENE_HAS_TIME`. Coverage sparsity still remains: atoms without module tags `0`, atoms without scene matches `1709`, modules without atom evidence `38`, and scenes without atom evidence `309`. So the graph problem is no longer graph disconnection, but weak scene coverage and incomplete evidence hit rates across parts of the taxonomy. Remaining publication risk is still Git history: the old exposed DashScope key must be rotated and Git history must be cleaned or replaced before public GitHub push.

## Delta Update On 2026-05-15

The following changes happened after the 2026-05-14 carry-over and should now be treated as current state:

1. `src/business_taxonomy_app.py` had a temporary regression caused by earlier scripted replacements:
   - graph browsing page UI text became corrupted / mojibake in several sections
   - `render_batch_recall_extension()` was accidentally missing, causing a runtime `NameError`
2. that regression has now been fixed:
   - `render_batch_recall_extension()` has been restored
   - `render_browser_tab(...)` has been repaired to valid Chinese UI text
   - `render_scenario_tab(...)` has been repaired to valid Chinese UI text
   - `build_entity_summary_rows(...)` column names were restored to readable Chinese labels
   - `build_graph(...)` mode checks were restored to the intended values: `对比` / `场景精召回` / `模块宽召回`
3. validation completed after the repair:
   - `python -m py_compile src/business_taxonomy_app.py` passes
   - import smoke check confirms `render_browser_tab`, `render_scenario_tab`, and `render_batch_recall_extension` all exist again

## Current Graph UI State

The Streamlit graph browser remains the preferred in-project visualization path.

Current confirmed behavior:

1. business taxonomy nodes and entity-matching nodes are rendered in the same graph in `src/business_taxonomy_app.py`
2. the single graph includes:
   - category / module / scene nodes
   - entity nodes for `actor` / `object` / `time_context`
   - atom nodes
3. Streamlit-side entity colors are currently aligned in code as:
   - `BusinessActor`: blue family
   - `BusinessObject`: green family
   - `BusinessTimeContext`: purple family
4. the user does **not** want Neo4j Browser styling / GraSS management as the main path anymore

## Neo4j Browser Guidance Changed

The previous attempt to support Neo4j Browser custom styling turned out to be too cumbersome for the current user workflow.

As of 2026-05-15:

1. the project should no longer depend on Neo4j Browser GraSS style import for demonstration
2. the preferred Neo4j demonstration method is now:
   - run plain Cypher
   - return graph paths
   - rely on Neo4j Browser default per-label coloring
3. custom Neo4j Browser helper assets were intentionally removed at user request

Removed files:

1. `docs/neo4j_browser_entity_style.grass`
2. `docs/neo4j_business_taxonomy_queries.md`
3. `docs/neo4j_entity_three_views.cypher`

Also updated:

1. `docs/compliance_recall_guide.md` no longer references the removed Neo4j guide file

## Current Minimal Neo4j Demonstration Strategy

For future discussion, the supported simple Neo4j demonstration path is:

1. use plain Cypher only
2. return path objects, not only scalar tables
3. keep business nodes and entity nodes in one returned graph
4. do not require manual Browser style editing

Important practical note:

1. a previous Browser query only showed two nodes because the scene key string was broken by line wrapping during manual input
2. this was not a data problem
3. local validation confirmed that `BIZ-02-03-SCENE-01` does have entity links

Validated local counts for `BIZ-02-03-SCENE-01`:

1. actor links: `42`
2. object links: `13`
3. time links: `42`

So when Neo4j Browser only shows `BusinessModule -> BusinessScene`, first suspect query input / line break issues before suspecting graph loss.

## Meaning Of Repeated Atom Fields In Neo4j

The user explicitly asked about fields such as:

- `who`
- `who_terms`
- `who_terms_normalized`
- `when`
- `when_terms`
- `when_terms_normalized`

These should be interpreted as three different layers, not accidental duplication:

1. raw text layer:
   - `who`
   - `what`
   - `when`
2. extracted candidate term layer:
   - `who_terms`
   - `what_terms`
   - `when_terms`
3. normalized canonical entity layer:
   - `who_terms_normalized`
   - `what_terms_normalized`
   - `when_terms_normalized`

If a field already uses a canonical expression, all three layers may display the same value. That is expected and does **not** mean the graph is duplicated. The semantic distinction still matters:

1. raw text is for provenance / readability
2. extracted terms are for intermediate matching
3. normalized terms are for stable graph linkage and retrieval

For future simplification discussions, the likely display simplification path is:

1. keep raw fields for traceability
2. keep normalized term arrays for graph linkage
3. de-emphasize intermediate `*_terms` in UI if the user wants a cleaner display
