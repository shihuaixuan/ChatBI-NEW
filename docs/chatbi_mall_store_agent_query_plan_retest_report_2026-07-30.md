# ChatBI“商城店铺主题”Agent 查询计划改造后真实测试报告

## 1. 测试结论

本轮基于当前工作区代码，使用真实 Chat Agent、真实模型、真实语义层和真实商城 MySQL 数据执行了 10 个不同分析层面的问题。所有业务筛选对象均使用具体 ID；Q10 出现结构化指标澄清后，5 个选项分别通过独立 Chat、Record 和 Agent Run 实际选择并完成验证。

本轮共执行 `15` 个 Agent Run：

- 10 个主问题 Run，其中 Q4 为批量测试前的独立真实冒烟 Run。
- Q10“客户数”的 5 个指标澄清分支 Run。

按“问题是否最终得到正确业务答案”判断，10 个场景通过 `6/10`，通过率为 `60%`：

- 通过：Q1、Q4、Q7、Q8、Q9、Q10。
- 失败：Q2、Q3、Q5、Q6。

Q10 的 5 个澄清选项全部通过，选项分支通过率为 `5/5`。

本轮主要结论如下：

1. **排名、分组、排序和 TopN 的上游表达已经可以工作。**
   - Q4 正确识别 `ranking_analysis`。
   - 正确把商品 ID 作为 `group_by` 维度。
   - 正确生成 `ORDER BY stock_qty DESC LIMIT 5` 并返回 Top5。

2. **具体业务 ID 没有被当作语义资产检索词。**
   - 店铺 ID `100011 / 100012`、商品 ID `P1000101001`、订单号 `USO202606300001` 均作为筛选值保留。
   - 语义检索负责匹配指标和维度资产，具体 ID 在检索后进入结构化过滤条件。

3. **比较问题的理解和可信查询计划已经修复，但暴露了新的编译结果资产回填问题。**
   - Q5 正确识别两个店铺 ID、`IN` 筛选、按店铺分组和比较意图。
   - 规则编译器可以生成正确 SQL。
   - 但编译结果按 `biz_name` 回填 `used_assets` 时，多个模型中的同名 `stall_id` 发生覆盖，Asset `278` 被错误回填为 Asset `315`，随后被编译后覆盖校验拒绝。

4. **普通单日查询仍可能被模型注入非法 `time_bucket`。**
   - Q2 的可信计划本身正确，但模型连续三次加入不需要的 `time_bucket`，均触发 `SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED`。
   - Q8 也发生两次相同错误，第三次去掉 `time_bucket` 后才成功。

5. **日期范围归一化和结构化澄清仍不稳定。**
   - Q3 的完整日期范围 `2026年6月1日至2026年6月30日` 被标记为 `unsupported`。
   - 问题理解已经判定 `clarification_required`，但没有真正创建 `clarify` Tool Call，Run 直接失败。

6. **显式分组排序问题仍可能在问题理解 DTO 校验阶段失败。**
   - Q6 在 Agent 工具循环开始前失败，步骤数和 Token 均为 `0`。
   - 错误为 `dimension_mentions 与 dimension_slots 必须一一对应`。

7. **重复检索、重复编译和预算熔断仍然存在。**
   - 15 个 Run 共发生 67 次工具调用，其中成功 51 次、失败 6 次、拒绝 10 次。
   - `search_semantic_assets` 有 5 次重复调用被拒绝。
   - `compile_semantic_sql` 20 次调用中只有 9 次成功，另有 6 次失败、5 次拒绝。
   - Q2、Q5 均在多次无效编译后以 `budget_exhausted` 结束，且没有进入 SQL 执行。

## 2. 测试环境

