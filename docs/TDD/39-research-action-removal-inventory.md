# 39. 旧 ResearchAction 删除清单与引用扫描报告

> doc38 阶段 8（§12）的准备产物。状态：**准备完成（2026-08-23）；删除执行被 §12.2
> 前置条件第 1–3 项阻断**——它们指向同一事实：默认引擎尚未切为 `agent`，
> Shadow/小流量门槛尚未通过，回滚观察期尚未开始。本文让删除日在前置条件满足后
> 变成一次纯机械执行。

## 1. §12.2 前置条件审计

| # | 条件 | 状态 | 依据 |
| --- | --- | --- | --- |
| 1 | 默认 Research 引擎已切为 `agent` | ◐ 代码侧已闭合，待运营拨配置 | 阶段 7.5 切流接线完成（2026-08-23，doc38 §11.8）：路由入口三态放行、dispatch 经 `_dispatch_research` 接 `ResearchAgentPipeline`（冻结输入投影 + fail-loud）、恢复续跑经 `Harness.resume()`、agent rollout 短路不采样。`CHATBI_RESEARCH_EXECUTION_MODE=agent` 即全量生效；默认仍 `legacy`，切流动作与观察期是运营决策（§12.2 条件 2–3） |
| 2 | Shadow 和小流量切流门槛已通过 | ❌ 未满足 | `CHATBI_RESEARCH_EVAL_CONFIG` 为空 → 五项质量门槛全部 `not_configured` 阻断；无生产双跑样本；doc38 §11.5 第 3–6 条待运营数据 |
| 3 | 回滚观察期结束 | ❌ 未满足 | 未切流，观察期无从起算 |
| 4 | 无正在运行/等待恢复的 legacy Run | ◐ 执行日检查 | 仓库层面无法证明；删除日须先清残留（见 §7 runbook） |
| 5 | 历史 Run 查看不依赖加载旧 Action DTO | ✅ 结构满足 | api 层无 `ResearchState`/`EvidenceSnapshot` 反序列化点（全仓 grep 为零）；历史 derived_state/Trace/Artifact 按 JSON 直出。注意：旧 **RUNNING run 的恢复路径**反序列化旧 DTO——这属 §12.3.5 范畴（删除前完成/取消/终止），与第 4 条同门 |
| 6 | 新评测集覆盖旧阶段 2、3 业务不变量 | ⚠️ 映射已建立，执行日终审 | 不变量级覆盖由新测试套承接（§6 映射表）；场景级评测集 11 例聚焦归因业务场景。删除日按映射表逐条勾验"孤儿不变量=0"后再动手 |
| 7 | 直接 SQL 尚未与旧 Action 清理耦合 | ✅ 满足 | 阶段 9（受限 SQL）独立成章且未动工 |

结论：**现在不能删**。前置条件 1–3 本质是同一件事（还没切流）。本文以下内容使
删除动作在前置条件满足当天即可执行完毕。

## 2. 引用扫描总览

扫描方法：对 §12.3 点名的每个符号做全仓 `grep -rl --include=*.py`
（apps/scripts/tests，排除 `__pycache__`）。复核命令见 §9。

关键数字：

| 对象 | 行数 | 性质 |
| --- | --- | --- |
| `services/research/actions.py` | 1341 | 旧 Action 物化/合并引擎，`origin="research_action"` 唯一出现点 |
| `orchestration/pipeline/research.py` | 1351 | 旧 ResearchPipeline（含恢复/取消入口） |
| `models/dto/research.py` | 924 | 旧契约 DTO 全集 |
| `services/research/requirements.py` | 888 | `build_research_requirement`（路由期冻结旧 Requirement） |
| `services/research/hypotheses.py` | 313 | 旧假设归并（仅旧 pipeline 引用） |
| `services/research/policy.py` | 184 | 旧 Policy 服务 |
| `services/research/evidence.py` | 212 | 旧证据投影（仅旧 pipeline 引用） |
| `services/research/policy_rules.py` | 101 | 旧 Policy 规则 |
| `services/research/action_batches.py` | 63 | 旧批次校验 |
| `services/research/ports.py` | 14 | 旧 Policy Prompt 端口 |
| `adapters/prompts/research_policy.py` | - | 旧 Policy Prompt 构造器 |

新路径误伤检查（重要）：`hypotheses.py`/`evidence.py`/`requirements.py` 均**只被
旧 pipeline 或旧链路引用**；新 Harness 的假设管理走 `hypothesis_evaluator.py`，
证据走 `dto/research_agent.ResearchEvidence` + `state_snapshot.py`。全仓不存在
新路径对旧 Action 符号的运行时依赖；`orchestration/pipeline/research_agent.py`
仅在模块 docstring 里提及 `ResearchPolicyDecision`（措辞更新即可）。

## 3. 删除对象分组清单

### 3.1 DTO 层（§12.3.1）

- **整文件删除**：`apps/chatbi/models/dto/research.py`。"保留或迁移"的时间绑定/
  不可变筛选/Scope/版本快照/Evidence/Hypothesis 通用含义已由
  `dto/research_agent.py` 平行承接（新契约自带同名字段），因此整文件可删；
  唯一顺序约束：shadow 投影（`build_shadow_agent_requirement`）退役之前不得删
  （见 §4）。
