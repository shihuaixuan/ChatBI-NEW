# 后端项目结构与代码职责

## 1. 说明

本文只说明 `backend/` 当前有效的后端结构。目录树中的注释描述目录或代码文件的主要职责，不展开虚拟环境、缓存、日志和运行数据。

为保持文档可读，以下内容按下列范围处理：

- 逐文件说明生产代码，包括 `apps/`、`common/`、`interfaces/`、`platform/`、`main.py` 和 `alembic/env.py`。
- `alembic/versions/` 是历史数据库版本链，只说明目录职责，不逐个解释迁移文件。
- `tests/` 和 `scripts/` 分别是验证代码和运维脚本，只说明目录职责。
- 各级 `__init__.py` 用于声明 Python 包或集中导出该层的公开对象；如果还有额外职责，会单独注明。

## 2. 模块内部结构

后端是模块化单体。业务代码按领域放在 `apps/<module>/`，通用工作流能力放在 `platform/`，共享技术能力放在 `common/`。

```text
apps/<module>/
├── __init__.py            # 模块公共入口，只导出允许其他模块使用的对象
├── api/                   # HTTP 接口、参数接收、权限入口和错误映射
├── services/              # 业务流程、业务校验和调用顺序
├── repository/            # 仓储接口以及数据库、外部系统等具体实现
│   ├── sqlmodel/          # SQLModel/PostgreSQL 持久化实现
│   ├── connectors/        # 数据库驱动或其他技术连接器
│   └── external/          # 外部服务适配实现
├── models/
│   ├── dto/               # 模块内及跨模块使用的数据契约
│   ├── orm/               # 本模块拥有的数据库持久化模型
│   └── rules/             # 不依赖数据库的业务规则
├── adapters/              # LLM、提示词、执行器等非持久化技术适配
├── orchestration/         # Agent、Graph 等多步骤执行编排
├── composition.py         # 模块唯一依赖组装入口
└── errors.py              # 模块错误类型和错误码
```

并非所有模块都需要完整目录。简单 CRUD 模块只保留实际需要的层；检索、ChatBI 等管道型模块按数据流组织；跨模块调用应使用对方公开 Service、DTO 或 `composition.py`，不直接访问对方 ORM。

## 3. 后端总体结构

```text
backend/
├── main.py                         # FastAPI 应用入口，注册中间件、生命周期、迁移、缓存、MCP 和静态资源
├── pyproject.toml                  # Python 依赖、项目元数据以及 Ruff、Mypy、Pytest 配置
├── uv.lock                         # 后端依赖锁定结果
├── alembic.ini                     # Alembic 数据库迁移配置
├── alembic/
│   ├── env.py                      # 加载全部 ORM 元数据并执行在线或离线数据库迁移
│   └── versions/                   # 从初始建表到当前版本的数据库迁移链
├── apps/
│   ├── __init__.py                 # 业务模块包声明
│   ├── api.py                      # 后端业务总路由，只负责组装和注册各模块 Router
│   ├── access_control/             # 本地身份、工作空间、认证、授权、API Key 和数据权限
│   ├── ai_model/                   # AI 模型配置、密钥管理和运行时客户端
│   ├── assistant/                  # 嵌入式助手、访问凭证和助手数据源
│   ├── chatbi/                     # 对话式问数、SQL 生成、执行、Agent 和 Graph 编排
│   ├── conversation/               # 会话、问数记录和步骤日志的数据所有者
│   ├── dashboard/                  # 仪表板配置和图表数据查询
│   ├── datasource/                 # 数据源连接、物理元数据、表关系和 Excel 导入
│   ├── knowledge/                  # SQL 示例和推荐问题
│   ├── platform_config/            # 平台参数和外观配置
│   ├── retrieval/                  # 语义资产与 SQL 示例的统一索引、召回和评测
│   ├── semantic/                   # 语义域、模型、数据集、指标、维度、术语和语义 SQL
│   └── system/                     # 用户 Excel 导入导出等少量系统兼容接口
├── common/                         # 数据库、配置、缓存、审计、国际化、安全和通用工具
├── interfaces/                     # 不属于单一业务模块的 HTTP、MCP 对外接口
├── platform/
│   └── workflow_engine/            # 与具体业务无关的通用图工作流运行平台
├── sqlbot_platform/
│   ├── __init__.py                 # 通用平台的稳定 Python 导入命名空间
│   └── workflow_engine -> ...      # 指向 platform/workflow_engine 的导入链接
├── templates/
│   └── template.yaml               # ChatBI 使用的集中式提示词模板
├── locales/                        # 后端业务国际化文本
├── scripts/                        # 数据迁移、评测和维护脚本
├── tests/                          # 单元、契约、仓储、流程和架构守卫测试
├── data/                           # 本地运行产生或读取的数据文件
└── logs/                           # 后端运行日志
```

## 4. 应用入口与通用层

### 4.1 应用入口

```text
backend/
├── main.py                         # 创建 FastAPI 应用；启动时迁移数据库、恢复 Artifact 清理任务、初始化缓存与动态 CORS
├── apps/
│   ├── __init__.py                 # 声明业务应用包
│   └── api.py                      # 注册访问控制、模型、助手、ChatBI、数据源、语义层等业务路由
├── alembic/
│   └── env.py                      # 汇总 ORM 模型并建立 Alembic 迁移上下文
└── sqlbot_platform/
    └── __init__.py                 # 将 platform 下的源码暴露为 sqlbot_platform 命名空间，避免与标准库 platform 重名
```

### 4.2 `common` 通用技术层

```text
common/
├── __init__.py                     # 通用包声明
├── error.py                        # 全局基础异常和通用错误定义
├── audit/
│   ├── __init__.py                 # 审计包声明
│   ├── models/
│   │   ├── __init__.py             # 导出审计 ORM
│   │   └── log_model.py            # 系统操作日志持久化模型
│   └── schemas/
│       ├── __init__.py             # 审计辅助对象导出
│       ├── log_utils.py            # 生成和记录操作日志
│       ├── logger_decorator.py     # 为接口或业务调用提供审计日志装饰器
│       └── request_context.py      # 维护审计需要的请求上下文中间件
├── core/
│   ├── __init__.py                 # 核心基础设施包声明
│   ├── cache_keys.py               # 统一定义缓存键格式
│   ├── config.py                   # 从环境变量加载项目、数据库、安全、模型和功能配置
│   ├── db.py                       # 创建 SQLModel/SQLAlchemy 数据库引擎
│   ├── deps.py                     # FastAPI Session、当前用户、翻译函数等依赖类型
│   ├── file.py                     # 通用文件响应和文件处理基础能力
│   ├── models.py                   # 通用数据库模型字段和基础模型
│   ├── pagination.py               # 通用分页请求与分页结果结构
│   ├── response_middleware.py      # 统一成功响应、异常响应和异常处理
│   ├── schemas.py                  # 通用创建、更新、查询等基础 DTO
│   ├── security.py                 # 密码摘要、密码校验和安全辅助函数
│   ├── security_config.py          # 安全相关配置和 RSA 密钥持久化模型
│   └── sqlbot_cache.py             # 内存缓存实现及缓存初始化入口
├── interfaces/
│   ├── __init__.py                 # 通用接口包声明
│   ├── i18n.py                     # 加载接口国际化、标签说明和翻译函数
│   └── locales/
│       ├── en.json                 # 英文接口描述
│       └── zh.json                 # 中文接口描述
└── utils/
    ├── __init__.py                 # 工具包声明
    ├── aes_crypto.py               # AES 加密解密实现
    ├── command_utils.py            # 受控执行系统命令
    ├── crypto.py                   # 本地 RSA 密钥生成、加密和解密
    ├── data_format.py              # 查询结果和通用数据格式转换
    ├── data_format_schema.py       # 数据格式化所需的结构定义
    ├── excel.py                    # 通用 Excel 读写辅助
    ├── file_utils.py               # 文件类型、大小、安全路径、上传和删除处理
    ├── http_utils.py               # HTTP 请求相关辅助
    ├── locale.py                   # 语言标识和本地化辅助
    ├── random.py                   # 随机字符串生成
    ├── snowflake.py                # 雪花 ID 生成器
    ├── time.py                     # 日期时间转换与格式化
    ├── tree_utils.py               # 列表与树形结构互转
    ├── utils.py                    # 日志、JSON 等仍在共享的基础工具
    └── whitelist.py                # 认证中间件使用的公开路径白名单
```

