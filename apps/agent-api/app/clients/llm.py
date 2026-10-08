"""LangChain-backed chat model adapter."""

from __future__ import annotations

from typing import Protocol

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.core.errors import AppError


class ChatClient(Protocol):
    model_name: str

    async def invoke(self, message: str) -> str: ...


class LangChainChatClient:
    """Adapt an OpenAI-compatible LangChain model to the application port."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_configured:
            raise AppError(
                code="LLM_NOT_CONFIGURED",
                message=(
                    "未配置模型密钥；请设置 LLM_API_KEY 环境变量（本地可使用项目根目录 .env）。"
                ),
                status_code=503,
            )
        self.model_name = settings.llm_model or "unknown"
        # DeepSeek enables thinking by default. Keep the initial ReAct/tool-call
        # path in non-thinking mode so SDK message round-trips stay predictable.
        extra_body = (
            {"thinking": {"type": "disabled"}}
            if (settings.llm_base_url or "").rstrip("/") == "https://api.deepseek.com"
            else None
        )
        self._model = ChatOpenAI(
            model=self.model_name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            temperature=0,
            extra_body=extra_body,
        )

    @property
    def chat_model(self) -> ChatOpenAI:
        return self._model

    async def invoke(self, message: str) -> str:
        try:
            response = await self._model.ainvoke([HumanMessage(content=message)])
        except TimeoutError as error:
            raise AppError(
                code="LLM_TIMEOUT",
                message="模型服务响应超时，请稍后重试。",
                status_code=503,
                retryable=True,
            ) from error
        except Exception as error:  # Provider exceptions lack a stable common base type.
            raise AppError(
                code="LLM_UNAVAILABLE",
                message="模型服务当前不可用，请稍后重试。",
                status_code=503,
                retryable=True,
            ) from error

        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                item if isinstance(item, str) else str(item.get("text", "")) for item in content
            )
        return str(content)
