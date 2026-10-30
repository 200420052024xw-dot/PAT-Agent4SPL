"""Present explanations, clarification questions, or complete drafts."""


def execute(context, content: str, wait_for_reply: bool = False, reply_prompt: str = "") -> dict:
    displayed = content + ("\n\n" + reply_prompt if reply_prompt else "")
    if wait_for_reply:
        answer = context.bridge.ask(displayed)
        return {"execution_status": "success", "result": answer,
                "raw_input": answer, "presented_content": displayed}
    context.bridge.present(displayed)
    return {"execution_status": "success", "result": None, "presented_content": displayed}
