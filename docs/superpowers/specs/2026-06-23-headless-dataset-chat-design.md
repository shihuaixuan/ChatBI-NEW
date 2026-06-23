# Headless 数据集绑定智能问数设计

## 背景

当前前端新建智能问数时通过 `ChatCreator` 列出数据源，并向 `/chat/start` 提交 `datasource`。Graph ChatBI v1 已经以 `dataset_id` 作为主输入，`GraphWorkflowAnswer` 目前只是把 `currentChat.datasource` 当成 `dataset_id` 使用。这个过渡态会让会话绑定对象和 Headless 语义数据集不一致。

本次目标是全量切换智能问数的新建、历史、发问和图表复用链路，使会话绑定 Headless 数据集，而不是直接绑定数据源。

## 目标

- 新建智能问数选择 Headless `dataset`。
- 会话和记录持久化 `dataset_id`，以 Headless 数据集作为业务绑定对象。
- 保留 `datasource` 字段作为执行兼容字段，由后端从 dataset 模型解析得到。
- Graph workflow 直接使用 `dataset_id`。
- 旧 SQL、图表、导出、仪表板复用等仍可使用记录上的 `datasource` 执行。
- 升级迁移时清理旧会话、旧会话记录和相关会话日志，不保留 datasource-only 历史会话。

## 非目标

- 不为旧 datasource-only 会话做 UI 兼容。
- 不迁移旧会话到某个推断 dataset。
- 不重构数据源管理、Headless 数据集管理页面。
- 不一次性移除旧 SQL/图表执行链路对 `datasource` 的依赖。

## 数据模型

新增字段：

- `chat.dataset_id`: Headless 数据集 ID。
- `chat_record.dataset_id`: 发问记录对应的数据集 ID。

保留字段：

- `chat.datasource`: 执行数据源 ID，由后端解析 dataset 时写入。
- `chat_record.datasource`: 记录执行数据源 ID，用于旧 SQL、图表、导出、仪表板复用。

API DTO 增加：

- `CreateChat.dataset_id`
- `ChatInfo.dataset_id`
- `ChatInfo.dataset_name`
- `ChatInfo.dataset_exists`
- `ChatRecord.dataset_id`

命名兼容：

- 前端继续保留 `datasource`、`datasource_name` 字段消费旧组件。
- 新 UI 展示数据集字段，例如 `dataset_name`。

## 后端行为

`/chat/start` 和 `/chat/assistant/start`：

1. 要求新建智能问数提供 `dataset_id`。
2. 校验 `HeadlessDataSet.oid == current_user.oid` 且 `status == 1`。
3. 根据 dataset 的 `default_model_id` 或 `headless_dataset_model_config` 中启用模型解析 `HeadlessModel.datasource_id`。
4. 校验解析到的数据源属于当前工作区。
5. 写入：
   - `chat.dataset_id = dataset.id`
   - `chat.datasource = resolved_datasource_id`
   - `chat.engine_type = resolved_datasource.type_name`
6. 首条 `first_chat` 记录同步写入 `dataset_id` 和 `datasource`。
7. 返回 `dataset_name` 和兼容的 `datasource_name`。

`save_question`：

1. 从 `chat.dataset_id` 和 `chat.datasource` 复制到 `chat_record`。
2. Graph workflow 使用 `dataset_id`。
3. 旧回答链路继续使用 `datasource`。

`list_recent_questions`：

- 参数从 `datasource_id` 调整为 `dataset_id`。
- 查询条件改为 `Chat.dataset_id == dataset_id`。

权限：

- 新建时服务内校验 dataset 所属工作区。
- 如果继续复用 `@require_permissions`，需要新增 `dataset` 资源类型；更简单的实现是移除新建接口上的 datasource 权限表达式，改由服务内显式校验 dataset 和解析出的 datasource。
- `chat` 类型权限保持不变。

## 前端行为

`ChatCreator`：

