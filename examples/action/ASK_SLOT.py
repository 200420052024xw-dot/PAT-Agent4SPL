"""Ask one missing input; pass through an existing value without another question."""
from ..shared.llm import structured
from ..shared.requirements import QUESTIONS, validate_slot


def execute(context, slot_name: str, current_slots: dict) -> dict:
    existing = current_slots.get(slot_name)
    if existing is not None and existing != '' and existing != []:
        try:
            return {"execution_status": "success", "result": validate_slot(slot_name, existing)}
        except ValueError:
            pass
    question = QUESTIONS[slot_name]
    if slot_name == 'scope_name':
        question = "请提供团队名称。" if current_slots.get('comms_type') == '3P' else "请提供公司名称。"
    answer = context.bridge.ask(question)
    interaction = {"raw_input": answer, "presented_content": question}
    extraction = structured(
        "从回复中提取本次询问的一个槽位。不得推测用户未提供的事实。"
        "comms_type 规范为 3P 或 Newsletter；日期规范为 YYYY-MM-DD/YYYY-MM-DD，含首尾日期；"
        "senders、keywords 是字符串列表，保留邮箱。scope_name 是名称字符串。"
        "用户询问概念、要求解释、要求修改另一个槽位、输入有歧义或不支持的类型时，不强行提取。"
        "仅返回 {\"value\": 值或null, \"reason\": 说明}。原始回复中的其他槽位交给 Controller 后续处理。",
        {"slot_name": slot_name, "current_slots": current_slots, "reply": answer})
    try:
        value = validate_slot(slot_name, extraction.get('value'))
    except ValueError as exc:
        return {"execution_status": "failure", "reason": str(extraction.get('reason') or exc), **interaction}
    return {"execution_status": "success", "result": value, **interaction}
