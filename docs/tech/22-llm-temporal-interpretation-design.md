# ChatBI 模型时间理解技术设计

## 1. 文档状态

本文描述 ChatBI 时间处理从“自研固定词组和正则解析”演进为“自然语言解析器或模型理解、服务端确定性计算”的目标方案。

本文同时描述整体目标和分阶段状态。第零阶段固定评估基线、第一阶段时间计划契约和结构化解析器、
第二阶段独立模型时间任务及 Agent 旁路观察、第三阶段 Agent 与 Graph 权威时间接入，以及第四阶段原文解析器
替换代码均已完成。权威路径由 `TEMPORAL_MODEL_AUTHORITY_ENABLED` 控制并默认关闭。第二阶段已经完成
固定样本和既有 Agent Run 的离线只读回放，但重复回放仍出现计划状态和模型输出合法性波动，三轮重复回放的
完整时间语义稳定率只有 70%。因此第三阶段仍是“实现完成但生产启用准入未满足”，第四阶段则已完成 JioNLP
接入和回归验证。没有修改环境开关，也没有将不稳定模型结果用于线上执行。
当前线上时间处理机制见
[ChatBI 确定性时间处理技术文档](./21-chatbi-deterministic-temporal-processing.md)。

当前 Agent 默认执行边界已调整为：问题理解只负责重写和意图识别，保留 `time_range.raw`；ReAct
首轮在需要时并发调用 `parse_time_range` 与 `search_semantic_assets`。时间 Tool 使用 JioNLP
和 Run 固定的 `TemporalContext` 生成绝对范围，结果投影后再进入语义查询计划和 SQL 编译。

## 2. 背景

当前 `backend/apps/temporal` 的普通文本时间入口使用 JioNLP 和项目适配层；结构化计划仍由服务端校验和计算：

- `extractor.py` 调用 JioNLP 时间实体抽取，判断文本是否属于时间表达。
- `jionlp_adapter.py` 调用 JioNLP 时间解析，并转换为项目绝对范围结构。
- `resolver.py` 负责 Run 时间上下文、自然周配置和结构化计划解析。
- `calendar.py` 提供结构化计划所需的日期边界计算。
- `sql_renderer.py` 只将可信绝对范围渲染成 SQL。

JioNLP 负责自然语言时间识别和初步解析，项目适配层负责固定 Run 基准时间、结果结构转换和边界校验。
当前不为财年文本增加项目专用解析规则；`fiscal_period` 结构化契约仍保留用于既有计划兼容。

大模型已经能够较好地理解开放的自然语言时间表达，因此适合承担语言理解任务。但日期边界计算、时区、
自然周配置、Run 回放和 SQL 安全不能直接依赖未经校验的解析器或模型结果。

## 3. 设计目标

### 3.1 目标

1. 使用独立模型任务理解开放的自然语言时间表达。
2. 示例只作为模型指导，不再作为表达白名单。
3. 模型只输出结构化时间计划，不生成 SQL 和可信绝对日期。
4. 服务端使用固定 `TemporalContext` 计算绝对时间范围。
5. Agent 与 Graph 共用时间计划契约、校验器和解析器。
6. 时间歧义必须在语义资产检索和 SQL 编译前进入用户澄清。
7. Run 重试、澄清恢复和回放复用同一时间计划和时间上下文。
8. 保持现有左闭右开时间范围和 SQL 渲染安全边界。

### 3.2 非目标

本方案暂不处理：

- 工作日计算。
- 节假日计算。
- 企业日历计算。
- 由模型直接生成 SQL。
- 由模型选择时间维度资产或数据库字段。
- 用模型当前日期替代 Run 的 `reference_at`。

## 4. 核心设计原则

### 4.1 模型理解语言，代码计算日期

模型负责：

- 找出时间表达。
- 判断表达类型。
- 区分查询筛选、指标口径、时间分组和比较时间。
- 识别缺失信息、冲突和多个合理解释。
- 输出严格的结构化时间计划。

服务端负责：

- 校验模型输出结构。
- 校验时间业务约束。
- 使用 `TemporalContext` 计算日期边界。
- 绑定时间维度。
- 生成时间分桶。
- 渲染 SQL 时间条件。
- 持久化、恢复和审计。

### 4.2 模型输出不是可信执行事实

模型不能直接产生可进入 SQL 编译器的可信时间范围。只有经过以下步骤后，时间结果才可执行：

```text
模型时间计划
  → 严格结构校验
  → 时间业务校验
  → 确定性日期计算
  → ResolvedTemporalPlan
  → 兼容 TimeRange 投影
  → 时间维度绑定
  → SQL 编译
```

### 4.3 一个权威事实源

目标状态下，`TemporalPlan` 是时间语义的权威事实源，现有 `TimeRange` 只是下游兼容投影。

禁止让模型生成的 `time_range` 和服务端生成的 `TemporalPlan` 分别参与执行，否则两个结构可能发生漂移。

## 5. 目标运行流程

```text
用户问题
  → 问题重写
  → 指标和时间线索抽取
  → 独立模型时间理解任务
  → 严格结构校验
  → 时间业务校验
  ├─ 存在业务歧义：生成时间澄清并暂停 Run
  ├─ 模型调用或输出失败：明确终止当前阶段
  └─ 时间语义明确：确定性计算绝对范围
        → 合并问题理解结果
        → 时间维度绑定
        → 语义资产检索
        → SQL 编译
        → SQL 执行
```

