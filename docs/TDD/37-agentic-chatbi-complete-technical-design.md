# 37. 智能问数完整技术设计

## 0. 文档定位

问数流程包括：

1. 问题重写并输出检索短语；
2. 语义资产混合检索；
3. 候选资产绑定与问题语义解析；
4. 执行需求分析与模式路由；
5. Fast、Plan 或 Research 模式执行；
6. 查询编译与执行；
7. 多结果计算；
8. 结果校验；
9. 答案生成；
10. 澄清、失败和状态恢复。

# 1. 支撑问数分析的语义资产设计

## 1.1 设计结论

后续问数分析不能只依赖指标、维度的名称、别名、英文标识和类型。指标和维度描述的是
“业务资产是什么”，维度层级、指标维度能力、指标公式和指标关系描述的是“这些资产允许
怎样组合分析”。Fast、Plan 和 Research 只能执行已经人工配置、服务端校验并正式发布的
语义组合，不能由 LLM 在运行时根据名称自行推断并直接生效。

维度下钻和指标驱动关系与现有指标、维度一样，属于需要人工维护的语义资产。LLM 可以在
配置阶段提出候选建议，但候选只能进入草稿状态，必须经过人工确认和契约发布后才能进入
运行时 Schema。

当前第三阶段为了验证 Research 动作，暂时从数据集 `query_config` 读取
`dimension_hierarchies` 和 `research_relationships`。该方式缺少独立持久化、引用保护、
统一校验、发布状态、资产版本、影响分析和配置界面，只能作为开发阶段临时入口，不能作为
正式语义资产设计。

## 1.2 现有语义资产基础

当前 Semantic 模块已经具备以下基础：

1. 物理模型、模型字段和模型度量；
2. 指标和物理维度；
3. 业务实体和逻辑维度；
4. 物理维度到逻辑维度的绑定；
5. 指标结果粒度、可加性、默认聚合、去重键和时间语义；
6. 指标使用逻辑维度时的分组、筛选和明细能力；
7. 模型关系、关系基数、指标传播方向和聚合安全性；
8. 数据集契约完整度校验、发布、版本和 Schema 指纹。

后续设计应扩展这套正式契约，不在 `query_config`、指标 `ext` 或维度 `ext` 中另建一套
长期运行事实源。所有关系型语义资产由 Semantic 模块唯一写入和校验，ChatBI 只消费
Semantic 公开的严格 DTO，不读取 Semantic ORM，也不解释任意配置字典。

目标关系如下：

~~~text
BusinessEntity
  -> LogicalDimension
       -> PhysicalDimensionBinding
       -> DimensionHierarchyLevel -> DimensionHierarchy

Metric
  -> MetricContract
  -> MetricDimensionCapability -> LogicalDimension
  -> MetricFormulaDefinition
  -> MetricRelationship -> Driver Metric

Dataset
  -> 选择模型、指标、维度和关系资产
  -> 校验并发布 DatasetSchema 快照
  -> Fast / Plan / Research 只读取已发布快照
~~~

## 1.3 维度层级资产

### 1.3.1 资产边界

下钻描述的是多个逻辑维度之间有顺序的关系，不是单个维度的布尔属性。禁止只在维度表中
增加 `can_drilldown`、`next_dimension_id` 等字段，因为这些字段无法完整表达：

- 一个维度属于多套层级；
- 严格的层级顺序；
- 是否允许跳级或反向下钻；
- 层级的认证状态和版本；
- 同一逻辑维度在不同物理模型中的绑定；
- 某个指标是否真的支持层级中的全部维度。

第一版新增正式资产：

~~~text
headless_dimension_hierarchy
- id
- oid
- domain_id
- name
- biz_name
- description
- hierarchy_type
- contract_status
- version
- status

headless_dimension_hierarchy_level
- id
- hierarchy_id
- logical_dimension_id
- level_order
~~~

层级节点必须引用稳定的 `logical_dimension_id`，不能保存某个数据集中的物理
`DIMENSION:<id>:<model_id>`。例如：

~~~text
区域逻辑维度 -> 城市逻辑维度 -> 档口逻辑维度
~~~

运行时由 Semantic 根据目标指标的 `MetricDimensionCapability`、当前数据集包含的模型和
经过证明的模型关系，把逻辑层级解析为当前查询可执行的物理维度。

### 1.3.2 第一版能力边界

第一版只支持固定级别层级，并且 Research 下钻只能沿相邻层级执行：

~~~text
区域 -> 城市
城市 -> 档口
~~~

不自动授权区域直接跳到档口，不支持反向下钻。组织机构树等“同一逻辑维度内部的成员父子
关系”属于另一类成员层级，后续单独设计，不能与第一版固定级别层级混用。

层级能够进入某个指标的 Research Scope，必须同时满足：

1. 层级已发布且状态为 `CERTIFIED`；
2. 层级所有逻辑维度属于同一主题域；
3. 数据集明确包含该层级资产；
4. 目标指标对每一层逻辑维度具有 `GROUP_BY` 能力；
5. 每一层都能解析到唯一物理维度；
6. 跨模型时存在聚合安全、方向正确的关系路径；
7. 当前时间语义和查询粒度可以在各层保持一致。

## 1.4 指标关系资产

### 1.4.1 正式关系模型

目标指标与驱动指标之间的关系不能只表示“相关”，还必须明确关系来源、验证方法、变化方向、
共同维度、时间角色和执行路径。建议新增：

~~~text
headless_metric_relationship
- id
- oid
- domain_id
- target_metric_id
- driver_metric_id
- relationship_type
- validation_method
- expected_direction
- supported_time_roles
- relation_path
- contract_status
- version
- status

headless_metric_relationship_dimension
- relationship_id
- logical_dimension_id
~~~

允许的关系类型：

~~~text
FORMULA_COMPONENT
CERTIFIED_DRIVER
GOVERNED_ANALYSIS_RELATION
~~~

第一版允许的确定性验证方式：

~~~text
SAME_DIRECTION
OPPOSITE_DIRECTION
FORMULA_RECONCILIATION
~~~

`expected_direction` 至少支持 `POSITIVE`、`NEGATIVE` 和 `UNKNOWN`。例如订单量增加通常与
GMV 同向，退款增加可能与净 GMV 反向。没有明确方向时只能生成“共同变化线索”，不能把
同向或反向变化直接判定为支持、削弱，更不能陈述因果关系。

### 1.4.2 共同分析能力

关系中的支持维度必须引用逻辑维度。发布时需要证明目标指标和驱动指标在每个支持维度上都
具有可执行能力，并检查：

- 双方结果粒度是否兼容；
- 时间语义和时间对齐策略是否兼容；
- 是否需要预聚合；
- 跨模型关系路径是否会导致指标重复；
- 关系路径的指标传播方向是否正确；
- 当前验证方式是否有确定性执行结构。

第一版可先限制目标指标和驱动指标属于同一模型。跨模型驱动验证不能只因为关系已配置就直接
执行；后续需要由 Plan 生成“分别查询、按共同逻辑维度对齐、再计算”的完整子计划，并对
模型关系、粒度和时间对齐完成 `PROVEN` 证明。

## 1.5 指标公式和分析能力重构

### 1.5.1 结构化指标公式

现有 `metric_refs` 只能说明派生指标依赖了哪些指标，不能说明分子、分母、加项、减项等依赖
角色。后续应把派生指标公式收敛为结构化定义，例如：

~~~json
{
  "operation": "RATIO",
  "components": [
    {"metric_id": 271, "role": "numerator"},
    {"metric_id": 265, "role": "denominator"}
  ]
}
~~~

结构化公式是派生指标依赖的唯一事实源。`FORMULA_COMPONENT` 关系由该定义确定性投影，
不能要求配置人员同时维护公式和另一份重复关系。这样服务端才能判断组成方向、执行公式对账，
并区分公式分解与普通驱动分析。

### 1.5.2 显式贡献度能力

能够按某个维度分组不等于允许按该维度计算变化贡献。现有
`MetricDimensionCapability.usages` 应扩展 `CONTRIBUTION`，贡献度动作必须同时满足：

1. 指标可加性为 `FULL`；
2. 指标与逻辑维度关系明确允许 `CONTRIBUTION`；
3. 当前期和对比期已归一化且可比较；
4. 维度差值合计能够与总差值对账；
5. 使用服务端配置的确定性对账容差；
6. 对账失败时明确失败，不生成贡献结论。

第一版不再把全部 `GROUP_BY` 维度自动视为贡献度维度。

### 1.5.3 业务时间语义

现有指标已经具备事件、快照、默认时间维度和快照聚合策略，但完整问数还需要业务日历和比较
规则。后续建议新增或补齐：

- 数据集默认时区；
- 自然周、自然月、自然季度和自然年；
- 财务周、财务月和财年；
- 工作日、节假日和营业日；
- 指标允许比较的时间粒度；
- 跨指标的时间对齐策略；
- 快照指标的期初、期末和区间聚合口径。

同比、环比和时间偏移继续由规则生成，但规则只能使用已发布的日历和指标时间契约，不能把
业务日历逻辑写进 Prompt。

## 1.6 语义资产管理操作

维度层级和指标关系至少支持以下操作：

1. 创建草稿；
2. 编辑草稿；
3. 停用；
4. 查看引用；
5. 发布前校验；
6. 发布；
7. 查看版本和变更记录；
8. 删除或修改前的影响分析；
9. 批量导入和导出；
10. 查询某个指标当前可执行的分析能力及拒绝原因。

建议公开 API：

~~~text
POST   /semantic/dimension-hierarchies
PUT    /semantic/dimension-hierarchies/{id}
DELETE /semantic/dimension-hierarchies/{id}

POST   /semantic/metric-relationships
PUT    /semantic/metric-relationships/{id}
DELETE /semantic/metric-relationships/{id}

GET    /semantic/datasets/{id}/analysis-capabilities
GET    /semantic/datasets/{id}/contract-report
POST   /semantic/datasets/{id}/publish-contract
~~~

`analysis-capabilities` 需要同时返回可执行能力和拒绝原因，例如：

~~~json
{
  "metric_id": 271,
  "available_operations": [
    "breakdown",
    "contribution",
    "drilldown"
  ],
  "rejected_operations": [
    {
      "operation": "validate_hypothesis",
      "reason": "METRIC_RELATIONSHIP_NOT_CERTIFIED"
    }
  ]
}
~~~

删除逻辑维度、指标、模型关系、维度层级或指标关系前必须执行引用检查。已被发布数据集引用的
资产不能直接物理删除，只能先创建新版本、完成影响分析并重新发布相关数据集。

## 1.7 数据集选择、校验和发布

维度层级和指标关系建议作为数据集可选择的正式资产类型，纳入现有
`SemanticDatasetAsset`，避免同一主题域中的所有已认证关系自动进入每个数据集。数据集只有
明确包含相关指标、逻辑维度和关系资产后，才能暴露对应分析能力。

正式发布流程：

~~~text
人工配置或确认候选资产
  -> 保存为 DRAFT
  -> Semantic 执行完整性和可执行性校验
  -> 人工确认
  -> 发布为 CERTIFIED
  -> 增加 contract_version
  -> 生成 DatasetSchema 和 schema_fingerprint
  -> 重建语义检索索引
  -> Fast / Plan / Research 冻结本次版本快照
~~~

维度层级、层级节点、指标关系和关系支持维度必须进入：

- `DatasetSchemaAssets`；
- Schema Repository 和 Loader；
- 集中式 `validate_semantic_contracts()`；
- 数据集 `contract_version`；
- `asset_versions`；
- `schema_fingerprint`；
- 发布完整度报告；
- 运行 Trace 和 Research `version_snapshot`。

运行中的 Research Scope 不跟随配置变化。配置发布新版本后，只影响新 Run；已有 Run 继续
使用启动时冻结的 Schema、Contract 和 Scope 指纹。

## 1.8 Semantic 与 ChatBI 的职责边界

Semantic 负责：

- 保存关系资产；
- 校验引用、主题域、粒度、时间、模型路径和聚合安全；
- 发布和版本管理；
- 把逻辑关系解析为当前数据集可执行的物理绑定；
- 输出严格的公开 DTO 和能力拒绝原因。

ChatBI 负责：

- 根据用户问题选择 Fast、Plan 或 Research；
- 从已发布能力中冻结本次执行范围；
- 选择允许的分析动作；
- 把动作物化为 `ExecutionRequirement`；
- 执行 Plan 证明、查询、计算、证据和报告流程。

ChatBI 不负责判断一组维度是否构成业务层级，也不负责判断一个指标是否是另一个指标的业务
驱动因素。`build_research_requirement()` 只能根据 Semantic 返回的已验证契约做范围交集和
预算裁剪，不能继续解析 `query_config` 原始字典或补充新的治理语义。

## 1.9 配置界面

现有语义配置页面主要管理模型、指标、维度、数据集和术语。后续应增加“语义治理”区域：

- 业务实体；
- 逻辑维度和物理绑定；
- 指标维度能力；
- 维度层级编辑器；
- 指标关系编辑器；
- 数据集分析能力预览；
- 契约完整度报告；
- 发布操作和版本记录。

层级编辑器使用有序列表配置层级，不允许重复维度；指标关系编辑器必须显示目标指标、驱动
指标、关系类型、方向、验证方式、共同维度、时间角色和认证状态。系统可以给出候选建议，
但“确认并发布”必须是明确的人工操作。

## 1.10 分阶段实施

### 第一阶段：关系资产正式化

1. 新增维度层级、层级节点、指标关系和关系维度 ORM；
2. 新增严格 DTO、Repository、Service 和 CRUD API；
3. 所有关系引用逻辑维度 ID 和指标 ID，不保存运行时物理 Ref；
4. 接入统一契约校验、引用保护和发布流程；
5. 纳入数据集资产选择、Contract 版本和 Schema 指纹；
6. 将 `DatasetSchema` 中的原始字典替换为严格公开 DTO；
7. Research 改为只消费正式公开契约；
8. 删除 `query_config` 中两个临时配置入口。

### 第二阶段：完善分析契约

1. 将派生指标依赖重构为结构化公式；
2. 增加指标关系方向和确定性验证方式；
3. 增加显式贡献度能力和对账容差；
4. 增加业务日历和指标时间对齐能力；
5. 增加能力解释和变更影响分析；
6. 在治理关系证明充分后支持跨模型驱动验证。

### 第三阶段：高级分析语义

在真实问题集证明存在需求后，再依次设计：

- 目标值、预算值和基准指标；
- 异常检测策略；
- 漏斗、留存和队列分析；
- 同一逻辑维度内部的成员父子树；
- 预测、模拟和 what-if。

这些能力不能提前塞进通用 `ext` 或自由 JSON 中。每类能力需要明确业务不变量、确定性执行
方式和发布校验后再成为正式资产。

## 1.11 验收标准

第一、二阶段完成后至少验证：

