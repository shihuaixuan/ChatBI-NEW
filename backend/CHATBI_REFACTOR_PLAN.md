# ChatBI 减重与 Conversation 拆分执行计划

> 状态：R6-a 至 R6-d 已实施；ChatBI 减重阶段完成
> 范围：后端模块拆分、内部实现收敛和架构守卫  
> 不包含：数据库表改名、前端接口路径调整、微服务拆分

## 1. 背景

当前 `apps/chatbi` 已完成依赖边界和内部结构重构，但模块仍包含 172 个 Python 文件，同时承担以下职责：

- 会话和问数记录生命周期；
- 问题理解、数据源选择和语义规划；
- SQL、答案、图表和推荐问题生成；
- SQL 权限、校验、执行和结果保存；
- Agent 和 Graph 两种执行编排；
- 现有 `/chat` 接口及 `legacy_*` 实现。

其中，会话和问数记录已经具备独立的数据、仓储、Service 和业务规则，适合拆分为 `apps/conversation`。理解、规划、生成和执行仍是一次问数流程的连续阶段，不拆成独立顶层模块。

当前 `chatbi/api/legacy_*.py` 已全部删除（R6-b/c/d）。会话创建与联合删除由 ChatBI composition 与 `ChatApplicationService` 正式组装；主问数走 Agent/Graph，辅助能力走 Generation Service。

## 2. 目标

本计划完成后应达到以下结果：

1. `apps/conversation` 成为 `chat`、`chat_record`、`chat_log` 三类数据的唯一所有者。
2. ChatBI 只负责一次问数的应用编排，不再拥有会话 ORM、仓储和状态机。
3. Agent、Graph 和辅助分析流程共同调用 ChatBI Service，不维护重复业务实现。
4. 当前 `/chat`、`/chat/agent`、`/graph` 路径和前端契约保持不变。
5. `chat`、`chat_record`、`chat_log` 表名和字段保持不变。
6. 删除 `chatbi/api/legacy_*.py`，不新增等价兼容文件。
7. Dashboard、MCP、Access Control 和 Workflow Engine 不再依赖 ChatBI 的会话内部实现。

## 3. 非目标

本计划不处理以下事项：

- 不将后端拆成多个部署服务；
- 不把 understanding、planning、generation、execution 拆成顶层模块；
- 不重命名数据库表和字段；
- 不修改现有 HTTP 路径；
- 不重写 Agent 或 Graph 运行机制；
- 不顺带重构与 Conversation 拆分无关的前端代码；
- 不通过重新导出旧路径长期保留兼容层。

## 4. 目标结构

```text
apps/
├── conversation/
│   ├── __init__.py
│   ├── composition.py
│   ├── errors.py
│   ├── models/
│   │   ├── dto/
│   │   │   ├── conversation.py
│   │   │   ├── chat_record.py
│   │   │   └── chat_history.py
│   │   └── orm/
│   │       ├── chat.py
│   │       ├── chat_record.py
│   │       └── chat_log.py
│   ├── repository/
│   │   ├── conversation_repository.py
│   │   ├── chat_record_repository.py
│   │   └── sqlmodel/
│   │       ├── conversation_repository.py
│   │       └── chat_record_repository.py
│   ├── services/
│   │   ├── conversation.py
│   │   ├── chat_record.py
│   │   ├── history_query.py
│   │   └── deletion.py
│   └── resource_scope.py
└── chatbi/
    ├── api/
    ├── adapters/
    ├── models/
    │   ├── dto/
    │   └── orm/
    │       └── agent_run.py
    ├── services/
    │   ├── understanding/
    │   ├── planning/
    │   ├── generation/
    │   └── execution/
    ├── orchestration/
    │   ├── agent/
    │   └── graph/
    └── composition.py
```

依赖方向固定为：

```text
HTTP / MCP / ChatBI 编排
        │
        ├──> Conversation 公共 Service
        ├──> Semantic 公共 Service
        ├──> Datasource 公共 Service
        ├──> Retrieval 公共 Service
        └──> Workflow Engine 公共端口

Conversation 不得反向依赖 ChatBI、Agent、Graph 或 Workflow Engine。
```

## 5. 数据和职责所有权