- **导出清理**：`apps/chatbi/models/dto/__init__.py` 中 `ResearchActionType`、
  `Research{Compare,Breakdown,Drilldown,FilterFromResult,Contribution,
  ValidateHypothesis,Finish}Action`、`ResearchAppliedFilter`、
  `ResearchIterationRecord`、`ResearchActionFailure`、`ResearchPolicyDecision`
  等 re-export 行。
- **字段删除**：`allowed_actions`（仅存于旧 Requirement；新契约 docstring 已声明
  不包含它）；`max_actions_per_iteration`（旧 Budget 字段）。

### 3.2 服务层（§12.3.2）

- **整文件删除**：`actions.py`、`action_batches.py`、`policy.py`、
  `policy_rules.py`、`hypotheses.py`、`evidence.py`、`requirements.py`、
  `ports.py`、`adapters/prompts/research_policy.py`。
- **包出口调整**：`services/research/__init__.py` 移除
  `build_research_requirement` 导出，**保留** `SemanticQueryBuilder`。
- 附带消失：`materialize_research_action()`、
  `merge_research_action_requirements()`、`MaterializedResearchAction`、
  Action 指纹（`action_fingerprint`）与旧专用完成度函数。

### 3.3 编排层（§12.3.3）

- **整文件删除**：`orchestration/pipeline/research.py`（旧 `ResearchPipeline`，
  含 `origin="research_action"` 的豁免分支所在调用方）。
- `orchestration/agent/run_orchestrator.py`：`research_pipeline` 构造参数、
  dispatch 的 research 分支（`elif selected_mode == "research" ...`）、
  `ResearchPipelineError` 处理、`max_actions_per_iteration=` 传参（~line 426）。
- `orchestration/agent/composition.py`：`ResearchPipeline(+
  Dependencies)` 装配、`ResearchPolicy(...)` + `DefaultResearchPolicyPromptBuilder()`
  构建、`adapters.prompts.research_policy` import。
- `orchestration/pipeline/mode_router.py`：`build_research_requirement` 调用
  （routing 期冻结旧 Requirement 的唯一生产点）。切到 agent 后该分支改为构造
  新契约（配合 §1 条件 1 的"7.5 接线"）。
- `orchestration/pipeline/research_agent.py`：docstring 措辞更新。
- **可见性与 Prompt**：旧 Action 工具可见性白名单随 `reasoning_profile` 的
  RESEARCH 分支核对（该分支现已固定四工具，删除日仅需移除遗留注释）。

### 3.4 配置与环境变量（§12.3.6 一部分）

- `AgentConfig.max_actions_per_iteration`（`models/dto/agent.py`）。
- `Settings.CHAT_AGENT_RESEARCH_MAX_ACTIONS_PER_ITERATION`（`common/core/config.py`）。
- `orchestration/agent/service.py` 中该设置的映射行。
- `CHATBI_RESEARCH_EXECUTION_MODE` 的取值收敛：删除完成后 `legacy`/`shadow`
  两值失去意义，Literal 收敛为 `agent` 单值或整个移除（执行日决策）。

### 3.5 脚本与守卫

- `scripts/check_research_action_freeze.py`：**整个废弃**（冻结协议随被冻结物消亡）。
- `scripts/check_research_agent_dependencies.py`：引用旧符号作为守卫输入——改为
  **反向断言**（全仓不存在对这些符号的 import）。
- `scripts/run_plan_stage4_question_cases.py`：清理 `materialize_research_action`
  等引用或归档。
- `scripts/run_research_agent_eval.py`：`action_fingerprint` 口径改走
  `ResearchRunSnapshot` 指纹。

## 4. 过渡期设施处置：Shadow 栈退役决策点（执行日必须裁决）

`rollout.py` / `shadow.py` / `comparison.py` / `gates.py` +
orchestrator `_maybe_spawn_shadow` + 三个 `research_shadow_*` 配置字段的寿命
**绑定过渡期**：shadow 把旧路径当用户面、新路径当影子；一旦默认引擎切为
`agent`（前置条件 1），旧侧数据源（`execution_requirement.research_requirement`
冻结 + 旧 pipeline 持久化的 `research_state`/`research_evidence`）不再产生，
comparison 输入将恒为 `not_comparable`。

依赖顺序约束：**先退役 shadow 分支，再删 `requirements.py` 与
`dto/research.py`**（`build_shadow_agent_requirement` 依赖两者）。

建议方案（供执行日采纳或推翻）：

1. 切流当天即停用 spawn 分支（把 `research_execution_mode` 配置拨离 `shadow`），
   观察 agent 单独运行的回滚窗口；
2. 删除日移除 `shadow.py`/`rollout.py`/`comparison.py` 与 spawn 分支及三个配置
   字段；
3. `gates.py` 中**单侧可判定**的检查（无来源引用、引用通过率、跨 Run 引用、
   fallback/proof-failed 错误码扫描）不依赖 legacy 侧，可迁入 agent 持续质量
   监控（运行手册承载），其余双侧维度随比较器退役。