默认 Agent 路径的时间解析属于 ReAct 中的只读 Tool，不是问题理解前置阶段的固定模型子任务。提示词要求
在时间表达存在时与语义检索并发调用；Tool 结果只允许服务端结果投影更新时间范围和查询计划，Agent 不能修改解析结果。

## 6. 数据契约

### 6.1 TemporalInterpretationOutput

建议新增模型时间任务的权威输出：

```json
{
  "schema_version": "1",
  "status": "resolved",
  "expressions": [
    {
      "raw": "过去七天",
      "source": "rewritten_question",
      "start_offset": 0,
      "end_offset": 4,
      "role": "query_filter",
      "kind": "rolling_range",
      "direction": "past",
      "amount": 7,
      "unit": "day",
      "include_reference_date": true
    }
  ],
  "grouping": {
    "grain": "day"
  },
  "comparison": null,
  "ambiguities": [],
  "confidence": 0.96
}
```

### 6.2 status

| 值 | 含义 | 后续动作 |
| --- | --- | --- |
| `no_time` | 用户没有表达全局时间要求 | 不生成时间筛选 |
| `resolved` | 时间语义完整且可进入确定性解析 | 计算绝对范围 |
| `clarification_required` | 存在缺失信息或多个合理解释 | 询问用户 |
| `unsupported` | 无法形成系统支持的时间计划 | 要求用户换一种方式表达或提供明确日期 |

`unsupported` 只描述业务表达无法转换，不能用于表示模型服务超时、网络错误或输出格式错误。

`resolved` 表示整个时间任务已经明确，不要求必须存在查询筛选。只要求时间分组的请求可以使用
`expressions=[]` 和非空 `grouping`。`resolved` 至少需要在 `expressions`、`grouping` 中包含一项；
第一版不处理 `comparison`。

### 6.3 role

每个时间表达必须明确用途：

| 值 | 含义 | 是否生成全局 SQL 时间筛选 |
| --- | --- | --- |
| `query_filter` | 查询数据范围 | 是 |
| `metric_definition` | 属于完整指标口径 | 否 |

例如“近 7 天销量”可能是完整指标名称。此时“近 7 天”应标记为 `metric_definition`，不能再次生成全局
时间筛选。

第一版只允许 `query_filter` 和 `metric_definition`。`comparison_base`、`comparison_target` 随第五阶段
时间比较能力一起加入，避免在尚无比较解析器时提前形成不可执行契约。

### 6.4 kind

第一版只支持以下类型：

| 类型 | 必需字段 | 说明 |
| --- | --- | --- |
| `absolute_date` | `date` | 用户明确提供的单个日期 |
| `absolute_range` | `start`、`end_inclusive` | 用户明确提供的日期范围 |
| `relative_date` | `offset_days` | 相对基准日偏移 |
| `rolling_range` | `direction`、`amount`、`unit`、`include_reference_date` | 最近或过去 N 个周期 |
| `calendar_period` | `unit`、`offset` | 本周、上月、上上季度等自然周期 |
| `fiscal_period` | `unit`、相对或指定财年字段 | 本财年、FY2026 Q2 等财政周期 |

不同 `kind` 使用 Pydantic 可区分联合类型定义。每种类型只允许自己的字段，避免一个数据类包含大量互相
冲突的可选字段。

所有表达共同包含：

- `raw`：重写问题或确认上下文中的原始片段。
- `source`：第一版只允许 `rewritten_question` 或 `user_confirmation`。
- `start_offset`、`end_offset`：原始片段在 `source` 文本中的左闭右开字符位置；两个字段必须同时存在。
- `role`：第一版只允许 `query_filter` 或 `metric_definition`。

字段规则：

- `absolute_date.date`、`absolute_range.start` 和 `absolute_range.end_inclusive` 必须是包含年份的 ISO 日期。
  “7 月 1 日”这类缺少年份且无法从确认上下文唯一确定的表达进入澄清。
- `rolling_range.unit` 第一版只允许 `day`、`week`、`month`、`year`，`amount` 必须大于零。
- `calendar_period.unit` 只允许 `week`、`month`、`quarter`、`year`，`offset=0` 表示当前周期，
  `offset=-1` 表示上一周期。
- `fiscal_period` 使用 `unit=year|quarter`。相对财政周期使用 `offset`；指定财年使用
  `fiscal_year`，指定财季还必须提供 `fiscal_quarter`。指定财年和相对偏移不能同时存在。

`relative_weekday`、`period_to_date`、时间比较和多个查询时间范围放到第五阶段，不进入第一版契约。

### 6.5 grouping

时间分组与时间筛选分开表示：

```json
{
  "grouping": {
    "grain": "day"
  }
}
```

`grain` 只允许：

- `day`
- `week`
- `month`
- `quarter`
- `year`

模型只能表达用户要求的时间粒度，不能直接生成编译参数 `time_bucket`。服务端仍通过
`derive_time_bucket` 和已绑定的时间维度生成最终分桶计划。

### 6.6 comparison

时间比较独立于基础筛选，但不进入第一版可执行契约。第一版 `comparison` 必须为 `null`：