### 4.3 `interfaces` 对外接口层

```text
interfaces/
├── __init__.py                     # 对外接口包声明
├── http/
│   ├── __init__.py                 # HTTP 接口包声明
│   └── file_download.py            # 公共文件下载接口
└── mcp/
    ├── __init__.py                 # MCP 接口包声明
    ├── router.py                   # MCP 登录、数据源查询和问数工具路由
    └── schemas.py                  # MCP 请求与响应结构
```

## 5. 业务模块

### 5.1 `access_control` 访问控制

该模块是用户、工作空间、认证、授权、API Key、变量和数据权限的唯一业务入口。

```text
apps/access_control/
├── __init__.py                     # Access Control 领域声明
├── cache.py                        # 当前用户和权限相关缓存读写
├── composition.py                  # 组装身份、认证、授权、API Key、变量和数据策略 Service
├── data_policy.py                  # 将当前会话的数据权限转换为查询策略
├── errors.py                       # 访问控制领域异常和错误码
├── http_error_mapping.py           # 将访问控制异常映射为 HTTP 异常
├── identity.py                     # 对外提供用户身份读取能力
├── permission.py                   # 权限枚举、权限声明和接口权限装饰器
├── token_authentication.py         # Bearer Token、API Key 和助手凭证认证规则
├── api/
│   ├── __init__.py                 # 访问控制接口包声明
│   ├── access_variable.py          # 系统变量及用户变量绑定接口
│   ├── api_key.py                  # API Key 创建、查询、更新和删除接口
│   ├── authentication.py           # 请求认证中间件和令牌上下文建立
│   ├── data_permission.py          # 数据源行列权限配置接口
│   ├── login.py                    # 本地账号登录、退出和当前用户接口
│   ├── permission.py               # 权限资源查询接口
│   ├── request_context.py          # 从请求中读取用户和工作空间上下文
│   ├── user.py                     # 用户维护和工作空间成员接口
│   └── workspace.py                # 工作空间维护接口
├── models/
│   ├── __init__.py                 # 集中导出访问控制模型
│   ├── dto/
│   │   ├── __init__.py             # 导出访问控制 DTO
│   │   ├── access_variable.py      # 系统变量和用户变量契约
│   │   ├── authentication.py       # 登录、令牌和认证身份契约
│   │   ├── authorization.py        # 权限判断输入与结果契约
│   │   ├── data_policy.py          # 行权限、列权限和策略契约
│   │   ├── identity.py             # 用户身份创建、更新和查询契约
│   │   └── workspace.py            # 工作空间和成员关系契约
│   └── orm/
│       ├── __init__.py             # 导出访问控制 ORM
│       ├── access_variable.py      # 系统变量及用户绑定持久化模型
│       ├── api_key.py              # API Key 持久化模型
│       ├── data_policy.py          # 数据源权限规则持久化模型
│       ├── identity.py             # 用户身份持久化模型
│       └── workspace.py            # 工作空间和成员关系持久化模型
├── repository/
│   ├── __init__.py                 # 导出仓储接口和组合仓储
│   ├── access_variable_repository.py       # 变量持久化端口
│   ├── api_key_repository.py               # API Key 持久化端口
│   ├── data_policy_repository.py           # 数据策略持久化端口
│   ├── identity_workspace_repository.py    # 用户、工作空间和成员关系持久化端口
│   ├── resource_scope.py                   # 汇总各业务模块的工作空间资源归属
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel 仓储实现
│       ├── access_variable_repository.py   # 变量仓储的 SQLModel 实现
│       ├── api_key_repository.py           # API Key 仓储的 SQLModel 实现
│       ├── data_policy_repository.py       # 数据策略仓储的 SQLModel 实现
│       └── identity_workspace_repository.py# 用户和工作空间仓储的 SQLModel 实现
└── services/
    ├── __init__.py                         # 导出访问控制 Service
    ├── access_variable_service.py          # 变量定义、校验和用户绑定流程
    ├── api_key_service.py                  # API Key 生命周期和密钥生成规则
    ├── authentication_service.py           # 本地账号认证流程
    ├── authorization_service.py            # 角色、工作空间和资源归属授权判断
    ├── data_policy_service.py              # 数据权限规则维护与校验
    └── identity_workspace_service.py       # 用户、工作空间和成员关系业务流程
```

### 5.2 `ai_model` AI 模型

```text
apps/ai_model/
├── __init__.py                     # AI Model 模块公共入口
├── composition.py                  # 组装模型管理、运行配置和历史密钥迁移 Service
├── embedding.py                    # 创建统一 Embedding 模型调用对象
├── errors.py                       # AI 模型配置领域异常
├── llm.py                          # 创建统一大语言模型调用对象
├── model_factory.py                # 根据供应商和模型类型选择客户端实现
├── reference.py                    # 为其他模块提供模型存在性等最小引用查询
├── runtime.py                      # 加载配置并建立模型运行时
├── streaming.py                    # 统一模型流式输出处理
├── api/
│   ├── __init__.py                 # 模型接口包声明
│   └── model_config.py             # 模型配置增删改查、连通性测试和默认模型接口
├── models/
│   ├── __init__.py                 # 集中导出模型配置结构
│   ├── dto/
│   │   ├── __init__.py             # 导出 AI Model DTO
│   │   ├── model_management.py     # 模型管理输入、输出和测试契约
│   │   └── runtime_config.py       # LLM、Embedding 运行配置契约
│   └── orm/
│       ├── __init__.py             # 导出 AI Model ORM
│       └── model_config.py         # AI 模型配置持久化模型
├── openai/
│   ├── __init__.py                 # OpenAI 兼容客户端包声明
│   └── llm.py                      # OpenAI 兼容协议的 LLM 实现
├── repository/
│   ├── __init__.py                 # 导出模型仓储端口
│   ├── model_config_repository.py  # 模型运行配置读取端口
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel 实现
│       ├── model_config_repository.py      # 模型运行配置读取实现
│       └── model_management_repository.py  # 模型管理持久化实现
└── services/
    ├── __init__.py                         # 导出 AI Model Service
    ├── model_config_rules.py               # 默认模型、供应商和配置字段校验规则
    ├── model_management_service.py         # 模型配置管理、测试和密钥加密流程
    ├── runtime_config_service.py           # 解析指定或默认模型运行配置
    └── secret_migration_service.py         # 将历史明文模型密钥迁移为本地密文
```