1. 配置人员可以像配置指标、维度一样配置层级和指标关系；
2. 未发布、已停用或引用无效的关系不会进入 `DatasetSchema`；
3. 层级只能沿认证的相邻逻辑维度下钻；
4. 指标关系只能在已声明的共同维度、时间角色和方向下验证；
5. 贡献度只在显式允许的指标维度组合上执行并通过对账；
6. 关系资产变更会增加版本并改变 Schema 指纹；
7. 运行中的 Research 不会因配置变更扩大 Scope；
8. 删除被引用资产时返回明确错误；
9. 能力解释接口能说明每个动作允许或拒绝的原因；
10. 真实问题集同时覆盖成功下钻、贡献度、驱动验证、并行分析、治理能力不足和越界拒绝。

# 2. 问题重写

## 2.1 设计目标

用户输入是自然语言问题，可能包含代词、省略、上下文引用和条件修改。

问题重写负责将当前用户问题结合会话上下文，转换为一个完整、明确、可以独立理解的问题，
并从重写后的问题中识别用于语义资产检索的指标短语和维度短语。

问题重写只处理自然语言表达和短语边界，不负责识别查询意图、绑定语义资产或生成查询计划。

## 2.2 处理位置

问题重写位于基础请求处理之后、语义解析之前：

    用户输入
      -> 基础请求校验
      -> 身份、租户和权限校验
      -> 加载会话上下文
      -> 问题重写
      -> 后续问数流程

问题重写之前只处理不依赖业务语义的内容：

- 请求格式；
- 用户身份；
- 租户和数据集权限；
- 会话加载；
- 当前时间和时区；
- 空问题判断。

问题重写阶段不进行：

- 执行需求分析与模式路由；
- Fast、Plan 或 Research 模式判断；
- 指标、维度或筛选值绑定；
- 元数据检索；
- SQL 生成；
- 查询计划生成。

问题重写模型可以识别指标短语和维度短语，但不能把它们转换成资产、字段或内部名称。

## 2.3 输入

问题重写的输入包括：

### 当前问题

用户本轮提交的原始自然语言问题。

### 会话上下文

用于处理当前问题中对历史内容的引用，包括：

- 最近几轮用户问题和系统回答；
- 最近一轮重写后的问题；
- 用户已经确认的补充或修改；
- 当前是否处于澄清流程。

上下文只提供自然语言内容和已确认信息，不直接提供：

- 未确认的指标或维度绑定；
- 未确认的业务口径；
- 上一轮 SQL；
- 表名、字段名和内部资产标识；
- 其他会话或其他租户的信息。

### 运行时信息

只提供问题重写所需的环境信息，例如：

- 当前时间；
- 当前时区；
- 数据集或业务空间；
- 当前语言。

运行时信息不替代后续语义解析。

## 2.4 输出

问题重写输出为一个对象：

~~~json
{
    "original_question": "原始问题",
    "rewrite_question": "重写后的问题",
    "metric_phrases": ["指标业务短语"],
    "dimension_phrases": ["维度业务短语"]
}
~~~

字段含义：

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| original_question | 用户本轮提交的原始问题 | 审计、追踪和上下文记录 |
| rewrite_question | 结合上下文后的完整问题 | 后续语义解析、检索和路由 |
| metric_phrases | 从 rewrite_question 中识别出的指标短语 | 指标资产检索 |
| dimension_phrases | 从 rewrite_question 中识别出的维度短语 | 维度资产检索 |

后续流程使用 rewrite_question 处理条件和分析关系，使用两个短语列表进行语义资产检索。
original_question 只用于审计、追踪和会话记录。


## 2.5 重写规则

### 2.5.1 独立问题

当前问题能够独立表达完整含义时，不继承上一轮无关的指标、维度、时间和筛选条件。

例如：

    上一轮：查询 2026 年 6 月总 GMV。
    当前问题：2026 年 7 月订单数是多少？
    rewrite_question：查询 2026 年 7 月的订单数。

### 2.5.2 代词消解

代词只有一个明确指向时，将代词替换为具体对象。

例如：

    上一轮：查询 2026 年 6 月店铺 100013 的总 GMV。
    当前问题：那它 5 月是多少？
    rewrite_question：查询店铺 100013 在 2026 年 5 月的总 GMV。

存在多个可能指向时，不得自行选择。

### 2.5.3 省略补全

当前问题省略上一轮中明确且唯一的条件时，补全省略内容。

例如：

    上一轮：查询 2026 年 6 月店铺 100013 的总 GMV。
    当前问题：那 100021 呢？
    rewrite_question：查询 2026 年 6 月店铺 100021 的总 GMV。

补全内容只能来自当前会话中已经明确表达的信息。

### 2.5.4 条件修改

用户使用“改成”“换成”“只看”“去掉”“再加上”等表达时，只修改用户明确指定的内容，保留其他条件。

例如：

    上一轮：查询 2026 年 6 月华东地区各店铺的总 GMV。
    当前问题：只看直营网店。
    rewrite_question：查询 2026 年 6 月华东地区各直营网店的总 GMV。

例如：

    上一轮：查询 2026 年 6 月各店铺的总 GMV。
    当前问题：时间改成 7 月。
    rewrite_question：查询 2026 年 7 月各店铺的总 GMV。

### 2.5.5 澄清回复

当前问题是对上一轮澄清要求的补充时，将补充内容合并到原问题中。

例如：

    系统：请提供需要查询的店铺。
    用户：100021。
    rewrite_question：查询店铺 100021 的相关数据。

无法确定当前回复对应哪个缺失条件时，不得强行合并。

### 2.5.6 时间表达

问题重写可以结合明确的会话时间和运行时参考时间，补全“上个月”“同期”等自然语言表达。

例如：

    当前问题：上个月的总 GMV 呢？
    rewrite_question：2026 年 7 月的总 GMV是多少？

问题重写不生成时间结构、时间表达式，也不确定指标使用哪个日期字段。

时间表达无法唯一确定时，不得自行猜测。

### 2.5.7 业务短语

完整保留用户的业务表达，不拆分、不替换为内部字段。

例如以下表达应整体保留：

- 销售订单平均客单价；
- 客户当日购买件数；
- 超时未发订单金额；
- 当前期末库存；
- 已支付未发货订单数。

上述业务短语作为指标短语或维度短语输出时，保持自然语言中的完整边界，不能按字符位置、
固定句式或固定词表截取。

### 2.5.8 用户值

店铺编号、区域名称、渠道名称、组织名称、商品名称、状态名称和其他筛选值，除非用户明确要求，否则保持原样。

例如“网店”不能未经确认改写为“线上渠道”，也不能在问题重写阶段转换成内部枚举值。

### 2.5.9 不增加用户意图

问题重写只能补全表达，不能增加用户没有提出的业务要求。

不得自行增加：

- 排序；
- Top N；
- 分组；
- 同比或环比；
- 去重；
- 预测；
- 默认时间；
- 默认组织范围；
- 未确认的指标口径。

## 2.6 输出约束

问题重写输出对象必须满足：

1. original_question 保留用户本轮原始问题；
2. rewrite_question 为完整自然语言问题；
3. rewrite_question 不为空；
4. rewrite_question 不包含 JSON、Markdown、解释过程或 SQL；
5. rewrite_question 不丢失用户明确表达的条件；
6. rewrite_question 不增加用户没有表达或确认的条件；
7. metric_phrases 和 dimension_phrases 只能来自 rewrite_question 中明确表达的业务短语；
8. 两个短语列表不得包含时间表达、维度值、疑问词、SQL、字段名或资产 ID；
9. 下游使用 rewrite_question 处理完整问题，使用两个短语列表进行资产检索；
10. original_question 只用于审计、追踪和会话记录。

问题重写不是问题回答。它输出的是后续流程需要查询的问题，不输出查询结果。

## 2.7 不可重写情况

以下情况不能输出猜测后的 rewrite_question：

- 当前代词存在多个可能指向；
- 当前省略内容无法唯一确定；
- 当前问题与已确认上下文发生冲突，且无法确定用户要修改什么；
- 上下文不足以恢复完整问题；
- 模型返回空内容或非问题文本。

这些情况由上层流程进入澄清或失败状态。问题重写输出对象不增加 missing_slots、confidence 等字段来表达异常。

## 2.8 问题重写提示词

问题重写模型使用以下系统提示词：

~~~text
你是智能问数系统的问题重写器。

你的任务是：根据用户当前问题和必要的会话上下文，将当前问题重写为一个完整、明确、可以独立理解的自然语言问题，
并从重写后的问题中识别指标短语和维度短语，供语义资产检索使用。

你不能回答用户问题，也不能判断应该使用 Fast、Plan 或 Research 模式。

重写规则：

1. 保留用户当前问题中明确表达的指标、维度、筛选条件、时间范围和展示要求。
2. 消解代词、省略和上下文引用。
3. 只有在历史上下文中存在唯一明确指向时，才能补全省略内容。
4. 当前问题是对上一轮问题的修改时，只修改用户明确要求修改的部分，保留其他未修改条件。
5. 当前问题可以独立表达完整含义时，不继承上一轮无关内容。
6. 保留会影响后续模式分类的查询动作和分析关系，包括：
   - 同时查询多个指标；
   - 比较不同时间或对象；
   - 计算差值、占比、增长率；
   - 查询趋势或环比；
   - 排名和下钻；
   - 分析原因、归因和影响因素。
7. 可以把口语化表达改写为明确的自然语言，但不能改变用户原意。
8. 可以补全明确的相对时间表达，但不能猜测无法确定的时间。
9. 保留用户原有的业务名称、指标名称、店铺编号、区域名称、渠道名称和筛选值。
10. 不得增加用户没有提出的指标、维度、筛选条件、排序、Top N、比较关系、计算关系或分析目标。
11. 不得将业务名称转换为表名、字段名、内部资产 ID 或 SQL。
12. 不得输出问题分类、意图、模式、置信度、缺失字段、资产 ID、字段名或解释说明。
13. 如果上下文存在多个可能指向，无法唯一确定时，不得自行猜测；rewrite_question 保留原问题中无法确定的表达，交由后续流程处理。
14. metric_phrases 和 dimension_phrases 必须依据 rewrite_question 的自然语言含义识别，不得按字符位置、固定句式或固定词表截取。
15. 指标短语保留完整的业务限定词和核心指标；维度短语保留完整的业务对象名称。
16. 时间表达、维度值、疑问词、排序词和计算关系不进入两个短语列表。

输出要求：

只输出一个合法 JSON 对象，且只能包含以下四个字段：

{
    "original_question": "用户本轮提交的原始问题",
    "rewrite_question": "重写后的完整问题",
    "metric_phrases": ["指标业务短语"],
    "dimension_phrases": ["维度业务短语"]
}

字段要求：

- original_question 必须保留用户本轮提交的问题原文；
- rewrite_question 必须是后续流程使用的完整自然语言问题；
- metric_phrases 只包含指标业务短语；
- dimension_phrases 只包含维度业务短语；
- 不得输出 Markdown 代码块；
- 不得输出 JSON 以外的文字；
- 不得增加其他字段。
~~~

调用提示词需要提供以下输入：

~~~text
当前用户问题：
{{current_question}}

会话上下文：
{{conversation_context}}

当前时间：
{{reference_datetime}}

当前时区：
{{timezone}}
~~~

## 2.9 当前实现边界

当前实现中的问题重写位于 QuestionUnderstandingService 内，并在统一理解之前执行。

按照本设计：

1. 重写服务一次输出 original_question、rewrite_question、metric_phrases 和 dimension_phrases；
2. 不再设置独立的检索短语模型调用；
3. 检索直接消费两个短语列表，后续语义解析只使用 rewrite_question 和检索候选；
4. message_type、inherited_context、missing_slots、confidence 不属于问题重写模型输出；
5. 问题重写不承担完整语义解析、资产绑定、模式路由、Fast、Plan、Research 和 SQL 执行职责。

---

# 3. 检索短语输出

## 3.1 设计目标

检索短语是问题重写模型的输出，不是独立的模型阶段。
问题重写模型在输出 rewrite_question 的同时，返回指标短语和维度短语，供语义资产检索使用。

例如：

    2026年6月店铺100023的总GMV和订单数是多少？

生成的检索短语可以是：

    metric_phrases: 总GMV、订单数
    dimension_phrases: 店铺

其中以下内容不作为语义资产检索对象：

- `2026年6月`：用户的时间条件；
- `100023`：用户输入的维度值。

问题重写可以澄清短语边界，但不能生成语义层中不存在的指标、维度或业务口径。

## 3.2 输入与输出

短语随问题重写结果一起输出：

~~~json
{
  "rewrite_question": "2026年6月店铺100023的总GMV和订单数是多少？",
  "metric_phrases": ["总GMV", "订单数"],
  "dimension_phrases": ["店铺"]
}
~~~

`metric_phrases` 只用于指标资产检索，`dimension_phrases` 只用于维度资产检索。
完整问题由 `rewrite_question` 保留，供后续语义解析使用。当前语义资产索引单元是短语，
不使用完整问题生成资产向量。

## 3.3 处理边界

短语输出规则：

- 指标短语保留完整的业务限定词和核心指标；
- 维度短语保留完整的业务对象名称；
- 不把维度值和时间表达放入短语；
- 不按字符位置、固定句式或固定词表切分；
- 不解析指标计算关系；
- 不绑定语义资产；
- 不把用户输入的值转换成语义层资产；
- 不把时间表达转换成时间字段；
- 不生成 Fast、Plan 或 Research。

## 3.4 当前实现边界

当前实现不在检索层执行字符分词，也不使用固定句式规则。
问题重写模型一次输出 `rewrite_question`、`metric_phrases` 和 `dimension_phrases`，
检索层直接消费两个短语列表，不从意图或 MentionGraph 推导短语。

检索规划器对语义资产检索的所有通道使用模型输出的短语文本：

- 指标名称精确匹配、别名匹配、BM25 等词法通道使用 `metric_phrases`；
- 维度名称精确匹配、别名匹配、BM25 等词法通道使用 `dimension_phrases`；
- 指标和维度的向量通道也使用对应短语，因为当前资产索引单元保存的是短语而不是完整句子；
- `rewrite_question` 不作为当前语义资产向量检索文本；
- 两个短语列表不产生指标、维度或维度值资产 ID；
- 没有短语时不使用整句替代短语，候选结果返回 `missed`。

检索请求只保留下游实际使用的字段：

~~~python
RetrievalRequest(
    request_id=...,
    tenant_id=...,
    actor_id=...,
    metric_phrases=[...],
    dimension_phrases=[...],
    scope=...,
    profiles=...,
    strategy_version=...,
)
~~~

其中：

- `metric_phrases` 和 `dimension_phrases` 是候选资产检索的唯一文本来源；
- `RetrievalRequest` 不包含完整问题、问题意图或 MentionGraph；
- 检索规划器不从任何意图结果或 MentionGraph 推导指标、维度检索短语；
- `rewrite_question` 只用于问题重写结果留存，不属于二期候选检索请求。

候选结果返回后进入语义解析模型，不创建 `RetrievalBindingRequest`。
语义解析模型只返回候选资产引用、时间表达、筛选、排序和计算要求，仍不生成
执行白名单或查询计划。

# 4. 语义资产混合检索

## 4.1 检索范围

当前阶段只检索两类语义资产：

