# 兼容入口台账（COMPAT_LEDGER）

> 建立：2026-07-19（R0 批次）。规则见 `apps/AGENTS.md` §8.4：新增任何兼容导出/别名/转发路由必须同批登记本台账；台账外禁止新增。
> 删除一条兼容时：核对"删除条件"→ 删除代码与本行 → changelog 记录。
> 状态：`外部阻塞`（仓内已清偿，但外部发布包或调用方尚未迁移）/ `待删`（条件已满足待执行）/ `已清`（保留行一段时间供追溯，可定期清理）/ `已转正`（正式契约，不再作为兼容债务）。
>
> R6 审计口径：2026-07-23 通过 Python import hook 实际加载当前 `sqlbot_xpack`
> 全部可编译模块，记录其运行时导入路径；不能由本仓独立删除的入口统一标为
> `外部阻塞`，不再保留模糊的“活跃”状态。

## A. xpack 硬编码路径类（删除条件普遍依赖 sqlbot_xpack 发布包更新）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | `apps/terminology/` 整目录 | XPack 固定导入路径，查询别名实际指向 `headless_term` | sqlbot_xpack | xpack 改用 semantic 公开入口；R6 运行时审计仍命中 | R6 | 外部阻塞 |
| A2 | `apps/data_training/models/data_training_model.py` | Knowledge SQL 示例对象别名；依赖基线守卫仅对该已登记外部兼容文件豁免 | sqlbot_xpack | xpack 改用 knowledge 公开入口；R6 运行时审计仍命中 | R6 | 外部阻塞 |
| A3 | `apps/system/crud/assistant.py`、`assistant_manage.py` | 转发 Assistant 公开 Service（含 `get_assistant_ds` 兼容入口） | sqlbot_xpack | xpack 改用 assistant 公开入口；R6 运行时审计仍命中 | R6 | 外部阻塞 |
| A4 | `apps/system/models/system_model.py`、`user.py` | 同对象转发至 access_control / ai_model / assistant ORM；未被 xpack 使用的 `system_variable_model.py` 已删除 | sqlbot_xpack | xpack 与残余调用方切换；R6 运行时审计仍命中这两个文件 | R6 | 外部阻塞 |
| A5 | 原 `apps/system/schemas/permission.py` | 调用方已改用 `apps.access_control.permission`，旧文件删除 | 无 | 仓内与 xpack 运行时导入均为零 | R6 | 已清 |
| A6 | `apps/system/api/user.py` 内 Excel 适配与 create/edit 同名入口 | 转调 Access Control Service | sqlbot_xpack | xpack 改用 access_control 入口；R6 运行时审计仍命中 user/user_excel | R6 | 外部阻塞 |
| A7 | `apps/chat/models/chat_model.py` 旧 Chat 模型与 `AxisObj` 兼容导出 | 指向 `apps.chatbi.models` 与中立展示 Schema 的同一对象 | sqlbot_xpack | xpack 改用 ChatBI 公开模型与中立 Schema 路径；R6 运行时审计仍命中 | R6 | 外部阻塞 |

## B. 旧导入路径类（内部调用方，可自主清偿）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | `apps/chat/curd/chat.py` | 写侧转发已删；读侧已迁 `chatbi/api/legacy_read.py`；Dashboard 改走 ChatBI 公开面并销账基线 | 旧 Chat 内部、dashboard | R3-d 内部调用方清零 | R3-d | 已清 |
| B2 | `apps/chat/models/chat_model.py`（除 A7 外） | 运行时内部 Chat/ChatRecord/DTO 旧路径调用已全部切到 `apps.chatbi.models`；兼容契约测试与外部 xpack 依赖并入 A7 管理 | 历史 common 调用（已清）、兼容契约测试、sqlbot_xpack | 运行时内部调用清零；外部删除条件见 A7 | R3-d | 已清 |
| B3 | `apps/chat/task/external_datasource.py` | 旧路径已删除，能力随旧流程迁入 `chatbi/api/legacy_external_datasource.py` | `chatbi/api/legacy_chat_flow.py` | 旧 Chat 包内调用清零 | R3-d | 已清 |
| B4 | 原 `apps/capabilities/question_understanding.py`、`time_slots.py` | 调用方已改用 ChatBI understanding 子域，兼容导出随目录删除 | 无 | 调用方切 ChatBI 子域公开入口 | R4-d | 已清 |
| B5 | 原 `apps/capabilities/semantic/compile.py`、`retrieval.py` | 调用方已改用 planning / retrieval 公开服务，兼容函数删除 | 无 | 调用方切公开 Service | R4-d | 已清 |
| B6 | 原 `apps/capabilities/sql/`、`apps/capabilities/schemas.py` | validator、执行器与 ToolResult 使用既有 ChatBI 所有者；repair 迁入 Graph 适配器 | 无 | validator/repair 归位并切换剩余调用方后整目录删除 | R4-d | 已清 |
| B7 | 原 `apps/workflow/capabilities/adapters/intent_validation.py`、`time_slots.py` | Graph 已直接调用 ChatBI Service，兼容导出随目录迁移删除 | 无 | Graph 调用方已切换 | R4-c | 已清 |
| B8 | 原 `apps/retrieval/models/__init__.py`（包转发）、`apps/retrieval/schemas.py` | 调用方全部切至 `models/orm`、`models/dto`，旧文件删除 | 无 | 调用方核对为零 | R6 | 已清 |
| B9 | `apps/datasource/models/datasource.py` | Datasource 对象导入兼容；仓内运行时调用已清零 | sqlbot_xpack | xpack 改用 Datasource 公开契约或规范模型路径；R6 运行时审计仍命中 | R6 | 外部阻塞 |
| B10 | `apps/mcp/mcp.py → apps.chat.composition` | MCP 已改用 `apps.chatbi.composition` | apps/mcp | 调用方已切换 | R3-d | 已清 |

