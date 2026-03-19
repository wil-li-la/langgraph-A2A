"""Template A2A JSON-RPC client for this backend.

Usage:
  python a2a_template_client.py "What can you do?"
  A2A_ENDPOINT="http://10.0.0.12:9999/" python a2a_template_client.py "請將阿斯匹靈送給張小明"
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_ENDPOINT = os.getenv("A2A_ENDPOINT", "http://localhost:9999/")
DEFAULT_ROLE = os.getenv("A2A_CALLER_ROLE", "agent").strip().lower()


def normalize_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            "Endpoint must be a full URL, e.g. http://localhost:9999/ "
            "or http://10.x.x.x:9999/"
        )
    path = parsed.path or "/"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def extract_first_text(payload: Any) -> str:
    """Find first text part in task artifacts/status/history."""
    if isinstance(payload, dict):
        if payload.get("kind") == "text" and isinstance(payload.get("text"), str):
            return payload["text"]
        for value in payload.values():
            text = extract_first_text(value)
            if text:
                return text
    elif isinstance(payload, list):
        for item in payload:
            text = extract_first_text(item)
            if text:
                return text
    return ""


def fetch_agent_card(base_endpoint: str) -> dict[str, Any] | None:
    parsed = urlparse(base_endpoint)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        try:
            resp = requests.get(base + path, timeout=8)
            if resp.ok:
                return resp.json()
        except requests.RequestException:
            continue
    return None


def send_message(endpoint: str, message: str, role: str = "agent") -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": str(uuid.uuid4()),
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": role,
                "parts": [{"kind": "text", "text": message}],
            }
        },
    }
    response = requests.post(endpoint, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A JSON-RPC template client")
    parser.add_argument("message", nargs="?", default="What can you do?")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--role", default=DEFAULT_ROLE, choices=["agent", "user"])
    args = parser.parse_args()

    endpoint = normalize_endpoint(args.endpoint)
    card = fetch_agent_card(endpoint)
    if card:
        print("Agent card:")
        print(f"- name: {card.get('name')}")
        print(f"- version: {card.get('version')}")
        print(f"- skills: {[s.get('id') for s in card.get('skills', [])]}")
    else:
        print("Agent card: not reachable via well-known paths")

    try:
        result = send_message(endpoint, args.message, role=args.role)
        text = extract_first_text(result)
        print("\nA2A response text:")
        print(text if text else "(No text part found)")
        print("\nRaw JSON:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except requests.Timeout:
        print("\nError: request timed out")
    except requests.RequestException as exc:
        print(f"\nError: failed to call A2A endpoint: {exc}")
    except ValueError as exc:
        print(f"\nError: invalid JSON response: {exc}")


if __name__ == "__main__":
    main()
