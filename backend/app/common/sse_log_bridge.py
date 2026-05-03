"""Log bridging helper used by SSE stream endpoints.

Captures logs from a specific worker thread into an asyncio Queue, so the
HTTP handler coroutine can yield them as SSE 'log' events with correct
log levels.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import contextmanager
from typing import Iterator

# Attaching to the root logger is sufficient: Python logging propagates
# records from every child to the root by default. `app.workflows.*` and
# `cure.skills.*` both propagate (see _setup_cure_loggers in
# medication_delivery.py — it does not set propagate=False). Attaching to
# children as well would duplicate every record.
_CAPTURED_LOGGERS = ("",)


def _level_name(record: logging.LogRecord) -> str:
    """Map Python log level to one of 'info' / 'warning' / 'error'."""
    if record.levelno >= logging.ERROR:
        return "error"
    if record.levelno >= logging.WARNING:
        return "warning"
    return "info"


class ThreadLocalQueueHandler(logging.Handler):
    """A logging.Handler that enqueues records from one specific thread.

    Records are pushed to the given asyncio.Queue via `loop.call_soon_threadsafe`
    as dicts `{"type": "log", "text": str, "level": str}`. Records from other
    threads are ignored.
    """

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, thread_id: int):
        super().__init__(level=logging.INFO)
        self._queue = queue
        self._loop = loop
        self._thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if threading.get_ident() != self._thread_id:
            return
        text = record.getMessage().rstrip("\n")
        if not text.strip():
            return
        item = {"type": "log", "text": text, "level": _level_name(record)}
        try:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError:
            pass


@contextmanager
def bridge_logs_to_queue(
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    thread_id: int,
) -> Iterator[ThreadLocalQueueHandler]:
    """Attach a ThreadLocalQueueHandler to the captured loggers for the
    lifetime of the context, then detach on exit.

    Usage inside a worker thread:

        with bridge_logs_to_queue(q, loop, threading.get_ident()):
            # run workflow; all logger.info/warning/error from this thread
            # flow into `q` as {"type": "log", "text": ..., "level": ...}
            ...
    """
    handler = ThreadLocalQueueHandler(queue, loop, thread_id)
    attached: list[logging.Logger] = []
    try:
        for name in _CAPTURED_LOGGERS:
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            attached.append(lg)
        yield handler
    finally:
        for lg in attached:
            lg.removeHandler(handler)