| 项目 | 值 |
|---|---|
| 测试日期 | 2026-07-30 |
| Git 分支 | `codex/headless-dataset-chat` |
| 基准提交 | `384dd036`，包含当前未提交工作区修改 |
| 主题域 | 商城店铺主题 |
| Domain ID | `244` |
| 数据集 | 商城店铺数据集 |
| Dataset ID | `243` |
| 数据源 | 商城 MySQL |
| Datasource ID | `13` |
| 用户 / 工作空间 | User ID `1` / Workspace ID `1` |
| Agent 最大步骤数 | `12` |
| 最大澄清次数 | `2` |
| Token 预算 | `100000` |
| 单次运行超时 | `120` 秒 |
| 数据日期范围 | 2026-06-01 至 2026-06-30 |

### 2.1 语义模型

| Model ID | 模型 | 物理表 |
|---:|---|---|
| `246` | 档口订单日事实表 | `fct_stall_order_daily` |
| `247` | 未发订单快照表 | `snap_unshipped_order` |
| `248` | 商品库存快照表 | `snap_product_inventory` |
| `249` | 客户交易日事实表 | `fct_customer_trade_daily` |
| `250` | 客户欠款快照表 | `snap_customer_arrears` |

### 2.2 测试使用的具体业务 ID

| 类型 | 测试值 |
|---|---|
| 店铺 / 档口 ID | `100011`、`100012` |
| 商品 ID | `P1000101001` |
| 未发订单号 | `USO202606300001` |

## 3. 十个测试问题设计

| # | 测试问题 | 覆盖层面 | 预期关键结构 |
|---:|---|---|---|
| Q1 | 2026年6月30日店铺100011的总下单客户数是多少？ | 单指标、单店铺、单日 | 指标聚合、等值筛选 |
| Q2 | 2026年6月30日店铺100011的销售类GMV、订货类GMV和总GMV分别是多少？ | 同模型多指标 | 一次编译三个指标 |
| Q3 | 2026年6月1日至2026年6月30日店铺100011的总GMV按天趋势如何？ | 日期范围、趋势、时间粒度 | 按日分组并按日期排序 |
| Q4 | 2026年6月30日店铺100011库存量最高的5个商品ID是什么？同时给出当前库存件数。 | 排名、分组、排序、TopN | 商品 ID 分组、库存降序、Limit 5 |
| Q5 | 2026年6月30日对比店铺100011和店铺100012的总GMV，哪个更高？ | 多 ID、比较、分组 | `IN` 筛选并按店铺分组 |
| Q6 | 2026年6月30日按店铺ID分组统计店铺100011和店铺100012的总订单数，并按总订单数从高到低排序。 | 显式分组、显式排序 | 店铺分组、订单数降序 |
| Q7 | 2026年6月30日店铺100011的未发订单号USO202606300001的订单金额、未发件数和是否超时是多少？ | 明细、多字段、订单 ID、布尔值 | 店铺与订单号双筛选 |
| Q8 | 2026年6月30日店铺100011的销售类GMV占总GMV的比例是多少？ | 占比、派生计算 | 查询分子和分母后计算比例 |
| Q9 | 2026年6月30日店铺100011商品ID为P1000101001的当前库存件数、是否负库存和近30天销量是多少？ | 双 ID、多指标、状态字段 | 店铺与商品双筛选 |
| Q10 | 2026年6月30日店铺100011的客户数是多少？ | 指标歧义、结构化澄清 | 返回候选指标并逐项恢复执行 |

## 4. 主问题执行结果

