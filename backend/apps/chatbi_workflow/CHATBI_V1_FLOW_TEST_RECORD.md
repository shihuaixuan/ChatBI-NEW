# ChatBI v1 当前流程测试记录

本文档记录当前 `chatbi/v1` 图流程的真实环境测试过程，包括测试输入、节点流转、关键节点输入输出、交互恢复以及当前阻塞点。

## 1. 测试结论

当前 v1 图流程可以完成：

- API 创建 v1 run。
- 问题分类。
- 问题重写。
- 图表画像节点。
- 意图识别。
- Headless knowledge 检索。
- 指标歧义识别。
- 进入 `ask_metric_selection` 等待用户选择。
- 用户回答后恢复 run。
- 用户选择合并回 `variables.knowledge`。
- SQL 生成。

当前已完成的知识检索优化：

- `recognize_intent` 输出自然语言槽位线索：`metric_mentions`、`dimension_mentions`、`time_mentions`、`filter_mentions`、`required_slot_types`、`query_shape`。
- `knowledge.retrieve` 已改为优先按上述 mention 分槽位召回 Headless 候选。
- `rewritten_question` 只在必需槽位缺候选时作为 fallback。
- 候选进入 `CandidateGate` 前会执行可解释 rerank，输出 `base_score`、`rerank_strategy`、`rerank_reason`。
- 已通过单元测试验证：“访问人数”优先于“转化人数/关注人数”等仅部分重叠候选；只有“人数”这种弱词时仍保留 `metric_ambiguous`。

2026-07-06 完成 Step 3 执行域重构：

- 单查询与拆分查询统一输出 `variables.execution.queries[]/results[]`。
- 迁移期继续镜像 `variables.sql_execution`，旧消费端可平滑回退。
- 拆分子查询改为有界并行，每个真实查询使用独立数据库 Session。
- 完整结果正文写入文件 artifact，`workflow_artifact` 保存元数据；上下文仅保留引用、统计和样本行。
- 答案模型改读白名单投影视图，不再接收 SQL、候选 payload 与全量 variables。
- Trace 与前端优先消费统一结果结构，保留旧结果形状回退。

当前流程阻塞在：

- `execute_sql` 节点。
- 阻塞原因不是图路由或 SQL 生成，而是真实 datasource 执行链路导入异常。

## 2. 自动化回归测试

执行命令：

```bash
cd /Users/twenty/LLM/ChatBI/SQLBot/backend
uv run pytest tests/headless tests/workflow_engine tests/chatbi_workflow -q
```

结果：

```text
177 passed, 86 warnings
```

说明：

- Headless 语义资产测试通过。
- Workflow engine 测试通过。
- ChatBI workflow adapter 和 v1 flow 测试通过。
- 自动化测试里的图流程、交互恢复、SQL repair、推荐问题、澄清交互均未出现回归。

## 3. 真实 v1 流程测试输入

执行命令：

```bash
cd /Users/twenty/LLM/ChatBI/SQLBot/backend
uv run python tests/workflow_engine/run_graph_flow_demo.py '今日访问人数' \
  --dataset-id 3 \
  --oid 1 \
  --user-id 1 \
  --definition-version v1 \
  --run-id flow-test-v1-current-1
```

输入参数：

```json
{
  "question": "今日访问人数",
  "dataset_id": 3,
  "oid": 1,
  "user_id": 1,
  "definition_version": "v1",
  "run_id": "flow-test-v1-current-1"
}
```

## 4. 第一次执行结果

注意：本节记录的是第一阶段 rerank 优化前的真实流程结果，用于说明当时暴露的问题。

HTTP 状态：

```text
200
```

Run 状态：

```text
waiting_input
```

当前节点：

```text
ask_metric_selection
```

原因：

- `retrieve_knowledge` 命中多个“人数”类指标。
- `CandidateGate` 判断 Top 指标候选分数接近，无法安全绑定唯一指标。
- 图路由进入 `ask_metric_selection`，等待用户选择指标。
- 该问题已推动后续优化：当前 `knowledge.retrieve` 会先根据 `metric_mentions=["访问人数"]` 对候选做 slot-aware rerank，完整短语命中的候选会优先于只命中“人数”的候选。

