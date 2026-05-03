"""LLM provider factory.

Reads LLM_PROVIDER env (none|ollama|openai|google|anthropic) and returns
a LangChain BaseChatModel, or None when the provider is "none" or
unavailable. Callers MUST handle the None case with deterministic
fallback so the service keeps working with no LLM at all.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised by callers that wrap LLM use; the factory itself never raises."""


_cache_lock = Lock()
_cached_llm: object | None = None
_cached_signature: tuple | None = None


def _signature() -> tuple:
    return (
        os.getenv("LLM_PROVIDER", "none").lower(),
        os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"),
        os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        os.getenv("LLM_TIMEOUT_SECONDS", "30"),
    )


def get_llm() -> Optional[object]:
    """Return a cached LangChain chat model, or None.

    None when LLM_PROVIDER=none, when the provider's package isn't
    installed, or when construction fails. Never raises.
    """
    global _cached_llm, _cached_signature

    sig = _signature()
    with _cache_lock:
        if _cached_signature == sig:
            return _cached_llm

        provider = sig[0]
        if provider in ("", "none", "off", "disabled"):
            _cached_llm = None
            _cached_signature = sig
            return None

        try:
            timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        except ValueError:
            timeout = 30.0

        llm = None
        try:
            if provider == "ollama":
                from langchain_ollama import ChatOllama

                llm = ChatOllama(
                    model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
                    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                    temperature=0,
                    timeout=timeout,
                )
            elif provider == "openai":
                from langchain_openai import ChatOpenAI

                llm = ChatOpenAI(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    temperature=0,
                    timeout=timeout,
                )
            elif provider == "google":
                from langchain_google_genai import ChatGoogleGenerativeAI

                llm = ChatGoogleGenerativeAI(
                    model=os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"),
                    temperature=0,
                    timeout=timeout,
                )
            elif provider == "anthropic":
                from langchain_anthropic import ChatAnthropic

                llm = ChatAnthropic(
                    model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
                    temperature=0,
                    timeout=timeout,
                )
            else:
                logger.warning("Unknown LLM_PROVIDER=%r; LLM disabled", provider)
                llm = None
        except ImportError as e:
            logger.warning(
                "LLM_PROVIDER=%s but its package isn't installed (%s); LLM disabled",
                provider,
                e,
            )
            llm = None
        except Exception as e:
            logger.warning(
                "Failed to construct LLM for provider=%s (%s); LLM disabled",
                provider,
                e,
            )
            llm = None

        if llm is not None:
            logger.info("LLM enabled: provider=%s", provider)

        _cached_llm = llm
        _cached_signature = sig
        return llm