### 5.3 `assistant` 嵌入式助手

```text
apps/assistant/
├── __init__.py                     # 导出助手公共模型和外部数据源契约
├── audit.py                        # 助手访问和操作审计辅助
├── composition.py                  # 组装助手仓储、数据源目录和模型引用服务
├── cors.py                         # 根据助手配置建立动态跨域规则
├── errors.py                       # 助手领域异常
├── public.py                       # 提供应用启动所需的助手公共初始化入口
├── token_authentication.py         # 校验嵌入式助手 app_id 和 app_secret
├── api/
│   ├── __init__.py                 # 助手接口包声明
│   ├── assistants.py               # 助手管理、公开信息、校验和运行接口
│   └── page_embedded.py            # 嵌入页面配置、凭证生成和凭证轮换接口
├── models/
│   ├── __init__.py                 # 集中导出助手模型
│   ├── dto/
│   │   ├── __init__.py             # 导出助手 DTO
│   │   └── assistant.py            # 助手配置、公开信息和嵌入参数契约
│   └── orm/
│       ├── __init__.py             # 导出助手 ORM
│       └── assistant.py            # 助手及嵌入凭证持久化模型
├── repository/
│   ├── __init__.py                         # 导出助手仓储端口
│   ├── assistant_repository.py             # 助手持久化端口
│   ├── external_datasource_repository.py   # 助手外部数据源访问端口
│   ├── external/
│   │   ├── __init__.py                     # 导出外部数据源实现
│   │   └── http_datasource.py              # 通过 HTTP 获取和查询助手外部数据源
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel 助手仓储
│       └── assistant_repository.py         # 助手仓储的 SQLModel 实现
└── services/
    ├── __init__.py                         # 导出 Assistant Service
    └── assistant_service.py                # 助手配置、数据源绑定、凭证和公开信息流程
```

### 5.4 `dashboard` 仪表板

```text
apps/dashboard/
├── __init__.py                     # Dashboard 模块公共入口
├── composition.py                  # 组装仪表板仓储和图表查询能力
├── api/
│   ├── __init__.py                 # 仪表板接口包声明
│   └── dashboard_api.py            # 仪表板增删改查、复制和图表数据接口
├── models/
│   ├── __init__.py                 # 导出 Dashboard 模型
│   ├── dto/
│   │   ├── __init__.py             # 导出 Dashboard DTO
│   │   └── dashboard.py            # 仪表板创建、更新、查询和复制契约
│   └── orm/
│       ├── __init__.py             # 导出 Dashboard ORM
│       └── dashboard.py            # 仪表板持久化模型
├── repository/
│   ├── __init__.py                 # 导出仪表板仓储端口
│   ├── dashboard_repository.py     # 仪表板持久化端口
│   └── sqlmodel/
│       ├── __init__.py             # 导出 SQLModel 仓储
│       └── dashboard_repository.py # 仪表板仓储的 SQLModel 实现
└── services/
    ├── __init__.py                 # 导出 Dashboard Service
    └── dashboard_service.py        # 仪表板维护、复制和图表查询流程
```

### 5.5 `datasource` 数据源

```text
apps/datasource/
├── __init__.py                     # 导出数据源公共 DTO、目录和外部连接能力
├── catalog.py                      # 提供跨模块只读数据源目录
├── composition.py                  # 组装数据源、连接、元数据、关系和 Excel 导入 Service
├── contracts.py                    # 定义跨模块使用的数据源目录和外部数据源契约
├── database.py                     # 保留的数据库连接公共入口
├── external_connection.py          # 建立助手外部数据源连接和配置
├── policy_catalog.py               # 为访问控制提供数据源表字段目录
├── resource_scope.py               # 提供数据源所属工作空间查询
├── api/
│   ├── __init__.py                 # 数据源接口包声明
│   ├── datasource.py               # 数据源管理、测试连接、元数据和 Excel 导入接口
│   └── table_relation.py           # 物理表关系查询和维护接口
├── crud/
│   ├── __init__.py                 # 旧 CRUD 包声明
│   └── datasource.py               # 尚在使用的数据源接口兼容查询
├── embedding/
│   ├── __init__.py                 # 数据源向量能力包声明
│   ├── table_embedding.py          # 表和字段文本向量计算与相似度查询
│   └── utils.py                    # 数据源向量文本处理辅助
├── models/
│   ├── __init__.py                 # 集中导出数据源模型
│   ├── dto/
│   │   ├── __init__.py             # 导出数据源 DTO
│   │   ├── connection.py           # 连接配置和连接测试契约
│   │   ├── datasource.py           # 数据源创建、更新、摘要和记录契约
│   │   ├── excel_import.py         # Excel 数据源导入契约
│   │   ├── physical_relation.py    # 物理表关系契约
│   │   └── physical_schema.py      # 物理表、字段和 Schema 契约
│   ├── orm/
│   │   ├── __init__.py             # 导出数据源 ORM
│   │   ├── datasource.py           # 数据源及连接配置持久化模型
│   │   └── physical_schema.py      # 物理表、字段和表关系持久化模型
│   └── rules/
│       ├── __init__.py             # 数据源规则包声明
│       └── physical_relation.py    # 表关系方向、重复和端点校验规则
├── repository/
│   ├── __init__.py                         # 导出数据源仓储和技术端口
│   ├── connection_gateway.py               # 数据库连接和查询技术端口
│   ├── datasource_repository.py            # 数据源基本信息持久化端口
│   ├── excel_import_gateway.py              # Excel 建表和导入技术端口
│   ├── maintenance_gateway.py               # 数据源删除等数据库维护端口
│   ├── metadata_repository.py               # 物理表字段持久化端口
│   ├── physical_relation_repository.py      # 物理表关系持久化端口
│   ├── connectors/
│   │   ├── __init__.py                     # 导出数据库连接器
│   │   ├── connection_gateway.py           # 数据库驱动连接端口实现
│   │   ├── database.py                     # 关系数据库驱动选择、连接和查询
│   │   ├── database_types.py               # 数据库类型、方言和驱动映射
│   │   ├── elasticsearch.py                # Elasticsearch 连接和查询实现
│   │   ├── excel_import.py                 # 将 Excel 数据导入 PostgreSQL
│   │   ├── local_engine.py                 # 本地 PostgreSQL 引擎创建
│   │   ├── maintenance_gateway.py          # 数据库对象清理实现
│   │   └── sql_templates.py                # 各类数据库的元数据查询 SQL 模板
│   └── sqlmodel/
│       ├── __init__.py                             # 导出 SQLModel 数据源仓储
│       ├── datasource_repository.py                # 数据源基本信息仓储实现
│       ├── metadata_repository.py                  # 物理元数据仓储实现
│       ├── physical_relation_repository.py         # 表关系仓储实现
│       └── recommendation_config_repository.py     # 数据源推荐问题配置存储实现
├── services/
│   ├── __init__.py                         # 导出 Datasource Service
│   ├── connection_service.py               # 数据源连接解析、测试和查询
│   ├── datasource_service.py               # 数据源生命周期和工作空间校验
│   ├── excel_import_service.py             # Excel 上传、建表和导入流程
│   ├── metadata_service.py                 # 物理表字段发现、同步和查询
│   └── physical_relation_service.py        # 物理表关系维护流程
└── utils/
    ├── __init__.py                         # 数据源工具包声明
    ├── excel.py                            # Excel 表名、字段名和类型处理
    └── utils.py                            # 数据源连接参数等通用转换
```