## 5. 第一次执行节点流转

| 顺序 | 节点 | 状态 | 路由原因 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | `classify_question` | succeeded | `QUESTION_DATA_OR_FOLLOWUP` | 问题进入数据问答链路 |
| 2 | `rewrite_question` | succeeded | `DEFAULT_EDGE` | 问题无需澄清，继续执行 |
| 3 | `draw_image_profile` | succeeded | `DEFAULT_EDGE` | 输出默认图表候选 |
| 4 | `recognize_intent` | succeeded | `DEFAULT_EDGE` | 识别为指标查询 |
| 5 | `retrieve_knowledge` | succeeded | `KNOWLEDGE_METRIC_AMBIGUOUS` | 识别到指标歧义 |
| 6 | `ask_metric_selection` | waiting_input | - | 暂停等待用户选择指标 |

## 6. 关键节点输入输出

### 6.1 `classify_question`

注意：本节记录的是修复前测试时观察到的历史结果。该问题已经调整：分类模型调用失败或输出非法时，现在会让 `classify_question` 节点失败并中断 run，不再降级为 `data` 继续执行后续数据链路。

输入：

```json
{
  "question": "今日访问人数",
  "tenant_id": 1,
  "user_id": 1,
  "dataset_id": 3
}
```

输出：

```json
{
  "category": "data",
  "confidence": 0.0,
  "reason": "classification_model_failed",
  "risk_level": "medium"
}
```

说明：

- 当时分类模型调用失败后使用了降级结果。
- 这个策略已确认不合理，现已改为模型失败即节点失败。

### 6.2 `rewrite_question`

输出：

```json
{
  "rewritten_question": "今日访问人数",
  "need_user_input": false,
  "missing_slots": [],
  "image_profile_hint": null
}
```

说明：

- 当前问题无需进入 `ask_rewrite_clarification`。

### 6.3 `draw_image_profile`

输出：

```json
{
  "profile": "default_table_chart",
  "chart_candidates": ["table", "line"]
}
```

说明：

- 该节点当前仍是规则/默认候选。

### 6.4 `recognize_intent`

输出：

```json
{
  "intent_type": "metric_query",
  "confidence": 0.85,
  "metric_mentions": ["访问人数"],
  "dimension_mentions": [],
  "time_mentions": ["今日"],
  "filter_mentions": [],
  "required_slot_types": ["metric", "time_range"],
  "query_shape": {
    "select_mode": "aggregation",
    "time_grain": "day"
  },
  "ambiguous_slots": [],
  "conflict_slots": []
}
```

说明：

- 识别为普通指标查询。
- 新字段只表达自然语言层面的检索线索，不代表已经绑定 Headless 资产。
- 没有进入 `ask_intent_clarification`。

### 6.5 `retrieve_knowledge`

注意：以下候选摘要是第一阶段 rerank 优化前的历史结果，体现了“文本重叠只命中人数导致多个候选同分”的问题。

输出摘要：

```json
{
  "hit": true,
  "status": "metric_ambiguous",
  "dataset_id": 3,
  "metrics": [],
  "dimensions": [],
  "tables": [],
  "fields": [],
  "decision": {
    "status": "ambiguous",
    "strategy": "candidate_gate",
    "reason": "Top 指标候选分数接近，无法安全绑定唯一指标。"
  }
}
```

命中的候选指标包括：