```json
{
  "comparison": {
    "kind": "previous_period"
  }
}
```

第五阶段可以扩展：

- `previous_period`
- `year_over_year`
- `month_over_month`
- `custom_period`

比较目标的绝对范围必须由服务端根据基础范围计算，不能直接信任模型日期运算。

### 6.7 clarification_required 示例

用户问题：

```text
查一下最近的销售额
```

模型输出：

```json
{
  "schema_version": "1",
  "status": "clarification_required",
  "expressions": [],
  "grouping": null,
  "comparison": null,
  "ambiguities": [
    {
      "code": "time_range_amount_missing",
      "raw": "最近"
    }
  ],
  "confidence": 0.72
}
```

模型只返回歧义代码和原始表达。面向用户的问题、选项和允许写入的状态路径由服务端生成。
第一版歧义代码是受控枚举，只允许：

- `time_range_amount_missing`
- `time_range_unit_missing`
- `time_range_conflict`
- `time_role_ambiguous`
- `time_calendar_ambiguous`
- `time_reference_inclusion_ambiguous`
- `time_expression_unsupported`

模型不得新造同义代码、改变大小写或把指标定义内部时间作为全局时间歧义。

## 7. 模型任务设计

### 7.1 输入

模型时间任务接收：

```json
{
  "rewritten_question": "过去七天按天查看店铺20001的客户数",
  "metric_mentions": ["客户数"],
  "time_mentions": ["过去七天", "按天"],
  "conversation_context": {},
  "temporal_config": {
    "timezone": "Asia/Shanghai",
    "locale": "zh-CN",
    "week_start": "monday",
    "fiscal_year_start_month": 1,
    "fiscal_year_label": "start_year"
  },
  "user_feedback": {}
}
```

模型不需要通过系统时间推测“今天”。`reference_at` 可以作为解释上下文提供，但模型不能将相对表达自行
计算成可信绝对日期。

### 7.2 指导表

下面的示例用于指导模型理解语义，不是表达白名单：

| 类型 | 非穷举示例 | 模型输出规则 |
| --- | --- | --- |
| 单日相对时间 | 今天、今日、当天、昨天、前天 | `relative_date + offset_days` |
| 滚动范围 | 最近 7 天、过去两周、往前看 3 个月 | `rolling_range` |
| 自然周期 | 本周、上上周、这个月、去年第四季度 | `calendar_period` |
| 明确日期 | 2026-07-31、2026 年 7 月 31 日 | `absolute_date` |
| 明确范围 | 7 月 1 日至 7 月 31 日 | `absolute_range` |
| 截止周期 | 本月至今、年初至今、截至昨天 | `period_to_date` |
| 财政周期 | 本财年、FY2026 Q2 | `fiscal_period` |
| 相对星期 | 上周三、下个月第二个周五 | 结构化星期计划 |
| 时间分组 | 按天、每周、按月趋势 | `grouping.grain` |
| 时间比较 | 同比、环比、较上期 | `comparison` |

### 7.3 提示词硬约束

提示词必须规定：

1. 只处理时间，不回答业务问题。
2. 不生成 SQL、表名、字段名和资产 ID。
3. 保留每个时间表达的用户原文。
4. 示例不是支持范围白名单。
5. 不补充用户没有表达的数量、单位、日期和比较对象。
6. 多个时间表达分别输出，不能合并成一个字符串。
7. 冲突表达全部保留并进入 `ambiguities`。
8. “最近”“前段时间”等模糊表达不能默认解释为最近 7 天。
9. 指标定义内部的时间不能作为全局筛选。
10. 模型不能输出 `time_bucket`。
11. 相对时间不能输出模型自行计算的绝对日期。
12. 只能输出一个符合目标结构的 JSON 对象。
13. 每个表达必须返回可由服务端验证的 `source`、`start_offset` 和 `end_offset`。
14. 全局 `query_filter` 与指标内部 `metric_definition` 可以同时存在，二者本身不构成冲突。
15. “最近、近、过去、往前看 N 个周期”第一版统一包含基准日，必须返回
    `include_reference_date=true`。
16. 歧义代码必须取自服务端受控枚举。

### 7.4 模型调用次数

一次问题理解最多允许：

- 一次正常时间理解调用。
- 一次因结构校验失败触发的修复调用。

时间任务不能进入 Agent 工具循环，也不能因为后续检索失败而重复调用。
每次调用使用 `QUERY_UNDERSTANDING_TIMEOUT_MS` 配置的请求超时；适配器复制模型配置后注入超时秒数，
不修改持久化模型配置或 Agent 共用配置。

## 8. 服务端校验

### 8.1 结构校验

使用严格 Pydantic 模型：

- `extra="forbid"`。
- `amount` 必须大于零。
- `unit`、`role`、`kind` 和 `grain` 必须是受控枚举。
- 日期必须是合法 ISO 日期。
- `resolved` 必须包含至少一个可解析表达或非空 `grouping`。
- `clarification_required` 必须包含歧义信息。
- 歧义代码必须属于第一版受控枚举。
- 不同 `kind` 只能包含自身允许的字段。
- `confidence` 必须在 0 到 1 之间。

第一次结构校验失败时，将精确字段错误反馈给模型。第二次仍失败时抛出明确错误，不使用空时间或默认时间。

