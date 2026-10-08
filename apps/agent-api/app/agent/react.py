"""The ReAct execution primitive used by later policy-bound request paths."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool


class ToolCallGuard(AgentMiddleware):
    def __init__(
        self,
        validate: Callable[[list[dict[str, Any]]], None],
        select_tools=None,
        response_instructions=None,
    ) -> None:
        self._validate = validate
        self._select_tools = select_tools
        self._response_instructions = response_instructions

    async def awrap_model_call(self, request, handler):
        if self._select_tools:
            request = request.override(tools=self._select_tools())
        if self._response_instructions:
            instructions = self._response_instructions()
            if instructions:
                original = str(request.system_message.content) if request.system_message else ""
                request = request.override(
                    system_message=SystemMessage(content=original + "\n" + instructions)
                )
        return await handler(request)

    async def aafter_model(self, state: Any, runtime: Any) -> None:
        self._validate(getattr(state["messages"][-1], "tool_calls", []))


class ReActAgentRunner:
    """Run a LangChain ReAct Agent, whose runtime is backed by LangGraph."""

    def __init__(
        self,
        model: Any,
        tools: Sequence[BaseTool],
        *,
        system_prompt: str | None = None,
        validate_tool_calls: Callable[[list[dict[str, Any]]], None] | None = None,
        select_tools: Callable[[], list[BaseTool]] | None = None,
        response_instructions: Callable[[], str] | None = None,
    ) -> None:
        middleware = (
            [ToolCallGuard(validate_tool_calls, select_tools, response_instructions)]
            if validate_tool_calls
            else []
        )
        self._graph = create_agent(
            model=model, tools=list(tools), system_prompt=system_prompt, middleware=middleware
        )

    async def invoke(self, message: str) -> str:
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=message)]}, config={"recursion_limit": 30}
        )
        content = result["messages"][-1].content
        return content if isinstance(content, str) else str(content)
