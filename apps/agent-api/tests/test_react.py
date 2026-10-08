from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.react import ReActAgentRunner


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """LangChain's list fake with the tool-binding capability ReAct requires."""

    def bind_tools(self, tools: object, **kwargs: object) -> ToolCallingFakeModel:
        return self


@pytest.mark.asyncio
async def test_react_agent_uses_test_only_tool_observation() -> None:
    @tool
    def test_lookup(customer_id: str) -> str:
        """Return a deterministic test observation for a customer id."""
        return f"customer={customer_id}; risk=high"

    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "test_lookup",
                        "args": {"customer_id": "C1001"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="C1001 的测试观察结果为 high 风险。"),
        ]
    )
    runner = ReActAgentRunner(model, [test_lookup])
    assert await runner.invoke("查询 C1001") == "C1001 的测试观察结果为 high 风险。"