## 5. 新路径承接映射（§12.3.4 的"不是降低覆盖率"保证）

| 旧能力/不变量 | 新承接载体 | 新测试 |
| --- | --- | --- |
| 时间绑定/不可变筛选/Scope/版本快照/Budget 冻结语义 | `dto/research_agent.py` 同名契约 + `validate_query` | `test_research_agent_contracts.py` |
| Semantic Query 主路径（PROVEN 门禁、Scope/版本/时间一致性） | `semantic_query_builder.py` + `semantic_runtime.py` | `test_semantic_query_runtime.py`、Dataset 243 端到端（doc38 §6.8） |
| 四工具行为（查询/检视/计算/收尾） | `tool_context.py` 注册的四工具 | `test_research_tools.py` |
| 假设状态机与裁决 | `hypothesis_evaluator.py`（取代旧 `hypotheses.py` 归并） | `test_research_report_quality.py` |
| 完成度判定 + 报告七类硬校验 | `completion.py`/`report_validator.py`/`report_draft.py` | `test_research_report_quality.py`（22 项） |
| 提交边界/证据 DAG/恢复/取消 | `run_lifecycle.py`/`state_snapshot.py` | `test_research_state_recovery.py`（16 项） |
| 动态循环（取代 Action 计划式循环） | `research_agent.py` Function Calling 循环 | `test_research_agent_harness.py`（17 项） |
| 双跑隔离/比较/门禁/采样 | `shadow.py`/`comparison.py`/`gates.py`/`rollout.py` | `test_research_shadow.py`（29 项） |

### 测试归属四分类（§12.3.4）

- **直接删除（只验证旧 DTO/Action 形态）**：`test_research_phase2.py`、
  `test_research_phase3.py`；`test_research_contracts.py` 的旧契约用例
  （**执行日须逐用例核对**上表中是否有新套件未覆盖的不变量，有则先补再删）。
- **改写**：`test_mode_router.py` 中围绕 `allowed_actions`/旧 Requirement 冻结的
  断言改为新契约冻结断言；阶段 1 边界测试在引擎切换时同步更新。
- **迁移到评测脚本**：旧测试中以真实业务问题形式出现的用例，并入
  `research_agent_eval_cases.json` 场景集。
- **保持**：上表右列全部新套件不动。

## 6. 历史 Run 处置 runbook（§12.3.5）

1. 删除日前扫描 `chatbi_agent_run`：`execution_mode="research"` 且
   `status ∈ {running, 及一切可恢复中间态}` 的行清单；
2. 逐条处置：让用户侧自然完成、调旧取消入口、或运营明确终止；**处置清单
   （run id + 方式）落盘为阶段产物**；
3. 历史 `derived_state`/Trace/Result Artifact 保持 JSON 直出展示，**不为历史 Run
   建立旧 Action → 新 Tool 的运行时适配器**（§12.3.5 明令禁止）；
4. 恢复入口（旧 pipeline 内 recover/resume）随旧 pipeline 一并删除，删除后历史
   RUNNING 行只能查看不能续跑——这是第 1 步必须在删除前完成的原因。

## 7. 执行日操作顺序

前置：§12.2 第 1–3 项已满足（含 §1 条件 1 所述的"7.5 接线"已完成并单独回归）。

1. 清理 RUNNING legacy 残留（§6，产出处置记录）；
2. 停用并删除 shadow 双跑分支（§4 方案；解除对 `requirements.py`/`dto/research.py`
   的依赖）；
3. 删除编排层（`pipeline/research.py`、composition 装配、orchestrator dispatch
   与预算传参、mode_router 旧冻结调用）；
4. 删除服务层九文件 + 包出口调整；
5. 删除 `dto/research.py` + DTO 导出清理 + `max_actions_per_iteration` 三处配置；
6. 更新脚本与守卫（freeze 废弃、dependency guard 反向断言、eval 指纹口径）；
7. 全量回归：fast / plan / `tests/chatbi` / `tests/agent` 四套件 + §12.5 六条验收
   逐条勾验（其中"全仓不存在旧 Action 类型引用 / origin=research_action"用 §9
   命令机械证明）；
8. 文档收尾（37 号文档折叠标注、doc38 标记删除完成日期、运行手册/错误码/Trace
   说明、API 示例与测试数据）。

## 8. 扫描方法与复核命令

```bash
cd backend
for sym in ResearchActionType ResearchCompareAction ResearchBreakdownAction \
  ResearchDrilldownAction ResearchFilterFromResultAction ResearchContributionAction \
  ResearchValidateHypothesisAction ResearchFinishAction ResearchPolicyDecision \
  allowed_actions materialize_research_action merge_research_action_requirements \
  'origin="research_action"' max_actions_per_iteration action_fingerprint; do
  echo "== $sym"; grep -rln --include='*.py' "$sym" apps scripts tests | grep -v __pycache__
done
```

删除完成的判定：上述命令对全部符号输出为空（docstring 措辞除外，执行日一并清理）。
