"""Browser-based text/voice input skill.

Mirrors the cure.skills.listen signature (returns a transcript string) but
sources the transcript from the dashboard instead of the robot's on-device ASR.

The SSE stream handler registers a request callback before running the
workflow; when the skill is invoked, it fires the callback (which emits an
'await_input' SSE event to the frontend) and blocks until POST
/api/workflow/input delivers a response (or it times out).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# session_id -> {"event": threading.Event, "result": str}
_pending: dict[str, dict] = {}

# session_id -> callback(prompt: str) — invoked when a skill call needs input.
# Set by the SSE stream handler before running the workflow.
_request_handlers: dict[str, Callable[[str], None]] = {}


def register_request_handler(session_id: str, handler: Callable[[str], None]) -> None:
    """Register an SSE-side callback that fires when a skill needs browser input."""
    _request_handlers[session_id] = handler


def unregister_request_handler(session_id: str) -> None:
    """Remove the callback when the SSE stream ends."""
    _request_handlers.pop(session_id, None)


def deliver_input(session_id: str, text: str) -> bool:
    """Unblock a waiting skill with the submitted text.

    Called by POST /api/workflow/input. Returns True if a skill was waiting.
    """
    pending = _pending.get(session_id)
    if not pending:
        return False
    pending["result"] = text
    pending["event"].set()
    return True


def browser_input_skill(session_id: str, prompt: str, *, timeout_s: float = 120.0) -> str:
    """Request text input from the dashboard and block until it arrives.

    Returns the submitted text, or an empty string on timeout / no handler.
    Interface parity with cure.skills.listen.listen_skill: takes no extra
    dependencies from the caller beyond the state it already has, returns a
    plain transcript string.
    """
    import os
    # DRY_RUN by itself still blocks here so the operator can verify the
    # await_input UX. Set DRY_RUN_AUTORESPOND=1 to auto-complete (used by
    # scripts/verify_workflow.mjs for unattended E2E runs).
    if os.environ.get("DRY_RUN_AUTORESPOND", "").lower() in ("1", "true", "yes", "on"):
        canned = os.environ.get("DRY_RUN_TRANSCRIPT", "好的，我是病患張小明")
        logger.info("[DRY_RUN_AUTORESPOND] browser_input_skill(%s) → %r", session_id, canned)
        return canned
    evt = threading.Event()
    _pending[session_id] = {"event": evt, "result": ""}

    handler = _request_handlers.get(session_id)
    if handler is None:
        logger.warning(
            "browser_input_skill: no SSE handler registered for session %s — "
            "will time out without emitting await_input event",
            session_id,
        )
    else:
        try:
            handler(prompt)
        except Exception as e:
            logger.error("browser_input_skill: handler raised %s", e)

    got = evt.wait(timeout=timeout_s)
    result = _pending.pop(session_id, {}).get("result", "")
    if not got:
        logger.warning("browser_input_skill: timed out after %.0fs for session %s",
                       timeout_s, session_id)
        return ""
    return result
