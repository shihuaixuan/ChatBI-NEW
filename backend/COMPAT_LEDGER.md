# 兼容入口台账（COMPAT_LEDGER）

> 建立：2026-07-19（R0 批次）。规则见 `apps/AGENTS.md` §8.4：新增任何兼容导出/别名/转发路由必须同批登记本台账；台账外禁止新增。
> 删除一条兼容时：核对"删除条件"→ 删除代码与本行 → changelog 记录。
> 状态：`外部阻塞`（仓内已清偿，但外部发布包或调用方尚未迁移）/ `待删`（条件已满足待执行）/ `已清`（保留行一段时间供追溯，可定期清理）/ `已转正`（正式契约，不再作为兼容债务）。
>
> 2026-07-23 最终决策：项目不再依赖 `sqlbot_xpack`。原先仅为该包保留的兼容入口
> 已直接删除，非本地认证、自定义提示词和 License 管理同时下线。

## A. Xpack 硬编码路径类

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| A1–A7 | terminology、data_training、system、chat、datasource、dashboard、swagger 的 Xpack 固定入口 | 原 Xpack 导入兼容 | 无 | 项目移除 Xpack 依赖 | R6 | 已清 |

## B. 旧导入路径类（内部调用方，可自主清偿）

| # | 路径 | 内容 | 调用方 | 删除条件 | 目标阶段 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | `apps/chat/curd/chat.py` | 写侧转发已删；读侧已迁 `chatbi/api/legacy_read.py`；Dashboard 改走 ChatBI 公开面并销账基线 | 旧 Chat 内部、dashboard | R3-d 内部调用方清零 | R3-d | 已清 |
| B2 | 原 `apps/chat/models/chat_model.py` | 运行时内部调用切到 `apps.chatbi.models`，Xpack 兼容入口最终删除 | 无 | 项目移除 Xpack 依赖 | R6 | 已清 |
| B3 | `apps/chat/task/external_datasource.py` | 旧路径已删除，能力随旧流程迁入 `chatbi/api/legacy_external_datasource.py` | `chatbi/api/legacy_chat_flow.py` | 旧 Chat 包内调用清零 | R3-d | 已清 |
| B4 | 原 `apps/capabilities/question_understanding.py`、`time_slots.py` | 调用方已改用 ChatBI understanding 子域，兼容导出随目录删除 | 无 | 调用方切 ChatBI 子域公开入口 | R4-d | 已清 |
| B5 | 原 `apps/capabilities/semantic/compile.py`、`retrieval.py` | 调用方已改用 planning / retrieval 公开服务，兼容函数删除 | 无 | 调用方切公开 Service | R4-d | 已清 |
| B6 | 原 `apps/capabilities/sql/`、`apps/capabilities/schemas.py` | validator、执行器与 ToolResult 使用既有 ChatBI 所有者；repair 迁入 Graph 适配器 | 无 | validator/repair 归位并切换剩余调用方后整目录删除 | R4-d | 已清 |
| B7 | 原 `apps/workflow/capabilities/adapters/intent_validation.py`、`time_slots.py` | Graph 已直接调用 ChatBI Service，兼容导出随目录迁移删除 | 无 | Graph 调用方已切换 | R4-c | 已清 |
| B8 | 原 `apps/retrieval/models/__init__.py`（包转发）、`apps/retrieval/schemas.py` | 调用方全部切至 `models/orm`、`models/dto`，旧文件删除 | 无 | 调用方核对为零 | R6 | 已清 |
| B9 | 原 `apps/datasource/models/datasource.py` | Datasource 旧模型入口随 Xpack 依赖删除 | 无 | 项目移除 Xpack 依赖 | R6 | 已清 |
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
| D1 | 原 `apps/terminology/`（见 A1） | Xpack 术语壳目录已删除 | 项目移除 Xpack 依赖 | R6 | 已清 |
| D2 | 原 `apps/data_training/`（见 A2） | Xpack SQL 示例壳目录已删除 | 项目移除 Xpack 依赖 | R6 | 已清 |
| D3 | 原 `apps/settings/models`、`schemas` 旧术语模型 | 无运行时引用，R6 已删除；文件下载接口迁至 `interfaces/http` | 无 | R6 | 已清 |
| D4 | 原 `apps/swagger/i18n.py` 最小转发 | 实现与 locales 已迁 `common/interfaces`，Xpack 转发已删除 | 项目移除 Xpack 依赖 | R6 | 已清 |
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
| E8 | 原 `apps/dashboard/models/dashboard_model.py` | Dashboard 的 Xpack 旧模型入口已删除 | 无 | 项目移除 Xpack 依赖 | R6 | 已清 |
