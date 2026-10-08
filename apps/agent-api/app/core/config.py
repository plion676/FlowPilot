"""Environment-backed application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-flash"


class SettingsError(ValueError):
    """Raised when a non-secret application setting is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    log_level: str
    timezone: str
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None = field(repr=False)
    llm_timeout_seconds: float
    mcp_url: str | None = None
    mcp_call_secret: str | None = field(default=None, repr=False)
    business_base_url: str | None = None
    internal_service_token: str | None = field(default=None, repr=False)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = environ if environ is not None else os.environ
        timeout_text = source.get("LLM_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(timeout_text)
        except ValueError as error:
            raise SettingsError("LLM_TIMEOUT_SECONDS 必须是正数") from error
        if timeout <= 0:
            raise SettingsError("LLM_TIMEOUT_SECONDS 必须大于 0")

        def optional(name: str) -> str | None:
            value = source.get(name, "").strip()
            return value or None

        return cls(
            app_env=source.get("APP_ENV", "development"),
            log_level=source.get("APP_LOG_LEVEL", "INFO").upper(),
            timezone=source.get("APP_TIMEZONE", "Asia/Shanghai"),
            llm_base_url=optional("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL,
            llm_model=optional("LLM_MODEL") or DEFAULT_LLM_MODEL,
            llm_api_key=optional("LLM_API_KEY"),
            llm_timeout_seconds=timeout,
            mcp_url=optional("MCP_URL"),
            mcp_call_secret=optional("MCP_CALL_SECRET"),
            business_base_url=optional("BUSINESS_BASE_URL"),
            internal_service_token=optional("INTERNAL_SERVICE_TOKEN"),
        )
