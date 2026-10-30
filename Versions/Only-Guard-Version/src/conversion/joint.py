"""一次 LLM 调用联合设计有限状态和判断映射，并核对代表值。"""

from __future__ import annotations

from dataclasses import asdict, replace
from itertools import product
import re
from typing import Any

from src.model import FiniteVariable, StateValue
from .domains import _identifier, _samples, valid_result
from .execution import result_cases, status_cases, status_steps
from .rego import MISSING, evaluate, rego_equal


def build_joint_domain(group: list[str], observations: list[Any], raw: dict[str, Any], semantic: Any, profile: dict[str, Any]) -> FiniteVariable:
    """复杂判断不再单独补 Guard，必须同时返回状态和判断表。"""
    if semantic is None or not hasattr(semantic, "resolve_abstraction"):
        raise ValueError(f"槽位 {group} 的跨类型或未知判断需要 resolve_abstraction 联合抽象")
    context = _context(group, observations, raw, profile)
    proposal = semantic.resolve_abstraction(context)
    domain = _read_domain(group, observations, raw, profile, proposal)
    _check_guards(domain, observations, proposal)
    _check_coverage(domain, context["candidate_states"])
    return domain


def _context(group: list[str], observations: list[Any], raw: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    used = {path.split("/")[2] for item in observations for path in item.sources if path.startswith("/stages/")}
    stages = {name: stage for name, stage in raw["stages"].items() if name in used or _uses_slot(stage, group)}
    action_names = {step["action"] for stage in stages.values() for key in ("entry", "doActivity", "exit") if (step := stage.get(key))}
    return {"slots": {name: raw["slot_information"][name] for name in group},
            "observations": [{**asdict(item), "status_cases": status_cases(item.expression)} for item in observations], "stages": stages,
            "actions": {name: raw["actions"][name] for name in action_names},
            "policies": raw.get("policies", {}), "description": raw.get("description", ""),
            "rego": profile, "joint": len(group) > 1,
            "candidate_states": _candidates(group, observations, raw)}


def _uses_slot(stage: dict[str, Any], group: list[str]) -> bool:
    """把只写入、不读取该槽位的 Stage 也交给联合抽象。"""
    for key in ("entry", "doActivity", "exit"):
        step = stage.get(key)
        if not step:
            continue
        write = step.get("when_success_write_to") or {}
        if write.get("target", "").removeprefix("slots.") in group:
            return True
    return False


def _candidates(group: list[str], observations: list[Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    options = []
    for name in group:
        atoms = [item.expression for item in observations if item.slots == [name] and item.category != "unknown"]
        options.append(_samples(raw["slot_information"][name], atoms))
    if _product_size(options) > 4096:
        raise ValueError("联合抽象候选超过 4096 个，请缩小相关槽位组")
    candidates = []
    for values in product(*options):
        if len(group) == 1:
            candidates.append({"sample": None if values[0] is MISSING else values[0], "missing": values[0] is MISSING})
        else:
            candidates.append({"sample": {name: value for name, value in zip(group, values) if value is not MISSING}})
    return candidates


def _product_size(options: list[list[Any]]) -> int:
    size = 1
    for values in options:
        size *= len(values)
    return size


def _read_domain(group: list[str], observations: list[Any], raw: dict[str, Any], profile: dict[str, Any], proposal: Any) -> FiniteVariable:
    if not isinstance(proposal, dict) or not str(proposal.get("reason", "")).strip():
        raise ValueError("联合抽象必须提供对象和 reason")
    states = proposal.get("states")
    if not isinstance(states, list) or not states or len(states) > 4096:
        raise ValueError("联合抽象 states 必须是非空有限数组")
    values = [_read_state(item, group, raw) for item in states]
    if len({state.name for state in values}) != len(values):
        raise ValueError("联合抽象状态名称重复")
    source = "/slot_information/" + group[0] if len(group) == 1 else "/abstraction_groups/" + "__".join(group)
    domain = FiniteVariable(_identifier("slot_" + "__".join(group)), source, values, 0, [item.expression for item in observations])
    domain.slot_types = {name: raw["slot_information"][name] for name in group}
    domain.joint, domain.profile, domain.reason = len(group) > 1, profile, proposal["reason"]
    default = _default(group, raw)
    domain.initial = domain.value_for(default)
    if domain.initial is None:
        raise ValueError("联合抽象未覆盖原默认值")
    if not domain.joint:
        domain.success_values = tuple(i for i, state in enumerate(values) if valid_result(state, domain.slot_types[group[0]]))
    return domain


def _read_state(item: Any, group: list[str], raw: dict[str, Any]) -> StateValue:
    if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item.get("name", ""))):
        raise ValueError("联合抽象状态必须有合法的具名标识")
    if "sample" not in item:
        raise ValueError("联合抽象状态缺少代表值 sample")
    sample = item["sample"]
    if len(group) > 1:
        if not isinstance(sample, dict) or set(sample) - set(group):
            raise ValueError("联合抽象组的 sample 必须是相关槽位组成的对象")
        for name in group:
            _check_sample(sample.get(name, MISSING), raw["slot_information"][name])
        return StateValue(item["name"], sample)
    missing = item.get("missing", False)
    if type(missing) is not bool or (missing and sample is not None):
        raise ValueError("missing 状态必须用 missing=true 和 sample=null 表示")
    _check_sample(MISSING if missing else sample, raw["slot_information"][group[0]])
    length = len(sample) if isinstance(sample, (str, list, dict)) else None
    return StateValue(item["name"], sample, length, missing=missing)


def _check_sample(sample: Any, slot: dict[str, Any]) -> None:
    if sample is MISSING:
        if "default_value" in slot and slot.get("allow_missing") is not True:
            raise ValueError("槽位不允许缺失状态")
    elif sample is None:
        if slot.get("default_value", MISSING) is not None and not slot.get("nullable") and None not in slot.get("enum_values", []):
            raise ValueError("槽位未声明可空，不能新增 null 状态")
    elif not valid_result(StateValue("sample", sample), slot):
        raise ValueError("联合抽象代表值与原槽位类型或枚举声明不一致")


def _default(group: list[str], raw: dict[str, Any]) -> Any:
    if len(group) == 1:
        return raw["slot_information"][group[0]].get("default_value", MISSING)
    return {name: raw["slot_information"][name]["default_value"] for name in group if "default_value" in raw["slot_information"][name]}


def _check_guards(domain: FiniteVariable, observations: list[Any], proposal: dict[str, Any]) -> None:
    guards = proposal.get("guards")
    if not isinstance(guards, list) or any(not isinstance(item, dict) for item in guards):
        raise ValueError("联合抽象必须同时提供 guards 数组")
    mapping = {item.get("id"): item for item in guards}
    if len(mapping) != len(guards) or set(mapping) != {item.id for item in observations}:
        raise ValueError("联合抽象判断编号重复、缺失或多余")
    for observation in observations:
        item = mapping[observation.id]
        if status_steps(observation.expression):
            _check_status_guards(domain, observation, item)
        else:
            answers = [evaluate(observation.expression, domain.sample_slots(state), domain.profile) for state in domain.values]
            _check_answers(observation.id, item, answers)
            domain.results[observation.expression] = answers


def _check_status_guards(domain: FiniteVariable, observation: Any, item: dict[str, Any]) -> None:
    """执行状态独立于槽位编码，每种组合都必须提供判断映射。"""
    proposed = item.get("cases")
    if not isinstance(proposed, list) or any(not isinstance(case, dict) for case in proposed):
        raise ValueError("含执行状态的联合 Guard 必须提供 cases 数组")
    actual = result_cases(observation.expression, domain)
    if len(proposed) != len(actual):
        raise ValueError("联合 Guard 的执行状态组合缺失或多余")
    for case in actual:
        matches = [candidate for candidate in proposed if candidate.get("status") == case["status"]]
        if len(matches) != 1:
            raise ValueError("联合 Guard 的执行状态组合重复或缺失")
        _check_answers(observation.id, matches[0], case["results"])
    domain.execution_results[observation.expression] = actual


def _check_answers(name: str, item: dict[str, Any], answers: list[bool | None]) -> None:
    """同时校验成立和未定义的状态，禁止把未定义直接当成假。"""
    allowed = _indexes(item.get("allowed"), len(answers))
    undefined = _indexes(item.get("undefined", []), len(answers))
    if set(allowed) != {i for i, value in enumerate(answers) if value is True}:
        raise ValueError(f"{name} 的 Guard 与代表值实际求值不一致")
    if set(undefined) != {i for i, value in enumerate(answers) if value is None}:
        raise ValueError(f"{name} 的 undefined 状态不一致")


def _indexes(values: Any, size: int) -> list[int]:
    if not isinstance(values, list) or any(type(value) is not int or not 0 <= value < size for value in values):
        raise ValueError("联合抽象判断索引超出有限状态域")
    if len(values) != len(set(values)):
        raise ValueError("联合抽象判断索引重复")
    return values


def _check_coverage(domain: FiniteVariable, candidates: list[dict[str, Any]]) -> None:
    """代表值覆盖检查可发现遗漏，不能证明业务语义的完备性。"""
    seen = {}
    for index, state in enumerate(domain.values):
        signature = domain.signature(MISSING if state.missing else state.sample)
        if signature in seen:
            raise ValueError("联合抽象含等价冗余状态，请合并相同判断组合")
        seen[signature] = index
        domain.values[index] = replace(state, representatives=(state.sample,))
    for candidate in candidates:
        sample = MISSING if candidate.get("missing") else candidate["sample"]
        signature = domain.signature(sample)
        if signature not in seen:
            raise ValueError("联合抽象遗漏了代码已知的合法判断组合")
        if sample is not MISSING:
            index = seen[signature]
            state = domain.values[index]
            if not any(rego_equal(sample, old) for old in state.representatives):
                domain.values[index] = replace(state, representatives=(*state.representatives, sample))