- 指标；
- 维度。

以下内容不作为语义资产检索对象：

- 维度值。维度值来自用户输入，且会持续变化；
- 时间表达式。用户时间通常是具体日期、月份或时间范围，语义层资产中
  保存的是时间字段，而不是用户本次输入的具体时间。

时间表达式和维度值不进入当前候选资产检索链路。

## 4.2 检索方式

对每个指标或维度短语执行多路检索：

- 名称精确匹配；
- 别名精确匹配；
- BM25 或其他关键词检索；
- 名称和别名的模糊匹配；
- 向量相似度检索；

各路检索结果不能直接把原始分数相加。不同检索方式的分数范围和含义不同，
必须先分别归一化或转换为排名，再进行合并。

## 4.3 候选分数

候选分数用于排序和提供给模型参考，不能单独作为最终资产绑定结果。

候选需要保留各检索通道的证据：

~~~json
{
  "ref": "METRIC:271:246",
  "asset_type": "METRIC",
  "name": "总GMV",
  "biz_name": "gmv_total",
  "matched_phrase": "总GMV",
  "matched_by": ["name_exact", "bm25", "dense"],
  "channel_scores": {
    "name_exact": 1.0,
    "bm25": 0.92,
    "dense": 0.81
  },
  "score": 0.94
}
~~~

匹配强度原则为：

    名称完全匹配 > 别名完全匹配 > 关键词覆盖度 > BM25 相似 > 向量相似

实际权重和归一化方式需要通过测试数据集调优。向量相似度不能覆盖名称或
别名完全匹配结果。

## 4.4 候选合并与截断

候选合并处理包括：

1. 按 `asset_type + asset_id + model_id` 去重；
2. 合并同一资产在不同通道和不同短语中的命中证据；
3. 按指标和维度分别排序；
4. 保证每个指标短语至少有候选，保证每个维度短语至少有候选；
5. 在满足短语覆盖后再进行最终 Top-K 截断；
6. 候选不足时返回缺失信息，不允许模型补充候选之外的资产。

不能只按完整问题的整体相似度做一个全局 Top-K。例如问题同时包含“总GMV”
和“订单数”时，两个指标都必须进入候选，不能因为“订单数”的整体相似度更高
而排除“总GMV”。

## 4.5 提供给模型的候选

候选上下文需要包含：

- 稳定的 `ref`；
- 资产类型；
- 名称和业务名称；
- 定义、计算口径和别名；
- 所属模型；
- 命中的短语；
- 各通道分数和合并分数。

候选列表作为语义解析模型的输入，不能直接作为 SQL 或执行依据。

# 5. 候选资产后的语义解析

当前实现从候选结果进入语义解析模型。本阶段不再创建单独的
`RetrievalBindingRequest`，而是直接生成 `SemanticParseOutput`。

## 5.1 设计目标

模型读取用户的 `rewrite_question` 和语义资产候选，在候选范围内选择用户真正
需要的指标和维度，并把时间、维度值、过滤、排序和计算要求结构化。

模型不能：

- 创建候选之外的指标或维度；
- 把维度值当作语义资产；
- 把时间表达绑定成未经确认的时间字段；
- 生成 SQL；
- 生成 Fast、Plan 或 Research；
- 生成执行计划。

## 5.2 输出结构

~~~json
{
  "status": "resolved",
  "measures": [
    {"ref": "METRIC:271:246"}
  ],
  "group_by": [
    {"ref": "DIMENSION:278:246"}
  ],
  "filters": [
    {
      "target_ref": "DIMENSION:278:246",
      "operator": "=",
      "value": "100023",
      "stage": "where"
    }
  ],
  "time_filters": [
    {
      "expression": "2026年6月",
      "role": "single"
    }
  ],
  "order_by": [],
  "limit": null,
  "calculations": [
    {
      "type": "growth_rate",
      "current_time_role": "current",
      "previous_time_role": "previous",
      "details": {
        "metric_ref": "METRIC:271:246",
        "result_name": "增长率"
      }
    }
  ],
  "unresolved": []
}
~~~

字段定义如下：

| 字段 | 含义 |
| --- | --- |
| status | `resolved`、`needs_clarification` 或 `missed` |
| measures | 用户要查询或参与计算的指标，只能引用指标候选 |
| group_by | 用户要求分组展示的维度，只能引用维度候选 |
| filters | 维度过滤或指标聚合后过滤 |
| target_ref | 过滤目标，可以是维度或指标 |
| value | 用户问题中的原始筛选值，不要求语义层存在对应资产 |
| stage | `where` 表示普通维度过滤，`having` 表示指标聚合后过滤 |
| time_filters | 用户的时间表达，保留原话，不绑定具体时间字段 |
| order_by | 用户明确要求的排序 |
| limit | 用户明确要求的数量，没有时为 `null` |
| calculations | 差值、增长率、占比、趋势等计算要求；可通过 `ref` 指向参与计算的指标，并通过 `alias` 标识计算结果 |
| unresolved | 无法安全确定且会影响后续执行的内容 |

`measures`、`group_by`、`filters`、`time_filters`、`order_by`、`calculations` 和
`unresolved` 没有内容时必须返回空数组，不能返回 `null`。

## 5.3 服务端校验

模型输出后必须执行结构和业务校验：

- 所有 `ref` 必须来自候选上下文；
- 指标引用只能用于 `measures`、指标排序或 `having`；
- 维度引用只能用于 `group_by`、普通过滤或维度排序；
- `target_ref` 的类型必须与 `stage` 匹配；
- 候选检索缺少用户明确提到的指标或维度时，不能输出 `resolved`；
- 存在多个模型的同名维度且无法确定时，输出 `needs_clarification`；
- `status=resolved` 时 `unresolved` 必须为空数组。

该阶段只确定“用户要查什么”，不确定“系统如何执行”。

代码实现：

- `SemanticParseOutput`：语义解析 JSON 契约；
- `SemanticParseService`：调用结构化模型并校验候选 `ref`；
- `semantic_parse`：模型调用阶段名称。

# 6. 执行需求分析与模式路由

## 6.1 设计目标

执行需求分析接收上一阶段已经校验过的语义解析 JSON，并根据资产 ID 补充完整的
语义定义，生成可供 Fast、Plan 或 Research 使用的执行需求。

该阶段不重新理解用户问题，不重新检索资产，也不重新选择指标和维度。上一阶段
已经确定的指标、维度、筛选条件、时间关系和计算要求，是本阶段的输入契约。

Research 是唯一需要额外研究范围的分支，但范围构建也不能改变上述约束：本阶段只能从
同一份已授权 DatasetSchema 快照和治理关系中，确定性投影“后续允许选择的候选范围”，
不能直接替用户选定下一步执行维度或驱动指标。真正选择哪个候选项必须由 Research
基于证据提出，并再次经过动作校验和完整执行需求校验。

本阶段的输出不是 SQL，也不是最终答案，而是：

- 使用哪些模型；
- 需要执行哪些查询；
- 每个查询查询哪些指标和维度；
- 每个查询使用什么过滤和时间条件；
- 查询结果如何合并和计算；
- 下一步进入 Fast、Plan 还是 Research。

## 6.2 输入

### 6.2.1 语义解析 JSON

输入来自候选资产绑定与问题语义解析阶段，例如：

~~~json
{
  "status": "resolved",
  "measures": [
    {"ref": "METRIC:271:246"},
    {"ref": "METRIC:280:248"}
  ],
  "group_by": [
    {"ref": "DIMENSION:278:246"}
  ],
  "filters": [],
  "time_filters": [
    {"expression": "2026年6月", "role": "current"},
    {"expression": "2026年5月", "role": "previous"}
  ],
  "order_by": [],
  "limit": null,
  "calculations": [
    {
      "type": "difference",
      "current_time_role": "current",
      "previous_time_role": "previous"
    }
  ],
  "unresolved": []
}
~~~

该 JSON 是本阶段最重要的输入。它已经表达了用户要查询的指标、维度、筛选、
时间和计算要求，本阶段不能用其他模型输出覆盖这些内容。

### 6.2.2 已绑定资产定义

根据 JSON 中的 `ref` 查询语义层，补充：

- 指标表达式和计算口径；
- 指标所属模型；
- 维度字段和所属模型；
- 指标支持的维度关系；
- 模型默认时间字段；
- 模型之间的关联键和关联关系；
- 资产权限、状态和版本。

例如：

~~~json
{
  "metrics": [
    {
      "ref": "METRIC:271:246",
      "model_ref": "MODEL:246",
      "expression": "SUM(gmv_total)"
    },
    {
      "ref": "METRIC:280:248",
      "model_ref": "MODEL:248",
      "expression": "SUM(stock_qty)"
    }
  ],
  "dimensions": [
    {
      "ref": "DIMENSION:278:246",
      "model_ref": "MODEL:246",
      "column": "stall_id"
    }
  ]
}
~~~

资产定义是执行依据，不能使用模型输出中的名称、别名或用户原话代替。

### 6.2.3 运行时信息

根据需要补充：

- 数据集和租户范围；
- 当前时间和时区；
- 数据权限；
- 查询超时和结果数量限制；
- 当前语义层版本。

## 6.3 处理步骤

### 6.3.1 绑定结果校验

在生成执行需求前，必须确认：

- `status` 为 `resolved`；
- `unresolved` 为空；
- 所有 `ref` 都来自候选资产；
- 所有指标和维度都能查询到完整定义；
- 指标、维度和模型关系有效；
- 用户明确提出的每个指标都没有遗漏。

如果校验失败，不生成执行需求，直接进入澄清或失败流程。

### 6.3.2 补充时间字段

语义解析阶段只保留用户时间表达，例如“2026年6月”。

本阶段根据已绑定指标、所属模型和模型时间字段配置，确定实际使用的时间字段：

~~~text
2026年6月
→ MODEL:246 的默认时间字段 stat_date
→ 2026-06-01 00:00:00 <= stat_date < 2026-07-01 00:00:00
~~~

如果同一个查询中的指标对应不同时间字段，不能直接合并查询，需要拆分查询或
进入澄清流程。

### 6.3.3 补充维度值

维度值不是语义资产。用户输入的值直接作为过滤值使用，并绑定到已经确认的
维度 `target_ref`：

~~~text
店铺100023
→ target_ref: DIMENSION:278:246
→ column: stall_id
→ value: 100023
~~~

本阶段只校验值的类型和维度关系，不要求在语义层中查到“100023”这个资产。

### 6.3.4 按模型和时间拆分查询

查询需求根据以下组合生成：

~~~text
可执行模型 × 时间角色 × 查询条件
~~~

例如：

~~~text
总GMV属于 MODEL:246，库存件数属于 MODEL:248；
时间包含 current 和 previous；

MODEL:246 + current  → gmv_current
MODEL:246 + previous → gmv_previous
MODEL:248 + current  → stock_current
MODEL:248 + previous → stock_previous
~~~

如果多个指标属于同一个模型、时间字段和过滤条件也兼容，应合并到一个查询中，
避免为每个指标单独执行查询。

### 6.3.5 生成查询后处理

根据语义解析 JSON 中的 `calculations` 生成查询后的处理要求：

- 多模型结果按照共同维度键合并；
- 多时间结果按照当前期和对比期对齐；
- `difference` 生成变化量计算；
- `growth_rate` 生成增长率计算；
- `share` 生成占比计算；
- `trend` 生成趋势计算。

查询后处理只描述输入、计算类型和依赖关系，不在本阶段计算实际结果。

## 6.4 执行需求输出

执行需求输出必须包含完整的查询信息，不能只输出模型 ID 和查询 ID。

~~~json
{
  "status": "ready",
  "route": {
    "mode": "plan",
    "reasons": [
      "multiple_time_ranges",
      "multiple_queries",
      "post_query_calculation"
    ]
  },
  "query_requirements": [
    {
      "id": "gmv_current",
      "model_ref": "MODEL:246",
      "metrics": [
        {
          "ref": "METRIC:271:246",
          "expression": "SUM(gmv_total)"
        }
      ],
      "group_by": [
        {
          "ref": "DIMENSION:278:246",
          "column": "stall_id"
        }
      ],
      "filters": [],
      "time": {
        "role": "current",
        "expression": "2026年6月",
        "column": "stat_date"
      },
      "order_by": [],
      "limit": null
    },
    {
      "id": "gmv_previous",
      "model_ref": "MODEL:246",
      "metrics": [
        {
          "ref": "METRIC:271:246",
          "expression": "SUM(gmv_total)"
        }
      ],
      "group_by": [
        {
          "ref": "DIMENSION:278:246",
          "column": "stall_id"
        }
      ],
      "filters": [],
      "time": {
        "role": "previous",
        "expression": "2026年5月",
        "column": "stat_date"
      },
      "order_by": [],
      "limit": null
    }
  ],
  "post_calculations": [
    {
      "id": "merge_gmv_by_stall",
      "type": "merge",
      "join_keys": ["stall_id"],
      "inputs": ["gmv_current", "gmv_previous"]
    },
    {
      "id": "difference_gmv",
      "type": "difference",
      "metric_ref": "METRIC:271:246",
      "current_input": "gmv_current",
      "previous_input": "gmv_previous"
    }
  ],
  "unresolved": []
}
~~~

字段职责如下：

| 字段 | 作用 |
| --- | --- |
| status | 表示执行需求是否可以生成 |
| route | 表示下一步进入 Fast、Plan 还是 Research |
| reasons | 记录路由依据，便于调试和审计 |
| query_requirements | 描述每个实际查询需要使用的模型、指标、维度、过滤和时间条件 |
| post_calculations | 描述查询结果合并和后续计算；每项必须有稳定的 `id`，供 `ComputeTask` 引用 |
| result_contract | 声明主要结果、辅助结果、展示顺序、完成策略和分析类型 |
| unresolved | 表示无法生成执行需求的原因 |

`result_contract` 用于表达多个结果如何共同回答用户问题，不用于表达 DAG 依赖。其核心
字段包括：

- `primary_requirement_id`：回答主要结论所依赖的查询或计算需求；
- `supporting_requirement_ids`：用于补充解释的并列结果；
- `ordered_requirement_ids`：答案中各结果的组织顺序；
- `completion_policy`：主要结果和辅助结果的完成要求；
- `analysis_type`：固定下钻、固定归因等分析类型。

当不同结果粒度无法安全合并时，例如“总量 + 一级分组 + 二级分组”，应通过
`result_contract` 声明为有顺序的并列结果，不能为了形成单一叶子节点而使用
`merge` 拼接不同粒度的数据。

## 6.5 模式路由规则

模式路由根据执行需求的结构判断，不根据用户问题中的关键词直接判断。

### Fast

满足以下条件时进入 Fast：

- 只需要一个模型；
- 只需要一个查询；
- 不需要跨查询合并；
- 不需要查询后计算。

单指标、多指标同模型、同模型的 Top N 和 `having` 条件，都可能进入 Fast。

### Plan

满足以下任一情况时进入 Plan：

- `query_requirements` 多于一个；
- 存在 `post_calculations`；
- 存在多个模型、多个时间范围或跨结果集处理；
- 存在目标明确的 `multi_step` 分析，例如下钻或固定归因模板。