### 5.6 `knowledge` 知识资产

```text
apps/knowledge/
├── __init__.py                     # Knowledge 领域声明
├── composition.py                  # 组装 SQL 示例维护和召回 Service
├── errors.py                       # 知识资产领域异常
├── recommended.py                  # 组装推荐问题服务及其共享依赖
├── api/
│   ├── __init__.py                 # Knowledge 接口包声明
│   ├── recommended_problem.py      # 推荐问题配置和生成接口
│   └── sql_example.py              # SQL 示例维护、验证和查询接口
├── models/
│   ├── __init__.py                 # 导出知识资产模型
│   ├── dto/
│   │   ├── __init__.py             # 导出 Knowledge DTO
│   │   ├── recommended_problem.py  # 推荐问题及推荐配置契约
│   │   └── sql_example.py          # SQL 示例创建、验证、查询和引用契约
│   └── orm/
│       ├── __init__.py             # 导出 Knowledge ORM
│       ├── recommended_problem.py  # 推荐问题持久化模型
│       └── sql_example.py          # SQL 示例及验证状态持久化模型
├── repository/
│   ├── __init__.py                         # 导出知识资产仓储端口
│   ├── recommended_problem_repository.py   # 推荐问题持久化端口
│   ├── reference_catalog.py                # SQL 示例跨模块只读引用目录
│   ├── sql_example_repository.py           # SQL 示例持久化端口
│   └── sqlmodel/
│       ├── __init__.py                         # 导出 SQLModel 知识仓储
│       ├── recommended_problem_repository.py   # 推荐问题仓储实现
│       └── sql_example_repository.py           # SQL 示例仓储实现
└── services/
    ├── __init__.py                         # 导出 Knowledge Service
    ├── recommended_problem_service.py      # 推荐问题生成、保存和数据源配置流程
    ├── sql_example_query_service.py        # 面向生成流程的 SQL 示例查询
    └── sql_example_service.py              # SQL 示例维护、验证和索引同步
```

### 5.7 `platform_config` 平台配置

```text
apps/platform_config/
├── __init__.py                     # 平台运行参数领域声明
├── composition.py                  # 组装平台参数查询 Service
├── api/
│   ├── __init__.py                 # 平台配置接口包声明
│   ├── appearance.py               # 外观参数、图片上传和图片读取接口
│   ├── forms.py                    # 参数配置接口表单结构
│   └── router.py                   # 平台参数查询和保存接口
├── models/
│   ├── __init__.py                 # 导出平台参数模型
│   └── orm/
│       ├── __init__.py             # 导出平台参数 ORM
│       └── parameter.py            # 通用平台参数持久化模型
├── repository/
│   ├── __init__.py                 # 导出平台参数仓储端口
│   ├── parameter_repository.py     # 平台参数持久化端口
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel 参数仓储
│       └── parameter_repository.py         # 平台参数仓储实现
└── services/
    ├── __init__.py                         # 导出 Platform Config Service
    └── parameter_service.py                # 按键读取和解析平台运行参数
```

### 5.8 `retrieval` 统一检索

```text
apps/retrieval/
├── __init__.py                     # 导出统一检索请求、结果、策略和 Service
├── embedding.py                    # 统一调用 Embedding 模型并规范化向量
├── errors.py                       # 检索索引和查询异常
├── models/
│   ├── dto/
│   │   ├── __init__.py             # 导出检索 DTO
│   │   └── retrieval.py            # 检索请求、资产引用、召回结果和决策契约
│   └── orm/
│       ├── __init__.py             # 导出检索 ORM
│       └── retrieval.py            # 检索文档、向量、索引任务等持久化模型
├── indexing/
│   ├── __init__.py                 # 索引管道包声明
│   ├── profile.py                  # 索引使用的 Embedding 配置快照
│   ├── semantic_worker.py          # 语义资产索引任务执行器
│   ├── service.py                  # 文档投影、向量生成和索引写入流程
│   └── worker.py                   # 提交并处理待执行索引任务
├── projection/
│   ├── __init__.py                 # 检索投影包声明
│   ├── contracts.py                # 索引文档和查询计划的数据契约
│   ├── payload.py                  # 构造稳定的索引载荷
│   └── planner.py                  # 将检索请求规划为词法和向量查询
├── query/
│   ├── __init__.py                 # 检索查询包声明
│   ├── compilation.py              # 校验召回资产是否满足语义编译要求
│   ├── evaluation.py               # 检索结果离线评测
│   ├── hybrid.py                   # 合并词法召回和向量召回结果
│   ├── lexical_benchmark.py        # 词法检索基准评测工具
│   ├── policy.py                   # 检索结果接受、拒绝和降级规则
│   ├── profiles.py                 # 不同问数场景的检索参数配置
│   ├── semantic_binding.py         # 将召回资产解析为语义绑定
│   ├── semantic_runtime.py         # 语义检索运行时组装
│   ├── service.py                  # 统一检索主入口和结果决策
│   └── sql_example_query.py        # SQL 示例专用召回入口
└── sources/
    ├── __init__.py                 # 检索来源包声明
    ├── semantic_indexing.py        # 语义资产索引协调器
    ├── semantic_projector.py       # 将语义资产投影为检索文档
    ├── sql_example_indexing.py     # SQL 示例索引协调器
    └── sql_example_projector.py    # 将 SQL 示例投影为检索文档
```

### 5.9 `system` 系统兼容接口

```text
apps/system/
├── __init__.py                     # System 包声明
├── api/
│   ├── __init__.py                 # 系统接口包声明
│   └── user.py                     # 用户 Excel 模板、批量导入和错误文件下载接口
├── crud/
│   ├── __init__.py                 # 用户 Excel 处理包声明
│   └── user_excel.py               # Excel 模板生成、行校验、批量写入和错误文件生成
└── middleware/
    └── __init__.py                 # 系统中间件包声明，目前无独立实现
```

### 5.10 `conversation` 会话

Conversation 是 `chat`、`chat_record`、`chat_log` 三张表及其基础业务规则的唯一所有者。其他模块通过包公共入口和 `composition.py` 获取 DTO 与 Service，不直接依赖 ORM 或 SQLModel 仓储。Service 文件位于明确的 `services/` 目录，因此文件名使用 `conversation.py`、`chat_record.py`，不重复添加 `_service` 后缀。