| 内容 | 目标所有者 | 说明 |
| --- | --- | --- |
| `chat` | Conversation | 会话基本信息、所属用户和工作空间、数据集绑定快照 |
| `chat_record` | Conversation | 单次问数记录、执行状态和最终结果 |
| `chat_log` | Conversation | 单次问数各步骤的历史日志 |
| `chatbi_agent_run` 等 Agent 表 | ChatBI | Agent 执行状态、步骤、轨迹和澄清 |
| Graph Run、事件和检查点 | Workflow Engine | 通用工作流运行数据 |
| 结果 Artifact | Workflow Engine | 大结果正文及清理任务 |
| 数据集绑定解析 | ChatBI Planning | 需要协调 Semantic 和 Datasource |
| SQL、答案、图表生成 | ChatBI Generation | 一次问数的生成阶段 |
| SQL 权限和执行 | ChatBI Execution | 一次问数的安全执行阶段 |
| 会话自身删除 | Conversation | 只删除 Conversation 拥有的数据 |
| Agent、Graph、Artifact 联合清理 | ChatBI | 由应用层协调多个模块，不进入 Conversation |

## 6. 文件迁移边界

### 6.1 迁入 Conversation

| 当前文件 | 目标位置 |
| --- | --- |
| `chatbi/models/orm/chat.py` | `conversation/models/orm/chat.py` |
| `chatbi/models/orm/chat_record.py` | `conversation/models/orm/chat_record.py` |
| `chatbi/models/orm/chat_log.py` | `conversation/models/orm/chat_log.py` |
| `chatbi/models/dto/conversation.py` | `conversation/models/dto/conversation.py` |
| `chatbi/models/dto/chat_record.py` | `conversation/models/dto/chat_record.py` |
| `chatbi/models/dto/chat_history.py` | `conversation/models/dto/chat_history.py` |
| `chatbi/repository/conversation_repository.py` | `conversation/repository/conversation_repository.py` |
| `chatbi/repository/chat_record_repository.py` | `conversation/repository/chat_record_repository.py` |
| `chatbi/repository/sqlmodel/conversation_repository.py` | `conversation/repository/sqlmodel/conversation_repository.py` |
| `chatbi/repository/sqlmodel/chat_record_repository.py` | `conversation/repository/sqlmodel/chat_record_repository.py` |
| `chatbi/services/conversation/conversation_service.py` | `conversation/services/conversation.py` |
| `chatbi/services/conversation/chat_record_service.py` | `conversation/services/chat_record.py` |
| `chatbi/resource_scope.py` | `conversation/resource_scope.py` |

以下内容从现有文件中拆出后归 Conversation：

- `apps/chatbi/errors.py` 中的 Conversation 和 ChatRecord 错误；
- `legacy_read.py` 中只读取会话、记录、日志和用量的逻辑；
- `deletion_service.py` 中只删除 `chat`、`chat_record`、`chat_log` 的逻辑；
- 当前 `apps/chatbi/models/__init__.py` 中属于 Conversation 的公开导出。

### 6.2 继续留在 ChatBI

| 文件或能力 | 保留原因 |
| --- | --- |
| `models/orm/agent_run.py` | 属于 Agent 执行状态，不是会话自身数据 |
| `repository/sqlmodel/agent_run_repository.py` | 持久化 Agent Run、Step、Trace 和 Clarification |
| `repository/sqlmodel/recommended_question_history.py` | 是推荐问题生成阶段的数据来源 |
| `models/dto/generation_history.py` | 是模型生成上下文，不是会话接口契约 |
| `services/conversation/dataset_binding.py` | 依赖 Semantic 与 Datasource，属于问数规划 |
| 当前联合删除流程 | 需要协调 Conversation、Agent、Graph 和 Artifact |
| `/chat/start` 和 `/chat/assistant/start` | 需要解析数据集绑定并协调多个模块 |
| `/chat/record/*/analysis`、`predict` | 属于问数结果的后续生成流程 |
| `/chat/recommend_questions/*` | 属于推荐问题生成流程 |

### 6.3 接口位置

R6-a 至 R6-c 保留 `chatbi/api/conversations.py` 作为 `/chat` 接口入口。它只能调用公开 Service，不得直接查询 Conversation ORM。

