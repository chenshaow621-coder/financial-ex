# MySQL Traceability Migration

## 1. 目标

当前项目的主链路已经跑通：

1. `run_stage1_2_ner.py` 输出 `phase1_entities_checkpoint.xlsx`
2. `main.py` 输出 `legal_atoms_v4_final.xlsx`
3. `business_taxonomy_pipeline.py` 输出分类结果、taxonomy catalog、taxonomy recall report，并加载 Neo4j
4. `compliance_recall_controller.py` 输出闭环召回报告 JSON

现在新增的 MySQL 侧车层，目标不是替换 Neo4j，而是把每一步的中间产物和最终产物落成可审计、可追踪、可查询的结构化记录。

建议职责划分：

- `Excel / JSON`：继续作为人可读导出物
- `Neo4j`：继续负责图谱关系和召回
- `MySQL`：作为 extraction -> classification -> recall -> compliance judgement 的事实库

## 2. 新增能力

新增文件：

- `src/mysql_traceability.py`

新增两类能力：

1. 历史产物回填
2. 流水线运行后自动同步到 MySQL

## 3. 表设计

核心表分两层。

第一层：批次和产物

- `trace_batches`：一次同步批次
- `trace_artifacts`：一个物理产物文件

第二层：按业务阶段拆开的明细表

- `phase1_chunks`
- `legal_atoms`
- `taxonomy_modules`
- `taxonomy_scenes`
- `scene_matches`
- `taxonomy_recall_queries`
- `compliance_recall_reports`
- `compliance_recall_rounds`
- `sample_review_rows`

粒度约定：

- 一次同步命令对应一个 `trace_batches`
- 一个输出文件对应一个 `trace_artifacts`
- `classified_atoms` 文件会在同一个 artifact 下同时写入：
  - `legal_atoms`
  - `scene_matches`

这样后面查链路时，可以稳定地从 batch -> artifact -> rows 往下追。

## 4. 产物映射

当前默认产物和 MySQL 表的映射关系：

| 文件 | artifact_type | 明细表 |
| --- | --- | --- |
| `phase1_entities_checkpoint.xlsx` | `phase1_entities` | `phase1_chunks` |
| `legal_atoms_v4_final.xlsx` | `legal_atoms` | `legal_atoms` |
| `business_taxonomy_catalog.xlsx` | `taxonomy_catalog` | `taxonomy_modules`, `taxonomy_scenes` |
| `legal_atoms_business_taxonomy.xlsx` | `classified_atoms` | `legal_atoms`, `scene_matches` |
| `business_taxonomy_recall_report.json` | `taxonomy_recall_report` | `taxonomy_recall_queries` |
| `compliance_recall*.json` | `compliance_recall_report` | `compliance_recall_reports`, `compliance_recall_rounds` |
| `sample_review_checklist_*.xlsx` | `sample_review_checklist` | `sample_review_rows` |

## 5. 历史回填

先初始化表：

```powershell
Set-Location "D:\pythonProject-financial ex"
python .\src\mysql_traceability.py `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace `
  --init-only
```

然后对 `data/processed/` 当前默认产物做一次回填：

```powershell
Set-Location "D:\pythonProject-financial ex"
python .\src\mysql_traceability.py `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace `
  --auto-discover `
  --batch-label backfill-20260507
```

说明：

- 默认会跳过相同 `artifact_path + sha1` 的重复导入
- 如果明确要重复导入同一份文件，追加 `--force-reimport`

## 6. 指定文件回填

如果你要回填指定产物，而不是默认扫描：

```powershell
python .\src\mysql_traceability.py `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace `
  --phase1-file ".\data\processed\phase1_entities_checkpoint.xlsx" `
  --atoms-file ".\data\processed\legal_atoms_v4_final.xlsx" `
  --taxonomy-catalog-file ".\data\processed\business_taxonomy_catalog.xlsx" `
  --classified-file ".\data\processed\legal_atoms_business_taxonomy.xlsx" `
  --taxonomy-recall-file ".\data\processed\business_taxonomy_recall_report.json" `
  --compliance-report ".\data\processed\compliance_recall_loop_report.json" `
  --sample-review-file ".\data\processed\sample_review_checklist_20260428_153322.xlsx" `
  --batch-label manual-backfill-20260507
```

## 7. 增量同步

四个脚本已经加了可选开关 `--mysql-sync`：

- `src/run_stage1_2_ner.py`
- `src/main.py`
- `src/business_taxonomy_pipeline.py`
- `src/compliance_recall_controller.py`

### 7.1 Phase1/2

```powershell
python .\src\run_stage1_2_ner.py `
  --output phase1_entities_checkpoint.xlsx `
  --mysql-sync `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace
```

### 7.2 原子抽取

```powershell
python .\src\main.py `
  --output legal_atoms_v4_final.xlsx `
  --mysql-sync `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace
```

### 7.3 业务分类 + taxonomy recall

```powershell
python .\src\business_taxonomy_pipeline.py `
  --skip-neo4j `
  --mysql-sync `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace
```

这个步骤会同步：

- `business_taxonomy_catalog.xlsx`
- `legal_atoms_business_taxonomy.xlsx`
- `business_taxonomy_recall_report.json`

如果同时使用 `--force-extract`，也会顺带同步 `legal_atoms_v4_final.xlsx`。

### 7.4 合规闭环召回

```powershell
python .\src\compliance_recall_controller.py `
  --question "未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？" `
  --query "银行汇票" `
  --who "持票人" `
  --max-rounds 0 `
  --dry-run `
  --output ".\data\processed\compliance_recall_loop_report.json" `
  --mysql-sync `
  --mysql-host 127.0.0.1 `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <your_password> `
  --mysql-database financial_trace
```

## 8. 可选参数

所有脚本共用的 MySQL 参数：

- `--mysql-sync`
- `--mysql-url`
- `--mysql-host`
- `--mysql-port`
- `--mysql-user`
- `--mysql-password`
- `--mysql-database`
- `--mysql-batch-label`
- `--mysql-notes`
- `--mysql-force-reimport`

如果你已经习惯用完整连接串，也可以只传：

```powershell
--mysql-url "mysql+pymysql://root:password@127.0.0.1:3306/financial_trace?charset=utf8mb4"
```

## 9. 推荐查询方式

建议先做三个视角的 SQL 视图或查询：

1. 批次视角
   - 看一次回填或一次运行同步了哪些 artifact
2. 原子视角
   - 从 `legal_atoms.atom_id` 追到所属 artifact、分类标签、场景挂接
3. 查验视角
   - 从 `compliance_recall_reports` 追到 `compliance_recall_rounds`

例如先看最近批次：

```sql
select batch_id, batch_label, source_dir, created_at
from trace_batches
order by created_at desc
limit 20;
```

再看某个批次下有哪些产物：

```sql
select artifact_type, artifact_name, row_count, created_at
from trace_artifacts
where batch_id = '<batch_id>'
order by created_at asc;
```

## 10. 下一步建议

当前这一步已经能解决“把 Excel/JSON 迁到 MySQL 并保持链路透明”的核心问题。下一步更值得做的是：

1. 在 Streamlit 页面加一个 “MySQL 同步 / 批次浏览” 区块
2. 为 `trace_batches` 和 `trace_artifacts` 增加筛选页
3. 做几个面向业务的视图：
   - `v_atom_trace`
   - `v_scene_trace`
   - `v_compliance_trace`
4. 把人工复核结果和闭环召回结果关联起来，形成可审计样本链路

这样 MySQL 会从“回填仓库”变成你整个抽取与召回查验过程的运营台账。
