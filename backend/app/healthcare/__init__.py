"""Healthcare-domain mock data (patients, medications, locations) + NLU.

The medication-delivery LangGraph workflow that consumes this data now
lives at `app.workflows.medication_delivery`. This package holds only the
domain constants and the keyword/LLM-backed instruction parser.
"""

# Lazy attribute access keeps `from app.healthcare import MockDatabase` etc.
# working without forcing mock_data to load on package import.

__all__ = [
    "MockDatabase",
    "MockRobotActions",
    "MockNLU",
]


def __getattr__(name: str):
    if name in __all__:
        from app.healthcare import mock_data
        return getattr(mock_data, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
