# 兼容入口台账（COMPAT_LEDGER）

> 建立：2026-07-19（R0 批次）。规则见 `apps/AGENTS.md` §8.4：新增任何兼容导出/别名/转发路由必须同批登记本台账；台账外禁止新增。
> 删除一条兼容时：核对"删除条件"→ 删除代码与本行 → changelog 记录。
> 状态：`活跃`（仍被调用）/ `待删`（条件已满足待执行）/ `已清`（保留行一段时间供追溯，可定期清理）。

## A. xpack 硬编码路径类（删除条件普遍依赖 sqlbot_xpack 发布包更新）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | `apps/terminology/` 整目录 | XPack 固定导入路径，查询别名实际指向 `headless_term` | sqlbot_xpack | xpack 改用 semantic 公开入口 | R6 | 活跃 |
| A2 | `apps/data_training/models/data_training_model.py` | Knowledge SQL 示例对象别名 | sqlbot_xpack | xpack 改用 knowledge 公开入口 | R6 | 活跃 |
| A3 | `apps/system/crud/assistant.py`、`assistant_manage.py` | 转发 Assistant 公开 Service（含 `get_assistant_ds` 兼容入口） | sqlbot_xpack | xpack 改用 assistant 公开入口 | R6 | 活跃 |
| A4 | `apps/system/models/system_model.py`、`user.py`、`system_variable_model.py` | 同对象转发至 access_control / ai_model / assistant ORM | sqlbot_xpack + 历史导入路径 | xpack 与残余调用方切换 | R6 | 活跃 |
| A5 | `apps/system/schemas/permission.py` | 转发 `apps.access_control.permission` | 历史导入路径 | 调用方核对为零 | R6 | 活跃 |
| A6 | `apps/system/api/user.py` 内 Excel 适配与 create/edit 同名入口 | 转调 Access Control Service | sqlbot_xpack | xpack 改用 access_control 入口 | R6 | 活跃 |
| A7 | `apps/chat/models/chat_model.py` 中 `AxisObj` 兼容导出 | 指向中立展示 Schema 同一对象 | sqlbot_xpack | xpack 改用中立 Schema 路径 | R6 | 活跃 |

## B. 旧导入路径类（内部调用方，可自主清偿）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | `apps/chat/curd/chat.py` | `save_*`、创建/列表/重命名等兼容转发 → ChatBI Service | 旧 Chat 内部、dashboard（基线在案） | 调用方切换公开 Service | R3-d | 活跃 |
| B2 | `apps/chat/models/chat_model.py`（除 A7 外） | Chat/ChatRecord/DTO 同对象兼容导出 | `common/utils/command_utils.py`、`common/audit/schemas/log_utils.py` 等 | common 调用方改用 `apps.chatbi.models` | R3-d | 活跃 |
| B3 | `apps/chat/task/external_datasource.py`（原 legacy_dependencies.py 的存留部分；模型运行时已转正为 `apps/ai_model/runtime.py`，本地数据源包装已删除） | 旧流程外部/本地数据源装配与外部 Schema 读取 | `apps/chat/task/llm.py` | run_task 收口（R3-b/c）或 Assistant 外部契约重构 | R3-c | 活跃 |
| B4 | `apps/capabilities/question_understanding.py`、`time_slots.py` | ChatBI 同一身份兼容导出 | 旧测试与历史导入 | 调用方切 `apps.chatbi.services` | R4-d | 活跃 |
| B5 | `apps/capabilities/semantic/compile.py`、`retrieval.py` | 兼容函数转发至 Semantic/ChatBI 公开服务 | 旧测试与历史导入 | 同上 | R4-d | 活跃 |
| B6 | `apps/capabilities/sql/`、`apps/capabilities/schemas.py` | R1-c 起 schemas/validator/permission/execution_gateway 均为**纯转发桩**（实现已迁入 chatbi models/execution/adapters）；executor.py、repair.py 为 workflow 调用方持有的活代码 | apps/workflow、旧测试 | executor/repair 随 R4-c 归位后整目录删除 | R4-d | 活跃 |
| B7 | `apps/workflow/capabilities/adapters/intent_validation.py`、`time_slots.py` | ChatBI Service 同一身份兼容导出 | Graph 历史导入 | Graph 调用方切换 | R4-c | 活跃 |
| B8 | `apps/retrieval/models/__init__.py`（包转发）、`apps/retrieval/schemas.py` | 旧路径转发新 orm/dto（守卫已禁运行时新用） | 历史导入 | 调用方核对为零 | R6 | 活跃 |
| B9 | `apps/datasource/models/datasource.py` | Datasource 对象导入兼容 | 历史导入 | 调用方核对为零 | R6 | 活跃 |
| B10 | `apps/mcp/mcp.py → apps.chat.composition` | MCP 会话创建仍经旧 chat 组装模块 | apps/mcp | R4-a 后改 `chatbi.composition` | R4-a | 活跃 |