| # | Chat / Record / Run | 状态 | 步骤 / Token | 结果 | 判定 |
|---:|---|---|---:|---|---|
| Q1 | `7134 / 7790 / 313` | finished | `4 / 24478` | 总下单客户数 `3` | 通过 |
| Q2 | `7135 / 7792 / 314` | failed | `6 / 42003` | 三次非法时间粒度编译，未执行 SQL | 失败 |
| Q3 | `7136 / 7794 / 315` | failed | `1 / 6829` | 日期范围未归一化，未创建结构化澄清 | 失败 |
| Q4 | `7133 / 7788 / 312` | finished | `5 / 37447` | 正确返回库存 Top5 商品 | 通过 |
| Q5 | `7137 / 7796 / 316` | failed | `7 / 58614` | 正确计划被编译后覆盖校验拒绝，未执行 SQL | 失败 |
| Q6 | `7138 / 7798 / 317` | failed | `0 / 0` | 问题理解 DTO 校验失败 | 失败 |
| Q7 | `7139 / 7800 / 318` | finished | `6 / 36237` | 金额 `4752`、未发 `3`、已超时 | 通过，物理 SQL 兜底 |
| Q8 | `7140 / 7802 / 319` | finished | `7 / 52613` | 销售类GMV占比 `100%` | 通过，但存在两次无效编译 |
| Q9 | `7141 / 7804 / 320` | finished | `5 / 33850` | 库存 `460`、负库存 `0`、近30天销量 `60` | 通过 |
| Q10 | `7142 / 7806 / 321` | waiting_user | `2 / 11130` | 返回 5 个客户数指标候选 | 5 个分支全部通过 |

主 Run 状态分布：

- `finished`：5 个。
- `waiting_user`：1 个，属于预期澄清状态。
- `failed`：4 个。

## 5. Q10 全部澄清选项测试

主 Run 返回的澄清问题为：

> 您问的是2026年6月30日店铺100011的客户数，请问您想要的是哪一种客户数？

每个选项均新建独立 Chat，并恢复相同问题后选择对应候选。

| 选择的指标 | Asset ID / Model ID | Chat / Record / Run | 步骤 / Token | 查询结果 | 判定 |
|---|---|---|---:|---:|---|
| 销售下单客户数 | `272 / 246` | `7143 / 7808 / 322` | `5 / 30306` | `3` | 通过 |
| 订货下单客户数 | `273 / 246` | `7144 / 7810 / 323` | `5 / 32769` | `0` | 通过 |
| 总下单客户数 | `274 / 246` | `7145 / 7812 / 324` | `5 / 30659` | `3` | 通过 |
| 新增成交客户数 | `313 / 249` | `7146 / 7814 / 325` | `5 / 30725` | `0` | 通过 |
| 欠款客户数 | `310 / 250` | `7147 / 7816 / 326` | `5 / 29791` | `9` | 通过 |

5 个分支的工具路径完全一致：

```text
search_semantic_assets
→ clarify
→ compile_semantic_sql
→ execute_sql
→ finish
```

验证结果：

- 没有选项漂移。
- 没有绑定丢失。
- 没有恢复失败。
- 没有嵌套澄清。
- 5 个分支均使用所选 Asset 生成 SQL，并得到正确结果。

## 6. 通过场景的关键证据

### 6.1 Q1：单指标标准语义路径

可信编译计划：

```json
{
  "metric_asset_ids": [274],
  "dimension_asset_ids": [],
  "filters": [
    {"asset_id": 278, "operator": "=", "value": "100011"},
    {
      "asset_id": 276,
      "operator": "=",
      "value": {
        "kind": "absolute_range",
        "start": "2026-06-30",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai"
      }
    }
  ],
  "order_by": [],
  "limit": null
}
```

生成 SQL：

```sql
select sum(fct_stall_order_daily.order_customer_cnt_total)
       as order_customer_cnt_total
from fct_stall_order_daily fct_stall_order_daily
where fct_stall_order_daily.stall_id = '100011'
  and fct_stall_order_daily.stat_date >= '2026-06-30'
  and fct_stall_order_daily.stat_date < '2026-07-01'
limit 100
```

结果：总下单客户数为 `3`。

### 6.2 Q4：排名、分组、排序和 TopN

问题理解正确输出：

```json
{
  "intent_type": "ranking_analysis",
  "query_shape": {
    "limit": 5,
    "select_mode": "aggregate",
    "needs_group_by": true,
    "needs_order_by": true,
    "order_direction": "desc"
  },
  "dimension_slots": [
    {
      "name": "档口ID",
      "role": "filter",
      "value": "100011"
    },
    {
      "name": "商品ID",
      "role": "group_by",
      "value": null
    }
  ]
}
```

生成 SQL：

