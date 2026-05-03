"""LLM provider abstraction.

`get_llm()` returns a LangChain BaseChatModel or None depending on
LLM_PROVIDER env. When None, callers must fall back to deterministic
behavior so the system stays functional without any LLM.
"""

from app.llm.factory import get_llm, LLMUnavailable

__all__ = ["get_llm", "LLMUnavailable"]
