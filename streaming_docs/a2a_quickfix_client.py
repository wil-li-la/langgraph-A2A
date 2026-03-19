"""Quick A2A smoke-test client (JSON-RPC endpoint).

Usage:
  python streaming_docs/a2a_quickfix_client.py "請將阿斯匹靈送給張小明"
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from urllib.parse import urlparse
from typing import Any

import requests


# WireGuard/private-network A2A JSON-RPC endpoint.
# Example: http://10.0.0.12:9999/
REMOTE_A2A_ENDPOINT = os.getenv("REMOTE_A2A_ENDPOINT", "http://localhost:9999/")
CALLER_ROLE = os.getenv("A2A_CALLER_ROLE", "agent").strip().lower()


def _extract_text(payload: Any) -> str:
    """Return the first text part found in a nested A2A response."""
    if isinstance(payload, dict):
        if payload.get("kind") == "text" and isinstance(payload.get("text"), str):
            return payload["text"]
        for value in payload.values():
            text = _extract_text(value)
            if text:
                return text
    elif isinstance(payload, list):
        for item in payload:
            text = _extract_text(item)
            if text:
                return text
    return ""


def _normalize_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            "REMOTE_A2A_ENDPOINT must be a full URL, e.g. "
            "'http://10.0.0.12:9999/'"
        )
    path = parsed.path or "/"
    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return normalized.geturl()


def _connectivity_precheck(endpoint: str) -> str | None:
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        return "Invalid endpoint: missing hostname."
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported scheme '{parsed.scheme}'. Use http or https."

    try:
        # Fast check to separate tunnel/network failures from A2A app errors.
        requests.get(f"{parsed.scheme}://{parsed.netloc}/agent-card", timeout=5)
    except requests.exceptions.RequestException as exc:
        return (
            "WireGuard/private endpoint is unreachable. "
            f"Check tunnel, route, and server bind/firewall. Details: {exc}"
        )
    return None


def call_remote_agent(message: str) -> str:
    """Calls a remote A2A agent via JSON-RPC method `message/send`."""
    if CALLER_ROLE not in {"agent", "user"}:
        return "A2A_CALLER_ROLE must be either 'agent' or 'user'."

    try:
        endpoint = _normalize_endpoint(REMOTE_A2A_ENDPOINT)
    except ValueError as exc:
        return str(exc)

    precheck_error = _connectivity_precheck(endpoint)
    if precheck_error:
        return precheck_error

    msg_id = str(uuid.uuid4())
    rpc_payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": str(uuid.uuid4()),
        "params": {
            "message": {
                "messageId": msg_id,
                "role": CALLER_ROLE,
                "parts": [{"kind": "text", "text": message}],
            }
        },
    }
    try:
        response = requests.post(endpoint, json=rpc_payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and "error" in data:
            msg = data["error"].get("message", "Unknown error")
            return f"Remote agent error: {msg}"

        text = _extract_text(data)
        if text:
            return text

        return (
            "Remote agent finished but returned no text.\n"
            f"Raw response: {json.dumps(data, ensure_ascii=False)[:500]}"
        )
    except requests.exceptions.Timeout:
        return "Remote agent request timed out."
    except requests.exceptions.RequestException as exc:
        return f"Failed to communicate with remote agent: {exc}"
    except ValueError as exc:
        return f"Invalid JSON response from remote agent: {exc}"


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]).strip() or "ping"
    print(call_remote_agent(query))
