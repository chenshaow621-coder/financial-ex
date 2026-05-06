# 2026-04-08 符号化场景迁移与最小回归

## 1. 文档范围

这份文档合并了原先的：

- `symbolic_scene_migration_progress_2026-04-08.md`
- `symbolic_scene_migration_progress_2026-04-08_round2.md`
- `minimal_regression_checklist_symbolic_bank_note_2026-04-08.md`

目标是把“迁移进度、设计原则、验证结果、最小回归命令”收口到同一份文档里。

## 2. 当前对 `停止召回` 的理解

当前系统中的 `停止召回` 更准确地表示为：

- `ready_to_judge`

它表示：

- 当前场景 requirement 已基本闭合
- 证据闭环程度已达到“可以进入具体业务判断”的前提
- 不再需要继续做召回补证

它不等于：

- 已完成最终业务判断
- 已可以无条件输出最终合规结论

因此当前明确拆成两层：

1. `召回是否可以停止`
2. `最终业务判断是否可以稳定给出`

## 3. 当前设计原则

当前更稳妥的路线是：

- 符号规则负责 requirement 闭环、缺口阻断和 `停止召回`
- 最终业务判断层负责纯符号推断、神经符号混合或规则结论 + 模型解释

控制器中仍保留模式切换，旧的 LLM 路径没有被强制删除。

## 4. 已完成的后层符号化

目前已完成三层后判断的符号化替换：

- `recall judgement`
- `atom analysis`
- `final judgement`

## 5. 已迁移业务场景

### 5.1 银行汇票提示付款

当前可显式识别：

- `身份证明材料`
- `解讫通知/进账单`
- `提示付款签章动作`
- `背书/委托收款动作`
- `现金字样`
- `现金支取边界`
- `未开户个人持票人边界`

当前效果：

- partial evidence -> `继续召回`
- requirement 补齐后 -> `停止召回`
- atom analysis 可输出字段级缺口

### 5.2 商业汇票（承兑/贴现）

当前 scene profile：

- `commercial_bill_acceptance_discount`

拆成两个子路径：

- `承兑路径`
- `贴现路径`

承兑路径 requirement：

- `提示承兑定义`
- `提示承兑动作`
- `提示承兑期限`
- `提示承兑回单`
- `承兑/拒承时限`
- `拒绝承兑证明`
- `附条件承兑边界`

贴现路径 requirement：

- `贴现申请动作`
- `贴现主体资格`
- `真实交易背景`
- `贴现申请材料`
- `转让背书动作`

### 5.3 银行本票

本轮新增第三个高频场景：

- `bank_note_lifecycle`

当前 requirement：

- `银行本票定义/适用范围`
- `本票种类/现金字样`
- `法定记载事项/效力`
- `出票申请与签发流程`
- `现金银行本票签发边界`
- `见票即付/提示付款期限`
- `开户持票人提示付款`
- `未开户个人现金支取`
- `委托提示付款`
- `背书转让边界`
- `逾期提示付款救济`
- `银行本票退款路径`
- `挂失止付边界`
- `失票法院证明救济`

## 6. 银行本票场景的新增设计

本轮对复杂法规没有停留在“关键词命中 -> requirement 命中”，而是做了三类扩展：

1. 显式化 `允许`、`禁止`、`边界`
2. 按业务链拆成前置动作、限制条件、救济路径
3. 引入 `scene_profile_ready_with_conflict`

其中：

- `scene_profile_ready_with_conflict` 表示 requirement 已闭环
- 但允许条款与禁止条款同时命中
- 这类问题不在召回层硬下结论，而是移交最终业务判断层

## 7. 最终业务判断层拆分

本轮新增：

- `src/formal_final_judgement_catalog.py`

拆分后的职责：

- `formal_final_judgement_catalog.py`
  - 维护最终业务判断规则目录
- `formal_rule_engine.py`
  - 负责构建 facts、编译规则、命中规则、输出最终结论卡片

已完成的接口整理：

- `scene_profile_id` 已进入最终业务判断 facts
- `matched_rules` / `decision_trace` 已显式带出 `scene_profile`

这为后续扩展下列能力留下接口：

- 通用 final judgement 规则
- scene-specific final judgement override
- 神经符号混合 final judgement

## 8. 当前验证结果

### 8.1 银行汇票提示付款

已验证：

- 字段级缺口可以正常输出
- 补齐真实证据后可进入 `scene_profile_ready`
- 特定问法下可单独报出 `背书/委托收款动作`

### 8.2 商业汇票（承兑/贴现）

贴现问题验证结果：

- partial evidence -> 缺口可细化到 `真实交易背景`、`转让背书动作`
- full evidence -> `scene_profile_ready` -> `停止召回=True`

承兑问题验证结果：

- partial evidence -> 缺口可细化到 `提示承兑期限`、`承兑/拒承时限`
- full evidence -> `scene_profile_ready` -> `停止召回=True`

### 8.3 银行本票

#### 出票申请 + 法定记载事项

- partial evidence -> `继续召回`
- full evidence -> `scene_profile_ready` -> `停止召回=True`

#### 现金兑付 + 委托提示付款 + 逾期/失票救济

- partial evidence -> 可细化输出：
  - `现金银行本票签发边界`
  - `逾期提示付款救济`
  - `挂失止付边界`
  - `失票法院证明救济`
- full evidence -> `scene_profile_ready_with_conflict` -> `停止召回=True`

这里的 `with_conflict` 是预期行为，因为同一问题中同时命中了：

- 现金本票允许挂失止付
- 非现金本票不得挂失止付

## 9. 当前代码落点

本轮主要改动文件：