本计划不要求将 HTTP 文件迁入 Conversation。ChatBI 是问数应用入口，可以继续聚合会话、执行和流式响应；模块所有权以数据和业务规则为准，不以 URL 前缀为准。

## 7. 全局约束

### 7.1 单向依赖

- ChatBI 可以依赖 Conversation。
- Conversation 不得依赖 ChatBI。
- Workflow Engine 不得依赖 Conversation 或 ChatBI。
- Access Control、Dashboard、MCP 等调用方只使用 Conversation 公共 DTO 和 Service。
- 任何模块不得导入 `conversation/models/orm`。

### 7.2 事务边界

跨模块不得共享 Session 或数据库事务。联合删除采用可重试的顺序协调：

1. 校验会话所有权；
2. 登记 Artifact 清理任务；
3. 清理 Graph 和 Agent 运行数据；
4. 调用 Conversation 删除自身数据；
5. 处理已登记的 Artifact 正文清理。

每一步必须可重复执行。中途失败时保留可重试状态，不通过宽泛异常捕获返回成功。

### 7.3 契约稳定

- HTTP 路径、请求字段和响应字段保持不变；
- 数据库表名、字段名和历史数据保持不变；
- `execution_type`、状态值和历史日志枚举保持可读；
- 不新增旧模块路径的重新导出；
- 必须保留兼容时，同批登记 `COMPAT_LEDGER.md` 并写明删除条件。

### 7.4 组合入口

- Conversation 只有一个 `composition.py`；
- ChatBI 通过 Conversation 的组合入口获得 Service；
- ChatBI Service 不接收数据库 Session；
- API 层不直接访问 Conversation Repository 或 ORM。

## 8. 执行批次

### R6-a：建立 Conversation 数据所有权

#### 目标

建立 `apps/conversation`，迁移会话、记录和日志的模型、仓储、状态机与基础业务规则。当前接口、表结构和执行方式保持不变。

#### 实施步骤

1. 创建 Conversation 包、公共入口、错误文件和组合根。
2. 迁移 `Chat`、`ChatRecord`、`ChatLog` ORM，保持表名和字段完全不变。
3. 迁移 Conversation、ChatRecord、ChatHistory DTO。
4. 迁移 ConversationRepository、ChatRecordRepository 及 SQLModel 实现。
5. 迁移 ConversationService 和 ChatRecordService。
6. 从 ChatBI 错误文件迁出 Conversation、ChatRecord 错误。
7. 将会话创建改为接收已经解析完成的绑定和推荐问题，不在 Conversation 内调用 Semantic、Datasource 或 Knowledge。
8. 将会话自身删除实现放入 Conversation；ChatBI 保留跨 Agent、Graph、Artifact 的删除协调器。
9. 更新 ChatBI Generation、Planning、Execution、Agent 和 Graph 对 ChatRecordService 的依赖。
10. 更新 MCP、Access Control 和其他调用方的导入。
11. 更新 Alembic 元数据加载入口，保证迁移环境可以发现三个表。
12. 将 Workflow Engine 中依赖 ChatRecord 的业务历史投影迁回 ChatBI 扩展层，保证 Workflow Engine 不依赖业务模块。
13. 删除 ChatBI 原文件，不保留转发别名。
14. 更新架构守卫、模块文档和测试路径。

#### 验收条件

- `apps/conversation` 不导入 `apps.chatbi`、Workflow Engine、Agent 或 Graph；
- ChatBI 不再拥有 `Chat`、`ChatRecord`、`ChatLog` ORM；
- 所有 ChatRecord 写入都通过 Conversation 的 ChatRecordService；
- `alembic current` 不产生新迁移，三个表的元数据名称不变；
- `/chat/list`、`/chat/start`、重命名、删除、历史读取响应保持不变；
- Graph 和 Agent 均可创建、更新并完成 ChatRecord；
- 架构测试禁止其他模块导入 Conversation ORM；
- 后端完整测试通过。

#### 回滚边界

本批不修改数据库结构。出现问题时只回滚代码移动和导入路径，不执行数据库降级，不处理数据恢复。

### R6-b：迁移历史读取并删除 `legacy_read.py`

> 状态：已实施（2026-07-24）

#### 目标

将仍在使用的读取能力归入明确所有者，删除 734 行的 `legacy_read.py`。