```text
apps/conversation/
├── __init__.py                     # 导出 Conversation 公共 DTO、错误和 Service
├── composition.py                  # 组装会话、记录、历史查询与步骤日志 Service
├── errors.py                       # 会话、所有权、状态迁移和结果限制错误
├── formatting.py                   # 历史记录、图表字段与结果数据的纯格式化
├── resource_scope.py               # 提供会话所属工作空间查询
├── models/
│   ├── __init__.py                 # 汇总 Conversation DTO 与持久化模型
│   ├── dto/
│   │   ├── chat_history.py         # 会话历史、步骤日志和记录结果契约
│   │   ├── chat_record.py          # 问数记录创建、状态和结果投影契约
│   │   └── conversation.py         # 会话创建、绑定、摘要和重命名契约
│   └── orm/
│       ├── __init__.py             # 导出 Conversation 持久化模型
│       ├── chat.py                 # chat 表及快速命令枚举
│       ├── chat_log.py             # chat_log 表及日志枚举
│       └── chat_record.py          # chat_record 表及完成步骤枚举
├── repository/
│   ├── __init__.py                 # 导出会话、记录与历史读取仓储端口
│   ├── chat_history_repository.py  # 历史记录、结果和步骤日志读写端口
│   ├── chat_record_repository.py   # 问数记录持久化和历史查询端口
│   ├── conversation_repository.py  # 会话生命周期持久化端口
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel 仓储实现
│       ├── chat_history_repository.py      # 历史读取和步骤日志 SQLModel 实现
│       ├── chat_record_repository.py       # 问数记录 SQLModel 实现
│       └── conversation_repository.py      # 会话 SQLModel 实现
└── services/
    ├── __init__.py                 # Service 包声明
    ├── chat_log.py                 # 步骤日志开始、结束、置错和终态维护
    ├── chat_record.py              # 问数记录创建、状态迁移、结果和历史规则
    ├── conversation.py             # 会话创建、快照、绑定、重命名和自身删除规则
    └── history_query.py            # 会话历史、结果、日志和用量统一查询入口
```

### 5.11 `chatbi` 对话式问数

ChatBI 是业务编排模块，负责把 Conversation、问题理解、数据源选择、语义检索、SQL 生成、安全执行和答案生成连接起来。它不拥有会话数据、数据源、权限、模型或语义资产，而是通过这些模块的公开 Service 完成完整问数流程。

