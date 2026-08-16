import secrets
import urllib.parse
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    Field,
    PostgresDsn,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    PROJECT_NAME: str = "Numora"
    #CONTEXT_PATH: str = "/sqlbot"
    CONTEXT_PATH: str = ""
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def API_V1_STR(self) -> str:
        return self.CONTEXT_PATH + "/api/v1"

    POSTGRES_SERVER: str = 'localhost'
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = 'root'
    POSTGRES_PASSWORD: str = "Password123@pg"
    POSTGRES_DB: str = "sqlbot"
    SQLBOT_DB_URL: str = ''
    # SQLBOT_DB_URL: str = 'mysql+pymysql://root:Password123%40mysql@127.0.0.1:3306/sqlbot'

    TOKEN_KEY: str = "X-SQLBOT-TOKEN"
    DEFAULT_PWD: str = "Numora@123456"
    ASSISTANT_TOKEN_KEY: str = "X-SQLBOT-ASSISTANT-TOKEN"

    CACHE_TYPE: Literal["redis", "memory", "None"] = "memory"
    CACHE_REDIS_URL: str | None = None  # Redis URL, e.g., "redis://[[username]:[password]]@localhost:6379/0"

    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    LOG_DIR: str = "logs"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s"
    SQL_DEBUG: bool = False
    BASE_DIR: str = "/opt/sqlbot"
    SCRIPT_DIR: str = f"{BASE_DIR}/scripts"
    UPLOAD_DIR: str = "/opt/sqlbot/data/file"
    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn | str:
        if self.SQLBOT_DB_URL:
            return self.SQLBOT_DB_URL
        # return MultiHostUrl.build(
        #     scheme="postgresql+psycopg",
        #     username=urllib.parse.quote(self.POSTGRES_USER),
        #     password=urllib.parse.quote(self.POSTGRES_PASSWORD),
        #     host=self.POSTGRES_SERVER,
        #     port=self.POSTGRES_PORT,
        #     path=self.POSTGRES_DB,
        # )
        return f"postgresql+psycopg://{urllib.parse.quote(self.POSTGRES_USER)}:{urllib.parse.quote(self.POSTGRES_PASSWORD)}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    MCP_IMAGE_PATH: str = '/opt/sqlbot/images'
    EXCEL_PATH: str = '/opt/sqlbot/data/excel'
    MCP_IMAGE_HOST: str = 'http://localhost:3000'
    SERVER_IMAGE_HOST: str = 'http://YOUR_SERVE_IP:MCP_PORT/images/'
    SERVER_IMAGE_TIMEOUT: int = 15

    LOCAL_MODEL_PATH: str = '/opt/sqlbot/models'
    DEFAULT_EMBEDDING_MODEL: str = 'BAAI/bge-m3'
    EMBEDDING_PROVIDER: str = "openai_compatible"
    EMBEDDING_API_BASE_URL: str = "https://api.siliconflow.cn/v1"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_API_TIMEOUT: float = 30.0
    EMBEDDING_ENABLED: bool = True
    EMBEDDING_DEFAULT_SIMILARITY: float = 0.4
    EMBEDDING_TERMINOLOGY_SIMILARITY: float = EMBEDDING_DEFAULT_SIMILARITY
    EMBEDDING_DATA_TRAINING_SIMILARITY: float = EMBEDDING_DEFAULT_SIMILARITY
    EMBEDDING_DEFAULT_TOP_COUNT: int = 5
    EMBEDDING_TERMINOLOGY_TOP_COUNT: int = EMBEDDING_DEFAULT_TOP_COUNT
    EMBEDDING_DATA_TRAINING_TOP_COUNT: int = EMBEDDING_DEFAULT_TOP_COUNT

    RETRIEVAL_EMBEDDING_ENABLED: bool = True
    RETRIEVAL_EMBEDDING_PROVIDER: str = "openai_compatible"
    RETRIEVAL_EMBEDDING_API_BASE_URL: str = "https://api.siliconflow.cn/v1"
    RETRIEVAL_EMBEDDING_API_KEY: str = ""
    RETRIEVAL_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    RETRIEVAL_EMBEDDING_DIMENSION: int = 1024
    RETRIEVAL_EMBEDDING_TOP_K: int = 20
    RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY: bool = True
    RETRIEVAL_EMBEDDING_ALLOW_LEXICAL_FALLBACK: bool = True

    # 用户记忆向量索引独立于语义资产索引，默认关闭，需单独配置后启用。
    CHATBI_MEMORY_EMBEDDING_ENABLED: bool = False
    # 用户隐含偏好提取默认关闭，启用后只处理明确澄清回答，不阻断问数主链路。
    CHATBI_MEMORY_LLM_EXTRACTION_ENABLED: bool = False
    # 用户记忆召回灰度默认关闭，开启后按用户稳定分配 control/treatment。
    CHATBI_MEMORY_RECALL_EXPERIMENT_ENABLED: bool = False
    CHATBI_MEMORY_RECALL_TREATMENT_PERCENT: int = 0
    CHATBI_MEMORY_RECALL_EXPERIMENT_SALT: str = "chatbi-memory-recall-v1"
    CHATBI_MEMORY_RECALL_MIN_EVALUATED_PER_VARIANT: int = 100
    CHATBI_MEMORY_RECALL_MAX_ADOPTION_DROP: float = 0.05

    SILICONFLOW_API_KEY: str = ""
    RETRIEVAL_RERANK_ENABLED: bool = True
    RETRIEVAL_RERANK_API_BASE_URL: str = "https://api.siliconflow.cn/v1"
    RETRIEVAL_RERANK_API_KEY: str = ""
    RETRIEVAL_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RETRIEVAL_RERANK_TIMEOUT_SECONDS: float = 30.0

    RETRIEVAL_QUERY_TIMEOUT_MS: int = 1500

    # Agent 问数链路（chatbi/orchestration/agent，LLM 自主规划）
    CHAT_AGENT_ENABLED: bool = False
    # P0 分诊与 verified query 上下文均可独立回退。
    CHATBI_TRIAGE_ENABLED: bool = True
    # 筛选值归一：把理解产出的筛选值生成 VALUE 检索槽，命中维值字典后替换 canonical 值。
    CHATBI_VALUE_BINDING_ENABLED: bool = True
    CHATBI_EXEMPLAR_CONTEXT_ENABLED: bool = True
    CHAT_AGENT_DATASOURCE_ALLOWLIST: str = ""
    CHAT_AGENT_MAX_STEPS: int = 12
    CHAT_AGENT_MAX_SQL_RETRIES: int = 2
    CHAT_AGENT_TIMEOUT_SECONDS: int = 120
    CHAT_AGENT_TOKEN_BUDGET: int = 100000
    CHAT_AGENT_DEFAULT_LIMIT: int = 100
    CHAT_AGENT_MAX_CLARIFICATIONS: int = 2
    CHAT_AGENT_HISTORY_ROUNDS: int = 3
    CHAT_AGENT_CONTEXT_FOLD_CHARS: int = 30000
    # P1-2/P1-3 灰度开关；默认只保留旧链路，避免新模式未经验证改变线上行为。
    CHAT_AGENT_EXECUTION_MODES: str = "react_legacy"
    CHATBI_PLAN_MAX_QUERY_TASKS: int = 5
    CHATBI_PLANNER_TIMEOUT_MS: int = 60000
    CHATBI_COMPUTE_ENABLED: bool = True
    CHATBI_ANSWER_CITATION_ENFORCED: bool = True
    # 企业时间口径在 Run 创建时写入不可变 TemporalContext。
    TEMPORAL_TIMEZONE: str = "Asia/Shanghai"
    TEMPORAL_LOCALE: str = "zh-CN"
    TEMPORAL_WEEK_START: Literal[
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ] = "monday"
    TEMPORAL_FISCAL_YEAR_START_MONTH: int = Field(default=1, ge=1, le=12)
    TEMPORAL_FISCAL_YEAR_LABEL: Literal["start_year", "end_year"] = "start_year"
    TEMPORAL_BUSINESS_CALENDAR_ID: str | None = None
    # 默认关闭；开启后只记录模型时间计划与现有结果的差异。
    TEMPORAL_MODEL_SHADOW_ENABLED: bool = False
    # 默认关闭；开启后模型时间计划经过确定性解析并成为查询时间语义来源。
    TEMPORAL_MODEL_AUTHORITY_ENABLED: bool = False
    # P0-9：Agent 观测默认开启，按 10% 采样；关闭或零采样时不加载 OpenTelemetry。
    AGENT_TRACING_ENABLED: bool = True
    AGENT_TRACING_SAMPLE_RATE: float = 0.1
    AGENT_TRACING_SERVICE_NAME: str = "numora-agent"
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: str = ""
    QUERY_UNDERSTANDING_ENABLED: bool = True
    QUERY_UNDERSTANDING_MODEL_ENABLED: bool = True
    # 统一问题理解包含指标、维度和比较关系的联合识别，允许模型最多运行 60 秒。
    QUERY_UNDERSTANDING_TIMEOUT_MS: int = 60000
    QUERY_UNDERSTANDING_MIN_CONFIDENCE: float = 0.65
    CORE_SLOT_MIN_CONFIDENCE: float = 0.65
    METRIC_ACCEPT_SCORE: float = 0.78
    METRIC_AMBIGUITY_GAP: float = 0.12
    EXACT_ALIAS_ACCEPT: bool = True
    CLARIFICATION_MAX_OPTIONS: int = 6

    # 是否启用SQL查询行数限制，默认值，可被参数配置覆盖
    GENERATE_SQL_QUERY_LIMIT_ENABLED: bool = True
    GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT: int = 3

    PARSE_REASONING_BLOCK_ENABLED: bool = True
    DEFAULT_REASONING_CONTENT_START: str = '<think>'
    DEFAULT_REASONING_CONTENT_END: str = '</think>'

    PG_POOL_SIZE: int = 20
    PG_MAX_OVERFLOW: int = 30
    PG_POOL_RECYCLE: int = 3600
    PG_POOL_PRE_PING: bool = True

    TABLE_EMBEDDING_ENABLED: bool = True
    TABLE_EMBEDDING_COUNT: int = 10
    DS_EMBEDDING_COUNT: int = 10

    ORACLE_CLIENT_PATH: str = '/opt/sqlbot/db_client/oracle_instant_client'

    @field_validator('SQL_DEBUG',
                     'EMBEDDING_ENABLED',
                     'GENERATE_SQL_QUERY_LIMIT_ENABLED',
                     'PARSE_REASONING_BLOCK_ENABLED',
                     'PG_POOL_PRE_PING',
                     'TABLE_EMBEDDING_ENABLED',
                     'RETRIEVAL_EMBEDDING_ENABLED',
                     'RETRIEVAL_EMBEDDING_ALLOW_LEXICAL_FALLBACK',
                     'QUERY_UNDERSTANDING_ENABLED',
                     'QUERY_UNDERSTANDING_MODEL_ENABLED',
                     'TEMPORAL_MODEL_SHADOW_ENABLED',
                     'TEMPORAL_MODEL_AUTHORITY_ENABLED',
                     'CHATBI_MEMORY_LLM_EXTRACTION_ENABLED',
                     'EXACT_ALIAS_ACCEPT',
                     mode='before')
    @classmethod
    def lowercase_bool(cls, v: Any) -> Any:
        """将字符串形式的布尔值转换为Python布尔值"""
        if isinstance(v, str):
            v_lower = v.lower().strip()
            if v_lower == 'true':
                return True
            elif v_lower == 'false':
                return False
        return v


settings = Settings()