#### 实施步骤

1. 在 Conversation 建立 HistoryQueryService，统一处理：
   - 会话及记录列表；
   - 已保存的数据、预测数据和图表配置；
   - ChatLog 历史和 Token 用量；
   - 最近问题所需的会话记录查询。
2. 将用户所有权校验放入 Conversation Service 或查询仓储，不在 API 中重复 SQL。
3. 将 `data_live` 的重新执行逻辑留在 ChatBI Execution，不放入 Conversation。
4. Dashboard 改为直接调用 Datasource 公开查询能力，不再通过 ChatBI `get_chart_data_ds`。
5. Excel 导出使用 Conversation 查询结果，不直接访问 ORM。
6. 更新 `chatbi/api/conversations.py` 和 `chatbi/api/queries.py`。
7. 删除 `legacy_read.py` 及 ChatBI 公共面中的对应旧导出。

#### 验收条件

- 仓库内没有 `legacy_read` 导入；
- ChatBI API 不直接查询 Conversation ORM；
- Dashboard 不依赖 ChatBI 内部读取函数；
- 会话详情、结果读取、日志、用量和 Excel 导出契约不变；
- 非本人会话和记录仍被拒绝；
- 后端完整测试通过。

#### 回滚边界

本批只迁移查询实现。接口路径和数据结构保持不变，可以按调用入口回滚，不涉及数据变更。

### R6-c：替换旧 `LLMService`

> 状态：已实施（2026-07-24）

#### 目标

将分析、预测和推荐问题改为直接调用现有 ChatBI Generation Service，删除旧 `LLMService` 及其专用技术实现。

#### 实施步骤

1. 梳理 `legacy_chat_flow.LLMService` 中仍被调用的方法，只保留当前接口真实使用的输入和输出。
2. 推荐问题接口直接调用 RecommendedQuestionService。
3. 分析和预测接口直接调用 AnalysisPredictionService 及对应答案投影能力。
4. 所有结果写入统一调用 Conversation ChatRecordService。
5. 建立统一的流式事件输出函数，保持现有 SSE 事件字段不变。
6. 将仍需支持的助手外部数据源输入转换放入正常 Adapter；无调用能力直接删除。
7. 删除：
   - `legacy_chat_flow.py`；
   - `legacy_external_datasource.py`；
   - `legacy_sse.py`。
8. 删除以 `legacy` 命名的测试和组合入口引用。

#### 验收条件

- 仓库内没有 `LLMService`；
- 推荐问题、分析和预测接口不导入 `legacy_*`；
- 三类接口的 SSE 事件类型、完成事件和错误事件保持兼容；
- Agent、Graph 和辅助分析共同使用 Generation Service；
- 外部数据源只保留真实调用路径；
- 后端完整测试和前端生产构建通过。

#### 回滚边界

本批按接口能力分别迁移。单个能力未达到兼容要求时，可以暂缓该能力删除，但不得复制旧 LLMService 形成第四套流程。

### R6-d：清理组合入口与兼容台账

> 状态：已实施（2026-07-24）

#### 目标

删除最后的旧组合代码，关闭内部兼容债务，形成稳定的 Conversation 与 ChatBI 边界。

#### 实施步骤

1. 用正常 ChatBI composition 组装会话创建和联合删除流程。
2. 删除 `legacy_composition.py`。
3. 将 `chatbi/api/router.py` 的说明改为正式聚合 Chat、Agent 和 Graph 路由。
4. 核对 `chatbi/api/` 下不存在 `legacy_*.py`。
5. 核对 `apps/chatbi/__init__.py` 不再导出会话内部实现。
6. 更新 `COMPAT_LEDGER.md`：
   - 内部 `legacy_*` 实现登记为已清；
   - `/chat`、`/chat/agent`、`/graph` 路径继续作为正式接口时，将 C2 转正；
   - 如果仍计划改路径，只保留外部路径迁移事项，不得影响内部代码清理。
7. 更新 `BACKEND_STRUCTURE.md`、DDD 计划和变更日志。
8. 运行最终架构检查和完整回归。

#### 验收条件

