"""Generalist ReAct agent that composes cure robot skills via LLM tool-use.

Unlike the scripted MedicationDeliveryAgent (app.healthcare.medication_delivery)
which executes a fixed 9-node DAG, this agent receives a free-form natural
language task ("fetch my water bottle", "deliver aspirin to Mr. Wang") and
chooses tool calls itself.

Determinism is preserved by the RobotGuard wrapping every tool call:
preconditions and a per-task budget cannot be bypassed by the LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Generator, Optional, Tuple

from langgraph.prebuilt import create_react_agent

from app.llm import get_llm
from app.safety import RobotGuard, set_guard, reset_guard
from app.tools import get_robot_tools, build_world_summary

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a hospital service robot (HelloRobot Stretch).
You complete the user's task by calling the available tools, one at a time.

ENVIRONMENT
{world_summary}

OPERATING PRINCIPLES
- If you are unsure what locations or objects exist, call what_can_i_see first.
- Use speak() to address the human and listen() to hear their reply.
- Before handing a medication to a patient, verify their identity by asking
  for their name with speak() and confirming via listen().
- A tool result starting with BLOCKED:, FAILED:, or UNKNOWN_LOCATION: means
  the action did NOT happen. Do not retry the same call — change your plan
  or ask the user.
- You have a strict tool-call budget. Be efficient. Plan, then act.
- When the task is complete (or you have determined you cannot complete it),
  STOP calling tools and produce a final natural-language summary.

You may use Traditional Chinese or English depending on the user's language.
"""


# Reason string surfaced via /api/agent/info when the agent cannot start.
AGENT_AVAILABLE_REASON = (
    "Set LLM_PROVIDER (e.g. 'ollama' with OLLAMA_HOST/OLLAMA_MODEL) "
    "to enable the agentic execution path."
)


class DeliveryAgent:
    """Lazily-built ReAct agent. Construction requires a configured LLM."""

    def __init__(
        self,
        llm: Any | None = None,
        budget: int = 30,
        recursion_limit: int = 50,
    ) -> None:
        if llm is None:
            llm = get_llm()
        if llm is None:
            raise RuntimeError(
                "Cannot build DeliveryAgent: LLM is not configured. "
                + AGENT_AVAILABLE_REASON
            )
        self.llm = llm
        self.tools = get_robot_tools()
        self.budget = budget
        self.recursion_limit = recursion_limit
        prompt = SYSTEM_PROMPT.format(world_summary=build_world_summary())
        self.app = create_react_agent(self.llm, self.tools, prompt=prompt)

    # ----- one-shot ----------------------------------------------------

    def execute(self, task: str) -> dict:
        """Run the agent to completion for `task`. Blocking."""
        guard = RobotGuard(budget=self.budget)
        token = set_guard(guard)
        start = time.time()
        try:
            result = self.app.invoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": self.recursion_limit},
            )
        finally:
            reset_guard(token)

        return _summarize_result(task, result, guard, time.time() - start)

    # ----- streaming ---------------------------------------------------

    def stream_execute(
        self, task: str
    ) -> Generator[Tuple[str, dict], None, None]:
        """Yield agent events as they happen.

        Event shapes (event_name, payload):
          ("started",     {"task": str, "budget": int})
          ("agent_message", {"text": str, "tool_calls": [...]})  # LLM said something
          ("tool_call",   {"name": str, "args": dict, "id": str})
          ("tool_result", {"name": str, "id": str, "result": str})
          ("done",        {full summary dict})
          ("error",       {"error": str})
        """
        guard = RobotGuard(budget=self.budget)
        token = set_guard(guard)
        start = time.time()
        last_message_count = 0
        final_state: dict = {}

        yield ("started", {"task": task, "budget": self.budget})

        try:
            try:
                stream = self.app.stream(
                    {"messages": [("user", task)]},
                    config={"recursion_limit": self.recursion_limit},
                    stream_mode="values",
                )
                for state in stream:
                    final_state = state
                    messages = state.get("messages", [])
                    new = messages[last_message_count:]
                    last_message_count = len(messages)
                    for ev in _classify_messages(new):
                        yield ev
            except Exception as e:
                logger.error("Agent stream failed: %s", e, exc_info=True)
                yield ("error", {"error": str(e)})
                return

            yield ("done", _summarize_result(task, final_state, guard, time.time() - start))
        finally:
            reset_guard(token)


# ---------- helpers -------------------------------------------------------


def _summarize_result(task: str, result: dict, guard: RobotGuard, elapsed: float) -> dict:
    """Squash a langgraph result + guard snapshot into a JSON-serializable dict."""
    messages = result.get("messages", []) if isinstance(result, dict) else []
    tool_calls: list[dict] = []
    final_text = ""
    for m in messages:
        for ev in _classify_messages([m]):
            if ev[0] == "tool_call":
                tool_calls.append(ev[1])
        # remember last assistant text as the natural-language outcome
        text, _ = _extract_text_and_tool_calls(m)
        if text and _msg_role(m) == "assistant":
            final_text = text

    return {
        "task": task,
        "summary": final_text,
        "tool_calls": tool_calls,
        "robot_state": guard.snapshot(),
        "elapsed_seconds": round(elapsed, 2),
        "message_count": len(messages),
    }


def _classify_messages(messages: list[Any]) -> list[Tuple[str, dict]]:
    """Turn raw langchain messages into agent stream events."""
    out: list[Tuple[str, dict]] = []
    for m in messages:
        role = _msg_role(m)
        text, tool_calls = _extract_text_and_tool_calls(m)
        if role == "tool":
            out.append((
                "tool_result",
                {
                    "name": getattr(m, "name", "") or "",
                    "id": getattr(m, "tool_call_id", "") or "",
                    "result": text,
                },
            ))
        elif role == "assistant":
            if text or tool_calls:
                out.append((
                    "agent_message",
                    {"text": text, "tool_calls": tool_calls},
                ))
            for tc in tool_calls:
                out.append(("tool_call", tc))
        # user messages from initial input — not surfaced as events
    return out


def _msg_role(m: Any) -> str:
    """Best-effort mapping from langchain message type to a simple role string."""
    cls = type(m).__name__
    if cls == "HumanMessage":
        return "user"
    if cls == "AIMessage":
        return "assistant"
    if cls == "ToolMessage":
        return "tool"
    if cls == "SystemMessage":
        return "system"
    return getattr(m, "type", "") or cls.lower()


def _extract_text_and_tool_calls(m: Any) -> tuple[str, list[dict]]:
    """Pull plain text + tool calls out of a langchain message."""
    content = getattr(m, "content", "") or ""
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif isinstance(part, str):
                text_parts.append(part)
        text = "".join(text_parts)
    else:
        text = str(content)

    tool_calls_raw = getattr(m, "tool_calls", None) or []
    tool_calls = [
        {
            "id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", ""),
            "name": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
            "args": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {}),
        }
        for tc in tool_calls_raw
    ]
    return text.strip(), tool_calls
