# Business Taxonomy Neo4j Queries

## 1. Top-Level Overview

```cypher
MATCH (b:BusinessBoard)-[:HAS_CATEGORY]->(c:BusinessCategory)-[:HAS_MODULE]->(m:BusinessModule)
RETURN b, c, m
LIMIT 200
```

## 2. Focus On Payment Settlement

```cypher
MATCH (b:BusinessBoard {name: "业务管理类"})-[:HAS_CATEGORY]->(c:BusinessCategory {name: "二、支付结算"})-[:HAS_MODULE]->(m:BusinessModule)
RETURN b, c, m
```

## 3. Focus On A Concrete Module

```cypher
MATCH (m:BusinessModule {code: "BIZ-02-03"})<-[:TAGGED_AS]-(a:BusinessAtom)
RETURN m, a
LIMIT 300
```

`BIZ-02-03` is `业务管理类 > 二、支付结算 > 票据结算`.

## 4. Scene-To-Module View

```cypher
MATCH (s:BusinessScene {name: "银行汇票"})<-[:HAS_SCENE]-(m:BusinessModule)
OPTIONAL MATCH (m)<-[:TAGGED_AS]-(a:BusinessAtom)
RETURN s, m, a
LIMIT 300
```

Note:
Current graph design tags atoms to `BusinessModule`, not directly to `BusinessScene`.
So scene-level traversal is a module-level broad recall.

## 5. Broad Recall Count For A Transaction Scene

```cypher
MATCH (s:BusinessScene {name: "银行汇票"})<-[:HAS_SCENE]-(m:BusinessModule)<-[:TAGGED_AS]-(a:BusinessAtom)
RETURN s.name AS scene, m.label_path AS module, count(DISTINCT a) AS broad_recall
```

## 6. More Precise Recall For "银行汇票"

```cypher
MATCH (a:BusinessAtom)-[:TAGGED_AS]->(m:BusinessModule {code: "BIZ-02-03"})
WITH a, m,
     coalesce(a.content_original, "") AS content,
     coalesce(a.what, "") AS what,
     coalesce(a.how, "") AS how
WHERE content CONTAINS "银行汇票"
   OR what CONTAINS "银行汇票"
   OR how CONTAINS "银行汇票"
RETURN a
LIMIT 200
```

## 7. Precise Recall Distribution By Rule Type

```cypher
MATCH (a:BusinessAtom)-[:TAGGED_AS]->(m:BusinessModule {code: "BIZ-02-03"})
WITH a,
     coalesce(a.content_original, "") AS content,
     coalesce(a.what, "") AS what,
     coalesce(a.how, "") AS how
WHERE content CONTAINS "银行汇票"
   OR what CONTAINS "银行汇票"
   OR how CONTAINS "银行汇票"
RETURN a.rule_type AS rule_type, count(*) AS atom_count
ORDER BY atom_count DESC
```

## 8. Precise Recall Sample Rows

```cypher
MATCH (a:BusinessAtom)-[:TAGGED_AS]->(m:BusinessModule {code: "BIZ-02-03"})
WITH a,
     coalesce(a.content_original, "") AS content,
     coalesce(a.what, "") AS what,
     coalesce(a.how, "") AS how
WHERE content CONTAINS "银行汇票"
   OR what CONTAINS "银行汇票"
   OR how CONTAINS "银行汇票"
RETURN a.id AS atom_id,
       a.rule_type AS rule_type,
       a.article_reference AS article_reference,
       a.what AS what,
       a.how AS how,
       a.content_original AS content
ORDER BY atom_id
LIMIT 50
```

## 9. Top Modules By Atom Count

```cypher
MATCH (m:BusinessModule)<-[:TAGGED_AS]-(a:BusinessAtom)
RETURN m.label_path AS module, count(a) AS atom_count
ORDER BY atom_count DESC
LIMIT 20
```
