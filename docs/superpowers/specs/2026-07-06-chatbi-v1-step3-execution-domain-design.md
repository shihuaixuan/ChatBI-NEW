# ChatBI v1 Step 3 执行域统一设计

## 1. 目标

本阶段只完成 `chatbi-v1-graph-refactor-analysis-and-design.md` 中的 Step 3：

1. 把单查询与拆分查询统一为 `execution.queries[]/results[]`；
2. 并行执行拆分子查询；
3. 把完整结果行写入 artifact，工作流上下文只保存引用、统计和少量样本；
4. 答案生成改读稳定投影视图，不再把全量 `variables` 注入模型；
5. 在迁移期保留旧 `sql_execution` 消费契约，避免 API、Trace、前端和推荐节点回归。

本阶段不实现结果校验、能力矩阵、比较/占比计划、交互域重构和观测元数据统一，这些仍属于 Step 4 至 Step 6。

## 2. 现状与约束

当前单查询输出为扁平 `sql_execution`，拆分查询则把每个子查询结果对象塞进 `sql_execution.rows`。两种形状迫使答案、Trace 和前端按节点名称分支。拆分执行使用串行循环。

`workflow_artifact` 已有元数据表和 `storage_uri`，但没有正文存储实现；`ArtifactRepository` 只写元数据。`SqlExecuteTool` 持有 SQLAlchemy Session，因此不能把同一个实例直接放进线程池并发调用。

迁移期间需要满足以下兼容约束：

- 图路由仍能依据执行整体状态进入成功或错误处理分支；
- `handle_sql_error`、推荐节点、Trace API 和现有前端在未完成后续步骤前仍可工作；
- 不在公开事件、Trace 或答案提示词中暴露完整 SQL 结果；
- artifact 写入失败不能被静默忽略，否则上下文中的引用将不可用。

## 3. 方案选择

### 3.1 采用方案：标准执行域 + 兼容镜像

主事实源为 `variables.execution`，使用统一的 `queries[]/results[]`。同一次节点输出同步镜像到 `variables.sql_execution`，镜像内容与标准结构相同，并额外保留少量旧顶层摘要字段。旧消费者逐步改为“优先读 execution，缺失时回退 sql_execution”。

该方案允许 Step 3 独立合入，同时避免直接切换导致现有 API 和前端失效。

### 3.2 未采用方案

- 直接删除 `sql_execution`：结构最干净，但会把 Step 3 扩大为 API、前端和全部下游消费者的同时切换，回滚困难。
- 只改 `sql_execution` 内部形状：无法建立目标上下文中的 `execution` 域，后续 Step 5 仍需再次迁移。
- 只返回 artifact 协议但不保存正文：不能满足 Step 3 的数据治理目标，引用也无法被实际读取。

## 4. 标准执行模型

```json
{
  "status": "succeeded",
  "queries": [
    {
      "query_id": "query-0",
      "sql": "select ...",
      "datasource_id": 13,
      "plan_ref": 0,
      "model_id": 3,
      "metrics": ["销售额"],
      "dimensions": ["店铺"]
    }
  ],
  "results": [
    {
      "query_id": "query-0",
      "status": "succeeded",
      "row_count": 120,
      "fields": ["店铺", "销售额"],
      "sample_rows": [{"店铺": "A", "销售额": 100}],
      "sampled_row_count": 1,
      "result_truncated": true,
      "artifact_ref": {
        "artifact_id": "result-...",
        "kind": "sql_result",
        "content_type": "application/json",
        "size": 4096,
        "digest": "sha256:...",
        "metadata": {
          "query_id": "query-0",
          "row_count": 120
        }
      },
      "execution_ms": 45,
      "error_code": null,
      "message": null
    }
  ],
  "row_count": 120,
  "fields": ["店铺", "销售额"],
  "execution_ms": 45,
  "sampled_row_count": 1,
  "result_truncated": true,
  "artifact_ref": {
    "artifact_id": "result-...",
    "kind": "sql_result",
    "content_type": "application/json",
    "size": 4096,
    "digest": "sha256:...",
    "metadata": {}
  },
  "rows": [{"店铺": "A", "销售额": 100}],
  "error_code": null,
  "message": null
}
```

`queries[]/results[]` 是新消费者必须读取的字段。顶层 `row_count`、`fields`、`execution_ms`、`rows`、`artifact_ref` 等字段只用于迁移兼容：

- 单查询时从唯一 result 派生；
- 多查询时 `row_count` 和 `execution_ms` 聚合，`rows` 为空，避免继续表达旧的嵌套多态；
- 多查询消费者应读取 `results[]`；
- Step 4 至 Step 6 的消费者迁移完成后再删除兼容字段。

整体状态规则：

- 所有 result 成功：`execution.status = succeeded`；
- 任一 result 失败：`execution.status = failed`；
- 失败时保留已经完成的成功 result 和失败 result，便于审计与解释；
- 整体 `error_code/message` 取第一个失败 result，继续复用现有 SQL 错误处理路径。

## 5. Artifact 存储

### 5.1 存储职责

新增文件正文存储实现，正文为 UTF-8 JSON：

```json
{
  "query_id": "query-0",
  "fields": ["店铺", "销售额"],
  "rows": [{"店铺": "A", "销售额": 100}],
  "row_count": 120
}
```

