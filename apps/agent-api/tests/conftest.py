from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        log_level="WARNING",
        timezone="Asia/Shanghai",
        llm_base_url=None,
        llm_model=None,
        llm_api_key=None,
        llm_timeout_seconds=1,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