```text
apps/chatbi/
├── __init__.py                     # 以惰性导入方式暴露 ChatBI 稳定公共 Service 和规则函数
├── composition.py                  # 组装问数流程所需的权限、模型、数据源、语义层、检索和工作流依赖
├── errors.py                       # ChatBI 各阶段领域异常和错误码
├── adapters/
│   ├── __init__.py                 # ChatBI 技术适配包声明
│   ├── analysis_prediction.py      # 将分析预测 Service 适配为 LLM 调用
│   ├── assistant_schema.py         # 助手外部 Schema 适配
│   ├── auxiliary_generation.py     # 推荐/分析/预测编排装配
│   ├── chart_generation.py         # 将图表生成 Service 适配为 LLM 调用
│   ├── datasource_selection.py     # 将数据源选择 Service 适配为 LLM 调用
│   ├── dynamic_sql_generation.py   # 动态 SQL 生成的模型适配
│   ├── embedding_ranking.py        # 数据源和表字段的 Embedding 排序适配
│   ├── execution.py                # 将 Datasource 查询能力适配为 ChatBI 执行端口
│   ├── execution_cleanup.py        # 以独立事务协调 Agent、Graph 和 Artifact 清理
│   ├── langchain.py                # LangChain 消息、模型输出和调用配置转换
│   ├── permission_sql_generation.py# 权限 SQL 生成的模型适配
│   ├── query_execution.py          # 旧查询流程使用的 SQL 执行适配
│   ├── query_result_projection.py  # 查询结果摘要与投影适配
│   ├── question_model.py           # 问题理解模型客户端及其组装入口
│   ├── recommended_questions.py    # 推荐问题生成的模型适配
│   ├── sql_generation.py           # SQL 生成的模型适配
│   └── prompts/
│       ├── __init__.py             # 提示词适配包声明
│       └── yaml_templates.py       # 从 template.yaml 加载并渲染提示词
├── api/
│   ├── __init__.py                 # ChatBI 接口包声明
│   ├── conversations.py            # 会话列表、详情、重命名和删除接口
│   ├── interactions.py             # Graph/Agent 澄清交互提交和恢复接口
│   ├── legacy_composition.py       # 为现有聊天接口组装旧流程依赖
│   ├── queries.py                  # 推荐问题、分析与预测接口
│   ├── sse.py                      # /chat 辅助接口 SSE 事件编码
│   └── router.py                   # 组合会话、查询、交互和工作流路由
├── models/
│   ├── __init__.py                 # 集中导出 ChatBI 编排 DTO 和 Agent ORM
│   ├── dto/
│   │   ├── __init__.py             # 导出 ChatBI 数据契约
│   │   ├── agent.py                # Agent 请求、运行状态和事件契约
│   │   ├── analysis_prediction.py  # 分析预测输入和结果契约
│   │   ├── answer_generation.py    # 答案生成输入和结果契约
│   │   ├── answer_projection.py    # 最终答案投影所需数据契约
│   │   ├── chart_generation.py     # 图表生成输入和结果契约
│   │   ├── datasource_selection.py # 数据源候选和选择结果契约
│   │   ├── dynamic_sql_generation.py# 动态 SQL 生成输入和输出契约
│   │   ├── execution_binding.py    # 查询执行所需的数据集和数据源绑定
│   │   ├── final_reply.py          # 统一最终回复结构
│   │   ├── generation_context.py   # SQL 示例、术语等生成上下文契约
│   │   ├── generation_history.py   # 生成阶段使用的对话历史契约
│   │   ├── generation_runtime_settings.py# 生成阶段模型运行参数
│   │   ├── generation_schema_context.py  # 生成阶段物理 Schema 上下文
│   │   ├── legacy_query.py         # 现有聊天接口的请求和响应兼容契约
│   │   ├── permission_sql_generation.py  # 权限 SQL 生成契约
│   │   ├── physical_schema.py      # ChatBI 使用的物理表字段快照
│   │   ├── query_result_projection.py    # 查询结果预览、统计和摘要契约
│   │   ├── question_model.py       # 问题模型调用输入和输出契约
│   │   ├── question_understanding.py     # 意图、时间范围、指标和维度理解结果
│   │   ├── recommended_question.py       # 推荐问题生成契约
│   │   ├── result_artifact.py      # 大结果集 Artifact 引用契约
│   │   ├── semantic_query.py       # 语义查询和语义 SQL 编译契约
│   │   ├── semantic_retrieval.py   # 语义资产检索输入和结果契约
│   │   ├── sql_generation.py       # SQL 生成输入、结果和校验反馈契约
│   │   ├── streaming.py            # SSE 流式事件契约
│   │   └── tool_result.py          # Agent 工具统一执行结果
│   └── orm/
│       ├── __init__.py             # 导出 ChatBI ORM
│       └── agent_run.py            # Agent 运行、步骤和澄清状态持久化模型
├── repository/
│   ├── __init__.py                 # ChatBI 专属仓储包声明
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel ChatBI 仓储
│       ├── agent_run_repository.py         # Agent 运行状态仓储实现
│       └── recommended_question_history.py # 从历史记录读取推荐问题
├── services/
│   ├── __init__.py                         # 汇总导出 ChatBI 子域 Service
│   ├── conversation/
│   │   ├── __init__.py                     # 导出 ChatBI 保留的数据集绑定能力
│   │   ├── dataset_binding.py              # 协调 Semantic 与 Datasource 解析会话绑定
│   │   ├── deletion_service.py             # 协调 Conversation、Agent、Graph 和 Artifact 删除
│   │   ├── history_reader.py                # 富化 Conversation 历史并执行 data_live
│   │   └── ports.py                        # 跨模块级联删除端口
│   ├── execution/
│   │   ├── __init__.py                     # 导出执行子域 Service 和规则
│   │   ├── guarded_query_service.py        # 校验 SQL、应用权限、限制结果并执行查询
│   │   ├── ports.py                        # 查询执行、权限策略和 Artifact 存储端口
│   │   ├── result_artifacts.py             # 保存、读取和清理大结果集 Artifact
│   │   ├── result_projection.py            # 将原始查询结果投影为可展示结果
│   │   ├── sql_permission.py               # 将数据权限规则应用到 SQL
│   │   └── sql_validator.py                # 限制 SQL 类型、语句数量和危险操作
│   ├── generation/
│   │   ├── __init__.py                     # 导出生成子域 Service 和纯函数
│   │   ├── analysis_prediction.py          # 判断问题是否适合继续分析
│   │   ├── answer_generation.py            # 根据问题、SQL 和结果生成文字答案
│   │   ├── answer_projection.py            # 组合答案正文、图表、错误和证据
│   │   ├── chart_generation.py             # 根据查询结果生成图表配置
│   │   ├── dynamic_sql_generation.py       # 根据执行反馈动态修正 SQL
│   │   ├── final_reply.py                  # 将各类终态统一投影为最终回复
│   │   ├── permission_sql_generation.py    # 生成受行列权限约束的 SQL
│   │   ├── ports.py                        # LLM Client 和 PromptBuilder 等生成端口
│   │   ├── recommended_questions.py            # 根据上下文生成后续推荐问题
│   │   ├── auxiliary_generation.py             # 推荐/分析/预测应用编排
│   │   ├── sql_generation.py               # 根据问题、Schema 和知识上下文生成 SQL
│   │   ├── streaming.py                    # 将生成过程转换为标准流式事件
│   │   └── context/
│   │       ├── __init__.py                 # 导出生成上下文能力
│   │       ├── history.py                  # 裁剪并投影模型所需对话历史
│   │       ├── knowledge.py                # 汇总术语和 SQL 示例知识上下文
│   │       ├── runtime_settings.py         # 解析模型、语言和生成运行参数
│   │       ├── schema_context.py           # 选择物理表字段并构造 Schema 上下文
│   │       └── scope.py                    # 确定当前生成的数据源、数据集和工作空间范围
│   ├── planning/
│   │   ├── __init__.py                     # 导出规划子域 Service 和规则
│   │   ├── datasource_candidates.py        # 生成并排序当前用户可用数据源候选
│   │   ├── datasource_selection.py         # 根据问题和候选选择数据源
│   │   ├── execution_binding.py            # 将数据集选择解析为实际执行连接
│   │   ├── physical_schema.py              # 读取并投影物理表字段
│   │   ├── semantic_compilation.py         # 调用 Semantic 编译语义查询
│   │   └── semantic_retrieval.py           # 调用 Retrieval 召回语义资产
│   └── understanding/
│       ├── __init__.py                     # 导出问题理解子域能力
│       ├── graph_contracts.py              # 将问题理解结果转换为 Graph 上下文
│       ├── intent_fallback.py              # 严格意图识别失败后的明确错误处理
│       ├── intent_projection.py            # 投影和规范化模型意图输出
│       ├── model_invocation.py             # 调用问题理解模型并解析响应
│       ├── prompts.py                      # 问题理解的业务约束和提示词规则
│       ├── time_range.py                   # 解析相对或绝对时间范围
│       ├── understanding_service.py        # 问题理解主流程
│       └── validation.py                   # 校验指标、维度、意图和时间范围结果
└── orchestration/
    ├── agent/
    │   ├── __init__.py                     # 导出 Agent 编排入口
    │   ├── budget.py                       # 限制 Agent 步骤、工具调用和令牌预算
    │   ├── events.py                       # 构造 Agent 运行事件
    │   ├── loop.py                         # 执行模型思考、工具调用和终止循环
    │   ├── prompts.py                      # Agent 系统提示词和工具使用规则
    │   ├── service.py                      # Agent 启动、恢复和状态持久化服务
    │   └── tools/
    │       ├── __init__.py                 # 导出 Agent 工具
    │       ├── base.py                     # Agent 工具抽象和公共输入输出
    │       ├── core.py                     # Schema、语义检索、SQL 编译和查询工具
    │       ├── interaction.py              # 向用户发起澄清交互的工具
    │       └── registry.py                 # 注册并按名称调度 Agent 工具
    └── graph/
        ├── __init__.py                     # 导出 ChatBI Graph 组装入口
        ├── api_extension.py                # 将 ChatBI 业务能力注册到通用工作流 API
        ├── runtime.py                      # 组装 ChatBI Graph 定义、节点和持久化运行时
        ├── capabilities/
        │   ├── __init__.py                 # 导出 Graph 能力集合
        │   ├── capability_matrix.py        # 声明各 Graph 定义允许使用的能力
        │   ├── config.py                   # Graph 能力运行配置
        │   ├── context.py                  # 构造能力执行上下文
        │   ├── execution.py                # 调度并记录单项业务能力执行
        │   ├── gateway.py                  # 向 Workflow Engine 暴露统一能力网关
        │   ├── interactions.py             # Graph 澄清交互创建和恢复
        │   ├── placeholder.py              # 未启用能力的显式占位实现
        │   ├── planning.py                 # 将节点输入规划为能力调用
        │   ├── real.py                     # 组装当前已实现的真实 ChatBI 能力
        │   └── adapters/
        │       ├── __init__.py             # Graph 能力适配包声明
        │       ├── answer.py               # 答案生成与最终回复能力适配
        │       ├── interaction.py          # 用户澄清交互能力适配
        │       ├── knowledge.py            # SQL 示例和术语知识能力适配
        │       ├── permission.py           # 数据权限解析能力适配
        │       ├── question.py             # 问题理解主能力适配
        │       ├── question_common.py      # 问题理解适配器共享规则
        │       ├── question_dimension.py   # 维度识别能力适配
        │       ├── question_input.py       # 问题输入规范化能力适配
        │       ├── question_intent.py      # 意图识别能力适配
        │       ├── recommendation.py       # 推荐问题能力适配
        │       ├── sql.py                  # SQL 生成、执行和结果能力适配
        │       └── sql_repair.py           # SQL 错误分析和修复能力适配
        ├── conditions/
        │   ├── __init__.py                 # 导出 Graph 条件函数
        │   └── core.py                     # 定义继续、澄清、重试和终止分支条件
        ├── definitions/
        │   ├── __init__.py                 # 导出 ChatBI Graph 定义
        │   ├── chatbi_minimal_v1.py        # 最小问数流程图定义
        │   └── chatbi_v1.py                # 完整 ChatBI v1 流程图定义
        ├── nodes/
        │   ├── __init__.py                 # 导出 Graph 节点处理器
        │   ├── answer.py                   # 答案与推荐问题节点
        │   ├── evidence.py                 # 知识检索和证据整理节点
        │   ├── query.py                    # 问题理解、数据源和 Schema 节点
        │   ├── sql.py                      # SQL 生成、校验、执行和修复节点
        │   └── v1.py                       # v1 流程通用起止和状态节点
        └── schemas/
            ├── __init__.py                 # 导出 Graph 上下文结构
            └── v1.py                       # ChatBI v1 Graph 的上下文 Schema
```