多个查询不要求全部串行执行。没有依赖关系的查询应在 Plan 执行阶段并行执行，
只有存在计算或后续查询依赖时才建立 DAG 边。

### Research

Research 不由普通的多查询或查询后计算直接触发。只有满足以下情况时才进入
Research：

- 用户提出开放式的原因、归因或影响因素问题，无法由固定模板完整表达，且需要根据
  中间结果提出或选择新的假设；
- 后续查询的筛选值、分析对象、维度或验证方向必须根据中间结果确定；
- 停止条件依赖已经获得的证据，而不是执行前固定的节点数量；
- 需要输出包含多个发现、证据和未验证假设的分析报告。

用户说“深入分析”不能单独触发 Research。如果全部查询、计算和依赖仍可在执行前
确定，应继续进入 Plan。Research 的决定依据是执行拓扑是否依赖中间结果，而不是措辞。

## 6.6 对下一步的作用

执行需求输出是后续模式执行的唯一输入之一：

- Fast 使用完整的执行需求输出规则生成单节点 `AnalysisPlan`，再进入 SQL 编译；
- Plan 使用完整的执行需求输出生成多节点 `AnalysisPlan`，再按 DAG 执行；
- Research 使用第 5 步生成的 `ResearchRequirement` 建立受限研究范围。每轮模型只选择
  结构化 `ResearchAction`，服务端将动作物化为完整 `ExecutionRequirement`，再复用
  Plan 完成证明和执行。

后续模式的主输入是第 5 步输出。可以附带第 5 步已经读取的指标、维度、模型和
契约的不可变版本快照，但不得重新检索资产或重新绑定指标、维度。若执行过程中
发现资产关系不成立，应回到澄清或失败处理，不能静默替换资产。

执行需求分析不直接生成最终 SQL。SQL 由后续查询编译阶段根据执行需求和完整
语义资产定义生成。

# 7. Fast 模式

## 7.1 设计目标

Fast 是确定性执行流水线，面向已经完成语义绑定且只需要一个查询结果集的问题。
Fast 不调用规划模型，不重新理解问题，不重新检索资产，也不重新绑定指标、维度
和筛选值。

Fast 模式的核心流程为：

    执行需求
      -> 规则生成单节点 AnalysisPlan
      -> QueryTask 校验
      -> 语义查询计划编译
      -> 权限和 SQL 安全校验
      -> 查询执行
      -> ResultStore
      -> 结果校验
      -> AnswerComposer

Fast 模式仍然可以使用前置的问题重写、语义解析和绑定模型，也可以使用答案生成
模型。这里的“无规划模型”只表示 Fast 不增加 AnalysisPlan 规划调用。

## 7.2 输入条件

进入 Fast 前必须满足：

- `status` 为 `ready`；
- `route.mode` 为 `fast`；
- `unresolved` 为空数组；
- `query_requirements` 只有一项；
- `post_calculations` 为空数组；
- 查询只涉及一个可执行模型和一个结果集。

同一模型下的多指标、分组、过滤、排序、Top N 和 `having` 条件仍然可以进入
Fast。这里的“一个查询”指一个外部 `QueryTask`，不限制编译器在 SQL 内部使用
CTE 或子查询。

如果输入不满足上述条件，说明第 5 步路由结果与执行需求不一致，应记录契约错误
并进入失败处理，不能在 Fast 内部静默切换模式。

## 7.3 AnalysisPlan 生成

Fast 由服务端规则根据第 5 步的唯一 `query_requirement` 生成单节点计划。规划结果
只引用第 5 步的需求 ID，查询规格由服务端从权威输入补齐：

~~~json
{
  "mode": "fast",
  "planner": "rule",
  "nodes": [
    {
      "id": "q:gmv_current",
      "type": "query",
      "source_requirement_id": "gmv_current"
    }
  ],
  "edges": [],
  "presentation": {
    "primary": "q:gmv_current"
  }
}
~~~

Fast 计划不允许新增指标、维度、过滤值、时间条件或物理字段。计划生成后写入
运行态，作为编译、执行、审计和回答引用的统一载体。

## 7.4 校验、编译和执行

1. 校验 `source_requirement_id` 存在且只引用第 5 步输出；
2. 将 `query_requirement` 转换为现有 `SemanticQueryPlan`；
3. 执行指标、维度、模型、粒度、时间和关系契约校验；
4. 通过 `PROVEN` 校验后生成 SQL 和计划指纹；
5. 通过数据权限、行列权限和 SQL 安全校验；
6. 执行查询并将结果写入 `ResultStore`；
7. 校验结果 schema、指标字段、行数和结果引用；
8. 将结果摘要和口径元数据交给 `AnswerComposer`。

SQL 由语义编译器生成，Fast 不允许模型直接生成 SQL。

## 7.5 失败处理

- `unresolved` 不为空时进入澄清；
- 语义校验失败时返回结构化失败原因；
- SQL 编译错误可以进入受限修复流程，但修复不能改变已确认语义；
- 查询执行失败时记录 QueryTask 错误；
- 失败后不得重新绑定资产或静默切换到 Plan；
- 已经产生有效结果时，按统一部分结果规则作答。

# 8. Plan 模式

## 8.1 设计目标

Plan 是有限、可提前确定、可完整校验的计划-执行模式，面向需要多个查询、跨结果集
计算或有限多步分析的问题。Plan 使用 `AnalysisPlan` 表达查询任务和计算任务之间的
依赖关系，再由确定性执行器完成编译、查询、计算和结果校验。

Plan 的核心边界为：

- 一个独立 `QueryTask` 可以完成且不需要结果后处理的问题进入 Fast；
- 多个 `QueryTask`、跨结果集计算或可提前完整确定的有限多步任务进入 Plan；
- 后续查询必须根据中间结果动态决定的问题进入 Research；
- 指标、维度、时间、筛选、连接键或口径不明确的问题进入澄清；
- 超出语义层、计算白名单或执行预算的问题进入拒答或问题拆分。

Plan 不根据问题中是否出现“同比”“分析”“下钻”等关键词判断。模式路由只根据
第 5 步已经生成的执行需求结构判断。

Plan 的核心流程为：

    执行需求
      -> 规则模板匹配
      -> 规则模板或通用规则生成器生成 AnalysisPlan 草案
      -> 计划结构、需求覆盖和计算契约校验
      -> 所有 QueryTask 规划、编译和 PROVEN 校验
      -> 冻结 PROVEN AnalysisPlan
      -> DAG 拓扑分批执行
      -> ComputeEngine 执行 ComputeTask
      -> ResultStore
      -> 结果校验
      -> AnswerComposer

Plan 模式不是自由 ReAct 循环。有限多步模型只允许在第 5 步分解结构化执行需求，
不能直接生成可执行计划、控制工具调用或绕过语义编译器和权限校验。

Plan 必须区分两个阶段：

1. **计划构建与证明**：完成全部 QueryTask 的语义校验、SQL 编译、权限校验和
   SQL 安全校验；只有全部 QueryTask 均为 `PROVEN`，整个计划才能进入执行阶段；
2. **计划执行**：按照已冻结的 DAG 分批执行，执行过程中不能修改指标、维度、
   时间、筛选、连接键和计算口径。

不得采用“编译一个 QueryTask 后立即执行，再继续编译下一个 QueryTask”的方式。
否则后续节点编译失败时，已经执行的节点属于一个尚未完成证明的计划。

## 8.2 输入条件

进入 Plan 前必须满足：

- `status` 为 `ready`；
- `route.mode` 为 `plan`；
- `unresolved` 为空数组；
- 存在多个 `query_requirements`，或存在 `post_calculations`，或存在目标明确的
  `multi_step` 分析；
- 第 5 步已经完成指标、维度、模型、时间和筛选条件的绑定。

Plan 不重新读取用户原始问题来决定查询内容。规划阶段的主输入是第 5 步输出，
必要的语义资产定义使用第 5 步已经读取的不可变版本快照。

以下情况不能进入 Plan：

- `status` 不是 `ready`；
- `unresolved` 不为空；
- 查询或计算所需的资产、时间、连接键尚未确定；
- 需要根据中间结果生成新的查询条件或选择新的分析方向；
- 计算操作不在白名单内；
- 预计查询数、节点数、数据量、执行时间或并发量超出预算。

## 8.3 执行需求完整性

`ExecutionRequirement` 是 Plan 的唯一业务输入。进入计划生成前，必须完成查询和
计算需求的完整性校验。

每个 `QueryRequirement` 必须完整包含：

- 稳定且唯一的查询需求 ID；
- 模型引用；
- 已绑定的指标和维度资产 ID；
- 已绑定的筛选条件和筛选阶段；
- 已归一化的时间范围、时间角色和时间维度；
- 排序、HAVING、LIMIT 和查询形状；
- 语义层版本和契约版本快照。

每个 `CalculationRequirement` 必须完整包含：

- 稳定且唯一的计算需求 ID；
- 白名单内的计算类型；
- 明确的输入查询或上游计算 ID；
- 必要的连接键；
- 输入字段和输出字段；
- 必要的当前期、对比期角色；
- 空值、除零和精度策略；
- 计算所需的其他受限参数。

P1 阶段支持的计算类型为：

- `merge`；
- `difference`；
- `growth_rate`；
- `share`；
- `ratio`；
- `topn_other`；
- `pivot`；
- `contribution`；
- 受限 `expr`。

计算类型必须使用枚举或判别联合契约，不能使用不受约束的任意字符串。输入数量、
连接键、字段、时间角色、除零策略等错误应在执行需求或计划校验阶段发现，不能推迟
到 ComputeEngine 执行时才失败。

多个查询结果如果共同参与用户答案，必须存在明确的 `ComputeTask` 将它们合并或
计算，或者明确声明为互不关联的并列结果。跨模型不能只生成多个 QueryTask 而没有
确定性的合并关系。

## 8.4 计划生成方式

完整 `ExecutionRequirement` 已经包含查询、计算、输入依赖和连接键，因此
`AnalysisPlan` 必须由服务端确定性生成。Plan 本身不调用模型决定 DAG。

规则模板负责识别已知分析结构并补齐统一的计划元数据；没有专用模板但执行需求
已经完整时，通用规则生成器根据 `QueryRequirement`、`CalculationRequirement`
及其输入依赖生成 DAG。

有限多步任务如果尚未形成完整执行需求，可以在第 5 步内部使用一次受限模型，将
`MultiStepRequirement` 分解为完整的查询和计算需求。分解结果通过第 5 步的全部
校验后，再交给确定性计划生成器。模型不直接产出可执行 `AnalysisPlan`。

### 8.4.1 规则模板匹配

规则模板是 Plan 的默认生成方式。模板匹配依据执行需求结构，不读取用户关键词。

规则模板必须提供统一入口：

~~~text
match(ExecutionRequirement) -> 不匹配 | 唯一匹配 | 匹配但信息不足
build(ExecutionRequirement) -> AnalysisPlan
~~~

只有唯一匹配时才允许直接生成计划。多个模板同时匹配且会产生不同业务结果时，
不能让模型自行选择，应进入澄清或通过确定性优先级消除重叠。

P1 阶段至少提供以下模板：

| 模板 | 匹配条件 | 计划结构 |
| --- | --- | --- |
| period_compare | 当前期、对比期查询，指标和粒度兼容 | 两个 QueryTask → compare/difference |
| growth_rate | 当前期、对比期查询，存在增长率计算 | 两个 QueryTask → growth_rate |
| cross_model_merge | 多模型查询，共同连接键明确 | 多个 QueryTask → merge |
| share | 分组结果和占比字段明确 | QueryTask → share |
| ratio | 分子、分母和结果字段明确 | 一个或两个 QueryTask → ratio |
| topn_other | 分组字段、排序指标和 N 明确 | QueryTask → topn_other |
| pivot | 行维度、列维度和值字段明确 | QueryTask → pivot |
| fixed_drilldown | 下钻层级和顺序明确，且不依赖中间结果 | 各粒度 QueryTask 并列输出；存在双时间角色时各层追加 difference |
| fixed_attribution | 加法指标、当前期、对比期和归因维度明确 | 总量与分组查询 → difference → contribution |

普通 Top N、HAVING、多指标同模型、单时间范围趋势和可由认证派生指标在一条 SQL 中
编译完成的比率仍然进入 Fast，不得因为指标或 SQL 表达复杂而进入 Plan。

### 8.4.2 有限多步模型分解门槛

受限模型只处理规则无法直接展开为完整执行需求的有限多步任务，并且必须同时满足：

- 所有资产、时间、筛选、连接键和计算口径已经确定；
- 允许使用的指标、维度、时间角色和计算操作已经确定；
- 任务节点数量和查询数量有明确上限；
- 后续节点不需要根据中间数据选择新的指标、维度或筛选值；
- 模型只需要把有限分析目标分解成结构化查询需求和计算需求。

如果 `ExecutionRequirement` 已经完整给出了全部查询、计算和输入依赖，则 DAG 可以
由服务端确定性生成，不应调用规划模型。

模型分解的输入只包括：

- 结构化 `MultiStepRequirement`；
- 已绑定资产的不可变快照；
- 允许使用的资产 ID、时间角色和计算操作；
- 节点数、查询数、Token、超时和并发预算；
- 上一次执行需求校验的结构化错误，且只在唯一一次修复调用中提供。

模型只能：

- 使用已绑定资产生成候选 `QueryRequirement`；
- 使用白名单操作生成候选 `CalculationRequirement`；
- 为计算需求引用候选查询或上游计算 ID；
- 声明主要结果对应的候选需求 ID。

模型不能：

- 新增或替换指标、维度和模型；
- 修改时间、筛选、排序、LIMIT 和连接键；
- 生成 SQL、物理字段或任意计算表达式；
- 创建白名单外的计算操作；
- 根据用户原始问题重新解释业务口径。

模型输出的是执行需求扩展，不是 `AnalysisPlan`。推荐结构如下：

~~~json
{
  "query_requirements": [
    {
      "id": "gmv_current",
      "template": "bound_query",
      "metric_refs": ["METRIC:271:246"],
      "dimension_refs": ["DIMENSION:278:246"],
      "time_role": "current"
    },
    {
      "id": "gmv_previous",
      "template": "bound_query",
      "metric_refs": ["METRIC:271:246"],
      "dimension_refs": ["DIMENSION:278:246"],
      "time_role": "previous"
    }
  ],
  "post_calculations": [
    {
      "id": "difference_gmv",
      "type": "difference",
      "inputs": ["gmv_current", "gmv_previous"],
      "join_keys": ["stall_id"]
    }
  ],
  "primary_requirement_id": "difference_gmv"
}
~~~

模型输出中的资产引用必须属于已绑定范围。服务端根据不可变资产快照补齐完整查询
规格，并重新执行第 5 步的覆盖、关系、粒度、时间、计算和预算校验。只有通过校验
后，扩展内容才能合并为正式 `ExecutionRequirement`。

模型分解或执行需求校验失败时，最多根据结构化错误修复一次。修复后仍然失败：

