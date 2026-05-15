# 合规闭环召回总说明

## 1. 文档范围

这份文档合并了原先的：

- `compliance_recall_loop_handover.md`
- `compliance_recall_execution_guide.md`

目的只有两个：

1. 说明当前“合规闭环召回”模块已经做到哪里
2. 给出统一的启动、验证、读结果和排障入口

## 2. 当前模块状态

当前系统的基础能力已经具备：

- 原子知识抽取完成
- 业务分类体系已接入
- 已构建“业务分类为关系边、原子知识为节点”的图谱
- 已有场景级召回页面
- 已能做 `模块宽召回 -> 场景精召回 -> 主体细筛`

当前重点不再是“能不能召回”，而是：

- 召回后的证据是否足够支撑合规判断
- 如果不够，应该继续往哪个方向补召回
- 整个补召回过程能否被页面和报告解释清楚

## 3. 本轮接入内容

### 3.1 新增闭环召回控制器

核心文件：

- `src/compliance_recall_controller.py`

核心职责：

1. 读取 3 份提示词文档
2. 基于业务分类和原子知识做初始召回
3. 对当前证据集做集合级判断
4. 对关键 atom 做最小可执行颗粒度检查
5. 沿 A-F 六个方向扩召回
6. 生成 `compliance_summary`
7. 在条件满足时生成 `final_conclusion`

### 3.2 接入的提示词文档

- `data/raw/单条原子版-增强稿.docx`
- `data/raw/原子知识最小可执行颗粒度判断提示词.docx`
- `data/raw/合规判断主提示词.docx`

提示词通过运行时读取 docx 的方式接入，后续修改提示词时不必同步改 Python 代码。

### 3.3 页面接入

核心文件：

- `src/business_taxonomy_app.py`

页面当前支持：

- `闭环召回` 区块
- `合规问题` / `业务 query` / `主体关键词` 输入
- `业务 query` 多行输入，每行可独立触发召回与推理
- 批量问题输入，每行支持 `问题 | query | who`
- 多 query / 多问题按页面 `并行数` 配置并行执行
- `继续召回` 按钮
- 最终结论卡
- 审查摘要卡
- 缺口总卡
- 逐轮解释区

### 3.4 LLM 失败回退

如果 Qwen 调用失败，系统不会直接中断，而会自动回退到本地结果：

- `LLM 正常`：显示真实闭环结果
- `LLM 异常`：显示 `LLM_ERROR`，同时保留本地解释结果

## 4. 闭环流程

### 4.1 业务定位

输入：

- `question`
- `query`，可为单个 query，也可在页面中按多行拆成多个 query 并行运行
- `who`

系统先定位：

- 业务大类
- 业务模块
- 业务场景

### 4.2 初始召回

初始召回综合以下来源：

1. 场景精召回
2. 模块宽召回
3. 问题焦点词语义命中
4. 主体关键词命中

### 4.3 集合级判断

判断当前证据集是否：

- 还需要继续召回
- 缺哪些维度
- 推荐往哪些方向扩召回
- 已经足够生成最终判断

### 4.4 atom 级颗粒度判断

目标是避免只命中抽象条款，而没命中真正可执行的：

- 材料
- 时限
- 例外
- 禁止
- 审核动作

### 4.5 A-F 六个扩召回方向

- `A` 业务向下召回
- `B` 同层横向召回
- `C` 法规结构邻接召回
- `D` 规则语义补全召回
- `E` 例外禁止优先召回
- `F` 上下位规范补充召回

### 4.6 停止条件

闭环执行到以下条件之一即停止：

- `llm_stop`
- `no_new_candidates`
- `max_rounds`
- `llm_connection_error`
- `dry_run`

## 5. 启动前检查

### 5.1 Neo4j

当前页面默认连接：

- URI：读取环境变量 `NEO4J_URI`，未设置时使用本机 `bolt://localhost:7687`
- 用户名：读取环境变量 `NEO4J_USER`
- 密码：读取环境变量 `NEO4J_PASSWORD`，不要写入仓库

至少保证：

- Neo4j 服务已启动
- 图谱已导入成功
- 页面能读取 `BusinessBoard / BusinessCategory / BusinessModule / BusinessScene / BusinessAtom`

图查询可参考：


### 5.2 Qwen

Qwen 配置来自：

- `qwen.env`

至少确认：

- `DASHSCOPE_API_KEY` 已配置
- `DASHSCOPE_BASE_URL` 正常
- `QWEN_MODEL / QWEN_REASONING_MODEL` 可用

当前经验是：

- VPN 开启时更容易出现 `LLM_ERROR`
- 关闭 VPN 后更容易正常连通

## 6. 推荐执行路径

建议按这个顺序：

1. 先确认 Neo4j 页面可打开
2. 再用 `dry-run` 验证本地召回、摘要卡和本地兜底结论
3. 再跑 live Qwen 闭环，观察 `can_make_final_compliance_judgement`
4. 若 ready，再重点观察最终结论卡稳定性

## 7. 常用启动命令

### 7.1 Streamlit 页面

```powershell
Set-Location "D:\pythonProject-financial ex"
python -m streamlit run ".\src\business_taxonomy_app.py"
```

页面入口：

- `业务场景演示`
- 页面下半部分的 `闭环召回`

### 7.2 命令行 dry-run

```powershell
Set-Location "D:\pythonProject-financial ex"
python .\src\compliance_recall_controller.py --question "未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？" --query "银行汇票" --who "持票人" --max-rounds 0 --dry-run --output ".\data\processed\compliance_recall_loop_report.json"
```

适合验证：

- 场景是否命中
- 初始召回是否合理
- 审查摘要卡是否正常
- 最终结论兜底卡是否正常