### 8.2 业务校验

服务端需要集中校验：

1. `source[start_offset:end_offset]` 是否严格等于 `raw`；没有位置字段的迁移数据才退回原文包含校验。
2. 是否出现多个互相冲突的 `query_filter`。
3. 时间比较是否存在基础范围。
4. 时间分组是否和其他问题理解结果冲突。
5. 财政时间是否符合当前企业财政配置。
6. `metric_definition` 是否被错误投影为全局筛选。
7. 模型是否为相对表达提交了不可信绝对日期。
8. 用户确认结果是否属于当前挂起的时间问题。
9. 绝对范围是否满足 `start < end_exclusive`。
10. 时间范围是否超过允许的最大跨度。
11. 被完整指标短语包含的时间表达必须使用 `metric_definition`，也不能被提交为时间歧义。

业务歧义和系统故障必须分开处理：

| 情况 | 处理 |
| --- | --- |
| 表达缺少数量、单位或存在多个解释 | 询问用户 |
| 模型调用超时或网络失败 | 返回明确系统错误 |
| 模型输出结构连续两次非法 | 返回明确模型输出错误 |
| 确定性日期计算失败 | 返回明确时间解析错误 |
| 用户明确表达了暂不支持的业务时间 | 要求用户提供明确日期或换一种表达 |

## 9. 确定性日期计算

模型输出：

```json
{
  "raw": "过去七天",
  "role": "query_filter",
  "kind": "rolling_range",
  "direction": "past",
  "amount": 7,
  "unit": "day",
  "include_reference_date": true
}
```

服务端使用固定 `TemporalContext` 计算：

```json
{
  "kind": "absolute_range",
  "start": "2026-07-25",
  "end_exclusive": "2026-08-01",
  "timezone": "Asia/Shanghai",
  "source_raw": "过去七天"
}
```

计算要求：

- 只读取 `TemporalContext.reference_at`。
- 统一输出左闭右开范围。
- 月份平移使用目标月末收敛规则。
- 自然周使用统一周起始配置。
- 财政周期读取统一财政年度配置。
- Run 恢复时不得重新读取系统当前日期。

现有 `calendar.py` 继续负责日期边界计算，现有 `sql_renderer.py` 继续只接受 `absolute_range`。

第一版滚动范围采用以下确定性规则：

- `day` 和 `week` 使用固定天数计算。
- `month` 和 `year` 使用日历月平移；目标月份不存在同一日时收敛到目标月末。
- `include_reference_date=true` 时范围包含基准日，否则从基准日前一天或后一天开始。
- “本月”“上个月”必须使用 `calendar_period`，不能使用 `rolling_range`。
- 最大允许跨度由调用方提供的统一策略参数校验；第一阶段不在 Temporal 模块内写死业务天数。

## 10. 时间澄清

### 10.1 澄清时机

以下情况必须在语义资产检索前暂停：

- 时间范围缺少数量或单位。
- 自然周期和财政周期存在歧义。
- 同时出现多个冲突时间。
- 比较查询缺少基准时间。
- 时间分组粒度冲突。

### 10.2 澄清选项

模型只返回歧义代码。服务端根据歧义代码生成选项。例如 `time_range_amount_missing` 可以生成：

- 最近 7 天。
- 最近 30 天。
- 本月。
- 自定义时间。

选项值应携带服务端定义的结构化时间计划，不能只保存展示文本。

### 10.3 澄清恢复

- 用户选择结构化选项后，直接校验并解析选中的时间计划。
- 用户输入自由文本后，只重新运行一次时间理解任务。
- 已经确认的时间计划写入 Run 状态。
- 恢复后复用原 `TemporalContext`。
- 不重新执行已完成的资产检索和无关模型任务。

## 11. Agent 接入

当前 Agent 问题理解入口是
`apps/chatbi/services/understanding/understanding_service.py::QuestionUnderstandingService.understand`。

目标顺序：

1. 问题重写。
2. 指标和分析形态识别。
3. 意图识别和维度识别并行。
4. 合并问题理解结果，只保留原始时间表达。
5. 统一问题理解校验。
6. 进入 ReAct；时间表达存在时，与语义资产检索并发调用时间 Tool。
7. 时间 Tool 返回不支持时调用澄清；语义歧义和时间歧义可以进入同一个澄清队列。
8. 时间结果和语义结果投影完成后，才允许 SQL 编译。

需要扩展 Agent 的前置澄清逻辑，使其能够处理：

- `time_range_ambiguous`
- `time_range_conflict`
- `time_comparison_incomplete`
- `time_grain_conflict`

时间 Tool 是否需要调用由提示词和 `time_range.raw` 共同约束；Tool 本身使用固定上下文并返回结构化结果，不能被模型绕过到 SQL。

## 12. Graph 接入

当前 Graph 将分析形态、指标时间线索和维度识别拆成并行子任务。

为了控制第一阶段改动范围，先采用：

1. 保留现有 `semantic` 子任务抽取 `time_mentions` 和 `time_range.raw`。
2. 三个原有子任务结束后调用共享时间理解服务。
3. 将 `TemporalPlan` 交给统一意图投影。
4. 校验和解析成功后再进入知识检索。