数据库 `workflow_artifact` 只保存元数据。文件路径使用不可预测的 artifact ID，不使用用户输入或查询内容拼接。写入流程为：

1. 稳定 JSON 序列化；
2. 计算 SHA-256、字节数；
3. 写入同目录临时文件；
4. 原子替换为最终文件；
5. 写入 `workflow_artifact` 元数据；
6. 返回不含 `storage_uri` 的 `ArtifactRef`。

若文件或元数据写入失败，执行节点返回结构化失败，不产生悬空引用。元数据写入失败时尽力删除已经原子落盘的最终正文；事务在更外层回滚造成的孤立文件由后续 artifact 生命周期清理任务处理，不在本阶段扩展分布式事务。

### 5.2 存储路径

通过配置项指定 artifact 根目录；默认使用后端数据目录下的 `workflow_artifacts`。目录必须位于版本控制范围外，并允许部署时挂载持久卷。`storage_uri` 使用 `file://` URI，但该 URI 不进入公开上下文。

本阶段不新增公开 artifact 下载接口。完整结果读取接口需要结合用户、租户、Run 所有权校验，后续单独实现；Step 3 只保证正文真实持久化且内部存储端口可读取。

## 6. 并行执行

拆分查询使用有界线程池，最大工作线程数为 `min(查询数, 4)`。单查询仍直接执行，避免无意义的线程切换。

为避免共享 SQLAlchemy Session：

- SQL adapter 不在线程间共享 `SqlExecuteTool(session)`；
- 引入执行网关/工厂，每次 `run(payload)` 创建独立 Session，完成 datasource 加载和 SQL 执行后关闭；
- 单元测试继续注入线程安全的 fake gateway；
- 每个子任务独立完成权限改写、SQL 执行、artifact 写入和结果构造。

结果按原始 query 顺序返回，不按线程完成顺序返回，保证可重复性和 `query_id` 对齐。

## 7. 答案投影视图

新增纯函数构建答案输入，只包含：

- 原始问题和改写问题；
- 查询计划中的指标、维度、过滤、时间、排序和限制；
- 每个查询的状态、字段、行数、耗时和少量 `sample_rows`；
- artifact 引用；
- 节点降级信息和 SQL 错误摘要；
- 已有知识决策的简短 reason，不包含候选资产 payload。

明确排除：

- SQL 原文；
- `candidate_groups`、`selected_assets`、`slot_bindings` 全量结构；
- 交互历史原始载荷；
- 全量 variables；
- 完整结果行；
- 内部权限策略和异常堆栈。

`build_answer_generation_prompt` 的参数从 `variables` 改为 `projection`。模型不可用、返回格式错误时的现有降级语义保持不变。

## 8. 消费端兼容

- `ChatBIRunContext` 新增 `execution`，优先读取 `variables.execution`，不存在时回退 `variables.sql_execution`；
- SQL 错误处理、条件和推荐 adapter 改经统一 accessor 读取；
- 执行节点一次 patch 同时写 `variables.execution` 与 `variables.sql_execution`；
- Trace API 优先读取新域，并把单/拆分节点统一投影为状态、查询数、行数、字段、耗时和 artifact 引用；
- 前端优先读取 `results[]`，旧 `rows` 只作为回退；
- 旧 API 的上下文摘要字段暂时保留，不在本阶段删除。

## 9. 错误处理

- 权限拒绝、缺少 datasource、SQL 执行异常分别落入对应 result；
- artifact 写入异常使用稳定错误码 `SQL_RESULT_ARTIFACT_WRITE_FAILED`；
- 并发任务抛出未预期异常时转换为失败 result，不让线程异常丢失；
- 任一子查询失败时不取消已经开始的其他查询，以获得完整的本轮执行诊断；
- 不自动重试 artifact 写入或 SQL 执行，仍由现有图级 SQL 错误处理决定后续路径。

## 10. 测试与验收

采用 TDD，至少覆盖：

1. 单查询输出包含一个 query 和一个 result；
2. 拆分查询输出多个同构 result，顺序与 query 一致；
3. 两个阻塞 fake 查询能够证明拆分执行发生并行；
4. 一个子查询失败时整体失败，同时保留其他子结果；
5. artifact 文件正文、SHA-256、size 和数据库元数据一致；
6. context 只保存样本行，不保存完整结果；
7. artifact 写入失败返回稳定错误码且不留下悬空元数据；
8. answer projection 不包含 SQL、candidate payload 和非样本结果；
9. 单/拆分查询的回答 prompt 使用相同投影结构；
10. 旧 `sql_execution` 条件、推荐、Trace 和前端兼容；
11. ChatBI workflow、workflow engine、headless SQL 编译相关回归测试通过。

验收时额外检查工作区中没有 artifact 测试文件残留，生产默认存储目录已被 `.gitignore` 排除。

## 11. 非目标

- artifact 对外下载 API 与权限模型；
- artifact 生命周期清理任务；
- 对象存储实现；
- 查询取消和超时传播；
- 结果质量校验；
- 比较、占比、HAVING、detail 等 Step 5 查询能力；
- 删除 `sql_execution`、`split_sql` 或旧顶层兼容字段；
- Step 6 的节点 metadata 和观测口径最终统一。
