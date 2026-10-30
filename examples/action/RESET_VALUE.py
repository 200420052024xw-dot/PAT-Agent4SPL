"""Clear stale evidence/feedback through an explicit IR result write."""


def execute(context, value) -> dict:
    return {"execution_status": "success", "result": value}