稳定后再考虑将“时间抽取 + 时间解释”合并成独立并行子任务，并将原 `semantic` 子任务收敛为指标线索任务。

Graph 当前共享校验能够产生 `time_range_unsupported`，但 Graph 投影主要处理维度和主题域槽位问题。
接入时必须将时间问题正式投影为 `clarification_required`，避免不支持的时间继续进入知识检索。

## 13. 持久化与恢复

需要持久化：

- 原始时间表达。
- 模型输出的时间计划。
- 服务端校验后的时间计划。
- 解析后的绝对范围。
- 时间计划版本。
- 模型和提示词版本。
- 解释来源。
- 固定 `TemporalContext`。

解释来源建议只允许：

- `jionlp`
- `model`
- `user_confirmation`

历史 Run 可能保留 `legacy_rule`，新兼容入口不再写入该来源。

恢复规则：

- 已有校验通过的时间计划时，不重新调用模型。
- 已有绝对范围时，不重新读取当前日期。
- 提示词或模型版本变化不能修改已经创建的 Run。
- 新问题创建新 Run 时才使用新版本时间规则。

## 14. 可观测性

时间任务至少记录：

- `stage=TEMPORAL_INTERPRETATION`
- 模型名称和版本。
- 提示词版本。
- 时间计划 schema 版本。
- 调用耗时。
- token 用量。
- 是否触发结构修复。
- 最终 `status`。
- 解释来源。
- 歧义代码。
- 确定性解析错误码。

建议监控：

- 模型时间理解成功率。
- 结构修复率。
- 时间澄清率。
- `unsupported` 比例。
- JioNLP 解析覆盖率和失败率。
- Agent 与 Graph 时间计划一致率。
- 时间任务平均延迟和 token 用量。

## 15. 兼容策略

迁移期间保留现有 `TimeRange`：

```json
{
  "raw": "过去七天",
  "value_status": "provided",
  "normalized": {
    "kind": "absolute_range",
    "start": "2026-07-25",
    "end_exclusive": "2026-08-01",
    "timezone": "Asia/Shanghai"
  }
}
```

但 `TimeRange` 只能由校验通过的时间计划投影产生：

```text
TemporalPlan
  → ResolvedTemporalPlan
  → TimeRange
  → 时间维度绑定和 SQL 编译
```

JioNLP 是当前原始时间表达的确定性兼容路径。使用该路径时记录
`interpretation_source=jionlp`；无法解析时返回明确的 `unsupported`，禁止静默回退。

## 16. 文件调整计划

### 16.1 建议新增

| 文件 | 职责 |
| --- | --- |
| `backend/apps/temporal/plan.py` | 时间计划数据结构、严格字段校验和时间业务校验 |
| `backend/apps/temporal/errors.py` | 时间计划校验和结构化解析的稳定错误类型 |
| `backend/apps/chatbi/services/understanding/temporal_interpretation.py` | 提示词、模型调用、一次修复重试和输出投影 |
| `backend/scripts/evaluate_temporal_shadow.py` | 只读汇总已持久化的 Agent Run 旁路观察 |
| `backend/scripts/replay_temporal_shadow.py` | 只读回放既有 Agent Run 的问题理解快照 |
| `backend/tests/temporal/test_temporal_plan.py` | 时间计划结构和业务校验测试 |
| `backend/tests/temporal/test_structured_temporal_resolver.py` | 结构化时间计划确定性解析测试 |
| `backend/tests/chatbi/test_temporal_interpretation_service.py` | 模型任务、修复和失败测试 |

### 16.2 建议修改

| 文件 | 修改内容 |
| --- | --- |
| `backend/apps/temporal/resolver.py` | 新增从结构化计划计算绝对范围的入口 |
| `backend/apps/temporal/calendar.py` | 让统一 `week_start` 配置参与自然周边界计算 |
| `backend/apps/temporal/__init__.py` | 导出统一时间计划和解析入口 |
| `backend/apps/temporal/models.py` | 收紧 `week_start` 契约并从企业配置创建 Run 上下文 |
| `backend/common/core/config.py` | 增加统一周起始配置和模型时间旁路开关 |
| `backend/apps/chatbi/models/dto/question_understanding.py` | 增加模型时间旁路观察契约 |
| `backend/apps/chatbi/services/understanding/prompts.py` | 增加共享时间理解指导规则 |
| `backend/apps/chatbi/services/understanding/understanding_service.py` | 接入 Agent 时间理解任务 |
| `backend/apps/chatbi/services/understanding/intent_projection.py` | 统一投影时间计划和兼容 `TimeRange` |
| `backend/apps/chatbi/services/understanding/validation.py` | 增加时间歧义、冲突和解析失败校验 |
| `backend/apps/chatbi/services/understanding/graph_contracts.py` | 将时间问题投影到 Graph 澄清契约 |
| `backend/apps/chatbi/orchestration/graph/capabilities/adapters/question.py` | 接入 Graph 时间理解任务 |
| `backend/apps/chatbi/orchestration/graph/capabilities/adapters/interaction.py` | 增加时间澄清卡片 |
| `backend/apps/chatbi/orchestration/agent/preparation.py` | 增加 Agent 检索前时间澄清 |

