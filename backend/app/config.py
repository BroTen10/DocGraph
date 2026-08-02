"""统一配置管理：从 .env 文件加载，所有 API key 通过环境变量注入，不硬编码。"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
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
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "doc_review"
    pg_user: str = "postgres"
    pg_password: str = "postgres"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4jpassword"

    # LLM (DeepSeek, OpenAI 兼容)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model_name: str = "deepseek-chat"
    llm_confidence_threshold: float = 0.9

    # OCR (阿里云百炼 通义千问 VL，OpenAI 兼容端点)
    ocr_api_key: str = ""
    ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ocr_model_name: str = "qwen-vl-max"

    # 审查容差默认值
    allow_same_day_receive_pay: bool = True
    amount_tolerance_percent: float = 5.0
    weight_tolerance_kg: float = 0.5

    @field_validator("upload_root")
    @classmethod
    def _abs_upload_root(cls, v: str) -> str:
        return str(Path(v).resolve())

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def neo4j_auth(self) -> tuple[str, str]:
        return (self.neo4j_user, self.neo4j_password)

    def ensure_upload_root(self) -> Path:
        p = Path(self.upload_root)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例配置。"""
    return Settings()


settings = get_settings()
