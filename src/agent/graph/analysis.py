# load_context and ChatService set every key this node reads (see graph/build.py).
# pyright: reportTypedDictNotRequiredAccess=false
"""The analysis node: a ReAct loop over the analysis tools (ADR 0004).

The loop keeps its own message list. Tool calls and their results (rows included) live only
there, for the steps of this turn; the node returns the final answer and ``ResultSummary``s,
so no row ever enters ``AgentState`` or a checkpoint (LLD 03 § What never enters state).
"""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

from agent.composition import Ports
from agent.config import Limits
from agent.contracts.tools import GetSchemaInput, RunSqlInput, RunSqlOk, ToolError
from agent.core.catalog import render
from agent.domain.query import ResultSummary
from agent.errors import MessageClass
from agent.graph.prompts import analyst_system, tool_result
from agent.graph.state import AgentState
from agent.ports.llm import ModelStep
from agent.tools.analysis import get_schema, run_sql, scope_note
from agent.tools.context import SqlBudget, TurnContext


def _spec(name: str, description: str, args: type[BaseModel]) -> dict[str, Any]:
    """An OpenAI-format tool definition: the model sees only the ``*Input`` schema, with its
    ``$defs`` inlined (Gemini's function declarations do not resolve references)."""
    spec = convert_to_openai_tool(args)
    spec["function"] |= {"name": name, "description": description}
    return spec


BOUND_TOOLS = [
    _spec(
        "get_schema",
        "The tables and columns you may query, with descriptions. Omit tables for all four.",
        GetSchemaInput,
    ),
    _spec(
        "run_sql",
        "Run one BigQuery SELECT and get the rows back. A LIMIT is added for you.",
        RunSqlInput,
    ),
]
"""Slice 1 binds two of the six ``ANALYSIS_TOOLS``; the rest are bound with their slice."""


class _Turn:
    """One turn's tool state: the context and the SQL budget the calls share."""

    def __init__(self, ctx: TurnContext) -> None:
        self.ctx = ctx
        self.budget = SqlBudget()
        self.summaries: list[ResultSummary] = []
        self.ended: MessageClass | None = None
        """Set when a tool returned a ``ToolError``: the turn ends with that catalog message."""

    def call(self, call: ToolCall) -> str:
        tools: dict[str, Callable[[dict[str, Any]], BaseModel]] = {
            "get_schema": lambda a: get_schema(GetSchemaInput.model_validate(a), self.ctx),
            "run_sql": self._run_sql,
        }
        name = call["name"]
        tool = tools.get(name)
        if tool is None:  # a made-up name never reaches the prompt
            return tool_result("unknown", json.dumps({"status": "error", "detail": "no such tool"}))
        try:
            out = tool(call["args"])
        except ValidationError as exc:
            detail = exc.errors(include_url=False, include_context=False, include_input=False)
            return tool_result(name, json.dumps({"status": "error", "detail": detail}, default=str))
        if isinstance(out, ToolError):
            self.ended = out.message_class
        return tool_result(name, out.model_dump_json())

    def _run_sql(self, args: dict[str, Any]) -> BaseModel:
        out, self.budget = run_sql(RunSqlInput.model_validate(args), self.ctx, self.budget)
        if isinstance(out, RunSqlOk):
            self.summaries.append(ResultSummary.of(out.result))
        return out


def analysis(state: AgentState, ports: Ports, limits: Limits) -> dict[str, Any]:
    user = state["user"]
    assert user is not None, "load_context refuses a turn without a User"
    persona = state["persona"].persona
    turn = _Turn(
        TurnContext(
            user=user,
            conversation_id=state["conversation_id"],
            turn_id=state["turn_id"],
            ports=ports,
            limits=limits,
        )
    )
    model = ports.llm.chat(ModelStep.ANALYST).bind_tools(BOUND_TOOLS)
    convo: list[BaseMessage] = [
        analyst_system(persona, scope_note(user.scope), state.get("trios", [])),
        *state["messages"],
    ]
    answer: str | None = None
    for _ in range(limits.max_analysis_steps):
        reply = model.invoke(convo)
        assert isinstance(reply, AIMessage)
        convo.append(reply)
        if not reply.tool_calls:
            answer = reply.text
            break
        for call in reply.tool_calls:
            convo.append(ToolMessage(content=turn.call(call), tool_call_id=call["id"] or ""))
        if turn.ended:  # a permanent refusal: no more model steps
            break
    message_class = turn.ended
    if answer is None:  # the step cap: answer from the catalog, not from a half-done loop
        message_class = message_class or MessageClass.SELF_CORRECTION_EXHAUSTED
    if message_class is not None:
        answer = render(message_class, persona, ref=state["trace_id"])
    return {
        "messages": [AIMessage(content=answer)],
        "message_class": message_class,
        "analysis_steps": sum(isinstance(m, AIMessage) for m in convo),
        "sql_attempts": turn.budget.attempts,
        "sql_retries": turn.budget.retries_used,
        "empty_diagnoses": turn.budget.diagnoses_used,
        "ran_sql": bool(turn.summaries),
        "last_query_scope": turn.budget.last_scope,
        "result_summaries": turn.summaries,
    }