`apps.temporal` 不得反向依赖 ChatBI、Agent、Graph 或模型调用代码。模型调用放在 ChatBI 问题理解服务中，
时间契约、校验和日期计算放在 `apps.temporal` 中。

## 17. 实施阶段

### 第零阶段：固定评估基线

- 建立覆盖自然时间、财政时间、分组、指标内部时间、冲突和模糊表达的固定样本集。
- 每个样本同时保存 `TemporalPlan`、`ResolvedTemporalPlan` 和最终 SQL 时间条件期望。
- 在同一 `TemporalContext` 下比较当前规则、候选成熟解析器和模型时间计划。
- 记录表达识别、作用判断、计划完全匹配、绝对范围匹配、澄清和 Agent/Graph 一致率。

完成标准：

- 非可信日期进入 SQL 的次数为零。
- 当前已支持表达的新旧绝对范围全部一致。
- 评估结果能够区分表达识别错误、计划错误、日期计算错误和时间维度绑定错误。

### 第一阶段：建立时间计划契约

状态：已完成，尚未接入线上问题理解主流程。

- 新增时间计划 Pydantic 模型。
- 新增结构校验和业务校验。
- 新增结构化计划解析器。
- 第一版只包含绝对日期、绝对范围、相对日期、滚动范围、自然周期、财政周期和时间分组。
- 保持现有线上主流程不变。
- 使用原有时间测试验证新解析结果。

完成标准：

- 结构化计划可以覆盖现有时间类型。
- 同一个 `TemporalContext` 下新旧解析结果一致。
- SQL 渲染器不需要放宽输入。

### 第二阶段：增加模型时间理解任务

状态：已完成，默认关闭，只接入 Agent 问题理解的旁路观察。

- 增加独立模型任务。
- 使用指导表和严格 JSON 契约。
- 接入统一结构化模型调用。
- 支持一次结构修复重试。
- 以 `TEMPORAL_MODEL_SHADOW_ENABLED` 控制旁路调用。
- 记录模型计划、确定性解析结果、旧 `TimeRange` 和字段级差异，不参与检索、计划或 SQL。
- 模型调用或连续两次非法输出记录为明确错误，不替换线上时间结果。
- 使用 `summarize_temporal_shadow_observations()` 汇总匹配、差异、不可比较、模型错误和解析错误；
  `scripts/evaluate_temporal_shadow.py` 按租户和时间窗口只读统计已持久化的 Agent Run。
- 使用 `scripts/replay_temporal_shadow.py` 从既有 Agent Run 读取问题理解快照，按重写问题去重后离线调用
  时间模型；脚本支持 2～10 轮重复试验，比较排除置信度后的完整时间语义，统计状态序列、稳定匹配和
  不稳定原因。脚本不更新 Run，不执行检索或 SQL，并且只输出聚合结果。

完成标准：

- 开放表达能够形成合法时间计划。
- 模型错误不会改变线上查询结果。
- 能统计模型结果与旧解析结果差异。

2026-07-31 使用当前默认模型执行 7 条固定时间基线：第一次运行 7 条都生成了合法计划，其中 6 条计划和
绝对范围完全一致；“近7天销量”被错误解释为全局时间筛选。随后将“时间表达被完整指标短语包含时必须是
`metric_definition`”提升为统一业务校验，目标样本在一次修复调用后通过，并且没有生成全局筛选。

同日只读扫描 165 个既有 Agent Run，其中 138 个包含可回放的问题理解快照，按重写问题去重后有 36 个不同
问题。对最新 20 个不同问题执行两次离线回放：第一次为 15 个 `matched`、5 个因澄清而不可比较；第二次为
16 个 `matched`、3 个因澄清而不可比较、1 个非法模型输出。两次运行中所有可比较样本均与旧权威时间结果
一致，但计划状态和模型输出合法性发生变化，说明即使温度为 0，当前模型结果也尚未达到稳定接管条件。

回放进一步发现“明确查询日期 + 指标内部当前/近 30 天”会被模型错误标记为冲突。修复不是针对问题文本增加
规则，而是在统一业务校验中收紧三项不变量：歧义代码使用受控枚举、指标定义内部时间不能成为全局歧义、
第一版滚动范围统一包含基准日。目标历史样本重新回放后为 `matched`，没有歧义和字段差异。

这些结果证明旁路修复和只读评估链路有效，但不能作为第三阶段上线依据。当前数据库仍无线上开关产生的真实
旁路观察记录，离线样本只有 20 个不同问题且重复调用存在波动，因此模型计划仍不得成为权威执行输入。

在上述约束收紧后，对最新 10 个不同问题执行 3 轮重复回放，共产生 30 条观察：28 条 `matched`，2 条因
`TEMPORAL_MODEL_CALL_FAILED` 形成 `model_error`，所有 28 条可比较结果均与旧权威时间一致。跨轮完整时间
语义只有 7 个问题稳定，稳定率为 70%；另外 1 个问题虽然三轮状态均为 `matched`，内部时间计划仍有变化。
因此只统计状态或绝对范围匹配率会遗漏计划不稳定问题。

本轮 30 条观察耗时约 9 分 15 秒，累计使用 59,499 tokens。离线回放保持串行以避免放大限流和费用；线上
旁路目前仍在问题理解请求内同步执行，而且只有全局布尔开关。缺少租户范围、采样比例和延迟预算控制时，
不得在当前环境全量开启。回放入口同时固定 OpenAI、HTTPX 和 HTTPCore 的最低日志级别为 WARNING，避免
调试日志输出历史问题和完整模型请求。

