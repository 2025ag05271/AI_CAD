from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    app_name: str = "CATIA Agent API"
    app_version: str = "0.2.0"

    environment: Literal[
        "development",
        "test",
        "production",
    ] = "development"

    api_host: str = "127.0.0.1"
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    ollama_base_url: str = (
        "http://127.0.0.1:11434"
    )
    ollama_model: str = "qwen3:4b"

    mcp_server_url: str = (
        "http://127.0.0.1:8001/mcp"
    )

    mcp_tool_cache_ttl_seconds: float = Field(
        default=60.0,
        gt=0,
    )

    max_conversation_history_messages: int = Field(
        default=20,
        gt=0,
    )

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    enable_json_logs: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CATIA_AGENT_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Load and validate application configuration once.

    Restart FastAPI after changing the .env file because
    settings are cached for the process lifetime.
    """

    return Settings()


settings = get_settings()