- 若存在唯一规则模板，使用该模板重新生成；
- 信息不足时进入澄清；
- 能力或预算不足时拒答或要求拆分问题；
- 不得生成语义更简单的计划；
- 不得静默降级为 Fast。

### 8.4.3 AnalysisPlan 元数据

`AnalysisPlan` 除任务和展示信息外，应记录：

- `mode=plan`；
- `planner.type=rule`；
- 规则模板名称和版本，或通用规则生成器版本；
- `decomposer.type=none|llm`，以及使用模型时的模型和提示词版本；
- `execution_requirement_fingerprint`；
- 语义层 schema 和 contract 版本；
- 查询数、节点数、并发数和超时预算；
- 完成策略；
- 计划版本。

计划来源和输入指纹必须可审计、可回放。执行需求或语义层版本变化后，旧计划不能
直接复用，必须重新完成证明。

## 8.5 计划校验

计划草案必须通过以下校验后才能执行：

1. JSON Schema 结构校验；
2. `source_requirement_id` 和 `source_calculation_id` 引用校验；
3. 查询需求和必要计算需求覆盖校验，不能遗漏、重复或凭空增加任务；
4. DAG 无环、自引用、未知节点和孤立计算节点校验；
5. `QueryTask` 与 `ComputeTask` 的输入输出字段、粒度和时间角色校验；
6. 计算操作、输入数量、连接键、空值和除零策略校验；
7. 每个 `QueryTask` 的指标、维度、粒度、时间和关系契约校验；
8. 每个 `QueryTask` 的 SQL 编译、数据权限、行列权限和 SQL 安全校验；
9. 每个 `QueryTask` 编译结果的 `PROVEN` 和计划指纹校验；
10. 结果集引用、主要展示节点、并列结果和口径一致性校验；
11. 主要展示节点必须是最终叶子节点，不能指向仍有下游计算的中间结果；
12. 节点数、查询数、结果大小、Token、超时和并发预算校验。

依赖关系只能有一个事实源。`ComputeTask.inputs` 是计算依赖的事实源，`edges` 由
服务端根据任务输入生成；如果为了展示和审计持久化 `edges`，校验器必须验证它与
任务输入推导出的边完全一致。任何规则或模型都不能同时独立生成两套依赖关系。

跨模型结果如果需要共同回答用户问题，必须存在明确的 `merge` 或其他 ComputeTask。
`merge` 不能在计划生成时被跳过。`difference` 应作为明确操作存在，不能只映射成
含义宽泛的 `compare`。

计划状态转换为：

    DRAFT
      -> 结构与契约校验通过
      -> 所有 QueryTask 编译和证明通过
      -> PROVEN

任一 QueryTask 未完成证明时，整个计划不能进入执行阶段。

计划校验失败时，使用同一份执行需求通过规则生成器重建一次；仍然失败则进入失败
处理。只有错误来自第 5 步的有限多步模型分解时，才允许向模型回传结构化错误并
修复一次。信息不足进入澄清，能力或预算不足进入拒答，不得无限重试。

## 8.6 DAG 批次执行

计划达到 `PROVEN` 后，由 DAG 调度器按照依赖关系生成执行批次。同一批中的节点
相互之间没有未满足依赖，可以在预算范围内并行执行；不同批次保持依赖顺序。

例如：

    q:current  ─┐
                ├─> c:growth ─> c:share
    q:previous ─┘
    q:total --------------------> c:share

执行批次为：

1. 第一批：`q:current`、`q:previous`、`q:total` 并行执行；
2. 第二批：依赖前两项的 `c:growth`；
3. 第三批：依赖 `c:growth` 和 `q:total` 的 `c:share`。

DAG 调度步骤为：

1. 按拓扑顺序生成执行批次；
2. 没有依赖关系的 QueryTask 在查询并发上限内并行执行；
3. 每个 QueryTask 使用独立数据库 Session、独立工具调用上下文和独立结果对象；
4. QueryTask 不得共享或覆盖 `last_execution`、`full_data`、`result_node_id` 等
   单任务可变状态；
5. 每个 QueryTask 的结果写入带有稳定 ID 的 `ResultStore`；
6. 主调度线程统一合并任务结果、更新计划快照并发布事件；
7. 依赖满足后，由 `ComputeEngine` 执行对应的 ComputeTask；
8. 同一批且相互独立的 ComputeTask 可以在计算预算内并行执行；
9. 计算结果作为新的命名结果集写回 `ResultStore`；
10. 所有必需结果集完成校验后进入 `AnswerComposer`。

单个节点的运行结果统一为：

~~~json
{
  "task_id": "q:gmv_current",
  "attempt": 1,
  "status": "succeeded",
  "compiled_query": {
    "plan_fingerprint": "...",
    "sql": "..."
  },
  "result_set_ref": {
    "result_set_id": "result:plan-123:q:gmv_current"
  },
  "execution_summary": {
    "row_count": 12,
    "duration_ms": 180
  }
}
~~~

节点运行状态至少包括：

- `PENDING`；
- `READY`；
- `RUNNING`；
- `SUCCEEDED`；
- `FAILED`；
- `SKIPPED_DEPENDENCY`；
- `CANCELLED`。

P1 阶段的 ComputeTask 仅允许使用确定性白名单操作，包括 `merge`、`difference`、
`growth_rate`、`share`、`topn_other`、`pivot` 和受限 `expr`。LLM 不参与结果计算。

## 8.7 ResultStore 和结果引用

每个 QueryTask 和 ComputeTask 只能产生一个正式命名结果集。结果集 ID 统一由
`plan_id + node_id` 生成，不能由调用方自由拼接。

结果集至少记录：

- `result_set_id`、`plan_id` 和 `node_id`；
- 结果类型：query 或 compute；
- 字段、行数和数值统计；
- 来源 SQL 或计算 SQL；
- 语义资产引用；
- Artifact 引用；
- 创建时间和任务 attempt。

ComputeTask 只能读取其 `inputs` 声明的结果集。AnswerComposer 只能把
`presentation.primary_result` 和明确声明的并列结果作为回答数字来源。

进模型上下文的结果只能是字段、统计摘要和有界采样行。全量结果只提供给
ComputeEngine 和前端表格。超过进程内计算行数或字节数门限时，不能静默截断后
计算，应使用外部计算执行器或明确失败。

## 8.8 失败处理和部分结果

- 独立 QueryTask 失败时，不阻塞没有依赖关系的其他任务；
- 依赖失败时，相关下游节点标记为 `SKIPPED_DEPENDENCY`；
- 查询编译或执行错误可以进入受限修复流程，但不能改变第 5 步的语义；
- 计划校验失败不能通过重新检索或重新绑定资产解决；
- SQL 修复、重试和恢复必须复用相同的执行需求、语义版本和计划指纹；
- 查询或计算重试使用 attempt 记录，不能产生多个无法区分的正式结果集；
- 主要结果节点失败时，整个 Plan 失败；
- 次要结果失败但主要结果成功时，只有完成策略允许才可以返回部分结果，并必须
  明确说明缺失节点和影响；
- P1 默认完成策略为 `require_primary`，即主要结果成功才允许回答；
- 预算耗尽时不得用未完成的中间结果假装完成原问题；
- Plan 不得静默降级为 Fast。若实际需求符合 Fast，应在第 5 步重新生成明确的路由
  结果后再执行。

## 8.9 实施顺序

Plan 按以下顺序落地：

### 第一阶段：完整的规则 Plan

1. 收紧 `CalculationRequirement` 契约；
2. 补充明确的 `merge` 和 `difference` 计算操作；
3. 建立 `PlanTemplateRegistry`；
4. 实现对比、增长率、差值、占比、比率、跨模型合并和 Top N + 其他模板；
5. 增加执行需求覆盖和多查询完整性校验；
6. 改为所有 QueryTask 先完成编译和证明，再开始执行；
7. 接通 Plan 前澄清和恢复。

实施状态（2026-08-19）：已完成。实现采用一个确定性 `AnalysisPlanner` 统一根据
`ExecutionRequirement` 生成任务、依赖边和主要结果，并在审计报告中记录匹配的模板名；
当前没有为模板建立只调用一次的注册类。计算白名单、执行需求完整性、多查询合并、
整份计划证明和语义解析澄清恢复已经接通。

### 第二阶段：DAG 批次调度

1. 增加拓扑批次生成器；
2. 抽出不共享运行态的 QueryTaskExecutor；
3. 为每个 QueryTask 使用独立数据库 Session 和工具调用上下文；
4. 实现查询批次并行执行；
5. 增加节点状态、失败传播、取消、超时和幂等处理。

实施状态（2026-08-19）：已完成。具体实现如下：

1. `dag_scheduler.py` 以 `ComputeTask.inputs` 为唯一依赖事实源，按计划节点原始顺序
   生成确定性的拓扑批次；
2. `QueryTaskExecutor` 在每个工作线程中创建独立数据库 Session、
   `DatasourceQueryService`、`ToolCallContext` 和结果对象，工作线程不读写
   `AgentRuntimeState`；
3. `PlanPipeline` 使用受并发上限控制的线程池并行执行同批 QueryTask 和 ComputeTask；
4. ResultStore 注册、`plan_task_states` 更新、Run 状态持久化和事件发布全部由主线程
   完成，不再使用共享的 `result_node_id`、`last_execution`、`full_data` 驱动多查询；
5. 节点状态已覆盖 `PENDING`、`READY`、`RUNNING`、`SUCCEEDED`、`FAILED`、
   `SKIPPED_DEPENDENCY` 和 `CANCELLED`，依赖失败会阻止下游计算，但不影响同批无依赖
   查询完成；
6. 查询截止时间取单任务超时和 Run 剩余时间的较小值，取消信号在查询前后检查；
7. ResultSet 使用稳定的 `plan_id + node_id` 标识，并记录 attempt；正式结果仍然只有
   一个命名结果集；
8. 查询批次并发上限由 `CHATBI_PLAN_QUERY_CONCURRENCY` 配置，默认值为 4。

手动验证脚本 `backend/scripts/run_plan_full_stage_cases.py` 已同步输出 DAG 批次、
节点 attempt 和各批执行结果，并使用独立 Session 实际并行执行查询。当前已用
`cross_model_merge` 和 `growth_rate` 两类双查询计划完成真实数据源验证。

### 第三阶段：有限多步分析

1. 增加固定下钻模板；
2. 增加固定归因模板；
3. 扩展语义解析和执行需求中的有限多步契约；
4. 明确静态多步任务与动态 Research 的边界。

实施状态（2026-08-20）：已完成。第三阶段仍然使用确定性规则生成
`ExecutionRequirement` 和 `AnalysisPlan`，不调用规划模型。当前实现如下：

1. `SemanticParseOutput.multi_step` 使用判别联合表达 `fixed_drilldown`、
   `fixed_attribution` 和 `dynamic_research`，不接受无约束字典；
2. 固定下钻要求模型、指标、层级和层级顺序在执行前完整确定。总量和不同下钻层级
   分别生成独立 QueryRequirement；不同粒度结果通过 `result_contract` 组织，不使用
   `merge` 强行合并；只有一个下钻层级且不要求总量时仍走 Fast；
3. 固定归因第一版只支持 `FULL` 加法指标，并要求当前期、对比期、指标和归因维度
   全部明确。规则生成总量当前期、总量对比期、分组当前期和分组对比期四个查询，
   再依次执行总差值、分组差值和贡献度计算；
4. `contribution` 输出分组差值、总差值、贡献度和 `reconciliation_difference`。
   执行器校验分组差值之和与总差值，超过容差时明确失败；总差值为零时贡献度为
   `NULL`，允许负贡献和大于 100% 的贡献；
5. `ExecutionResultContract` 明确主要结果、辅助结果、结果顺序和完成策略，
   `AnalysisPlanner` 将其映射为 `PresentationHint`，`AnswerComposer` 按顺序使用主要
   和辅助结果，不能混淆不同结果粒度；
6. 动态筛选、动态维度选择、需要依据中间结果继续确定查询方向以及开放式原因探索，
   均标记为 `dynamic_research`，不进入静态 Plan。Research 执行器未实现时返回明确的
   `EXECUTION_MODE_NOT_AVAILABLE:research`，不能静默降级；
7. DAG 调度器只处理已经确定的任务与依赖，不理解“下钻”或“归因”业务语义。固定
   多步结构由路由和规则展开阶段一次性生成，调度器继续按第二阶段的拓扑批次执行。

真实数据验证使用 dataset `243`、datasource `13`、指标 `METRIC:271:246` 和维度
`DIMENSION:278:246` 完成：固定下钻的总量与分组查询在同一批并行执行；固定归因按
“四个查询 → 两个差值计算 → 一个贡献度计算”三个批次执行。贡献度与总差值对账通过，
实际浮点误差约为 `2.18e-11`，处于容差范围内。

第三阶段的静态 Plan 与第四阶段的受限模型分解是两个独立能力。第三阶段不会因为
规则未匹配而调用模型；规则无法确定完整拓扑时，只能进入澄清、Research 或能力不足
处理。是否启用第四阶段以及模型分解的题型范围，需要在专项评测通过后单独决定。

### 第四阶段：受限多步模型分解

1. 只处理规则无法直接展开的有限多步任务；
2. 只允许使用已绑定资产、时间角色和白名单计算操作；
3. 输出完整执行需求扩展，不直接输出可执行 AnalysisPlan；
4. 执行需求校验失败时最多进行一次结构化修复；
5. 通过专项题集验证后再默认启用。

实施状态（2026-08-20）：已完成。第四阶段不是 Agent 循环，而是一次受限执行需求
分解；首次草案存在可修复的结构错误时，最多追加一次修复调用。具体实现如下：

1. `SemanticParseMultiStep` 增加 `limited_multistep`，只表达分析目标、已绑定指标和
   维度、允许的时间角色及用户要求的输出；需要读取中间结果后选择筛选值、维度或
   查询方向的问题仍输出 `dynamic_research`；
2. `LimitedMultiStepDecomposer` 的输入只包含已绑定资产摘要、时间角色、白名单操作和
   固定预算，不包含 SQL、物理字段、查询结果或工具；正常路径只调用一次模型；
3. 模型输出 `ExecutionRequirementDraft`，包含候选查询、白名单计算输入关系和
   ResultContract。模型不能输出 AnalysisPlan、SQL、DAG edges、未绑定资产或动态条件；
4. 草案使用严格 DTO 校验 ID、输入引用、无环依赖、资产范围、时间角色、操作白名单、
   查询数、计算数、总节点数、DAG 深度和叶子结果覆盖。默认预算为 6 个查询、6 个计算、
   10 个总节点和 4 层 DAG；模型返回的计算节点数组顺序不作为依赖依据，服务端根据
   `inputs` 稳定生成拓扑顺序后再物化正式计算需求；
5. 校验通过后，ModeRouter 使用权威资产和时间绑定补齐正式 QueryRequirement 与
   CalculationRequirement。AnalysisPlanner 继续确定性生成 AnalysisPlan，并在审计中
   同时记录 `planner_source=rule` 和分解模型、提示词版本、调用次数及是否修复；