### 5.12 `semantic` 语义层

Semantic 模块拥有语义域、语义模型、数据集、指标、维度和术语等权威数据，并向 ChatBI 提供数据集绑定、语义 Schema 查询和语义 SQL 编译能力。

```text
apps/semantic/
├── __init__.py                     # Semantic 领域声明
├── composition.py                  # 组装数据集、Schema、术语、Excel 和 SQL 编译公开 Service
├── errors.py                       # 语义资产领域异常和错误码
├── xpack_terminology_compatibility.py# 未被当前主流程引用的历史术语审计 ORM 别名
├── api/
│   ├── __init__.py                 # Semantic 接口包声明
│   ├── dataset_indexes.py          # 数据集检索索引状态和重建接口
│   ├── dataset_schema.py           # 数据集可查询 Schema 接口
│   ├── datasets.py                 # 语义数据集生命周期和发布接口
│   ├── datasources.py              # 语义层所需的数据源元数据发现接口
│   ├── dimensions.py              # 维度定义和发布接口
│   ├── domains.py                 # 语义域定义和维护接口
│   ├── error_mapping.py            # 将 Semantic 领域异常映射为 HTTP 异常
│   ├── legacy_terms.py             # 旧术语 HTTP 路径到 Semantic Service 的参数转换
│   ├── metrics.py                 # 指标定义、校验和发布接口
│   ├── models.py                  # 语义模型及模型关系维护接口
│   ├── router.py                  # 组合全部 Semantic 子资源路由
│   ├── term_excel.py              # 术语 Excel 模板、导入和导出接口
│   └── terms.py                   # 术语定义、查询和维护接口
├── models/
│   ├── __init__.py                 # 集中导出 Semantic DTO 和 ORM
│   ├── dto/
│   │   ├── __init__.py             # 导出 Semantic 数据契约
│   │   ├── base.py                 # 语义资产公共状态、分页和基础字段
│   │   ├── dataset.py              # 数据集创建、版本、绑定和发布契约
│   │   ├── dataset_index.py        # 数据集索引任务和状态契约
│   │   ├── dataset_reference.py    # 跨模块使用的数据集最小引用契约
│   │   ├── dataset_schema.py       # 数据集可查询指标、维度和模型 Schema
│   │   ├── dimension.py            # 维度定义、来源和质量信息契约
│   │   ├── domain.py               # 语义域创建、更新和查询契约
│   │   ├── metric.py               # 指标表达式、聚合、质量和发布契约
│   │   ├── model.py                # 语义模型、字段和模型关系契约
│   │   ├── sql_compilation.py      # 语义查询输入、编译结果和参数契约
│   │   ├── term.py                 # 术语、别名、定义和适用范围契约
│   │   └── term_excel.py           # 术语 Excel 行、校验结果和导入结果契约
│   └── orm/
│       ├── __init__.py             # 导出 Semantic ORM
│       ├── dataset.py              # 数据集、绑定、版本和资产关系持久化模型
│       ├── dimension.py            # 维度及维度版本持久化模型
│       ├── domain.py               # 语义域持久化模型
│       ├── knowledge.py            # 术语和知识类语义资产持久化模型
│       ├── metric.py               # 指标及指标版本持久化模型
│       └── model.py                # 语义模型、字段和模型关系持久化模型
├── repository/
│   ├── __init__.py                         # 导出 Semantic 仓储端口
│   ├── dataset_index_repository.py         # 数据集索引任务持久化端口
│   ├── dataset_repository.py               # 数据集生命周期持久化端口
│   ├── datasource_metadata_repository.py   # 数据源元数据发现端口
│   ├── dimension_repository.py             # 维度持久化端口
│   ├── domain_repository.py                # 语义域持久化端口
│   ├── metric_repository.py                # 指标持久化端口
│   ├── model_relation_repository.py        # 语义模型关系持久化端口
│   ├── model_repository.py                 # 语义模型持久化端口
│   ├── schema_repository.py                # 语义 Schema 读取端口
│   ├── term_repository.py                  # 术语持久化端口
│   ├── term_workbook_repository.py         # 术语 Excel 工作簿端口
│   ├── datasource/
│   │   ├── __init__.py                     # 数据源元数据适配包声明
│   │   ├── metadata_discovery.py           # 从 Datasource 公开契约发现物理元数据
│   │   └── metadata_repository.py          # Semantic 数据源元数据仓储适配
│   ├── excel/
│   │   ├── __init__.py                     # Excel 仓储适配包声明
│   │   └── term_workbook_repository.py     # 术语工作簿生成和解析实现
│   └── sqlmodel/
│       ├── __init__.py                     # 导出 SQLModel Semantic 仓储
│       ├── asset_relation_mapper.py        # 语义资产关系 ORM 与 DTO 映射
│       ├── dataset_binding_repository.py   # 数据集执行绑定查询实现
│       ├── dataset_catalog_repository.py   # 数据集只读目录实现
│       ├── dataset_index_repository.py     # 数据集索引任务仓储实现
│       ├── dataset_repository.py           # 数据集生命周期仓储实现
│       ├── dimension_repository.py         # 维度仓储实现
│       ├── domain_repository.py            # 语义域仓储实现
│       ├── metric_repository.py            # 指标仓储实现
│       ├── model_relation_repository.py    # 模型关系仓储实现
│       ├── model_repository.py             # 语义模型仓储实现
│       ├── results.py                      # 将数据库查询结果投影为仓储结果
│       ├── schema_loader.py                # 从持久化数据装载完整语义 Schema
│       ├── storage_consistency.py          # 检查版本、关系和发布状态一致性
│       ├── storage_sync.py                 # 同步统一资产表与具体语义资产表
│       └── term_repository.py              # 术语仓储实现
├── services/
│   ├── __init__.py                         # 汇总导出 Semantic Service
│   ├── dataset_binding_service.py          # 查询数据集到物理数据源的执行绑定
│   ├── dataset_catalog_service.py          # 提供数据集只读目录
│   ├── dataset_index_service.py            # 创建、重建和查询数据集索引任务
│   ├── dataset_reference_service.py        # 为跨模块引用校验数据集存在性和范围
│   ├── dataset_service.py                  # 数据集创建、更新、版本、发布和资产绑定
│   ├── datasource_service.py               # 发现可用于语义建模的数据源和物理表
│   ├── dimension_service.py                # 维度创建、更新、质量校验和发布
│   ├── domain_service.py                   # 语义域生命周期管理
│   ├── metric_service.py                   # 指标创建、更新、质量校验和发布
│   ├── model_relation_service.py           # 语义模型关系维护与闭环校验
│   ├── model_service.py                    # 语义模型和模型字段生命周期管理
│   ├── schema_service.py                   # 装载并查询数据集语义 Schema
│   ├── sql_compilation_service.py          # 校验语义查询并调用编译器
│   ├── sql_compiler.py                     # 将语义指标、维度和过滤条件编译为 SQL
│   ├── term_compatibility_service.py       # 旧术语接口到当前术语领域的参数转换
│   ├── term_excel_service.py               # 术语模板、导入、校验和导出流程
│   ├── term_migration.py                   # 将旧术语数据迁移到 Semantic 权威存储
│   ├── term_query_service.py               # 为 ChatBI 提供只读术语查询
│   ├── term_service.py                     # 术语创建、更新、查询和删除
│   ├── builders/
│   │   ├── __init__.py                     # 语义对象构建包声明
│   │   ├── metric_builder.py               # 从输入构建规范化指标对象
│   │   ├── model_builder.py                # 从物理元数据构建语义模型
│   │   └── schema_builder.py               # 组合数据集、模型、指标和维度 Schema
│   ├── matching/
│   │   ├── __init__.py                     # Schema 匹配包声明
│   │   └── schema_element_matcher.py       # 按名称和别名匹配指标、维度及字段
│   └── rules/
│       ├── __init__.py                     # Semantic 业务规则包声明
│       ├── metric_quality.py               # 指标表达式、聚合和可执行性质量规则
│       └── model_relation.py               # 模型关系端点、方向和环路规则
└── utils/
    ├── __init__.py                         # Semantic 工具包声明
    ├── model_update.py                     # 合并语义模型更新字段
    ├── orm_mapping.py                      # ORM、DTO 和枚举值转换
    ├── schema_selection.py                 # 从完整 Schema 中选择所需资产
    ├── semantic_aliases.py                 # 规范化名称和语义别名
    └── text.py                             # 语义资产文本清洗和标准化
```

