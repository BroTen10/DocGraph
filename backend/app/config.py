"""统一配置管理：从 .env 文件加载，所有 API key 通过环境变量注入，不硬编码。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。所有敏感字段（API key、DB 密码）从环境变量读取。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True
    app_secret_key: str = ""
    api_docs_enabled: bool = False
    session_cookie_secure: bool = True
    session_idle_minutes: int = Field(default=120, ge=5, le=1440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    upload_root: str = "./uploads"
    max_file_size_mb: int = 50
    # CORS 白名单（逗号分隔的前端地址）。生产收紧为明确来源，不再允许 *；
    # 同源部署（前端由后端静态托管）时留空即可，无需跨域。
    cors_origins_raw: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://localhost:8800"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    # 启动时是否重建数据库（DROP SCHEMA + 清空 Neo4j）。
    # 默认 False：仅幂等 create_all，不丢数据。
    # 仅在 schema 破坏性变更或需要干净环境时临时置 True。
    db_reset_on_startup: bool = False

    # Postgres
    database_url: str | None = None
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "doc_review"
    pg_user: str = "postgres"
    pg_password: str = "postgres"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4jpassword"

    # LLM（标普云 hub，OpenAI 兼容；模型由 hub 侧聚合，deepseek 系列走 Anthropic 语义）
    llm_api_key: str = ""
    llm_base_url: str = "https://hub.biaopu.cloud/v1"
    llm_model_name: str = "deepseek-v4.1-flash"
    llm_confidence_threshold: float = 0.9

    # OCR（多模态，OpenAI 兼容端点；与 LLM 同源）
    # OCR_API_KEY 留空时自动复用 LLM_API_KEY。
    ocr_api_key: str = ""
    ocr_base_url: str = "https://hub.biaopu.cloud/v1"
    ocr_model_name: str = "deepseek-v4.1-flash"

    # 审查容差默认值
    allow_same_day_receive_pay: bool = True
    amount_tolerance_percent: float = 5.0
    weight_tolerance_kg: float = 0.5

    # 邮件与对外链接
    email_backend: str = "smtp"
    public_base_url: str = "http://localhost:5173"
    mail_from: str = "no-reply@localhost"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    trust_proxy_headers: bool = False

    @field_validator("upload_root")
    @classmethod
    def _abs_upload_root(cls, v: str) -> str:
        return str(Path(v).resolve())

    @model_validator(mode="after")
    def _validate_security(self):
        if not self.app_debug and len(self.app_secret_key) < 32:
            raise ValueError("生产环境 APP_SECRET_KEY 至少 32 个字符")
        if not self.app_debug and self.app_secret_key.lower() in {
            "change-me",
            "changeme",
            "replace-me",
            "your-secret-key",
        }:
            raise ValueError("生产环境 APP_SECRET_KEY 不能使用示例值")
        if not self.app_debug and not self.session_cookie_secure:
            raise ValueError("生产环境必须启用 Secure Cookie")
        if not self.app_debug and not self.public_base_url.startswith("https://"):
            raise ValueError("生产环境 PUBLIC_BASE_URL 必须使用 HTTPS")
        if self.email_backend == "smtp" and not self.smtp_host.strip():
            raise ValueError("EMAIL_BACKEND=smtp 时必须配置 SMTP_HOST")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or self.pg_dsn

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def neo4j_auth(self) -> tuple[str, str]:
        return (self.neo4j_user, self.neo4j_password)

    @property
    def effective_ocr_api_key(self) -> str:
        """OCR Key：优先使用 OCR_API_KEY，未配置时复用 DeepSeek LLM Key。"""
        return self.ocr_api_key or self.llm_api_key

    def ensure_upload_root(self) -> Path:
        p = Path(self.upload_root)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例配置。"""
    return Settings()


settings = get_settings()
