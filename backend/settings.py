from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["gemini", "openai", "anthropic", "deepseek"] = Field(
        default="gemini",
        alias="LLM_PROVIDER",
    )
    llm_model_name: str | None = Field(default=None, alias="LLM_MODEL_NAME")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")

    max_agent_steps: int = Field(default=15, alias="MAX_AGENT_STEPS")
    action_timeout_seconds: float = Field(default=40.0, alias="ACTION_TIMEOUT_SECONDS")
    max_dom_elements: int = Field(default=150, alias="MAX_DOM_ELEMENTS")
    require_action_approval: bool = Field(default=True, alias="REQUIRE_ACTION_APPROVAL")

    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    agent_auth_token: str = Field(default="", alias="AGENT_AUTH_TOKEN")
    cors_allowed_origins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")
    agent_db_path: str | None = Field(default=None, alias="AGENT_DB_PATH")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