```sql
select snap_product_inventory.product_id as product_id,
       sum(snap_product_inventory.stock_qty) as stock_qty
from snap_product_inventory snap_product_inventory
where snap_product_inventory.stall_id = '100011'
  and snap_product_inventory.snapshot_date >= '2026-06-30'
  and snap_product_inventory.snapshot_date < '2026-07-01'
group by snap_product_inventory.product_id
order by stock_qty desc
limit 5
```

结果：

| 排名 | 商品 ID | 当前库存件数 |
|---:|---|---:|
| 1 | `P1000101014` | 515 |
| 2 | `P1000101016` | 504 |
| 3 | `P1000101015` | 500 |
| 4 | `P1000101018` | 478 |
| 5 | `P1000101001` | 460 |

该结果说明当前问题理解、语义检索、可信计划和规则编译器已经可以完成这个 TopN 场景。需要注意，检索成功后模型仍重复调用了一次 `search_semantic_assets`，被 `agent_action_not_available` 拒绝。

### 6.3 Q7：订单明细的物理 SQL 兜底

工具路径：

```text
search_semantic_assets
→ get_dataset_schema
→ get_sql_examples
→ validate_sql
→ execute_sql
→ finish
```

执行 SQL：

```sql
SELECT
    order_amount AS 订单金额,
    unshipped_qty AS 未发件数,
    is_overtime AS 是否超时
FROM snap_unshipped_order
WHERE snapshot_date = '2026-06-30'
  AND stall_id = 100011
  AND order_no = 'USO202606300001'
LIMIT 100
```

结果：

- 订单金额：`4752.00`。
- 未发件数：`3`。
- 是否超时：`1`，即已超时。

业务答案正确，但该路径依赖模型读取表结构后生成物理 SQL，并非标准语义编译路径。

### 6.4 Q8：占比查询成功，但重试代价较高

最终生成 SQL：

```sql
select sum(fct_stall_order_daily.gmv_sale) as gmv_sale,
       sum(fct_stall_order_daily.gmv_total) as gmv_total
from fct_stall_order_daily fct_stall_order_daily
where fct_stall_order_daily.stall_id = '100011'
  and fct_stall_order_daily.stat_date >= '2026-06-30'
  and fct_stall_order_daily.stat_date < '2026-07-01'
limit 100
```

结果：

- 销售类GMV：`8364.36`。
- 总GMV：`8364.36`。
- 占比：`100%`。

该 Run 使用 7 步、`52613` Token。成功前发生两次 `SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED` 和一次重复检索拒绝，说明最终答案正确，但工具选择和错误纠正仍不稳定。

### 6.5 Q9：具体商品 ID 的多指标查询

执行 SQL：

```sql
select sum(snap_product_inventory.stock_qty) as stock_qty,
       sum(snap_product_inventory.sales_qty_30d) as sales_qty_30d,
       sum(snap_product_inventory.is_negative_stock) as is_negative_stock
from snap_product_inventory snap_product_inventory
where snap_product_inventory.stall_id = '100011'
  and snap_product_inventory.product_id = 'P1000101001'
  and snap_product_inventory.snapshot_date >= '2026-06-30'
  and snap_product_inventory.snapshot_date < '2026-07-01'
limit 100
```

结果：

- 当前库存件数：`460`。
- 是否负库存：`0`，即否。
- 近30天销量：`60`。

店铺 ID 和商品 ID 均作为过滤值进入 SQL，没有参与资产名称检索。该 Run 同样发生一次重复语义检索拒绝。

## 7. 失败场景根因分析

### 7.1 Q2：可信计划正确，模型注入非法 `time_bucket`

问题理解和可信计划已经正确：

```json
{
  "intent_type": "metric_query",
  "query_shape": {
    "select_mode": "aggregate"
  },
  "metric_asset_ids": [269, 270, 271],
  "dimension_asset_ids": [],
  "filters": [
    {"asset_id": 278, "operator": "=", "value": "100011"},
    {
      "asset_id": 276,
      "operator": "=",
      "value": {
        "kind": "absolute_range",
        "start": "2026-06-30",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai"
      }
    }
  ],
  "order_by": [],
  "limit": null
}
```