6. 当前模型可使用 `merge`、`difference`、`growth_rate`、`share`、`ratio`、
   `topn_other`、`pivot` 和 `contribution`。`expr` 不对分解模型开放；contribution 仍
   强制要求 `FULL` 加法指标；
7. 首次输出校验失败时，修复输入只包含原受限上下文、第一次草案和结构化错误。第二次
   仍失败则明确返回 `LIMITED_MULTISTEP_OUTPUT_INVALID`，不继续循环，也不降级为语义
   更简单的计划。

真实问题集保存在 `backend/scripts/data/plan_stage4_real_questions.json`，手动脚本为
`backend/scripts/run_plan_stage4_question_cases.py`。当前题集包含：

- 差值结果继续执行 Top 3 + 其他，验证一次模型分解和三批 DAG；
- 增长率结果继续执行 Top 3 + 其他，验证同一有限拓扑下的另一种白名单计算；
- 固定归因，验证规则模板优先且不调用分解模型；
- 根据下降最大档口继续分析原因，验证进入 Research；
- 单日总 GMV，验证保持 Fast。

使用 tenant `1`、dataset `243`、datasource `13` 完成真实模型和数据库验证。两个有限
多步用例均生成两个并行 QueryTask，随后分别执行 difference 或 growth_rate，再执行
topn_other；最终返回 3 个档口及
一条 `OTHER` 汇总。五个真实问题的语义类型、模式边界、模型调用审计、SQL 编译和需要
执行的真实结果均通过验证。

P1 完成后，Plan 至少覆盖多时间范围、同比、环比、差值、增长率、跨模型合并、
跨结果集占比和比率、Top N + 其他、透视、固定下钻和固定归因。开放式原因探索、
根据中间结果继续选择查询方向的任务不属于 Plan，由 Research 处理。

# 9. Research 模式

## 9.1 设计目标与职责

Research 是面向动态多步分析的有界执行模式。它解决的问题不是“查询很多”，而是
“下一步查询什么必须观察当前结果后才能确定”。Research 允许根据证据动态选择分析
对象、筛选值、维度和验证方向，最终产出包含数据引用的结构化研究报告。

Research 的职责是：

1. 根据已有证据判断当前研究目标的完成程度；
2. 提出或更新可验证的候选假设；
3. 从已授权研究范围中选择下一批结构化分析动作；
4. 根据查询结果动态选择筛选对象、下钻方向或后续验证方向；
5. 在预算、重复动作和证据充分性约束下决定继续或结束；
6. 输出已确认发现、已否定假设、未验证假设和分析限制，并绑定结果引用。

Research 不负责：

- 生成 SQL、物理表名或字段名；
- 绕过语义编译、指标契约、关系校验、数据权限和 SQL 安全校验；
- 重新解释或替换已经确认的指标口径、时间范围和业务筛选条件；
- 在执行中自由检索并绑定未进入研究范围的资产；
- 执行任意 Python、任意表达式或任意外部工具；
- 自动扩大查询数、模型调用数、Token、时间和结果大小预算；
- 将数据贡献、共同变化或相关性表述为严格因果关系。

## 9.2 与 Fast、Plan 的边界

三种模式使用同一条判断原则：执行所需信息在什么时候才能确定。

| 模式 | 执行前可确定的信息 | 运行方式 |
| --- | --- | --- |
| Fast | 单个查询的全部语义 | 确定性单查询执行 |
| Plan | 全部查询、计算和依赖拓扑 | 冻结完整 `AnalysisPlan` 后执行 |
| Research | 只确定研究目标、范围和首轮动作；后续动作依赖中间结果 | 外层有界循环，内层执行多个不可变子计划 |

边界示例：

- “计算同比，再取同比最低的 3 个档口”属于 Plan，完整拓扑执行前可知；
- “找出下降最大的档口，再分析该档口下降原因”属于 Research，第二步筛选值来自第一步；
- “按档口计算 GMV 变化贡献”属于 Plan，归因维度和计算方式明确；
- “分析 GMV 为什么下降，找出主要影响因素”属于 Research，需要根据证据选择维度和假设；
- “按档口、品类、区域分别分析变化”属于 Plan，三个分解方向均已明确；
- “先看哪个维度最能解释变化，再沿该维度继续分析”属于 Research，后续方向依赖结果；
- “深入分析同比变化”不必然属于 Research，若可由固定有限拓扑完成仍进入 Plan。

Research 不能作为 Plan 校验失败后的回退模式。只有第 5 步明确形成动态研究需求时才能
进入 Research；信息不足进入澄清，能力或预算不足进入拒答或问题拆分。

## 9.3 Agent 范式

Research 采用单 Agent、假设驱动、结构化动作、服务端控制的有界循环：

~~~text
确定性初始查询
  -> Observe：读取受控 EvidenceSnapshot
  -> Hypothesize：提出或更新候选假设
  -> Decide：选择下一批 ResearchAction
  -> Validate：服务端校验动作、资产、来源和预算
  -> Execute：物化 ExecutionRequirement，生成并执行 PROVEN AnalysisPlan
  -> Evaluate：更新证据和假设状态
  -> 结束或进入下一轮
~~~

这不是自由 ReAct。模型不直接选择工具、生成 SQL 或控制运行时；模型只承担
`ResearchPolicy`，输出严格结构化决策。循环、状态、预算、校验、执行、恢复和停止由
服务端 `ResearchController` 控制。

这也不是一次性 Planner-Executor。Research 无法在开始时生成完整 DAG，但每一轮内部
仍复用现有 Plan 能力：

~~~text
外层：Hypothesis-Driven Research Loop
内层：ExecutionRequirement -> PROVEN AnalysisPlan -> ResultStore
~~~

第一版不采用多 Agent。SQL 编译、计划证明、查询执行、计算、结果存储和报告引用已经有
确定性组件负责，不再增加 SQL Agent、执行 Agent、批判 Agent 或独立报告 Agent。一次
Research Policy 调用同时完成证据评估、假设更新和下一步动作选择。

## 9.4 ResearchRequirement

Research 不能只依赖当前 `dynamic_research.goal` 和 `reason` 运行。第 5 步需要生成独立
`ResearchRequirement`，明确目标、研究范围、允许动作和硬预算。推荐契约如下：

~~~json
{
  "goal": "分析总GMV下降的主要影响因素",
  "reason": "open_ended_cause",
  "target_metric_refs": ["METRIC:271:246"],
  "time_roles": ["current", "previous"],
  "time_bindings": [
    {
      "role": "current",
      "expression": "2026年6月30日",
      "dimension_ref": "DIMENSION:276:246",
      "dimension_id": 276,
      "normalized": {
        "kind": "absolute_range",
        "start": "2026-06-30",
        "end_exclusive": "2026-07-01"
      }
    },
    {
      "role": "previous",
      "expression": "2026年6月29日",
      "dimension_ref": "DIMENSION:276:246",
      "dimension_id": 276,
      "normalized": {
        "kind": "absolute_range",
        "start": "2026-06-29",
        "end_exclusive": "2026-06-30"
      }
    }
  ],
  "immutable_filters": [],
  "research_scope": {
    "dimension_refs": ["DIMENSION:278:246"],
    "driver_metric_refs": ["METRIC:291:250"],
    "hierarchies": [],
    "allowed_filter_refs": ["DIMENSION:278:246"]
  },
  "allowed_actions": [
    "compare",
    "breakdown",
    "drilldown",
    "filter_from_result",
    "contribution",
    "validate_hypothesis",
    "finish"
  ],
  "budget": {
    "max_iterations": 6,
    "max_queries": 8,
    "max_model_calls": 8,
    "max_actions_per_iteration": 3,
    "max_duration_seconds": 300,
    "max_evidence_rows": 20,
    "max_evidence_chars": 12000
  },
  "version_snapshot": {
    "schema_version": 11,
    "contract_version": 2,
    "schema_fingerprint": "...",
    "scope_fingerprint": "..."
  }
}
~~~

`ResearchRequirement` 必须使用判别联合或严格 DTO，禁止无约束字典。它至少包含：

- 用户确认的研究目标和进入 Research 的结构化原因；
- 目标指标、时间角色和不可变业务筛选条件；
- 允许探索的维度、驱动指标、层级和动态筛选维度；
- 允许使用的研究动作；
- 查询、迭代、模型调用、单轮动作数、Token、时间和结果大小预算；
- schema、contract、权限和研究范围版本快照。

## 9.5 ResearchScope 构建

Research Scope 在进入循环前由服务端构建并冻结。它不是把整个数据集资产列表交给模型，
而是根据用户目标、已绑定资产和治理契约形成一个受限候选集合。

范围构建属于第 5 步执行需求分析，不属于 Research 运行时重新检索。只有语义解析已经
输出 `dynamic_research` 时才执行该分支：以目标指标和已绑定时间、筛选为起点，从当前
DatasetSchema 的不可变快照中确定性投影合格维度和驱动指标，并在路由前完成权限与契约
校验。Research 开始后不再调用检索服务扩大范围。

范围构建需要确定：

1. 用户关注的目标指标和指标口径；
2. 当前期、对比期及其时间维度；
3. 可用于分解目标指标的候选维度；
4. 可用于验证假设的驱动指标；
5. 已治理的维度层级和允许的下钻顺序；
6. 允许从结果动态取得筛选值的维度；
7. 跨模型时已经证明安全的关系路径和粒度；
8. 指标可加性、半可加条件、比率组成和其他计算限制。

候选维度和驱动指标必须来自已授权、已治理的资产关系，不能仅因为名称相似进入范围。
候选维度优先来自明确层级、目标模型维度和已证明聚合安全的关联维度；驱动指标只来自指标
组成契约、认证派生关系、管理员维护的分析关系或其他明确治理关系，不能把同模型全部指标
都视为可能原因。候选过多时使用确定性上限和稳定排序截断，并把未纳入范围的资产记录在
审计信息中，不能让模型从全量 Schema 自由筛选。
如果目标指标明确但不存在任何合格分析维度或驱动指标，应明确返回能力不足或请求用户
选择，不能让模型自由猜测字段。

研究循环运行期间 `ResearchScope` 不可扩大。确实需要范围外资产时必须暂停并进入澄清，
得到用户确认后创建新的 Research Run；不得在原 Run 中静默追加资产。

## 9.6 ResearchAction 契约

模型每轮只能返回白名单内的结构化动作，不能直接返回 `QueryTask`、`AnalysisPlan`、SQL
或工具调用。第一版支持以下动作。

### 9.6.1 compare

确认研究前提，例如目标指标是否确实下降、变化幅度和发生时间。目标指标和时间角色必须
来自 `ResearchRequirement`。

### 9.6.2 breakdown

按研究范围中的一个明确维度分解目标指标或变化值：

~~~json
{
  "type": "breakdown",
  "metric_ref": "METRIC:271:246",
  "dimension_ref": "DIMENSION:278:246",
  "calculation": "difference",
  "time_roles": ["current", "previous"]
}
~~~

### 9.6.3 drilldown

沿 `ResearchScope.hierarchies` 中已治理的下一层继续分析。模型不能跳过层级、创建新层级
或自行推断维度关系。

### 9.6.4 filter_from_result

根据已有结果选择分析对象，再生成后续查询条件：

~~~json
{
  "type": "filter_from_result",
  "source_result_id": "result:stall_difference",
  "row_selector": {
    "rank": 1,
    "order_by": {
      "metric_ref": "METRIC:271:246",
      "value_role": "difference"
    },
    "direction": "asc"
  },
  "target_dimension_ref": "DIMENSION:278:246",
  "analysis": {
    "type": "compare",
    "metric_refs": ["METRIC:291:250"],
    "time_roles": ["current", "previous"]
  }
}
~~~

模型不能直接输出从结果中观察到的原始筛选值。服务端必须根据 `source_result_id` 和选择器
从完整结果中确定真实值，并校验结果字段确实来自 `target_dimension_ref`。筛选值随后使用
现有值绑定、权限和查询校验入口生成正式 FilterRequirement。

`analysis` 声明选中对象后要执行的受限分析，第一版只允许 `compare` 或 `breakdown`，其中
资产和时间角色仍必须属于 Research Scope。`row_selector.order_by` 使用逻辑指标引用和
结果值角色，服务端根据结果血缘解析实际列；模型不能填写物理结果列名。

### 9.6.5 contribution

对明确的维度执行确定性贡献度计算，复用 Plan 的 `contribution` 操作及可加性、对账和
除零规则。模型不能自由定义贡献公式。

### 9.6.6 validate_hypothesis

验证一个结构化假设，例如目标变化是否主要来自订单量、客单价或某个已授权维度。假设只
能引用研究范围中的指标、维度和已有结果，不能使用自由计算表达式。

### 9.6.7 finish

结束研究并声明标准原因：`sufficient_evidence`、`premise_not_supported`、
`no_new_direction`、`data_insufficient`、`needs_clarification` 或 `budget_exhausted`。

## 9.7 模型决策输出

Research Policy 每轮只输出一个结构化决策，包含证据评估、假设变化和执行或结束决定：

~~~json
{
  "assessment": {
    "goal_progress": "partial",
    "evidence_summary": "GMV下降主要集中在两个档口",
    "information_gap": "尚未确认下降来自订单量还是客单价"
  },
  "hypothesis_updates": [
    {
      "hypothesis_id": "h1",
      "status": "supported",
      "evidence_ids": ["evidence:stall_breakdown"]
    }
  ],
  "new_hypotheses": [
    {
      "id": "h2",
      "statement": "目标档口下降主要来自订单量下降"
    }
  ],
  "decision": {
    "type": "execute",
    "actions": [
      {
        "type": "filter_from_result",
        "source_result_id": "result:stall_breakdown",
        "row_selector": {
          "rank": 1,
          "order_by": {
            "metric_ref": "METRIC:271:246",
            "value_role": "difference"
          },
          "direction": "asc"
        },
        "target_dimension_ref": "DIMENSION:278:246",
        "analysis": {
          "type": "compare",
          "metric_refs": ["METRIC:291:250"],
          "time_roles": ["current", "previous"]
        }
      }
    ]
  }
}
~~~

假设状态固定为 `pending`、`supported`、`weakened`、`inconclusive` 或 `invalid`。模型不能
创建新的状态值，也不能仅用自然语言覆盖服务端保存的假设状态。

一次调用最多返回 `budget.max_actions_per_iteration` 个相互独立的动作。多个动作没有数据
依赖时，服务端可将它们物化到同一个子计划并按 DAG 批次并行执行；存在结果依赖的动作
必须分轮处理，不能在同一批中引用尚未生成的结果。

首次模型输出结构错误时最多进行一次结构化修复。修复只能处理 DTO、引用或预算错误，不能
修改研究目标、扩大范围或降低语义。第二次仍失败则按已有证据生成部分报告或明确失败，不能
无限重试。

## 9.8 确定性初始步骤

Research 开始时不立即让模型自由选择方向。服务端先根据研究目标生成现象确认子计划：

1. 查询目标指标当前期；
2. 查询目标指标对比期；
3. 计算差值或增长率；
4. 校验用户描述的上涨、下降或异常是否成立；
5. 将结果写入首个 `EvidenceSnapshot`。

