# 后端架构规则（v2）

> 版本：v2（2026-07-19，R0 批次修订；v1 全文存档于 `backend/DDD_MIGRATION_CHANGELOG.md` 冻结部分）
> 适用范围：`backend/apps/` 及 `backend/platform/`（未来）全部业务代码
> 定位：本项目是**边界清晰的模块化单体**。规则分三层：全局边界规则（§1，人人适用）→ 子域风格分级（§2，规定每个模块允许的仪式上限）→ 具体结构与横切规则（§3–§8，按所属风格适用）。

## 1. 全局边界规则（适用所有模块，架构测试守卫）

1. **数据所有权唯一**：一类业务数据只有一个模块可写；禁止多事实源、禁止双写。
2. **跨域只走公开契约**：跨模块调用**直接使用对方公开 Service 与公开 DTO**（从对方包顶层 `__init__` 或 composition 导入）；禁止导入对方 ORM（`models/orm`）、具体仓储实现、内部模块；禁止跨模块共享数据库 Session 或事务。
3. **依赖方向单向**：模块依赖图无环；禁止用函数内导入、延迟导入掩盖循环依赖。出现循环时重新确认职责归属或抽取稳定数据契约。
4. **基线棘轮**：历史违规登记于 `tests/architecture/known_dependency_violations.json`，只减不增；清理历史违规的变更必须同批删除对应基线条目。

## 2. 子域风格分级

不同子域的复杂度性质不同，不套同一模板。下表规定**仪式上限**——允许更简单，不允许更复杂：

| 风格 | 适用模块 | 上限 |
| --- | --- | --- |
| A 完整战术分层 | `semantic`、`access_control` | api / services / repository(接口+实现) / models(orm+dto) / errors / utils 全套；仓储接口 + Fake 测试 |
| B 技术边界（六边形） | `datasource` | 公开 Service + 驱动/连接适配器；不追求领域建模 |
| C 管道 | `retrieval`；`chatbi` 的管道核心 | 按数据流分段组织（来源/投影/索引/查询，或理解/规划/生成/执行）；阶段默认纯函数；不强制仓储抽象 |
| D 带校验 CRUD | `knowledge`、`ai_model`、`assistant`、`dashboard` | 薄 Service + 仓储封顶；禁止继续加层 |
| E 通用平台 | `workflow_engine` | 自有分层；禁止导入任何业务模块 |
| 应用编排 | `chatbi`（整体定位） | 薄 Service（仅限有端口/状态/事务者）+ 函数管道 + `orchestration/`（Agent/Graph）+ 唯一对外问数 `api/` |

判断依据：一个模块配得上风格 A，当且仅当它拥有密集的业务不变量和真正的业务通用语言；当模块词汇表以 Projection / Adapter / Pipeline 等技术词为主时，它是管道或适配层，按 C 组织。有疑问时选更低仪式的风格。

## 3. 目录与分层（风格 A 全量适用；B–D 取所需子集）

```text
apps/<module>/
├── api/                   # 有接口入口时建立
├── services/              # 业务流程与规则；可按子域分包（单目录 .py 文件数软上限 12）
│   └── <subdomain>/
│       └── ports.py       # 本子域端口（见 §5）
├── orchestration/         # 仅当同一模块存在多种执行方式（如 Agent/Graph）
├── repository/            # 仓储接口 + <technology>/ 实现
├── adapters/              # 非持久化技术实现，按技术分组（langchain/ prompts/ …）
├── models/
│   ├── orm/               # 持久化对象，按业务资源拆分文件；不得被其他模块导入
│   └── dto/               # 数据契约；可镜像 services 分包
├── utils/                 # 无状态、无副作用且真实复用的辅助
├── errors.py              # 模块错误基类 + 错误码常量注册（见 §7）
├── composition.py         # 唯一组合根（见 §6）
└── __init__.py            # 模块公共面：只导出允许跨模块使用的 Service/DTO/错误
```

各层职责沿用 v1 核心约束：

- **api**：接口定义、身份上下文、依赖组装调用、领域错误→接口错误映射；不承载业务规则、查询与事务；不把框架对象传入 Service。
- **services**：业务流程、校验、权限判断与调用顺序；不直接使用 Session、驱动客户端、SQL；不处理接口状态码。主流程保持可读，**不为拆分而拆分**。
- **repository**：接口面向 Service 定义最小数据需求；实现负责查询、写入、关系同步与单次持久化原子性；不得依赖 Service/API；禁止静默吞错和默认数据兜底。
- **models/orm**：只描述持久化结构；不含业务流程；不用 `Entity` 命名。
- **models/dto**：不映射表、不持连接、不执行持久化；同一契约只定义一次。
- **utils**：仅无状态纯辅助；单流程私有代码不入 utils。

## 4. Service 准入判据（类还是函数）

一个类可以叫 `XxxService`，当且仅当满足至少一条：

1. 依赖至少一个端口/仓储（需要注入与替换）；
2. 承载跨调用状态或事务边界；
3. 表达一个有多个入口方法的内聚业务概念。

否则写**模块级纯函数**（`project_xxx()` / `resolve_xxx()` / `validate_xxx()`）。投影、映射、规则判断默认是函数；函数同样要求单元测试与严格类型。禁止无状态单方法类。

## 5. 端口与适配器

**先问要不要端口，再问叫什么。** 跨模块调用默认直连对方公开 Service（§1.2）。允许建立端口（`Protocol`）的仅三种情形：

