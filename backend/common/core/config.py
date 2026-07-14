import secrets
import urllib.parse
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
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
    PROJECT_NAME: str = "SQLBot"
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
    DEFAULT_PWD: str = "SQLBot@123456"
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
    SQLBOT_KEY_EXPIRED: int = 100  # License key expiration timestamp, 0 means no expiration

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

    HEADLESS_METRIC_EMBEDDING_ENABLED: bool = True
    HEADLESS_METRIC_EMBEDDING_PROVIDER: str = "openai_compatible"
    HEADLESS_METRIC_EMBEDDING_API_BASE_URL: str = "https://api.siliconflow.cn/v1"
    HEADLESS_METRIC_EMBEDDING_API_KEY: str = ""
    HEADLESS_METRIC_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    HEADLESS_METRIC_EMBEDDING_DIMENSION: int = 1024
    HEADLESS_METRIC_EMBEDDING_TOP_K: int = 20
    HEADLESS_METRIC_EMBEDDING_ALLOW_LEXICAL_FALLBACK: bool = True

    SEMANTIC_LAYER_ENABLED: bool = False
    SEMANTIC_APPROVED_ONLY: bool = True
    SEMANTIC_METRIC_TOP_K: int = 5
    SEMANTIC_DIMENSION_TOP_K: int = 8
    SEMANTIC_SEARCH_TIMEOUT_MS: int = 800
    SEMANTIC_ASSET_RUNTIME_ENABLED: bool = True
    SEMANTIC_ASSET_DOCUMENT_DYNAMIC_ENABLED: bool = True
    SEMANTIC_ASSET_INDEX_SYNC_ENABLED: bool = True
    SEMANTIC_ASSET_QUALITY_CHECK_ENABLED: bool = True
    SEMANTIC_ASSET_LEGACY_FALLBACK_ENABLED: bool = True
    SEMANTIC_ASSET_DEBUG_API_ENABLED: bool = False
    SEMANTIC_ASSET_CACHE_TTL_SECONDS: int = 300

    CHAT_AGENTIC_FLOW_ENABLED: bool = False
    CHAT_AGENTIC_FLOW_ASSISTANT_ENABLED: bool = False
    CHAT_AGENTIC_FLOW_DATASOURCE_ALLOWLIST: str = ""
    CHAT_AGENTIC_FLOW_MAX_STEPS: int = 12
    CHAT_AGENTIC_FLOW_DEFAULT_LIMIT: int = 100
    AGENTIC_MAX_STEPS: int = 12
    AGENTIC_MAX_TOOL_CALLS: int = 20
    AGENTIC_MAX_DURATION_SECONDS: int = 60
    AGENTIC_SQL_REPAIR_MAX_ATTEMPTS: int = 2
    AGENTIC_CLARIFICATION_EXPIRE_HOURS: int = 24
    AGENTIC_TRACE_PRIVATE_RETENTION_DAYS: int = 30
    # Agentic ChatBI v2（apps.chatbi_agent，LLM 自主规划）
    CHAT_AGENT_ENABLED: bool = False
    CHAT_AGENT_DATASOURCE_ALLOWLIST: str = ""
    CHAT_AGENT_MAX_STEPS: int = 12
    CHAT_AGENT_MAX_SQL_RETRIES: int = 2
    CHAT_AGENT_TIMEOUT_SECONDS: int = 120
    CHAT_AGENT_TOKEN_BUDGET: int = 100000
    CHAT_AGENT_DEFAULT_LIMIT: int = 100
    CHAT_AGENT_MAX_CLARIFICATIONS: int = 2
    CHAT_AGENT_HISTORY_ROUNDS: int = 3
    CHAT_AGENT_CONTEXT_FOLD_CHARS: int = 30000
    QUERY_UNDERSTANDING_ENABLED: bool = True
    QUERY_UNDERSTANDING_MODEL_ENABLED: bool = True
    QUERY_UNDERSTANDING_TIMEOUT_MS: int = 20000
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
                     'HEADLESS_METRIC_EMBEDDING_ENABLED',
                     'HEADLESS_METRIC_EMBEDDING_ALLOW_LEXICAL_FALLBACK',
                     'SEMANTIC_LAYER_ENABLED',
                     'SEMANTIC_APPROVED_ONLY',
                     'SEMANTIC_ASSET_RUNTIME_ENABLED',
                     'SEMANTIC_ASSET_DOCUMENT_DYNAMIC_ENABLED',
                     'SEMANTIC_ASSET_INDEX_SYNC_ENABLED',
                     'SEMANTIC_ASSET_QUALITY_CHECK_ENABLED',
                     'SEMANTIC_ASSET_LEGACY_FALLBACK_ENABLED',
                     'SEMANTIC_ASSET_DEBUG_API_ENABLED',
                     'CHAT_AGENTIC_FLOW_ENABLED',
                     'CHAT_AGENTIC_FLOW_ASSISTANT_ENABLED',
                     'QUERY_UNDERSTANDING_ENABLED',
                     'QUERY_UNDERSTANDING_MODEL_ENABLED',
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


settings = Settings()  # type: ignore