- `src/formal_scene_catalog.py`
- `src/formal_final_judgement_catalog.py`
- `src/formal_rule_engine.py`
- `src/business_taxonomy_app.py`

新增 demo preset：

- `bank_note_cash_remedy`

## 10. 最小回归测试

### 10.1 目的

这组回归只覆盖本轮新增和重构的最小闭环，不追求全量验收。

最小通过标准：

1. `py_compile` 通过
2. `bank_note_lifecycle` 可在召回判断中被命中
3. “出票申请 + 法定记载事项” synthetic smoke 可从 partial 到 `scene_profile_ready`
4. “现金兑付 + 委托提示付款 + 逾期/失票救济” synthetic smoke 可从 partial 到 `scene_profile_ready_with_conflict`
5. 最终业务判断层输出中的 `matched_rules` / `decision_trace` 带 `scene_profile`

### 10.2 执行前提

- 工作目录：`D:\pythonProject-financial ex`
- Python：`.\venv\Scripts\python.exe`
- 如需页面联调：本机 Neo4j 已启动

### 10.3 T1 静态编译

```powershell
Set-Location "D:\pythonProject-financial ex"
.\venv\Scripts\python.exe -m py_compile .\src\formal_scene_catalog.py .\src\formal_rule_engine.py .\src\formal_final_judgement_catalog.py .\src\business_taxonomy_app.py
```

通过标准：

- 退出码为 `0`
- 无语法错误

### 10.4 T2 命令行 dry-run

```powershell
Set-Location "D:\pythonProject-financial ex"
.\venv\Scripts\python.exe .\src\compliance_recall_controller.py --question '未在银行开立存款账户的个人持注明“现金”字样的银行本票向出票银行支取现金，是否可以委托他人提示付款；若超过提示付款期限未获付款或票据丧失，还应如何办理？' --query '银行本票' --who '持票人' --recall-judgement-mode symbolic --atom-analysis-mode symbolic --final-judgement-mode symbolic --max-rounds 0 --dry-run --output '.\data\processed\bank_note_symbolic_dry_run.json'
```

通过标准：

- 成功生成 `data/processed/bank_note_symbolic_dry_run.json`
- `final_decision = DRY_RUN`
- `final_conclusion.conclusion = 证据不足待补召回`
- `final_conclusion.matched_rules` 包含 `bank_note_lifecycle`

### 10.5 T3 命令行多轮符号闭环

```powershell
Set-Location "D:\pythonProject-financial ex"
.\venv\Scripts\python.exe .\src\compliance_recall_controller.py --question '未在银行开立存款账户的个人持注明“现金”字样的银行本票向出票银行支取现金，是否可以委托他人提示付款；若超过提示付款期限未获付款或票据丧失，还应如何办理？' --query '银行本票' --who '持票人' --recall-judgement-mode symbolic --atom-analysis-mode symbolic --final-judgement-mode symbolic --max-rounds 2 --output '.\data\processed\bank_note_symbolic_live.json'
```

通过标准：

- 命令成功完成
- 成功生成 `data/processed/bank_note_symbolic_live.json`
- `final_conclusion.matched_rules` 包含 `bank_note_lifecycle`

说明：

- 这条不要求当前知识库一定输出 `停止召回`
- 只要链路跑通且结果可解释即可

### 10.6 T4 Synthetic smoke: 出票申请 + 法定记载事项

验证目标：

- partial evidence -> `继续召回`
- full evidence -> `scene_profile_ready` -> `停止召回=True`

建议 smoke 内容：

- `银行本票申请书`
- `收妥款项签发银行本票`
- `六项法定记载事项`
- `缺少任一事项银行本票无效`

### 10.7 T5 Synthetic smoke: 现金兑付 + 委托提示付款 + 逾期/失票救济

验证目标：

- partial evidence -> `继续召回`
- partial case 能细化报出缺口：
  - `现金银行本票签发边界`
  - `逾期提示付款救济`
  - `挂失止付边界`
  - `失票法院证明救济`
- full evidence -> `scene_profile_ready_with_conflict` -> `停止召回=True`

建议 smoke 内容：

- `现金字样`
- `未开户个人持票人支取现金`
- `委托收款 / 被委托人`
- `提示付款期限最长不得超过2个月`
- `超过提示付款期限后的说明与请求付款`
- `现金本票可挂失止付`
- `非现金本票不得挂失止付`
- `人民法院票据权利证明`

### 10.8 T6 最终业务判断层拆分校验

检查文件：

- `src/formal_final_judgement_catalog.py`
- `src/formal_rule_engine.py`

通过标准：

- catalog 中存在通用 final judgement rule specs
- `formal_rule_engine.py` 中不再直接维护原先那整块最终判断规则常量
- `build_symbolic_final_conclusion(...)` 通过 catalog 构建规则
- 输出中的 `matched_rules` / `decision_trace` 带 `scene_profile`

### 10.9 T7 页面入口 smoke

```powershell
Set-Location "D:\pythonProject-financial ex"
.\venv\Scripts\python.exe -m streamlit run .\src\business_taxonomy_app.py
```

通过标准：

- 页面可以打开
- `业务场景模板` 中能看到 `银行本票现金兑付与救济`
- 选择该 preset 后页面不报错

### 10.10 最小结果记录模板

```text
T1 PASS
T2 PASS
T3 PASS
T4 PASS
T5 PASS
T6 PASS
T7 PASS
```

## 11. 下一步建议

当前更顺的顺序是：

1. 继续迁第四个高频业务场景
2. 给银行本票补一个 `背书转让/退款` 方向的 demo smoke case
3. 开始在 `formal_final_judgement_catalog.py` 上尝试第一批 scene-specific final judgement override