1. **可替换技术缝**：LLM/Embedding 客户端、数据库执行等存在多实现或必须在测试中替换的技术设施；
2. **防腐**：对方契约与本域模型存在真实转换（不是字段改名）且对方契约不稳定；
3. **本模块持久化**（仓储接口，仅限风格 A/D）。

1:1 转发对方公开 Service 的包装端口禁止存在。端口统一定义在所属子域的 `ports.py`，不得内联在 service 文件中。后缀只允许 4 种：

| 后缀 | 语义 | 实现位置 |
| --- | --- | --- |
| `*Repository` | 本模块持久化端口 | `repository/<technology>/` |
| `*Gateway` | 需防腐转换的跨模块端口 | `adapters/` 或 composition 内适配 |
| `*Client` | 外部技术设施端口 | `adapters/<technology>/` |
| `*PromptBuilder` | 提示词装配端口（与 `*Client` 成对用于 LLM 能力） | `adapters/prompts/` |

废止 `Provider` / `Applier` / `Reader` / `Ranker` / `View` 等后缀。

## 6. 组合根

- 每模块**唯一** `composition.py`；包根不得出现独立 builder 模块。
- `build_*` 工厂只允许出现在 `composition.py` 与 `adapters/` 内。
- 跨模块的完整链路组装（如完整问数）只发生在 ChatBI 的 composition 与 `chatbi/api/`；`apps/api.py` 只注册路由。

## 7. 错误

- 每模块一个 `errors.py`：模块基类（如 `ChatBIError`）+ 按子域分节的错误类型 + 错误码字符串常量（或 `StrEnum`）注册。
- 领域错误不绑定 HTTP 状态码；API 层只 import `errors.py` 做映射。
- service 代码中不得出现裸错误码字符串；未知异常直接失败，禁止宽泛捕获转成功或空数据。

## 8. 其他横切规则

### 8.1 DTO 后缀语义

| 后缀 | 语义 |
| --- | --- |
| `*Input` | 用例入参 |
| `*Result` | 用例出参 |
| `*Ref` | 跨边界最小引用（仅 ID/版本类字段） |
| `*Snapshot` | 某时点完整只读投影 |
| 裸名 | 模块内稳定值对象 |

禁止 `*Data` 同时用作输入与载荷。模块公共契约集中在模块 `__init__.py` 导出（软上限 40 符号）；禁止 services/dto 的大桶 `__init__` 无差别再导出（迁移期兼容 re-export 须登记台账）。

### 8.2 提示词归属

提示词**规则**（业务不变量，如必须包含的约束、字段白名单）放 services 层模块；提示词**模板与渲染**（文件模板、消息拼装、LangChain 转换）放 `adapters/prompts/`。不得出现第三个存放位置。

### 8.3 命名

沿用 v1：目录/文件 `snake_case`、类 `PascalCase`；名称表达业务职责；`Builder`/`Matcher`/`Mapper` 语义限定；禁止 `Manager`/`Helper`/`Common` 等模糊名；禁止单个 `service.py`/`models.py` 承载整个模块；历史品牌名与废弃方案名不得进入新命名。补充：`Legacy*` 前缀仅用于**确定随旧流程删除**的代码；长期能力不得用 Legacy 包装。

### 8.4 兼容入口

只做参数转换与转发；不承载独立业务逻辑；**新增兼容导出/别名/转发必须同批登记 `backend/COMPAT_LEDGER.md`**（路径/调用方/删除条件/目标阶段），台账外禁止新增。

## 9. 测试与守卫

- Service 测试用 Fake 仓储/端口，不建真实 Session；Repository 测试覆盖租户隔离、事务、关系同步、级联；API 测试覆盖契约、身份与错误映射；纯规则用单测覆盖业务不变量；跨模块只测公开契约。
- 架构守卫只有两个入口：`tests/architecture/test_dependency_baseline.py`（依赖基线棘轮）与 `tests/architecture/test_structure_rules.py`（表驱动结构规则）。**新增规则 = 在规则表加一行数据；禁止按批次/按能力新增守卫测试文件。**
- 缺陷测试覆盖产生机制，不只固定单一输入。

## 10. 批次检查清单（合并前自查）

```
[ ] 未新增无状态单方法 Service 类（投影/规则 → 函数）
[ ] 新端口满足准入判据（技术缝/防腐/仓储；跨域默认直连公开 Service），在 ports.py，后缀合规
[ ] 未超出所在子域风格分级仪式上限（§2）
[ ] 新错误在 errors.py，错误码走常量注册
[ ] 新 DTO 后缀合规（§8.1）
[ ] 未在 composition/adapters 之外新增 build_*
[ ] 未新增守卫测试文件
[ ] 兼容导出/别名已登记台账
[ ] changelog 已追加本批记录
```

## 11. 评审原则

- 先确认边界与业务不变量，再评审目录结构；先消除重复能力，再考虑抽取公共能力。
- 以业务主流程可读为优先；新增抽象必须形成稳定边界、减少真实重复或明显降低复杂度，三者皆无则拒绝。
- 名称相似不代表职责相同；抽取共享能力前确认语义与变化原因一致。
- 迁移不能只移动文件，必须同批修正错误依赖与重复职责。
- 对"要不要再拆一层/再加一个端口"的默认答案是**否**，举证责任在提议方。
