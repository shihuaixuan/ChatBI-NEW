# 商品店铺 Agent 接口测试报告

- 数据集：商城店铺数据集（ID 243）
- 主问题数：15
- 澄清分支数：5
- 实际 API 运行数：20
- 主问题有查询执行：6/15
- 主问题有最终答案事件：6/15
- 事件补拉完整：14/20
- 测试对象：Agent HTTP 接口；未调用 Graph 接口。

## 问题结果

| 问题 | 终态 | 理解校验 | 查询执行 | 最终答案 | 澄清 | 步骤 | 工具 | Trace节点 | 命中预期值 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| Q1 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 1/1 |
| Q2 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 1/1 |
| Q3 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 0/0 |
| Q4 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 1/1 |
| Q5 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 1/2 |
| Q6 | failed | 已通过 | 否 | 否 | 0 | 2 | 2 | 44 | 0/2 |
| Q7 | finished | 已通过 | 是 | 是 | 0 | 4 | 5 | 78 | 1/1 |
| Q8 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 12 | 0/0 |
| Q9 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/2 |
| Q10 | waiting_user | 已通过 | 否 | 否 | 2 | 2 | 3 | 55 | 0/0 |
| Q11 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/1 |
| Q12 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/1 |
| Q13 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/3 |
| Q14 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/1 |
| Q15 | failed | 未产生 | 否 | 否 | 0 | 0 | 0 | 9 | 0/2 |

## 澄清候选逐项覆盖

每个候选均通过独立 Chat 启动同一问题，再调用 resume 接口选择该候选。

| 主问题 | 候选 | 终态 | 查询执行 | 最终答案 | 事件补拉 | 选择值 |
|---|---|---|---:|---:|---:|---|
| Q10 | 总下单客户数 | waiting_user | 否 | 否 | 否 | METRIC:274:246|DIMENSION:278:246 |
  - Q10 总下单客户数 API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
| Q10 | 订货下单客户数 | waiting_user | 否 | 否 | 否 | METRIC:273:246|DIMENSION:278:246 |
  - Q10 订货下单客户数 API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
| Q10 | 欠款客户数 | waiting_user | 否 | 否 | 否 | METRIC:310:250|DIMENSION:315:250 |
  - Q10 欠款客户数 API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
| Q10 | 新增成交客户数 | waiting_user | 否 | 否 | 否 | METRIC:313:249|DIMENSION:306:249 |
  - Q10 新增成交客户数 API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
| Q10 | 销售下单客户数 | waiting_user | 否 | 否 | 否 | METRIC:272:246|DIMENSION:278:246 |
  - Q10 销售下单客户数 API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500

## 流程分析口径

1. 接入：检查 /chat/start 和 /chat/agent/stream 是否返回可关联的会话、记录和运行 ID。
2. 产品事件：检查 SSE 的序号、事件域和是否出现 sql.executed、answer.completed。
3. 执行过程：通过 Timeline 统计 Step、Tool Call 和终态错误。
4. 可观测性：通过 Trace 统计节点数量、节点类型和总览状态。
5. 持久化补拉：用 after_sequence=0 补拉事件，检查已发送事件可以被读取。

## 逐题问题文本

- **Q1**：2026年6月30日店铺100011的总下单客户数是多少？（覆盖：单指标、单店铺、单日时间）
- **Q2**：2026年6月30日店铺100011的销售类GMV、订货类GMV和总GMV分别是多少？（覆盖：多指标、同一店铺、指标并列）
- **Q3**：2026年6月1日至30日店铺100011的总GMV按天趋势如何？（覆盖：日期范围、时间粒度、趋势）
- **Q4**：2026年6月30日店铺100011库存量最高的5个商品ID是什么？同时给出当前库存件数。（覆盖：TopN、排序、商品维度）
- **Q5**：2026年6月30日对比店铺100011和店铺100012的总GMV，哪个更高？（覆盖：多店铺对比、分组、排序）
- **Q6**：2026年6月30日店铺100011的未发订单号USO202606300001的订单金额、未发件数和是否超时是多少？（覆盖：订单明细、订单号过滤、多指标）
  - 失败信息：问数消息必须成功执行查询后才能结束，禁止生成看似来自数据库的直接回答。
  - 错误分类：sql_failed
- **Q7**：2026年6月30日店铺100011的销售类GMV占总GMV比例是多少？（覆盖：占比、派生计算、多指标关系）
- **Q8**：2026年6月1日至30日店铺100011的总GMV是否存在异常波动？列出变化最大的3天。（覆盖：异常分析、日期范围、变化排名）
  - 失败信息：INTENT_RECOGNITION_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q9**：2026年6月30日店铺100011商品ID为P1000101001的当前库存件数、近7天销量和近30天销量是多少？（覆盖：店铺和商品双过滤、多时间窗口、多指标）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q10**：2026年6月30日店铺100011的客户数是多少？（覆盖：指标歧义、结构化澄清、跨模型候选）
  - API 错误：resume HTTP 500: Server error '500 Internal Server Error' for url 'http://127.0.0.1:8010/api/v1/chat/agent/stream'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
  - 澄清候选：总下单客户数；订货下单客户数；欠款客户数；新增成交客户数；销售下单客户数
- **Q11**：2026年6月30日店铺100011的总GMV是多少？（覆盖：单指标、单店铺、单日时间）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q12**：2026年6月30日店铺100011的订单平均客单价是多少？（覆盖：派生指标、单店铺、单日时间）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q13**：2026年6月30日店铺100011的未发订单明细，列出未发订单号、客户ID和未发件数。（覆盖：明细查询、快照模型、多字段）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q14**：2026年6月30日店铺100011、客户ID为C1000101009的客户当日GMV是多少？（覆盖：客户交易模型、店铺和客户双过滤）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed
- **Q15**：2026年6月30日店铺100011、客户ID为C1000101003的当前欠款余额和逾期欠款金额分别是多少？（覆盖：欠款快照、多指标、店铺和客户双过滤）
  - 失败信息：QUESTION_REWRITE_MODEL_CALL_FAILED
  - 错误分类：understanding_failed

## 测试结论

- 15 个主问题中，6 个进入真实 SQL 查询，6 个产生最终答案事件。
- Q10 的 5 个澄清候选均已通过独立会话和 resume 接口覆盖；5 个分支均停留在 waiting_user，未进入 SQL 执行。
- 主问题中有 7 个因模型调用失败停在问题理解阶段；这类结果与语义层或数据库查询失败分开统计。
- 6 个运行收到 API 错误；事件补拉不完整的运行数为 6/20，需要修复多轮 resume 的运行事件持久化或补拉游标。
- Q6 已进入 Agent 执行阶段，但因 SQL 查询未成功而被运行时守卫拒绝结束；Q10 分支暴露了澄清恢复后的状态未正确推进问题。
