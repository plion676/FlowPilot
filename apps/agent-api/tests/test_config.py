from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from app.clients.llm import LangChainChatClient
from app.core.config import Settings, SettingsError
from app.tools.registry import default_tool_registry


def test_settings_marks_empty_key_as_not_configured() -> None:
    settings = Settings.from_env(
        {
            "LLM_BASE_URL": "https://example.test/v1",
            "LLM_MODEL": "demo-model",
            "LLM_API_KEY": "",
        }
    )
    assert settings.llm_configured is False


def test_settings_rejects_non_positive_timeout() -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"LLM_TIMEOUT_SECONDS": "0"})


def test_deepseek_is_default_but_key_is_still_required() -> None:
    settings = Settings.from_env({})
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_configured is False


def test_deepseek_model_uses_non_thinking_tool_call_mode() -> None:
    settings = Settings.from_env({"LLM_API_KEY": "test-only-not-a-real-key"})
    client = LangChainChatClient(settings)
    assert client.model_name == "deepseek-flash"
    assert client.chat_model.extra_body == {"thinking": {"type": "disabled"}}
    assert "test-only-not-a-real-key" not in repr(settings)


def test_deepseek_serialized_function_name_has_no_domain_dot() -> None:
    settings = Settings.from_env({"LLM_API_KEY": "test-only-not-a-real-key"})
    spec = default_tool_registry().get("crm.get_customer_overview")
    assert spec is not None
    tool = StructuredTool.from_function(
        func=lambda customer_id: customer_id,
        name=spec.model_name,
        description="测试客户查询",
        args_schema=spec.input_schema,
    )
    binding = LangChainChatClient(settings).chat_model.bind_tools([tool])
    assert binding.kwargs["tools"][0]["function"]["name"] == "crm__get_customer_overview"


def test_custom_openai_compatible_provider_can_override_defaults() -> None:
    settings = Settings.from_env(
        {
            "LLM_BASE_URL": "https://example.test/v1",
            "LLM_MODEL": "custom-model",
            "LLM_API_KEY": "test-only-not-a-real-key",
        }
    )
    client = LangChainChatClient(settings)
    assert client.model_name == "custom-model"
    assert client.chat_model.extra_body is None