如果用户问“为什么 GMV 下降”，但确定性查询显示 GMV 未下降，Research 应以
`premise_not_supported` 结束并报告实际结果，不能继续寻找下降原因。没有对比目标的开放
探索可以使用对应的确定性基线查询，但基线规则仍由服务端生成。

## 9.9 每轮执行和子计划

每一轮遵循固定流程：

~~~text
ResearchState + EvidenceSnapshot
  -> ResearchPolicy 一次结构化决策
  -> ResearchActionValidator
  -> ResearchActionMaterializer
  -> ExecutionRequirement
  -> AnalysisPlanner
  -> 完整计划校验、SQL 编译和 PROVEN 证明
  -> PlanPipeline 执行
  -> ResultStore
  -> EvidenceProjector
  -> 追加 ResearchState
~~~

Research Run 不是一个不断修改的全局 `AnalysisPlan`。每轮生成独立、不可变、已经证明的
子计划。已执行子计划不能被后续模型修改；整个 Run 通过追加式记录关联每轮动作、子计划、
结果和证据。

每个动作必须经过：

- 动作类型和参数 DTO 校验；
- 资产是否属于 Research Scope 的范围校验；
- 来源结果、字段血缘和结果所有权校验；
- 动态筛选值的值绑定和权限校验；
- 动作指纹去重和预算预留；
- `ExecutionRequirement` 完整性校验；
- `AnalysisPlan` 结构、依赖、计算和结果契约校验；
- 所有 QueryTask 的语义编译、权限、SQL 安全和 PROVEN 校验。

任一子计划未达到 `PROVEN` 不能执行。Research 不能以“探索”为理由降低 Plan 的证明
标准，也不能让模型直接修复 SQL 或切换资产。

## 9.10 EvidenceSnapshot

Research Policy 只能读取受控证据摘要，不能读取全部结果行。推荐结构如下：

~~~json
{
  "evidence_id": "evidence:stall_difference",
  "result_id": "result:stall_difference",
  "purpose": "按档口分析GMV变化",
  "metric_refs": ["METRIC:271:246"],
  "dimension_refs": ["DIMENSION:278:246"],
  "time_roles": ["current", "previous"],
  "logical_columns": [
    {
      "dimension_ref": "DIMENSION:278:246",
      "value_role": "group_key"
    },
    {
      "metric_ref": "METRIC:271:246",
      "value_role": "difference"
    }
  ],
  "statistics": {
    "row_count": 36,
    "positive_count": 12,
    "negative_count": 24
  },
  "top_rows": [],
  "bottom_rows": [],
  "lineage": {
    "plan_id": "plan:research:2",
    "task_id": "c:difference"
  }
}
~~~

Evidence 由服务端根据结果 schema 和动作目的投影，采样和统计规则必须确定且可回放。完整
数据继续保存在 `ResultStore`，仅用于确定性计算、动态值选择、结果校验和前端展示。

模型生成的假设文本不是证据。只有来自成功 QueryTask 或 ComputeTask、通过结果校验且具有
完整血缘的 EvidenceSnapshot 才能支持最终发现。

## 9.11 ResearchState 与恢复

Research 使用显式、可持久化、追加式状态，不能依赖模型上下文记忆：

~~~json
{
  "goal": "分析GMV下降的主要影响因素",
  "iteration": 3,
  "evidence_ids": [
    "evidence:total_difference",
    "evidence:stall_breakdown"
  ],
  "hypotheses": [
    {
      "id": "h1",
      "statement": "GMV下降主要集中在目标档口",
      "status": "supported",
      "evidence_ids": ["evidence:stall_breakdown"]
    }
  ],
  "executed_action_fingerprints": [],
  "remaining_budget": {
    "iterations": 3,
    "queries": 4,
    "model_calls": 4
  }
}
~~~

运行状态为：

~~~text
INITIALIZING -> RUNNING -> CONCLUDING -> SUCCEEDED
~~~

其他终态包括：

- `PARTIAL`：已有有效证据，但预算耗尽或部分动作失败；
- `NEEDS_CLARIFICATION`：继续研究需要用户选择或范围外资产；
- `FAILED`：未获得任何可用证据；
- `CANCELLED`：用户取消；
- `BUDGET_EXHAUSTED`：达到硬预算且没有满足正常结束条件。

每轮持久化模型看到的 Evidence ID、结构化决策、校验结果、动作指纹、ExecutionRequirement、
子计划 ID、结果 ID、假设变化和继续或结束原因。恢复时从最后一个完整提交的轮次继续；已经
成功的动作不能重复执行。

## 9.12 预算、去重和停止条件

Research 预算由服务端配置并在每次动作物化前预留。模型调用预算覆盖 Research Policy、
结构修复和最终报告生成；Controller 必须为最终报告预留一次调用，不能在动作循环中耗尽
全部额度。第一版建议默认：

- 最多 6 轮；
- 最多 8 个查询；
- 最多 8 次 Research Policy 调用，结构修复计入模型调用；
- 单轮最多 3 个独立动作；
- 最长运行 300 秒；
- 结果摘要、采样行和模型 Token 分别设置硬上限。

满足以下任一条件时，服务端停止循环：

1. 模型返回合法 `finish`；
2. 达到迭代、查询、模型调用、Token、结果大小或时间预算；
3. 动作指纹已经执行，或连续动作没有产生新证据；
4. 所有允许维度、层级和候选假设均已覆盖；
5. 研究前提不成立；
6. 主要结论已有足够证据，继续查询不会改变结论等级；
7. 后续动作需要范围外或未授权资产；
8. 主要子计划连续失败；
9. 用户取消或 Run 截止时间到达。

动作指纹由动作类型、资产引用、时间角色、不可变筛选、来源结果和选择器规范化生成，不能
只依赖模型提供的动作 ID。模型不能修改预算，也不能通过更换 ID 重复同一动作。

`sufficient_evidence` 不能只由模型自行宣布。服务端至少校验：研究前提已有有效 Evidence、
每项主要发现都有 ResultSet 引用、至少一个核心假设已得到支持或削弱、报告不存在引用未知
结果的结论。若 ResearchRequirement 声明了必查维度、必验假设或最低覆盖数，还必须完成
这些要求；否则将 `finish` 判为不满足条件，并在剩余预算内要求模型选择下一步动作。

## 9.13 失败与部分结果

单个动作失败时：

- 同批其他独立动作可以完成；
- 依赖失败动作的后续分析不能执行；
- 不允许模型通过替换指标、维度或降低口径规避失败；
- 可恢复的结构错误按本章规定最多修复一次；
- 查询瞬时错误使用统一查询重试策略，不额外消耗研究决策轮次；
- 已获得有效证据时优先生成 `PARTIAL` 报告，明确哪些方向未完成；
- 没有有效证据时返回 `FAILED`，并保留结构化失败原因。

Research 不得静默降级为 Plan 或 Fast。某轮动作本身通过 Plan 执行，不代表整个 Research
Run 变成 Plan。

## 9.14 最终报告与引用

Research 最终输出结构化研究报告，至少包含：

1. 结论摘要；
2. 已确认的研究前提；
3. 主要发现及每项发现的 Evidence 和 ResultSet 引用；
4. 已支持、已削弱和无法判断的假设；
5. 尚未验证的可能方向；
6. 数据、口径、时间范围和方法限制；
7. 后续可以继续的分析方向。

所有数字必须通过结果引用校验，不能只引用模型自己的证据摘要。最终报告只允许根据
ResearchState 和 EvidenceSnapshot 生成，不能重新查询或补充未执行的结论。

第一版必须区分以下表述等级：

- **贡献**：某分组变化占总变化的确定性分解；
- **共同变化**：两个指标在相同范围内同时变化；
- **相关线索**：现有结果支持继续验证，但不能确认因果；
- **严格因果**：第一版不支持，不能输出因果确认结论。

## 9.15 组件职责

推荐的逻辑组件为：

~~~text
ResearchPipeline
  -> ResearchController
  -> ResearchPolicy
  -> ResearchActionValidator
  -> ResearchActionMaterializer
  -> PlanPipeline
  -> ResultStore
  -> EvidenceProjector
  -> ResearchReportComposer
~~~

实现时不要求为每一项创建独立单方法 Service。业务主流程应在 `ResearchPipeline` 或
`ResearchController` 中保持可读。只有模型端口、状态持久化、动作校验/物化的真实复用和
报告生成等稳定边界才单独抽取。

职责约束如下：

- `ResearchController`：循环、预算、状态、去重、停止和恢复；
- `ResearchPolicy`：一次结构化模型决策，不调用工具；
- `ResearchActionValidator`：动作、范围、来源结果、权限和预算校验；
- `ResearchActionMaterializer`：把动作转换为完整 ExecutionRequirement；
- `PlanPipeline`：证明并执行本轮不可变子计划；
- `EvidenceProjector`：从 ResultStore 生成可回放的受控证据摘要；
- `ResearchReportComposer`：生成并校验带引用的结构化报告。

## 9.16 第一版能力范围

第一版支持：

- 开放式指标上涨、下降原因探索；
- 找出异常对象，再针对该对象继续分析；
- 在预先绑定的候选维度中选择主要贡献维度；
- 使用已治理驱动指标验证指标分解假设；
- 在固定维度层级内动态下钻；
- 预算耗尽或部分失败时生成带引用的部分报告。

第一版不支持：

- 任意跨数据集探索；
- 未治理字段自动发现；
- 任意 SQL、Python 或自由表达式；
- 外部互联网研究；
- 预测、模拟和 what-if；
- 严格因果推断；
- 自动写回业务系统；
- 多 Agent 自由协作。

## 9.17 分阶段实施

### 第一阶段：契约和路由边界

1. 增加 `ResearchRequirement`、`ResearchScope` 和预算契约；
2. 增加 `ResearchAction`、模型决策、Hypothesis、EvidenceSnapshot 和 ResearchState DTO；
3. 将 `dynamic_research` 从简单 route 元数据扩展为完整研究执行需求；
4. 补齐 Fast、Plan、Research 正反边界题集；
5. Research 尚未执行时继续明确返回模式不可用，不做兼容或静默回退。

实施状态（2026-08-20）：已完成。当前实现如下：

1. 新增严格 `ResearchRequirement`、`ResearchScope`、`ResearchBudget`、时间绑定、不可变
   筛选和版本指纹契约；`ExecutionRequirement` 在 Research 模式下必须且只能携带
   `research_requirement`，不能同时携带查询、计算、结果契约或有限多步分解；
2. 新增 `ResearchAction` 判别联合、Research Policy 单轮决策、Hypothesis、
   EvidenceSnapshot、ResearchState 及终止原因契约。动作不允许携带 SQL、物理字段或
   原始结果筛选值；
3. Research Scope 由目标指标、用户已绑定维度和 `metric_dimension_capabilities` 确定性
   构建。能力契约必须允许 GROUP_BY 或 FILTER，且聚合安全性只能为 `SAFE` 或
   `PRE_AGGREGATE_REQUIRED`；派生指标的显式 `metric_refs` 是第一阶段唯一驱动指标来源；
4. 范围按稳定顺序限制为最多 20 个维度和 10 个驱动指标，未进入范围的同模型资产记录为
   `excluded_asset_refs`。Schema 指纹缺失、目标跨模型、时间未归一化或研究范围为空时明确
   失败；不会把同模型全部指标作为可能原因；
5. Research 预算已进入 AgentConfig 和 ModeRouteInput，但默认 execution_modes 仍只有
   Fast、Plan。显式启用 Research 后只完成需求构建，RunOrchestrator 继续返回
   `RESEARCH_MODE_NOT_READY`，不进入旧链路或其他模式；
6. 真实问题“先找出下降最大的档口，再根据该档口继续分析原因”已使用 tenant `1`、
   dataset `243`、datasource `13` 完成模型和 Schema 验证。输出包含目标 GMV、当前期与
   对比期、4 个治理维度、动作白名单、预算及 schema/scope 双指纹，未执行 Research 循环。

第一阶段不推断尚未治理的维度层级，因此 `hierarchies` 当前为空；层级来源和动态下钻在
第三阶段接入治理契约后启用，不能根据维度名称自行推断。

### 第二阶段：最小动态闭环

1. 实现确定性现象确认；
2. 支持 `breakdown`、`filter_from_result` 和 `finish`；
3. 每个动作物化为独立 ExecutionRequirement 和 PROVEN AnalysisPlan；
4. 实现查询预算、动作指纹去重和基本停止条件；
5. 验证“找出下降最大对象，再分析该对象变化”的真实流程。

实施状态（2026-08-20）：已完成代码实现，当前能力如下：

1. 新增 `ResearchPolicy` 单轮结构化模型决策。模型输入严格限制为清理后的逻辑资产目录、
   `ResearchRequirement`、`ResearchState`、`EvidenceSnapshot` 和剩余预算；不包含 SQL、
   物理字段、完整结果集或检索工具；
2. Research 启动后先由规则生成现象确认动作。存在 current/previous 时固定生成两期查询和
   difference 子计划；没有对比期的开放探索生成单期 baseline 子计划，不为得到计划而调用
   模型；
3. 第二阶段向 Policy 开放 `breakdown`、`filter_from_result` 和 `finish`。`compare` 仅由
   服务端用于现象确认。contribution、drilldown 和 validate_hypothesis 继续保留在契约中，
   但不进入第二阶段模型动作集合；
4. `filter_from_result` 只能引用已有 `result_id`，并使用逻辑指标、值角色、排名和方向选择
   结果行。服务端根据 Evidence 中记录的排序口径取得真实维度值，再构造筛选条件；模型不能
   回传观察到的原始值或物理结果列；
5. 每个动作物化为独立 `ExecutionRequirement`。Research 来源的单期 baseline 通过
   `ExecutionRoute.origin=research_action` 明确标识；除此之外普通 Plan 仍禁止无计算的单查询
   形态；
6. `PlanPipeline.execute_requirement` 已成为 Plan 和 Research 共用的生成、校验、SQL 编译、
   整体 PROVEN 证明、DAG 执行和 ResultStore 入口。Research 不调用 Plan 的回答生成逻辑；
7. 查询结果通过固定逻辑列映射、固定排序、行数和字符预算投影为 `EvidenceSnapshot`。证据
   记录 action fingerprint、plan/task 血缘、排序口径、正负/空值计数和截断状态；
8. Research Controller 已实现模型调用前扣减、查询执行前预算预留、动作指纹去重、最大
   轮数、查询数、模型调用数、总耗时、无新证据和模型 finish 等停止条件，并把 State 和
   Evidence 追加到运行派生状态；
9. `RunOrchestrator` 和组合根已装配 ResearchPipeline。默认 execution_modes 仍为 Fast、
   Plan；显式配置 Research 后进入新循环，不再返回 `RESEARCH_MODE_NOT_READY`，也不回退到
   Fast、Plan 或旧链路；
10. 第二阶段最终回答只输出模型最后一次证据摘要、标准终止原因和 Evidence 引用。完整发现、
    结论置信度、假设管理和引用报告仍属于后续阶段。