- `chatbi/api/` 不存在 `legacy_*.py`；
- `rg "legacy_chat_flow|legacy_read|legacy_sse|legacy_composition|legacy_external_datasource" backend` 无运行时代码结果；
- `/chat`、`/chat/agent`、`/graph` 当前前端调用保持可用；
- Conversation 与 ChatBI 依赖方向无环；
- 完整后端测试、Mypy、Ruff、前端生产构建通过；
- 项目结构文档和兼容台账与源码一致。

#### 回滚边界

本批只删除已经没有调用方的文件。删除前必须用静态扫描和测试证明调用为零；出现问题时按具体路由恢复调用，不恢复已淘汰的重复业务实现。

## 9. 测试与守卫

### 9.1 Conversation

- 用户只能读取、修改和删除自己的会话；
- 工作空间隔离保持有效；
- ChatRecord 状态转换规则保持不变；
- 成功、失败、取消和等待用户状态均可恢复；
- SQL、答案、图表和数据大小限制保持有效；
- 删除会话后 Chat、ChatRecord 和 ChatLog 不残留；
- 重复执行删除不会报错或返回部分成功。

### 9.2 ChatBI

- Graph 和 Agent 均通过 Conversation Service 写记录；
- 数据源选择、SQL 生成、权限、执行和答案阶段不直接写 ORM；
- 分析、预测和推荐问题使用统一 Generation Service；
- 联合删除可以清理 Agent、Graph 和 Artifact；
- 中途失败后可以安全重试。

### 9.3 API

- 当前前端使用的 `/chat` 请求和响应契约不变；
- SSE 事件类型、字段和结束格式不变；
- 会话、记录、日志和导出接口保持权限校验；
- Graph 和 Agent 的启动、恢复、取消和历史查询正常。

### 9.4 架构守卫

需要在现有结构规则表中增加或调整以下规则，不新增独立守卫文件：

- Conversation 禁止依赖 ChatBI；
- 其他模块禁止导入 Conversation ORM 和具体仓储；
- Workflow Engine 禁止导入 Conversation 和 ChatBI；
- ChatBI API 禁止直接访问 Conversation ORM；
- ChatRecord 核心字段禁止绕过 ChatRecordService 写入；
- `chatbi/api/legacy_*.py` 最终必须不存在。

## 10. 风险与处理

| 风险 | 影响 | 处理方式 |
| --- | --- | --- |
| ChatRecord 使用范围广 | 一次移动可能产生大量导入错误 | R6-a 同批更新全部仓内调用方，不保留长期重新导出 |
| 会话删除跨多个模块 | 中途失败可能出现部分清理 | 按可重试顺序协调，每个清理入口保持幂等 |
| 旧 LLMService 仍承载辅助功能 | 直接删除会破坏分析、预测和推荐问题 | R6-c 按能力逐项迁移并做 SSE 契约测试 |
| Workflow Engine 读取 ChatRecord | 形成平台层对业务模块的反向依赖 | 将业务历史投影迁入 ChatBI 扩展层 |
| Dashboard 通过 ChatBI 查询数据 | Conversation 拆分后仍保留错误依赖 | R6-b 改为调用 Datasource 公开能力 |
| C2 被标记为外部阻塞 | 容易把内部清理错误地等待外部改路径 | 保持现有 URL，内部实现清理与外部路径迁移分开处理 |
| ORM 文件移动影响 Alembic | 迁移环境可能漏加载表元数据 | R6-a 更新 `alembic/env.py` 导入并校验 `alembic current` |
| 兼容别名积累 | ChatBI 仍然无法减重 | 仓内调用同批迁移，新增兼容必须登记并有删除条件 |

## 11. 完成定义

只有同时满足以下条件，ChatBI 减重阶段才算完成：

- Conversation 成为会话、记录和日志的唯一数据所有者；
- ChatBI 不再包含会话 ORM、仓储和状态机；
- ChatBI 只保留问数阶段能力与 Agent、Graph 编排；
- 所有有效 `legacy_*` 能力已迁移，旧文件已删除；
- HTTP 路径和数据库结构没有非计划变更；
- 依赖方向、数据所有权和写入入口有架构测试守卫；
- 后端完整测试、类型检查、代码检查和前端构建通过；
- `BACKEND_STRUCTURE.md`、兼容台账和变更日志与最终源码一致。