但模型第一次编译时额外传入：

```json
{
  "time_bucket": {
    "start": "2026-06-30",
    "end": "2026-07-01"
  }
}
```

第二、三次改为：

```json
{
  "time_bucket": {
    "start": "2026-06-30",
    "end": "2026-07-01",
    "granularity": "day"
  }
}
```

三次均返回：

```text
SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED
```

之后第四次相同编译被 `tool_call_budget_rejected` 拒绝，Run 以 `budget_exhausted` 结束。

数据库基准结果为：

| 指标 | 正确结果 |
|---|---:|
| 销售类GMV | 8364.36 |
| 订货类GMV | 0 |
| 总GMV | 8364.36 |

真正根因不是指标匹配失败，而是可信查询计划只覆盖和约束了指标、维度、筛选、排序和条数，没有统一决定或清除 `time_bucket`。对于普通单日 `metric_query`，是否需要 `time_bucket` 应由服务端依据 `query_shape` 决定，不能继续允许模型自由补充。

### 7.2 Q3：日期范围未归一化，且未进入结构化澄清

问题理解结果：

```json
{
  "intent_type": "trend_analysis",
  "time_range": {
    "raw": "2026年6月1日至2026年6月30日",
    "normalized": {
      "kind": "unsupported",
      "raw": "2026年6月1日至2026年6月30日",
      "timezone": "Asia/Shanghai"
    },
    "value_status": "provided"
  },
  "query_shape": {
    "time_grain": "day",
    "select_mode": "aggregate",
    "needs_group_by": true
  }
}
```

校验结果已经正确指出：

```json
{
  "status": "clarification_required",
  "reason_codes": ["time_range_unsupported"],
  "clarification_slots": ["time_range"]
}
```

但系统没有创建真实 `clarify` Tool Call，Run 没有任何工具调用，并以 `sql_failed` 结束。

数据库存在完整 30 天数据：

- 行数：`30`。
- 最低单日总GMV：`5861.80`。
- 最高单日总GMV：`33362.72`。
- 30天合计：`458248.85`。
- 日均：`15274.96`。

该问题包含两个独立缺陷：

1. 日期归一化没有识别完整中文绝对日期范围。
2. 当问题理解返回 `clarification_required` 时，控制流没有确定性地转换为结构化澄清。

### 7.3 Q5：比较计划已正确，`used_assets` 跨模型同名覆盖

问题理解已正确识别：

```json
{
  "intent_type": "comparison_analysis",
  "dimension_slots": [
    {
      "name": "档口ID",
      "role": "filter",
      "value": ["100011", "100012"]
    }
  ],
  "query_shape": {
    "select_mode": "aggregate",
    "needs_group_by": true
  }
}
```

可信编译计划也正确：

```json
{
  "metric_asset_ids": [271],
  "dimension_asset_ids": [278],
  "filters": [
    {
      "asset_id": 278,
      "operator": "in",
      "value": ["100011", "100012"]
    },
    {
      "asset_id": 276,
      "operator": "=",
      "value": {
        "kind": "absolute_range",
        "start": "2026-06-30",
        "end_exclusive": "2026-07-01",
        "timezone": "Asia/Shanghai"
      }
    }
  ],
  "order_by": [],
  "limit": null
}
```

规则编译器能够生成正确 SQL：

```sql
select fct_stall_order_daily.stall_id as stall_id,
       sum(fct_stall_order_daily.gmv_total) as gmv_total
from fct_stall_order_daily fct_stall_order_daily
where fct_stall_order_daily.stall_id in ('100011', '100012')
  and fct_stall_order_daily.stat_date >= '2026-06-30'
  and fct_stall_order_daily.stat_date < '2026-07-01'
group by fct_stall_order_daily.stall_id
limit 100
```

问题发生在编译结果资产回填阶段。当前实现位于：

```text
backend/apps/semantic/services/sql_compilation_service.py::_used_assets
```