1. 改用 `headlessApi.datasetList()`。
2. 弹窗标题和文案改为“选择数据集”。
3. 选择项使用 `dataset.id`。
4. 创建会话提交 `{ dataset_id }`，不再提交 `{ datasource }`。
5. 不再执行 `datasourceApi.check_by_id`；后端负责 dataset 和执行数据源校验。

`chatApi` 类型：

- `Chat`、`ChatInfo`、`ChatRecord` 增加 `dataset_id`。
- `ChatInfo` 增加 `dataset_name`、`dataset_exists`。
- `startChat` 和 `startAssistantChat` payload 支持 `dataset_id`。
- `recentQuestions` 参数改为 `dataset_id`。

聊天页：

- 底部已选对象展示 `dataset_name`。
- quick question 和 recent question 传 `dataset_id`。
- `GraphWorkflowAnswer` 的 `datasetId()` 改为优先读取 `currentChat.dataset_id`，其次读取 `record.dataset_id`。

仪表板聊天图表复用：

- 继续使用 `record.datasource` 作为执行图表数据源。
- 如需展示绑定对象，展示 `chat.dataset_name`。

## 迁移策略

新增迁移版本，例如 `076_chat_headless_dataset.py`。

升级步骤：

1. 收集旧会话记录 ID：
   - `select id from chat_record`
2. 删除对应会话日志：
   - `delete from chat_log where type = '0' and pid in (:record_ids)`
   - `type = '0'` 对应 `TypeEnum.CHAT.value`
3. 删除旧会话记录：
   - `delete from chat_record`
4. 删除旧会话：
   - `delete from chat`
5. 添加字段：
   - `chat.dataset_id bigint null`
   - `chat_record.dataset_id bigint null`
6. 添加索引：
   - `idx_chat_dataset` on `(oid, dataset_id, create_time)`
   - `idx_chat_record_dataset` on `(chat_id, dataset_id)`

字段先保持 nullable，避免部署时旧代码或局部流程在短窗口内写入失败。业务层要求新建会话必须带 `dataset_id`。

降级策略：

- 删除新增索引和字段。
- 不恢复已删除的旧会话数据。

## 错误处理

- `dataset_id` 缺失：返回 400，提示“请选择数据集”。
- dataset 不存在或不属于当前工作区：返回 404 或 403。
- dataset 没有启用模型：返回 400，提示“数据集未配置可用模型”。
- dataset 解析不到执行数据源：返回 400，提示“数据集未绑定可用数据源”。
- 解析出的数据源不存在或跨工作区：返回 400 或 403。
- Graph workflow 记录没有 `dataset_id`：前端显示“当前会话没有可用数据集，无法启动 Graph Workflow。”

## 测试设计

后端优先补单测：

- `/chat/start` 使用合法 `dataset_id` 创建会话，返回 `dataset_id/dataset_name/datasource/datasource_name`。
- `/chat/start` 缺少 `dataset_id` 失败。
- dataset 跨工作区失败。
- dataset 无启用模型失败。
- `save_question` 会复制 `dataset_id` 和 `datasource` 到 `chat_record`。
- `list_recent_questions` 按 `dataset_id` 过滤。
- 迁移脚本删除 `chat`、`chat_record`、相关 `chat_log`，并添加新字段。

前端验证：

- `vue-tsc -b` 通过。
- `npm run build` 通过。
- 手动或浏览器验证：新建智能问数弹窗展示 Headless 数据集，创建后聊天页展示已选数据集，发送问题时 Graph workflow 请求体包含 `dataset_id`。

## 验收标准

- 新建智能问数不能再选择原始数据源。
- 新建会话请求体包含 `dataset_id`，不包含业务绑定意义上的 `datasource`。
- 后端会话持久化 `dataset_id`，并自动写入兼容执行用 `datasource`。
- 旧会话、旧记录、对应会话日志在迁移后被清空。
- Graph workflow 使用真实 Headless dataset。
- 旧图表/导出/仪表板复用不因本次切换失效。
