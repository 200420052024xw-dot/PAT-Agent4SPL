"""Apply one already stated requirement; never asks the same question again."""
from ..shared.requirements import validate_slot


def execute(context, slot_name: str, revised_value) -> dict:
    try:
        value = validate_slot(slot_name, revised_value)
    except ValueError as exc:
        return {"execution_status": "failure", "reason": str(exc)}
    return {"execution_status": "success", "result": value}