```json
[
  {
    "asset_id": 5,
    "name": "转化人数",
    "biz_name": "convert_uv",
    "score": 1.0,
    "source": "headless_asset_document",
    "matched_text": "人数"
  },
  {
    "asset_id": 7,
    "name": "关注人数",
    "biz_name": "follow_uv",
    "score": 1.0,
    "source": "headless_asset_document",
    "matched_text": "人数"
  },
  {
    "asset_id": 9,
    "name": "商品点击人数",
    "biz_name": "product_click_uv",
    "score": 1.0,
    "source": "headless_asset_document",
    "matched_text": "人数"
  },
  {
    "asset_id": 10,
    "name": "分享人数",
    "biz_name": "share_uv",
    "score": 1.0,
    "source": "headless_asset_document",
    "matched_text": "人数"
  },
  {
    "asset_id": 11,
    "name": "咨询人数",
    "biz_name": "stall_inquiry_uv",
    "score": 1.0,
    "source": "headless_asset_document",
    "matched_text": "人数"
  }
]
```

说明：

- 用户问题是“今日访问人数”，但当前数据集里多个指标都包含“人数”。
- 检索分数接近，所以系统没有自动绑定唯一指标。
- 这是符合预期的业务歧义分支。
- 当前代码已加入 slot-aware rerank：
  - 如果候选中存在完整短语“访问人数”或别名命中，会优先绑定该资产。
  - 如果问题或 mention 只有“人数”这类弱词，则仍保持 `metric_ambiguous`，等待用户选择。
  - trace 中可通过 `base_score`、`rerank_strategy`、`rerank_reason` 观察重排原因。

### 6.6 `ask_metric_selection`

输出：

```text
pending_interaction_id = 515aff47-cfcb-4472-aff3-2ddd8176600e
```

交互语义：

```json
{
  "prompt": "请选择要分析的指标。",
  "allowed_update_paths": ["variables.metric_selection"]
}
```

说明：

- Run 在该节点暂停。
- 需要用户选择具体指标后才能继续进入 SQL 生成。

## 7. 用户选择指标并恢复流程

模拟用户回答：

```json
{
  "asset_id": 5
}
```

含义：

```text
选择指标：转化人数
biz_name: convert_uv
```

调用接口：

```http
POST /graph/runs/flow-test-v1-current-1/interactions/515aff47-cfcb-4472-aff3-2ddd8176600e/responses
```

请求体：

```json
{
  "response": {
    "asset_id": 5
  }
}
```

恢复后，`ChatBIV1InteractionResponsePatcher` 把用户选择合并回 `variables.knowledge`。

合并后的关键上下文：

```json
{
  "knowledge": {
    "hit": true,
    "status": "hit",
    "metrics": ["convert_uv"],
    "ambiguities": [],
    "selected_assets": {
      "metrics": [
        {
          "asset_id": 5,
          "biz_name": "convert_uv",
          "display_name": "转化人数",
          "source": "user_selected"
        }
      ]
    },
    "slot_bindings": {
      "metrics": [
        {
          "asset_id": 5,
          "biz_name": "convert_uv",
          "display_name": "转化人数",
          "confidence": 1.0,
          "source": "user_selected"
        }
      ]
    },
    "decision": {
      "status": "user_selected",
      "strategy": "metric_selection",
      "reason": "用户已确认指标"
    }
  }
}
```

## 8. 恢复后节点流转

| 顺序 | 节点 | 状态 | 说明 |
| --- | --- | --- | --- |
| 7 | `generate_sql` | succeeded | 根据用户选择指标生成 SQL |
| 8 | `execute_sql` | failed | 真实 SQL 执行链路导入失败 |

## 9. SQL 生成结果

`generate_sql` 输出：

```json
{
  "sql": "select sum(stall_traffic_metrics.convert_uv) as convert_uv from stall_traffic_metrics stall_traffic_metrics limit 100",
  "strategy": "semantic_sql_compiler",
  "datasource_id": 10,
  "explanation": "基于 Headless 语义资产生成 SQL",
  "used_assets": [
    {
      "asset_type": "METRIC",
      "asset_id": 5,
      "biz_name": "convert_uv"
    }
  ]
}
```

说明：

- SQL 生成节点成功。
- SQL 使用用户选择的指标 `convert_uv`。
- SQL datasource_id 为 `10`。
- SQL 生成来自 `SemanticSQLCompiler`。

## 10. SQL 执行失败