## C. 兼容 API 路由类（删除条件依赖外部调用方确认）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | `/system/terminology`（`apps/semantic/api/legacy_terms.py`） | 转发 SemanticTermService，旧响应字段转换 | 外部 API 调用方 | 外部调用量确认为零 | R6 | 活跃 |
| C2 | `/chat`、`/chat/agent`、`/graph` 旧路由前缀 | R4-a 起由 `chatbi/api` 内部保持 | 前端、外部集成 | 前端与外部迁移新路径（如规划） | R6+ | 活跃 |
| C3 | `/system/data-training`、`/recommended_problem` 旧前缀 | Knowledge 所有权下的兼容路径 | 前端 | 前端迁移 | R6 | 活跃 |
| C4 | `/system/aimodel`、`/system/assistant`、`/user`、`/login` 等原路径 | 所有权已迁，路径长期保持（视为正式路径） | 前端 | 不删除（转正） | — | 已转正 |

## D. 壳目录与待确认项

| # | 路径 | 内容 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D1 | `apps/terminology/`（见 A1） | 3 文件 13 行 | 同 A1 | R6 | 活跃 |
| D2 | `apps/data_training/`（见 A2） | 3 文件 21 行 | 同 A2 | R6 | 活跃 |
| D3 | `apps/settings/models`、`schemas` 旧术语模型 | 无运行时引用，疑似未提交工作区内容（P1 登记待确认） | 与作者确认后删除 | R6 | 待确认 |
| D4 | `apps/swagger/` | 接口国际化支持 | 迁 common/接口层 | R6 | 活跃 |
| D5 | `apps/template/` | 提示词模板（**被 chatbi/adapters 活跃引用，非转发**） | R4-d 迁入 `chatbi/adapters/prompts/` | R4-d | 活跃 |
| D6 | `apps/system/` 残余（api/parameter、composition、repository、services） | 平台参数新实现暂驻 system | R4-d 迁至平台配置归属目录 | R4-d | 活跃 |

## E. 迁移期新增（R1 起在此登记，初始为空）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | `apps/chatbi/services/__init__.py` | R1-a 起降级为兼容导出层（旧符号 → 子域包新位置） | agent/workflow/chat/composition 等既有调用方 | 调用方改从子域包导入 | R4-d | 活跃 |
| E2 | 5 套流式 DTO 旧名别名（`{X}Message`/`{X}ModelChunk` → streaming 共享 DTO）、7 个旧 Client 协议名别名（generation/ports.py）、5 个旧 LangChain 客户端类名别名（各 adapter 文件） | R2 统一产生 | llm.py、旧测试、adapters | 调用方改用共享名 | R4-d | 活跃 |
| E3 | `understanding/model_invocation.py` 中 `QuestionModelService = StructuredModelService` 别名 | 类改名的旧名兼容 | question.py、composition、旧测试 | 调用方改用 `StructuredModelService` | R4-d | 活跃 |
| E4 | `understanding/graph_contracts.py` 中 `QuestionIntentValidationService` 薄包装类 | 意图校验函数化后的旧类形态兼容 | `workflow/capabilities/adapters/intent_validation.py`（B7）、旧测试 | B7 删除时一并清偿 | R4-c | 活跃 |
| E5 | `execution/guarded_query_service.py` 中 `QueryService = GuardedQueryService` 别名 | 类改名旧名兼容 | agent/workflow/legacy 调用方 | 调用方改用新名 | R4-d | 活跃 |
| E6 | `planning/semantic_compilation.py` 中 `SemanticQueryService = SemanticCompilationService` 别名 | 类改名旧名兼容 | 同上 | 同上 | R4-d | 活跃 |
| E7 | `generation/context/schema_context.py` 中 `GenerationSchemaContextService = SchemaContextService` 别名 | 类改名旧名兼容 | composition、llm.py、旧测试 | 调用方改用新名 | R4-d | 活跃 |