### 7.3 命令行 live 闭环

```powershell
Set-Location "D:\pythonProject-financial ex"
python .\src\compliance_recall_controller.py --question "未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？" --query "银行汇票" --who "持票人" --max-rounds 2 --output ".\data\processed\compliance_recall_loop_live_report.json"
```

适合验证：

- Qwen 是否连通
- 闭环补召回是否正常
- `can_make_final_compliance_judgement` 是否可能变成 `True`
- 最终结论卡是否进入真实生成模式

## 8. 推荐验证 case

优先使用这个 case：

- 问题：`未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？`
- `query`：`银行汇票`
- `who`：`持票人`

原因：

- 当前图谱和召回链路对它较成熟
- 能同时观察材料、签章、现金支取、时限、限制条件几类证据

## 9. 核心输出文件

常见输出：

- `data/processed/compliance_recall_loop_report.json`
- `data/processed/compliance_recall_loop_live_report.json`
- `data/processed/compliance_recall_loop_ui_smoke.json`

重点字段：

- `final_decision`
- `judge_final_decision`
- `can_make_final_compliance_judgement`
- `stop_reason`
- `final_recall_atom_count`
- `compliance_summary`
- `final_conclusion`

## 10. 关键字段怎么读

### 10.1 召回与轮次

- `initial_recall_atom_count`：初始证据数量
- `final_recall_atom_count`：本次执行结束时的最终证据数量
- `rounds`：本次闭环执行的迭代轮次

注意：

- `最终召回数量变多` 不等于 `已经足够下结论`
- 更关键的是 `can_make_final_compliance_judgement`

### 10.2 决策字段

- `final_decision = DRY_RUN`
  - 没有真正进入 LLM 闭环
  - `final_conclusion` 是本地兜底结果

- `final_decision = LLM_ERROR`
  - Qwen 本次调用失败
  - 系统自动回退为本地结果

- `final_decision = 继续召回`
  - 当前证据还不够闭环
  - 如果轮次允许，系统还希望继续补召回

- `final_decision = 停止召回`
  - 当前证据集已经足够，或已不建议继续补召回
  - 仍需继续看 `can_make_final_compliance_judgement`

- `can_make_final_compliance_judgement = True`
  - 当前已达到“可以尝试生成最终结论”的门槛

### 10.3 页面三层阅读顺序

当前建议按这个顺序读页面：

1. 最终结论卡
2. 审查摘要卡
3. 缺口总卡
4. 缺口诊断表
5. 逐轮解释

## 11. 最终结论层

当前支持的结论类型包括：

- `可办理`
- `不可办理`
- `有条件可办理`
- `需补材料后办理`
- `需人工复核`
- `证据不足待补召回`

当前生成策略：

- ready 且模型可用时，优先走 Qwen 生成 `final_conclusion`
- `DRY_RUN`、`LLM_ERROR` 或尚未 ready 时，自动回退到本地安全结论

## 12. 缺口诊断口径

当前缺口按三层结构组织：

- `gap_type`
- `impact_scope`
- `severity`

主要缺口类型：

- `例外/禁止缺口`
- `主体范围缺口`
- `定义范围缺口`
- `判断条件缺口`
- `材料缺口`
- `流程动作缺口`
- `时限阈值缺口`
- `规范依据缺口`
- `事实核验缺口`
- `其他缺口`

影响范围：

- `全局阻断`
- `子结论阻断`
- `人工复核项`

严重程度：

- `阻断型`
- `关键型`
- `复核型`

### 12.1 三段总卡

`compliance_summary` 中当前已新增：

- `gap_summary_cards`

固定三张卡：

- `致命缺口总卡`
- `可人工复核缺口总卡`
- `仅风险提示总卡`

映射逻辑：

- `全局阻断` -> `致命缺口总卡`
- `子结论阻断` -> `可人工复核缺口总卡`
- `人工复核项` -> `仅风险提示总卡`

## 13. 常见问题排查

### 13.1 页面打不开或无数据

先检查：

- Neo4j 是否启动
- 账号密码是否与代码一致
- 图谱是否已导入

### 13.2 一直是 `LLM_ERROR`

先检查：

- 是否开启 VPN
- `qwen.env` 是否配置正确
- 模型名是否可用
- 当前网络是否能访问 DashScope

### 13.3 一直停在 `max_rounds`

先检查：

- 轮次是否太低
- 每轮证据上限是否太紧
- 当前 case 是否本来就缺关键法规依据

### 13.4 最终结论不稳定

先检查：

- 是否真的 `can_make_final_compliance_judgement = True`
- 直接依据是否抓对
- 是否存在限制条款与例外条款并存但未完成取舍
- 是否应该落到 `需人工复核`

## 14. 当前建议的后续工作

当前更合理的顺序是：

1. 先做 live case 验证，确认 Qwen 连通性和稳定性
2. 再观察 ready case 下最终结论卡是否稳定
3. 再做最终结论质量评估
4. 再把“结论 -> 操作项 / 审核清单 / 风险处置清单”的映射补上
5. 抽取完成后固定执行人工核查：按百分比抽取典型样本，并将 `is_ambiguous=true` 的模糊原子单独复核
6. 用 `人工通过复核率 = 通过样本数 / 已判定样本数` 观察抽取与分类质量趋势
7. 最后再做 benchmark、阈值调参和 prompt 微调

## 15. 一句话状态

当前系统已经从“可解释的闭环召回”推进到“可生成最终合规结论卡的闭环查验系统”；后续重点不再是“有没有结论生成器”，而是“最终结论在真实 case 下是否稳定、是否可审计、是否能映射到业务动作”。
