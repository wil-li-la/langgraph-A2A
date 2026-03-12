"""Healthcare-related functionality."""

# Use lazy imports to avoid a RuntimeWarning when running
#   python -m app.healthcare.medication_delivery
# Eager imports here cause medication_delivery to appear in sys.modules before
# Python's -m machinery executes it.

__all__ = [
    "MedicationDeliveryAgent",
    "MockDatabase",
    "MockRobotActions",
    "MockNLU",
]


def __getattr__(name: str):
    if name == "MedicationDeliveryAgent":
        from app.healthcare.medication_delivery import MedicationDeliveryAgent
        return MedicationDeliveryAgent
    if name in ("MockDatabase", "MockRobotActions", "MockNLU"):
        from app.healthcare import mock_data
        return getattr(mock_data, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