核心逻辑为：

```python
element_by_name = {element.biz_name: element for element in elements}
```

商城数据集的多个语义模型都存在 `biz_name = "stall_id"`。字典只按 `biz_name` 建键，后出现的元素会覆盖前一个元素，因此编译结果把本次使用的 Dimension Asset `278` 错误回填为另一个模型中的 Asset `315`。

随后编译后覆盖校验发现：

- 可信计划要求 Asset `278`。
- 编译结果证据声明使用 Asset `315`。

因此正确拒绝执行并返回：

```text
compiled_query_plan_not_covered
```

此后模型又发起重复检索、加入非法 `time_bucket` 并重复编译，最终触发预算熔断。

数据库基准结果为：

| 店铺 ID | 总GMV |
|---|---:|
| `100012` | 19040.40 |
| `100011` | 8364.36 |

正确结论是店铺 `100012` 更高。

这个问题不能通过放宽覆盖校验解决。覆盖校验阻止了错误资产证据进入 SQL 执行，真正需要修复的是 `_used_assets` 的资产定位方式：资产身份必须同时包含模型或表上下文，不能只依赖跨模型不唯一的 `biz_name`。

### 7.4 Q6：问题理解 DTO 契约失败

Q6 在问题理解阶段直接失败，错误为：

```text
DIMENSION_RECOGNITION_MODEL_OUTPUT_INVALID:
dimension_mentions 与 dimension_slots 必须一一对应
```

Run 没有进入 Agent 工具循环：

- Agent 步骤：`0`。
- Token：`0`。
- 工具调用：`0`。

数据库基准结果为：

| 店铺 ID | 总订单数 |
|---|---:|
| `100012` | 5 |
| `100011` | 4 |

这个问题不是 SQL 编译器不会处理分组或排序，而是模型输出在进入检索前就违反了 DTO 约束。当前“修复模型输出后重试”的机制仍未稳定地产生满足 `dimension_mentions` 与 `dimension_slots` 一一对应关系的结果。

## 8. 工具调用统计

### 8.1 总体统计

| 状态 | 次数 | 占比 |
|---|---:|---:|
| succeeded | 51 | 76.1% |
| failed | 6 | 9.0% |
| rejected | 10 | 14.9% |
| 合计 | 67 | 100% |

### 8.2 按工具统计

| 工具 | 总计 | 成功 | 失败 | 拒绝 |
|---|---:|---:|---:|---:|
| `search_semantic_assets` | 18 | 13 | 0 | 5 |
| `compile_semantic_sql` | 20 | 9 | 6 | 5 |
| `execute_sql` | 10 | 10 | 0 | 0 |
| `finish` | 10 | 10 | 0 | 0 |
| `clarify` | 6 | 6 | 0 | 0 |
| `get_dataset_schema` | 1 | 1 | 0 | 0 |
| `get_sql_examples` | 1 | 1 | 0 | 0 |
| `validate_sql` | 1 | 1 | 0 | 0 |

### 8.3 错误码统计

| 错误码 | 次数 | 含义 |
|---|---:|---|
| `SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED` | 6 | 模型为不需要或不支持时间桶的查询传入 `time_bucket` |
| `agent_action_not_available` | 5 | 已完成检索后再次发起重复检索 |
| `compiled_query_plan_not_covered` | 3 | Q5 编译结果的资产证据没有覆盖可信计划 |
| `tool_call_budget_rejected` | 2 | 同类失败重复达到预算限制后拒绝继续调用 |

### 8.4 工具统计反映的问题

1. SQL 一旦进入执行阶段，`execute_sql` 和 `finish` 的成功率均为 `100%`。
2. 当前主要失败集中在执行前的理解、计划参数生成和编译结果校验阶段。
3. `compile_semantic_sql` 是当前最不稳定的环节，成功率为 `9/20`。
4. 工作状态守卫能够拒绝重复检索和超预算编译，但拒绝发生前，模型规划所消耗的步骤和 Token 无法收回。
5. 因此不能只继续增加预算；需要减少模型可自由修改的编译参数，并为已知错误提供确定性的下一动作。