## C. 兼容 API 路由类（删除条件依赖外部调用方确认）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | `/system/terminology`（`apps/semantic/api/legacy_terms.py`） | 转发 SemanticTermService，旧响应字段转换 | 外部 API 调用方 | 缺少生产调用量证据；取得连续一个发布周期零调用证据后删除 | R6 | 外部阻塞 |
| C2 | `/chat`、`/chat/agent`、`/graph` 旧路由前缀 | `chatbi/api/router.py` 聚合入口保持；Agent router 已于 R4-b 归入 `chatbi/api/interactions.py`，Graph router 由最外层注入 | 前端、外部集成 | 前端和外部集成完成新路径迁移并提供调用清单 | R6+ | 外部阻塞 |
| C3 | `/system/data-training`、`/recommended_problem` 旧前缀 | Knowledge 所有权下的兼容路径 | 前端 | 前端完成路径迁移并提供调用清单 | R6 | 外部阻塞 |
| C4 | `/system/aimodel`、`/system/assistant`、`/user`、`/login` 等原路径 | 所有权已迁，路径长期保持（视为正式路径） | 前端 | 不删除（转正） | — | 已转正 |

## D. 壳目录与待确认项

| # | 路径 | 内容 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D1 | `apps/terminology/`（见 A1） | 3 文件 13 行 | 同 A1；R6 运行时审计确认 xpack 阻塞 | R6 | 外部阻塞 |
| D2 | `apps/data_training/`（见 A2） | 3 文件 21 行 | 同 A2；R6 运行时审计确认 xpack 阻塞 | R6 | 外部阻塞 |
| D3 | 原 `apps/settings/models`、`schemas` 旧术语模型 | 无运行时引用，R6 已删除；文件下载接口迁至 `interfaces/http` | 无 | R6 | 已清 |
| D4 | `apps/swagger/i18n.py` 最小转发 | 实现与 locales 已迁 `common/interfaces` | xpack 改用 `common.interfaces.i18n`；R6 运行时审计仍命中旧路径 | R6 | 外部阻塞 |
| D5 | 原 `apps/template/` | YAML 读取与生成器已迁入 `chatbi/adapters/prompts/`，旧目录删除 | R4-d 迁入 `chatbi/adapters/prompts/` | R4-d | 已清 |
| D6 | 原 `apps/system/` 参数模块（api/parameter、composition、repository、services） | 已迁入 `apps/platform_config/`，旧模块删除 | R4-d 迁至平台配置归属目录 | R4-d | 已清 |

## E. 迁移期新增（R1 起在此登记，初始为空）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | `apps/chatbi/services/__init__.py` | 兼容导出已删除，文件只标识子域导航；调用方均从具体子域导入 | 无 | 调用方改从子域包导入 | R4-d | 已清 |
| E2 | 原 5 套流式 DTO 旧名、7 个旧 Client 协议名、5 个旧 LangChain 客户端类名 | 旧别名删除，调用方统一使用 ModelMessage / ModelStreamChunk / GenerationModelClient / LangChainGenerationModelClient | 无 | 调用方改用共享名 | R4-d | 已清 |
| E3 | 原 `QuestionModelService = StructuredModelService` 别名 | 旧名删除，调用方统一使用 `StructuredModelService` | 无 | 调用方改用 `StructuredModelService` | R4-d | 已清 |
| E4 | 原 `understanding/graph_contracts.py` 中 `QuestionIntentValidationService` 薄包装类 | Graph 与测试已直接调用 `validate_intent()`、`intent_retry_feedback()` | 无 | B7 删除时一并清偿 | R4-c | 已清 |
| E5 | 原 `QueryService = GuardedQueryService` 别名 | 旧名删除；运行时调用方统一使用 `GuardedQueryService`，Agent 工具局部 Protocol 不属于该别名 | 无 | 调用方改用新名 | R4-d | 已清 |
| E6 | 原 `SemanticQueryService = SemanticCompilationService` 别名 | 旧名删除，调用方统一使用 `SemanticCompilationService` | 无 | 同上 | R4-d | 已清 |
| E7 | 原 `GenerationSchemaContextService = SchemaContextService` 别名 | 旧名删除，调用方统一使用 `SchemaContextService` | 无 | 调用方改用新名 | R4-d | 已清 |
| E8 | `apps/dashboard/models/dashboard_model.py` | Dashboard ORM/DTO 分层后的旧模型入口最小转发 | sqlbot_xpack | xpack 改用 Dashboard 规范模型路径；R6 运行时审计仍命中 | R6 | 外部阻塞 |