`execute_sql` 节点状态：

```text
failed
```

公开事件错误码：

```text
SQL_EXECUTE_FAILED
```

node_execution 内部错误信息：

```text
Invalid format 'json' for '%' style
```

直接调用 `SqlAdapter.execute()` 复现时，堆栈显示失败发生在：

```text
apps.agentic_chat.tools.sql_executor.SqlExecuteTool.run()
  -> from apps.datasource.crud.datasource import get_ds
  -> sqlbot_xpack import chain
  -> common.utils.utils.setup_logging()
  -> logging.Formatter(settings.LOG_FORMAT)
  -> ValueError: Invalid format 'json' for '%' style
```

说明：

- 当前环境中 `LOG_FORMAT=json`。
- Python 标准库 `logging.Formatter` 默认按 `%` 风格解析格式。
- 字符串 `json` 不是合法的 `%` 风格 logging format。
- 因此 datasource 执行链路在 import 阶段失败。

## 11. 修正 LOG_FORMAT 后的进一步阻塞

临时设置：

```bash
LOG_FORMAT='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s'
```

再次直接调用 SQL 执行链路，出现新的导入问题：

```text
ImportError: cannot import name 'get_ws_ds' from partially initialized module
apps.datasource.crud.datasource
```

堆栈显示：

```text
apps.datasource.crud.datasource
  -> sqlbot_xpack.permissions.models.ds_rules
  -> sqlbot_xpack
  -> apps.system.schemas.permission
  -> apps.datasource.crud.datasource
```

说明：

- 修正日志格式后，datasource/xpack 链路继续暴露循环导入问题。
- 这说明当前真实 SQL 执行链路还不能稳定被 `SqlExecuteTool` 在该测试环境中导入和调用。

## 12. 当前流程状态判断

已验证通过：

- 图创建。
- 节点路由。
- 真实问题分类降级。
- 问题重写。
- 意图识别。
- Headless 知识检索。
- 指标歧义识别。
- 指标选择交互。
- 用户回答恢复。
- 用户选择合并回 knowledge。
- SQL 生成。
- 第一阶段知识检索 rerank 单元测试：
  - “访问人数”完整短语优先。
  - “人数”弱词仍保持指标歧义。

未通过：

- 真实 SQL 执行。
- 因 `execute_sql` 节点直接失败，后续没有进入：
  - `handle_sql_error`
  - `generate_question_answer`
  - `recommend_questions`
  - `compose_final_reply`
  - `finish`

当前根因：

- `SqlExecuteTool` 在导入 datasource 执行链路时发生异常。
- 该异常发生在 `SqlExecuteTool.run()` 内部 import 阶段，当前没有被包装成 `ToolResult(success=False)`。
- 因此 `SqlAdapter.execute()` 直接抛异常，导致图节点失败，而不是返回 `status=failed` 让图走 `handle_sql_error`。

## 13. 下一步建议

知识检索侧：

- 第一阶段 slot-aware rerank 已完成。
- 下一步继续接入 BM25 / embedding / hybrid score，增强候选召回与重排。
- 补充 Headless 资产同义词/别名治理，例如“访问人数/访客数/UV/访问量”。

SQL 执行侧：

优先修复 `SqlExecuteTool` 的异常边界：

```text
apps.agentic_chat.tools.sql_executor.SqlExecuteTool.run()
```

建议行为：

- 把 `get_ds` 和 `exec_sql` 的 import 放入 `try` 内。
- import 失败、datasource 获取失败、SQL 执行失败都统一返回 `ToolResult(success=False)`。
- 不让 datasource/xpack/logging 的导入问题直接抛出到图运行时。

预期修复后：

```text
execute_sql
  -> 输出 status=failed
  -> 路由 sql.execution_failed
  -> handle_sql_error
  -> generate_question_answer
  -> recommend_questions
  -> compose_final_reply
  -> finish
```

这样即使真实 datasource 当前不可用，v1 图也能形成稳定的生产闭环，而不是 run failed。