## 9. 与上一轮测试的变化

### 9.1 已改善

1. **Q4 从失败变为成功。**
   - 上一轮不能稳定识别商品 ID 的分组角色。
   - 本轮问题理解明确输出 `商品ID / group_by`，并正确完成 Top5。

2. **Q5 的上游比较计划已经修复。**
   - 两个店铺值被保留为数组。
   - 过滤条件正确使用 `operator: "in"`。
   - `dimension_asset_ids` 正确包含店铺维度 Asset `278`。
   - `query_shape.needs_group_by` 为 `true`。

3. **Q10 澄清恢复保持稳定。**
   - 5 个选项仍全部成功，没有出现候选资产或选择上下文丢失。

### 9.2 新暴露或仍未解决

1. Q5 上游计划修复后，暴露了 `_used_assets` 按跨模型非唯一 `biz_name` 回填资产的问题。
2. Q2 本轮因模型注入 `time_bucket` 失败，说明相同问题在不同模型采样中仍可能从成功变为失败。
3. Q3 的中文绝对日期范围仍未被归一化。
4. Q6 表明问题理解 DTO 的严格约束与模型修复输出之间仍不稳定。
5. 重复检索和失败后重复编译仍然存在，预算守卫只能停止损耗，不能指导模型正确纠错。

## 10. 修复优先级建议

本报告只记录测试和问题，不修改代码。建议后续按以下顺序处理：

1. **P0：修复编译结果资产身份回填。**
   - `_used_assets` 不能只按 `biz_name` 查找。
   - 应使用模型 ID、表、资产 ID 或编译节点来源等能够唯一定位资产的上下文。
   - 保留现有 `compiled_query_plan_not_covered` 校验，不要放宽。

2. **P0：让服务端统一决定 `time_bucket`。**
   - 普通单日聚合查询强制清空。
   - 只有趋势或明确时间分组问题才根据 `query_shape.time_grain` 生成。
   - 模型不应自由构造该参数。

3. **P0：补全中文绝对日期范围归一化。**
   - 至少覆盖“YYYY年M月D日至YYYY年M月D日”等明确范围表达。
   - 归一化结果应统一为半开区间 `start / end_exclusive`。

4. **P0：将 `clarification_required` 确定性路由到结构化澄清。**
   - 不允许模型用普通文本代替 `clarify` Tool Call。

5. **P1：稳定问题理解 DTO 的模型输出修复。**
   - 对 `dimension_mentions` 和 `dimension_slots` 建立单一、可自动修复的生成规则。
   - 修复重试后仍不满足契约时，应保留原始输出和具体差异，便于定位。

6. **P1：错误码驱动下一动作。**
   - `agent_action_not_available` 后直接采用服务端推荐动作。
   - `SEMANTIC_SQL_TIME_GRAIN_UNSUPPORTED` 后由服务端删除不合法时间桶，避免模型重复猜测。
   - `compiled_query_plan_not_covered` 应重新构建资产证据或终止，不应继续重复相同编译。

## 11. 最终判断

当前 Agent 已经能够完成单指标、多 ID 筛选、TopN、占比、具体商品查询和结构化指标澄清。Q4 的成功表明，比较、排名、分组、排序和 TopN 并非必须立即切换到 LLM SQL 生成器，至少结构明确的单模型 TopN 可以继续由可信计划和规则编译器稳定生成。

当前通过率受四类执行前问题影响：

1. 时间范围归一化失败。
2. 问题理解 DTO 输出不稳定。
3. 模型可注入不受可信计划约束的编译参数。
4. 编译结果使用跨模型不唯一字段名回填资产身份。

其中 Q5 的失败尤其说明：SQL 文本本身可以正确，但 SQL 的资产证据仍可能错误。后续无论加入 LLM SQL 生成、LLM SQL 校验，还是继续扩展规则编译器，都必须以唯一资产身份和可信查询计划为基础，否则校验阶段仍会出现相同问题。