第二阶段测试覆盖 current/previous 现象确认、单期 baseline、breakdown、Evidence 排序、
动态值绑定、非来源排序拒绝、动作指纹稳定性、Research/Plan 路由边界以及架构依赖守卫。
真实问题“先找出 2026 年 6 月 30 日比 6 月 29 日总 GMV 下降最大的档口，再根据该档口
继续分析下降原因”已在 tenant `1`、dataset `243`、datasource `13` 完成验证：现象确认、
档口 breakdown、从 Evidence 选择下降最大档口和后续维度 breakdown 三个子计划均达到
`PROVEN` 并成功执行。脚本继续逐段输出 ResearchRequirement、子计划、查询/计算结果和
EvidenceSnapshot。

### 第三阶段：开放原因探索

实施状态：方案已明确，代码尚未开始。

第三阶段的目标是在第二阶段最小动态闭环上增加受治理的开放原因探索。它不改变
Research 的外层范式：每轮仍由一次 `ResearchPolicy` 调用完成证据评估、假设变化和下一步
动作选择；每个动作仍必须物化为完整 `ExecutionRequirement`，进入同一套 AnalysisPlan
校验、SQL 编译、整体 `PROVEN` 证明、DAG 执行和 ResultStore。第三阶段不引入多 Agent，
也不允许模型直接生成 SQL、物理字段、原始筛选值、自由公式或工具调用。

第三阶段主要增加以下能力：

1. 接入治理后的维度层级和指标驱动关系，扩展 Research Scope；
2. 支持 `drilldown`、`contribution` 和 `validate_hypothesis`；
3. 支持候选假设的创建、验证和状态更新；
4. 支持同轮多个独立动作的受控并行执行；
5. 生成所有发现均有 Evidence 引用的结构化研究报告；
6. 建立开放原因分析专项真实问题集和评测标准。

#### 第三阶段前置条件：语义治理契约

第三阶段不能仅根据资产名称判断层级、驱动关系或因果方向。开始实现动作前，Semantic 的
公开 `DatasetSchema` 必须提供或接入以下治理信息：

1. 维度层级及严格顺序，例如区域 -> 城市 -> 门店；
2. 目标指标与驱动指标的明确关系来源，例如公式组成、认证驱动关系或管理员维护的分析关系；
3. 目标指标和驱动指标允许共同分析的维度、粒度和时间角色；
4. 指标可加性、半可加限制和允许执行贡献度分析的维度；
5. 必要时提供已经证明安全的跨模型关系路径；
6. 关系的治理状态、版本和指纹。

推荐公共契约如下：

~~~json
{
  "dimension_hierarchies": [
    {
      "id": "region_city_stall",
      "dimension_refs": [
        "DIMENSION:REGION",
        "DIMENSION:CITY",
        "DIMENSION:STALL"
      ],
      "status": "CERTIFIED"
    }
  ],
  "research_relationships": [
    {
      "target_metric_ref": "METRIC:GMV",
      "driver_metric_ref": "METRIC:ORDER_COUNT",
      "relationship_type": "certified_driver",
      "supported_dimension_refs": ["DIMENSION:STALL"],
      "supported_time_roles": ["current", "previous"],
      "relationship_fingerprint": "..."
    }
  ]
}
~~~

不存在治理契约时必须明确返回能力不足，不能因为字段分别叫“区域”和“城市”就自动组成
层级，也不能把同模型全部指标视为目标指标的可能原因。

#### Research Scope 扩展

`ResearchScope` 需要在进入循环前冻结以下内容：

- 可用于分解目标指标的维度；
- 允许从结果动态取得筛选值的维度；
- 已认证的维度层级及下钻顺序；
- 允许验证的驱动指标；
- 目标指标和驱动指标支持的共同维度、粒度和时间角色；
- 允许贡献度计算的指标与维度组合；
- 每个关系的来源、版本和指纹；
- 被排除资产和关系的明确原因。

建议新增严格关系契约：

~~~python
class ResearchDriverRelationship(BaseModel):
    target_metric_ref: str
    driver_metric_ref: str
    relationship_type: Literal[
        "formula_component",
        "certified_driver",
        "governed_analysis_relation",
    ]
    dimension_refs: tuple[str, ...]
    time_roles: tuple[str, ...]
    relationship_fingerprint: str
~~~

Scope 在 Research Run 内仍然不可扩大。需要范围外资产时必须结束或暂停当前 Run 并请求用户
确认，不能在运行中重新检索后静默加入。

#### drilldown

`drilldown` 只允许沿已治理层级向相邻的下一层执行。服务端必须校验：

1. `hierarchy_id` 属于当前 Scope；
2. `current_dimension_ref` 和 `next_dimension_ref` 在同一层级中相邻；
3. `source_result_id` 属于当前 Research Run；
4. 来源 Evidence 包含当前层级维度及有效逻辑排序口径；
5. 下钻对象仍然通过行选择器取得，模型不能填写原始维度值；
6. 下钻查询继承全部不可变筛选和已确认的动态筛选；
7. 禁止跳级、反向下钻或切换到未治理层级。

执行流程如下：

~~~text
ResearchDrilldownAction
  -> 层级、来源 Evidence 和行选择器校验
  -> 服务端取得上级对象真实值
  -> 生成下一层 ExecutionRequirement
  -> PROVEN AnalysisPlan
  -> 执行并生成新 EvidenceSnapshot
~~~

#### contribution

`contribution` 复用 Plan 已有的确定性 contribution 计算，不增加新的计算执行器。物化前
必须校验：

1. 目标指标的可加性为 `FULL`；
2. 存在 current 和 previous 两个已归一化时间角色；
3. 维度属于 Scope 中允许贡献度分析的维度；
4. 维度差值合计可以与总差值对账；
5. 总差值为零时按现有除零规则返回空贡献度，不能生成误导性比例；
6. 对账误差必须小于治理契约声明的容差。

动作物化形成固定 DAG：

~~~text
当前期按维度查询 + 对比期按维度查询 -> 维度差值
当前期总值查询   + 对比期总值查询   -> 总差值
维度差值 + 总差值 -> contribution
~~~

全部 QueryTask 可以按现有 Plan DAG 批次并行，但所有查询仍需先完成整个子计划的 PROVEN
证明。

#### validate_hypothesis

第三阶段允许 Policy 创建候选假设，例如“GMV 下降主要来自订单量下降”，但假设文本本身
不是证据。`validate_hypothesis` 必须校验：

1. `hypothesis_id` 已存在并处于可验证状态；
2. 指标属于目标指标或 Scope 中已治理的驱动指标；
3. 目标指标和驱动指标之间存在明确的 Research Relationship；
4. 指标在请求的维度、粒度和时间角色下可以比较；
5. 引用的 Evidence 属于当前 Run；
6. 动作不包含自由公式、相关性算法或模型自行填写的结果值。

第一版验证方式限制为服务端已经实现的确定性结构：

- 目标指标和驱动指标是否同方向变化；
- 变化是否集中在相同对象；
- 驱动指标是否覆盖目标指标的主要异常对象；
- 对公式组成关系执行确定性组成验证。

第三阶段只能陈述“证据支持该驱动方向”或“证据不足”，不能把同时变化直接表述为因果关系。

#### 假设状态管理

第二阶段禁止创建和更新假设；第三阶段需要增加统一的假设状态归并规则。推荐使用纯函数：

~~~python
apply_hypothesis_updates(
    current_hypotheses,
    policy_decision,
    available_evidence,
) -> tuple[ResearchHypothesis, ...]
~~~

统一校验以下不变量：

1. 新假设 ID 不重复，且初始状态只能是 `pending`；
2. 更新的假设必须已经存在；
3. `supported`、`weakened` 和 `inconclusive` 必须引用当前 Run 的新 Evidence；
4. 模型自然语言不能代替 Evidence；
5. 已结束假设不能重新变回 `pending`；
6. 同一轮不能同时创建和更新相同 ID；
7. 资产或治理关系无效时由服务端标记 `invalid`，不能让模型降低校验标准。

假设状态机必须在一个地方表达，不能分别散落在 Policy、ResearchPipeline 和报告生成逻辑中。

#### Research Policy 扩展

第三阶段 Policy 输入在第二阶段基础上增加：

- 治理后的维度层级；
- 目标指标与驱动指标关系；
- 当前假设和状态；
- 每个 Evidence 覆盖的指标、维度和假设；
- 已执行动作摘要；
- 尚未覆盖的治理方向。

Policy 可选择的动作扩展为：

~~~text
breakdown
filter_from_result
drilldown
contribution
validate_hypothesis
finish
~~~

`compare` 继续由服务端用于确定性现象确认。一次 Policy 调用仍然同时返回证据评估、假设变化
和下一步动作；不拆分为规划 Agent、批判 Agent 或假设 Agent。

#### 同轮多个独立动作并行

Policy 一轮可以返回多个动作，但服务端必须确定性判断依赖关系：

- 动作只读取本轮开始前已经存在的 result_id，彼此没有结果依赖时，可以进入同一批并行；
- 动作引用另一个动作尚未生成的结果时，必须拆到下一轮；
- `filter_from_result` 和 `drilldown` 只能引用已有 Evidence，不能引用同轮未来结果；
- 并行前一次性预留全部查询预算，预算不足时整批不执行；
- 每个动作分别生成不可变 ExecutionRequirement、Plan、Result 和 Evidence；
- 一个动作失败不能被静默忽略，失败归属和是否允许保留其他成功证据必须写入轮次记录。

建议增加确定性动作批次函数：

~~~python
build_research_action_batches(
    actions,
    existing_result_ids,
) -> tuple[tuple[ResearchAction, ...], ...]
~~~

该函数只根据显式 result_id 依赖和服务端并发上限构建批次，不调用模型重新规划拓扑。

#### EvidenceSnapshot 扩展

第三阶段 Evidence 需要继续使用逻辑列，并增加以下表达能力：

- contribution 值、总差值和对账结果；
- 驱动指标的 value、difference 和 growth_rate；
- Evidence 支持验证的 hypothesis IDs；
- 数据覆盖范围和截断状态；
- 零分母、对账失败和不可比较等确定性限制；
- 并行动作的批次 ID；
- Evidence 对假设的支持、削弱或无法判断结果。

完整结果仍只保存在 ResultStore。Policy 只能读取受行数和字符预算控制的 EvidenceSnapshot。

#### ResearchState 扩展

第三阶段需要在现有 Evidence IDs、Hypotheses、动作指纹和剩余预算之外，追加以下审计记录：

- 已执行动作的结构化摘要；
- action -> plan -> result -> evidence 映射；
- 每轮 Policy 决策摘要及指纹；
- 假设状态变化；
- 已覆盖维度和驱动指标；
- 连续无新方向次数；
- 并行动作的成功、失败和预算消耗。

推荐增加追加式轮次记录：

~~~python
class ResearchIterationRecord(BaseModel):
    iteration: int
    policy_decision_fingerprint: str
    action_fingerprints: tuple[str, ...]
    plan_ids: tuple[str, ...]
    result_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    failed_actions: tuple[ResearchActionFailure, ...]
~~~

第三阶段只要求状态完整、可审计；断线恢复、提交点和重放属于第四阶段。

#### 结构化研究报告

第三阶段结束时生成严格 `ResearchReport`，至少包含：

- 研究目标和现象确认结果；
- 主要发现及 Evidence 引用；
- 已支持、被削弱、无法确认和未验证的假设；
- 数据范围和限制；
- 标准结束原因。

每条发现必须引用 Evidence：

~~~json
{
  "statement": "GMV 下降主要集中在 A 档口",
  "evidence_ids": ["evidence:2"],
  "confidence": "high"
}
~~~

报告可以使用一次受控模型调用生成结构化草案，但它是普通 `ResearchReportComposer`，不是
独立 Report Agent。该调用计入模型预算，且服务端必须完成引用校验：

1. 引用的 Evidence 存在并属于当前 Run；
2. 发现涉及的指标、维度和数值出现在所引 Evidence 中；
3. `pending` 或 `inconclusive` 假设不能写成确定结论；
4. 报告不能新增查询、Evidence、指标、维度或模型自行生成的数字；
5. 引用校验失败时明确失败或生成受限部分报告，不能删除引用后继续输出结论。

#### 代码职责调整

第三阶段保留 `ResearchPipeline` 负责循环、预算、Policy 调用、动作批次、执行、停止和报告
收口。避免继续把所有业务不变量写入主循环，新增或扩展以下少量稳定模块：

- `requirements.py`：治理关系进入 Scope 的确定性投影；
- `actions.py`：drilldown、contribution、validate_hypothesis 校验和物化；
- `evidence.py`：新增结果角色和假设证据投影；
- `hypotheses.py`：假设状态归并纯函数；
- `action_batches.py`：同轮独立动作批次；
- `report.py`：结构化报告生成和引用校验；
- `policy.py` / `policy_rules.py`：开放新动作和假设决策。

不新增大量只调用一次的私有方法，也不增加 Manager、Planner Agent、SQL Agent、Critic Agent
或 Report Agent。

#### 第三阶段真实问题集

至少覆盖以下问题：

1. 层级下钻：找出 GMV 下降最大的区域，再下钻到城市和档口；
2. 贡献度：分析各档口对总 GMV 下降的贡献；
3. 驱动验证：GMV 下降主要是订单量下降还是客单价下降；
4. 多方向并行：同时从区域、档口和商品类别分析 GMV 下降；
5. 证据不足：验证 GMV 是否由订单量下降导致，但不存在可比较的订单量指标；
6. 防越界：模型选择 Scope 外资产、跳级下钻或引用其他 Run 的 Evidence；
7. 报告约束：模型试图把 pending 假设、未查询数字或无引用文本写成结论。

验收时必须证明：

- 所有子计划执行前均达到 `PROVEN`；
- 下钻严格遵循治理层级；
- 贡献度通过总差值对账；
- 假设状态变化都有当前 Run 的 Evidence；
- 独立动作并行，存在结果依赖的动作分轮；
- Scope 在运行中不扩大；
- 报告每条发现都有有效 Evidence 引用；
- 模型无法提交 SQL、物理字段、原始筛选值、自由公式或未治理关系。

#### 推荐实施顺序

1. 先在 Semantic 公共 Schema 中补齐维度层级和指标驱动关系；
2. 扩展 ResearchScope 和 Scope 构建测试；
3. 实现 drilldown 和 contribution；
4. 实现假设状态归并和 validate_hypothesis；
5. 实现同轮独立动作批次；
6. 实现结构化报告和引用校验；
7. 使用真实问题集完成端到端执行、边界和报告评测。

### 第四阶段：可靠性和恢复

1. 持久化 ResearchState 和每轮提交点；
2. 支持断线恢复、取消、截止时间和部分结果；
3. 支持预算耗尽报告、Trace 回放和成本统计；
4. 增加真实问题跑批、结果引用判分、动作重复率和研究停止质量评测；
5. 评测通过后再按数据集配置默认启用 Research。

# 10. 查询编译与执行

待设计。

# 11. 多结果计算

待设计。

# 12. 结果校验与答案生成

待设计。

# 13. 澄清、失败和状态恢复

待设计。
