"""联合抽象接口的固定回答，用于检查调用次数和状态语义。"""

from copy import deepcopy

from src.conversion.rego import evaluate


class JointSemantic:
    """用明确代表值模拟 LLM，不调用在线服务。"""

    def __init__(self, states=None):
        self.states = states or [
            {"name": "EMPTY", "sample": []},
            {"name": "SHORT_OTHER", "sample": ["x"]},
            {"name": "SHORT_A", "sample": ["a"]},
            {"name": "LONG_OTHER", "sample": ["x", "y"]},
            {"name": "LONG_A", "sample": ["a", "x"]},
        ]
        self.calls = []

    def resolve_abstraction(self, context):
        self.calls.append(context)
        states = deepcopy(self.states)
        name = next(iter(context["slots"]))
        guards = []
        for observation in context["observations"]:
            cases = []
            for status in observation.get("status_cases", [{}]):
                answers = []
                for state in states:
                    slots = state["sample"] if context["joint"] else {} if state.get("missing") else {name: state["sample"]}
                    answers.append(evaluate(observation["expression"], slots, context["rego"], status))
                cases.append({"status": status, "allowed": [i for i, value in enumerate(answers) if value is True],
                              "undefined": [i for i, value in enumerate(answers) if value is None]})
            body = {"cases": cases} if cases[0]["status"] else {key: cases[0][key] for key in ("allowed", "undefined")}
            guards.append({"id": observation["id"], **body})
        return {"states": states, "guards": guards, "reason": "固定代表值覆盖本测试中的全部判断组合"}

    def resolve_failure(self, context):
        return {"failure_behavior": "unresolved", "reason": "本测试不补充失败去向"}


def controller(slots, guards, step=None, description="测试流程"):
    """构造只包含一个普通阶段和一个终止阶段的小模型。"""
    return {"description": description, "slot_information": slots, "participants": {}, "policies": {},
            "actions": {"DO": {"description": description}},
            "stages": {"initial": {"kind": "initial", "description": description,
                                  "entry": None, "doActivity": step, "exit": None,
                                  "transitions": [{"target": "done", "guard": guard} for guard in guards]},
                       "done": {"kind": "termination", "transitions": []}}}


def action(value=None, target=None):
    """按需声明动作结果写回，调用者可以移除 value 表示未知结果。"""
    step = {"action": "DO", "parameters": {"value": value}, "when_success_write_to": None}
    if target:
        step["when_success_write_to"] = {"operation": "overwrite", "target": "slots." + target}
    return step
