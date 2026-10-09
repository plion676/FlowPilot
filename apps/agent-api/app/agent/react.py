"""The ReAct execution primitive used by later policy-bound request paths."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.traces.recorder import safe_code, visible_content


class ToolCallGuard(AgentMiddleware):
    def __init__(
        self,
        validate: Callable[[list[dict[str, Any]]], None],
        select_tools=None,
        response_instructions=None,
        trace=None,
    ) -> None:
        self._validate = validate
        self._select_tools = select_tools
        self._response_instructions = response_instructions
        self._trace = trace

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
        trace = self._trace
        if trace:
            trace.step += 1
            await trace.emit(
                "system_message",
                {
                    "content": visible_content(request.system_message.content)
                    if request.system_message
                    else ""
                },
            )
            await trace.emit(
                "model_start",
                {"tools": [getattr(tool, "name", "unknown") for tool in request.tools]},
            )
        started = perf_counter()
        try:
            result = await handler(request)
        except Exception as error:
            if trace:
                await trace.emit(
                    "model_error",
                    {
                        "error_code": safe_code(error, "MODEL_FAILED"),
                        "duration_ms": round((perf_counter() - started) * 1000),
                    },
                )
            raise
        if trace:
            response = getattr(result, "model_response", result)
            messages = getattr(response, "result", [response])
            for message in messages:
                await trace.emit(
                    "assistant_message",
                    {
                        "content": visible_content(message.content),
                        "duration_ms": round((perf_counter() - started) * 1000),
                    },
                )
                for call in getattr(message, "tool_calls", []):
                    await trace.emit("tool_call", trace.tool(call), call_id=call.get("id", ""))
        return result

    async def aafter_model(self, state: Any, runtime: Any) -> None:
        calls = getattr(state["messages"][-1], "tool_calls", [])
        try:
            self._validate(calls)
        except Exception as error:
            if self._trace:
                for call in calls:
                    await self._trace.emit(
                        "tool_rejected",
                        {
                            **self._trace.tool(call),
                            "error_code": safe_code(error, "POLICY_REJECTED"),
                            "executed": False,
                        },
                        call_id=call.get("id", ""),
                    )
            raise

    async def awrap_tool_call(self, request, handler):
        trace = self._trace
        call = request.tool_call
        step = trace.step if trace else 0
        if trace:
            await trace.emit("tool_start", trace.tool(call), call_id=call.get("id", ""), step=step)
        started = perf_counter()
        try:
            result = await handler(request)
        except Exception as error:
            if trace:
                await trace.emit(
                    "tool_error",
                    {
                        **trace.tool(call),
                        "error_code": safe_code(error, "TOOL_FAILED"),
                        "duration_ms": round((perf_counter() - started) * 1000),
                    },
                    call_id=call.get("id", ""),
                    step=step,
                )
            raise
        if trace:
            await trace.emit(
                "tool_result",
                {
                    **trace.tool(call),
                    "content": (
                        {"error_code": "TOOL_REPORTED_ERROR"}
                        if getattr(result, "status", "") == "error"
                        else visible_content(getattr(result, "content", ""))
                    ),
                    "status": getattr(result, "status", "unknown"),
                    "duration_ms": round((perf_counter() - started) * 1000),
                },
                call_id=call.get("id", ""),
                step=step,
            )
        return result


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
        recursion_limit: int = 30,
        trace=None,
    ) -> None:
        self._recursion_limit = recursion_limit
        middleware = (
            [
                ToolCallGuard(
                    validate_tool_calls or (lambda calls: None),
                    select_tools,
                    response_instructions,
                    trace,
                )
            ]
            if validate_tool_calls or trace
            else []
        )
        self._graph = create_agent(
            model=model, tools=list(tools), system_prompt=system_prompt, middleware=middleware
        )

    async def invoke(self, message: str) -> str:
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"recursion_limit": self._recursion_limit},
        )
        content = result["messages"][-1].content
        return content if isinstance(content, str) else str(content)