### 第三阶段：接入 Agent 和 Graph

实现状态：受控接入已完成，生产启用准入未满足。代码路径由
`TEMPORAL_MODEL_AUTHORITY_ENABLED=false` 默认关闭；旁路与权威模式互斥，禁止同时启用。

生产启用前仍至少需要：

- 在真实业务问题上积累覆盖主要时间类型、指标内部时间、无时间和模糊时间的旁路记录。
- 对相同输入重复回放，确认计划状态、歧义代码和输出合法性达到约定的稳定性门槛。
- 开启线上观察前增加租户范围、采样比例、单次请求延迟和累计模型用量控制，不能只使用全局布尔开关。
- 分别审查 `different`、`not_comparable`、`model_error` 和 `resolution_error`，不能只看总体匹配率。
- 指标内部时间被错误投影为全局筛选的未解决样本数为零。
- 所有差异可以归因到 JioNLP 解析限制、模型计划错误或确定性适配错误，并形成固定回归样本。
- 完成 Agent 与 Graph 同输入、同 `TemporalContext` 的旁路一致性验证。

已实现：

- 权威开关启用时，模型时间计划成为 Agent 与 Graph 的时间语义输入；旧 `TimeRange` 只由解析结果投影。
- `TemporalInterpretationService` 统一执行模型调用、结构和业务校验、确定性解析以及兼容投影。
- 模型调用、模型输出或确定性适配失败时明确终止，不回退到默认时间。
- 时间歧义统一产生 `temporal_clarification_required`，Agent 和 Graph 都在资产检索前暂停。
- 常用澄清选项携带服务端生成的结构化 `TemporalPlan`，选择后直接校验和解析，不调用模型；自由文本回答只
  重跑时间任务，不重复问题形态、指标或维度识别。
- Agent 将时间计划、解析结果和解释来源写入 `question_understanding` 并随 Run 派生状态持久化；Graph 将同一
  契约写入工作流 `intent` 状态。
- 权威路径仍复用 Run 创建时固定的 `TemporalContext`，不会在澄清恢复时重新读取当前日期。

完成标准：

- [ ] 真实模型对同问题和同 `TemporalContext` 达到约定的时间计划稳定性门槛；当前为 70%。
- [x] 时间不明确时不发生资产检索和 SQL 编译。
- [x] 澄清恢复后不重复执行已经完成的无关阶段。
- [x] Agent 与 Graph 使用同一时间计划契约、校验器、解析器和澄清选项生成入口。
- [x] 默认配置下保持旧权威时间路径不变。

### 第四阶段：替换自研原文匹配

实现状态：普通文本时间入口已接入 JioNLP，模型权威路径仍由开关独立控制。

已实现：

- Agent 权威模式在时间模型调用前后都不再执行兼容原文入口，也不使用固定词组推断
  时间粒度或指标内部时间作用。
- Graph 权威模式禁用规则降级服务中的时间词提取，语义子任务只保留模型抽取的原始线索，最终时间范围和
  分组只由 `TemporalPlan` 投影。
- 统一时间投影会先删除旧 `time_grain`、`time_dimension` 和 `time_range` 派生结果，再根据当前计划生成，
  防止旧模型字段和时间计划形成两套执行事实。
- 共享校验在权威模式下根据 `TemporalPlan.expressions` 判断普通维度值是否误装时间，不再调用固定中文
  时间表达识别器；旧模式仍保留该识别器作为兼容校验。
- Retrieval 只消费已经存在的 `absolute_range`，不再根据 `time_range.raw` 补解析；SQL 编译 Tool 也不再
  将模型提交的时间字符串重新解释后与可信范围比较。
- `TimeRange.interpretation_source` 记录 `jionlp`、`model` 或 `user_confirmation`；历史数据可能保留
  `legacy_rule`。权威模式失败直接终止，不能静默转入其他来源。
- `normalize_time_range()`、`normalize_time_range_payload()` 保留为兼容入口，原文解析统一调用 JioNLP，
  并将结果转换为 `absolute_range`。
- 新增只读 `scripts/evaluate_temporal_source_usage.py`，按租户和时间窗口统计来源及 `jionlp_rate`，
  报告不包含问题、时间原文或时间计划。

完成标准：

- [x] 权威路径支持“往前看两周”等开放表达，不需要新增固定中文词组。
- [ ] JioNLP 兼容路径覆盖率和解析失败率达到线上准入要求；当前权威开关默认关闭，尚未执行生产来源统计。
- [x] 权威模式不存在旧原文解析和 `TemporalPlan` 同时参与执行的两套时间事实。
- [x] JioNLP 使用有明确来源，且可通过只读脚本统计。

### 第五阶段：复杂时间关系

- 同比和环比。
- 上一周期。
- 多个时间范围。
- 截止日期和周期至今。
- 多时间字段绑定。
- 财政周期比较。

本阶段仍不包含工作日、节假日和企业日历。

## 18. 测试设计

### 18.1 基础表达