## 6. 通用工作流平台

`platform/workflow_engine` 不依赖任何业务模块。它只负责工作流定义、图运行、状态持久化、交互暂停、事件输出和 Artifact 生命周期；ChatBI 通过公开端口和扩展接口接入。

```text
platform/workflow_engine/
├── __init__.py                     # 导出工作流平台稳定公共对象
├── artifact_gateway.py             # 为业务模块提供 Artifact 保存、读取和清理入口
├── composition.py                  # 组装运行存储、事件发布、交互和节点执行记录端口
├── run_cleanup.py                  # 删除工作流运行及其关联记录和 Artifact
├── api/
│   ├── __init__.py                 # 工作流接口包声明
│   ├── extension.py                # 注册业务模块提供的通用工作流 API 扩展和投影错误
│   ├── router.py                   # 工作流定义、运行、事件、取消和恢复接口
│   ├── schemas.py                  # 工作流 HTTP 请求和响应结构
│   └── service.py                  # API 层使用的定义、运行和交互应用服务
├── domain/
│   ├── __init__.py                 # 导出工作流领域对象
│   ├── artifact.py                 # Artifact 元数据、状态和清理任务领域模型
│   ├── checkpoint.py               # 工作流检查点领域模型
│   ├── context.py                  # 工作流上下文值和更新规则
│   ├── definition.py              # 节点、边、条件和工作流定义模型
│   ├── errors.py                  # 工作流定义和运行领域异常
│   ├── event.py                   # 运行事件类型和事件载荷
│   ├── execution.py               # 节点执行请求、结果和状态
│   ├── interaction.py             # 用户交互请求、响应和暂停状态
│   └── run.py                     # 工作流 Run 生命周期、租约和终态规则
├── ports/
│   ├── __init__.py                 # 导出工作流外部端口
│   ├── artifact_store.py           # Artifact 正文存储端口
│   ├── capability_gateway.py       # 业务能力执行网关端口
│   ├── event_publisher.py          # 运行事件发布端口
│   ├── run_store.py                # 工作流 Run 和检查点存储端口
│   └── runtime_persistence.py      # 交互、节点执行和运行时持久化端口
├── registry/
│   ├── __init__.py                 # 导出工作流注册表
│   ├── condition_registry.py       # 注册和解析边条件函数
│   ├── definition_validator.py     # 校验节点、边、入口、可达性和定义摘要
│   ├── handler_registry.py         # 注册和解析节点处理器
│   └── workflow_registry.py        # 管理工作流定义版本和激活状态
├── runtime/
│   ├── __init__.py                 # 导出 Graph Runtime 公共入口
│   ├── checkpoint_manager.py       # 保存和恢复工作流检查点
│   ├── context_patcher.py          # 按声明规则合并节点上下文更新
│   ├── graph_runtime.py            # 驱动节点执行、分支、暂停、恢复和终止
│   ├── interaction.py             # 运行时创建和消费用户交互
│   ├── lease.py                   # 工作流运行租约申请、续期和释放
│   ├── mapping.py                 # 将上下文值映射到节点输入
│   ├── path.py                    # 计算下一节点和执行路径
│   ├── public_projection.py       # 将内部运行状态投影为公开结果
│   ├── retry.py                   # 节点重试次数、退避和失败规则
│   ├── router.py                  # 根据边条件选择后续节点
│   └── scheduler.py               # 调度可执行节点并处理并发完成结果
└── infrastructure/
    ├── __init__.py                 # 工作流基础设施包声明
    ├── memory.py                   # 测试和轻量运行使用的内存端口实现
    ├── artifacts/
    │   ├── __init__.py             # Artifact 基础设施包声明
    │   ├── cleanup.py              # 执行待处理 Artifact 正文清理任务
    │   └── file_store.py           # Artifact 正文的本地文件存储实现
    ├── events/
    │   ├── __init__.py             # 事件基础设施包声明
    │   ├── outbox.py               # 从数据库读取未发布事件
    │   ├── publisher.py            # 将运行事件写入数据库
    │   └── stream.py               # 将持久化事件转换为 SSE 流
    └── persistence/
        ├── __init__.py                     # 导出工作流持久化实现
        ├── artifact_repository.py          # Artifact 元数据和清理任务仓储
        ├── definition_repository.py        # 工作流定义及版本仓储
        ├── interaction_manager.py          # 用户交互持久化实现
        ├── models.py                       # 工作流定义、运行、节点、事件等 ORM
        ├── node_execution_repository.py    # 节点执行记录仓储
        ├── run_repository.py               # Run、租约和检查点仓储
        └── unit_of_work.py                 # 工作流持久化事务边界
```

## 7. 非生产源码目录

这些目录属于后端仓库结构，但不进入前面的生产代码逐文件说明。

```text
backend/
├── alembic/versions/               # 数据库迁移历史；文件编号共同构成不可跳过的版本链
├── scripts/                        # 术语迁移、ChatBI 评测和维护命令
├── tests/
│   ├── access_control/             # 身份、认证、授权和数据权限测试
│   ├── ai_model/                   # 模型配置和运行时测试
│   ├── architecture/               # 模块依赖和目录结构守卫
│   ├── assistant/                  # 助手与嵌入接口测试
│   ├── chatbi/                     # 问数各阶段、Agent 和 Graph 测试
│   ├── datasource/                 # 连接、元数据和关系测试
│   ├── knowledge/                  # SQL 示例和推荐问题测试
│   ├── retrieval/                  # 索引、召回、策略和评测测试
│   ├── semantic/                   # 语义资产、Schema 和编译测试
│   └── workflow_engine/            # 通用工作流定义、运行和持久化测试
├── data/                           # 开发或测试数据
└── logs/                           # 运行日志
```