- 今天、昨天、明天。
- 本周、上周、上上周。
- 本月、上月、过去两个月。
- 本季度、去年第四季度。
- 最近 7 天、过去七日、往前看两周。

### 18.2 日期边界

- 月末平移。
- 2 月 29 日。
- 跨年范围。
- 季度边界。
- 财政年度边界。
- 不同时区的日期边界。

### 18.3 语义角色

- 全局查询时间。
- 指标定义内部时间。
- 时间分组。
- 同比和环比。
- 多个冲突时间。
- 时间和普通维度值分离。

### 18.4 歧义

- “最近的销售额”。
- “前段时间的订单数”。
- 自然季度和财政季度歧义。
- 比较查询缺少基准时间。
- 同时出现“最近 7 天”和“上月”。

### 18.5 模型失败

- 非 JSON 输出。
- 多余字段。
- 枚举值非法。
- 必填字段缺失。
- 第一次非法、第二次修复成功。
- 连续两次非法。
- 模型调用超时。

### 18.6 集成

- Agent 与 Graph 输出一致。
- 前置维度澄清发生在语义检索前；时间 Tool 产生的澄清发生在并发结果投影后。
- 指标和时间同时需要澄清时，用户完成当前卡片后展示队列中的下一张卡片。
- 用户选择每个时间澄清选项后均能恢复。
- 自由文本澄清只重新执行必要阶段。
- Run 重试和回放结果不变化。
- 时间模型任务不会进入 Agent 工具循环。
- SQL 编译器只收到绝对左闭右开范围。

测试不依赖浏览器，使用现有服务层、Agent、Graph 和语义 SQL 编译测试入口执行。

## 19. 验收标准

1. 原有时间处理测试全部通过。
2. 开放时间表达不再依赖新增固定词组。
3. 模型结果不能直接进入 SQL。
4. 同一个 Run 重试和回放的绝对时间范围不变化。
5. Agent 与 Graph 的结构化时间计划一致。
6. 时间歧义在 SQL 编译前进入澄清；默认 Agent 可先与资产检索并发执行。
7. 每次问题最多一次正常时间模型调用和一次结构修复调用。
8. 模型调用失败后不静默使用默认时间。
9. 时间分组仍由服务端绑定时间维度并生成 `time_bucket`。
10. SQL 编译器只接收服务端验证过的 `absolute_range`。

## 20. 当前实现映射

| 设计概念 | 当前实现位置 |
| --- | --- |
| 固定 Run 时间上下文 | `backend/apps/temporal/models.py` |
| 第一版时间计划和业务校验 | `backend/apps/temporal/plan.py` |
| 时间计划错误 | `backend/apps/temporal/errors.py` |
| 固定时间评估样本 | `backend/tests/temporal/fixtures/temporal_plan_baseline.json` |
| 模型时间提示词 | `backend/apps/chatbi/services/understanding/prompts.py::TEMPORAL_INTERPRETATION_SYSTEM_PROMPT` |
| 独立模型时间任务 | `backend/apps/chatbi/services/understanding/temporal_interpretation.py` |
| 权威时间执行结果 | `backend/apps/chatbi/models/dto/question_understanding.py::TemporalInterpretationResult` |
| 权威时间开关 | `backend/common/core/config.py::TEMPORAL_MODEL_AUTHORITY_ENABLED` |
| 旁路观察契约 | `backend/apps/chatbi/models/dto/question_understanding.py::TemporalShadowObservation` |
| 旁路统计契约 | `backend/apps/chatbi/models/dto/question_understanding.py::TemporalShadowStatistics` |
| 旁路统计函数 | `backend/apps/chatbi/services/understanding/temporal_interpretation.py::summarize_temporal_shadow_observations` |
| 只读统计脚本 | `backend/scripts/evaluate_temporal_shadow.py` |
| 既有 Run 只读回放脚本 | `backend/scripts/replay_temporal_shadow.py` |
| 时间解释来源使用率 | `backend/scripts/evaluate_temporal_source_usage.py` |
| 问题模型请求超时 | `backend/apps/chatbi/adapters/question_model.py` |
| Agent 旁路开关 | `backend/common/core/config.py::TEMPORAL_MODEL_SHADOW_ENABLED` |
| 时间表达识别 | `backend/apps/temporal/extractor.py` |
| JioNLP 时间适配 | `backend/apps/temporal/jionlp_adapter.py` |
| 原始表达和结构化计划解析 | `backend/apps/temporal/resolver.py` |
| 日历边界计算 | `backend/apps/temporal/calendar.py` |
| 时间分桶 | `backend/apps/temporal/binder.py` |
| SQL 时间条件渲染 | `backend/apps/temporal/sql_renderer.py` |
| Agent 问题理解 | `backend/apps/chatbi/services/understanding/understanding_service.py` |
| Graph 问题理解 | `backend/apps/chatbi/orchestration/graph/capabilities/adapters/question.py` |
| 共享问题理解校验 | `backend/apps/chatbi/services/understanding/validation.py` |
| Graph 意图契约投影 | `backend/apps/chatbi/services/understanding/graph_contracts.py` |
| Agent 检索前澄清 | `backend/apps/chatbi/orchestration/agent/preparation.py` |
| Graph 澄清卡片 | `backend/apps/chatbi/orchestration/graph/capabilities/adapters/interaction.py` |
