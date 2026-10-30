"""动作写回的有限结果展开。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import Any

from src.model import Effect, Expr, FiniteVariable


@dataclass(frozen=True)
class EffectChoice:
    guard: Expr
    effects: tuple[Effect, ...]
    label: str


def build_effect_choices(step: dict[str, Any], action: dict[str, Any], domains: dict[str, FiniteVariable], semantic: Any = None) -> list[EffectChoice]:
    """返回所有与明确写回约束一致的有限结果。"""
    write = step.get("when_success_write_to")
    if write is None:
        dynamic = _dynamic_from_action(action, domains)
        if dynamic[0].effects or semantic is None or not hasattr(semantic, "resolve_effects"):
            return dynamic
        proposal = semantic.resolve_effects({"action": action, "step": step, "domains": _domain_view(domains)})
        return _semantic_choices(proposal, action, domains)
    if not isinstance(write, dict):
        raise ValueError("when_success_write_to 必须是对象或 null")
    operation = write.get("operation", "overwrite")
    target = write.get("target", "")
    if not isinstance(target, str) or not target.startswith("slots."):
        raise ValueError("写回目标必须引用 slots.<槽位>")
    slot = target.removeprefix("slots.")
    if slot not in domains:
        raise ValueError(f"写回目标 {slot} 不存在")
    value = step.get("parameters", {}).get("value", _UNKNOWN)
    if operation == "overwrite":
        choices = _overwrite_choices(slot, value, domains[slot])
        spec = domains[slot].slot_types.get(slot, {})
        if spec.get("type") == "enum":
            return choices
        if value is _UNKNOWN and semantic is not None and hasattr(semantic, "resolve_effects"):
            proposal = semantic.resolve_effects({"action": action, "step": step, "domains": {slot: _domain_view(domains)[slot]}})
            choices = _restrict_choices(choices, proposal, action, step, slot)
        return choices
    if operation in {"append", "remove"}:
        return _list_update_choices(slot, value, operation, domains[slot])
    raise ValueError(f"未知写回操作 {operation}")


_UNKNOWN = object()


def _overwrite_choices(slot: str, value: Any, domain: FiniteVariable) -> list[EffectChoice]:
    unknown = value is _UNKNOWN or (isinstance(value, str) and value.startswith("@"))
    if domain.joint:
        from .updates import joint_overwrites

        return [EffectChoice(guard, (Effect(domain.name, index),), f"{slot}_{index}") for guard, index in joint_overwrites(slot, domain, value, unknown)]
    if unknown:
        indexes = domain.success_values if domain.success_values is not None else range(len(domain.values))
    else:
        mapped = domain.value_for(value)
        if mapped is None:
            raise ValueError(f"字面值无法进入槽位 {slot} 的有限域")
        indexes = [mapped]
    return [EffectChoice(Expr("true"), (Effect(domain.name, index),), f"{slot}_{index}") for index in indexes]


def _dynamic_from_action(action: dict[str, Any], domains: dict[str, FiniteVariable]) -> list[EffectChoice]:
    """识别描述中明确写出的有限参数化目标。"""
    description = str(action.get("description", ""))
    match = re.search(r"slots\.<([A-Za-z_][A-Za-z0-9_]*)>", description)
    if not match:
        return [EffectChoice(Expr("true"), (), "none")]
    spec = action.get("parameters", {}).get(match.group(1), {})
    candidates = spec.get("enum_values", []) if isinstance(spec, dict) else []
    if not candidates or any(slot not in domains for slot in candidates):
        raise ValueError("动态写回目标没有有效的有限候选集合")
    choices: list[EffectChoice] = []
    for slot in candidates:
        choices.extend(_overwrite_choices(slot, _UNKNOWN, domains[slot]))
    return choices


def _list_update_choices(slot: str, value: Any, operation: str, domain: FiniteVariable) -> list[EffectChoice]:
    from .updates import list_updates

    if domain.slot_types.get(slot, {}).get("type", "list") != "list":
        raise ValueError(f"{operation} 只适用于列表槽位")
    unknown = value is _UNKNOWN or (isinstance(value, str) and value.startswith("@"))
    return [EffectChoice(guard, (Effect(domain.name, after),), f"{slot}_{after}") for guard, after in list_updates(slot, domain, value, operation, unknown)]


def _domain_view(domains: dict[str, FiniteVariable]) -> dict[str, Any]:
    return {name: [{"index": index, "sample": state.sample, "missing": state.missing} for index, state in enumerate(domain.values)] for name, domain in domains.items()}


def _restrict_choices(choices: list[EffectChoice], proposal: Any, action: dict[str, Any], step: dict[str, Any], slot: str) -> list[EffectChoice]:
    if not isinstance(proposal, dict):
        raise ValueError("AI 效果提议不是对象")
    effects = proposal.get("effects", [])
    if not effects:
        return choices
    if len(effects) != 1 or effects[0].get("target") != f"slots.{slot}":
        raise ValueError("AI 效果与明确写回目标冲突")
    quote = str(proposal.get("source_quote", ""))
    if not quote or quote not in str(action) + str(step):
        return choices
    allowed = effects[0].get("allowed")
    if not isinstance(allowed, list) or not allowed or any(type(item) is not int for item in allowed):
        raise ValueError("AI 效果有限结果无效")
    narrowed = [choice for choice in choices if choice.effects[0].value in allowed]
    if {choice.effects[0].value for choice in narrowed} != set(allowed):
        raise ValueError("AI 效果引用超出有限域的值")
    return narrowed


def _semantic_choices(proposal: Any, action: dict[str, Any], domains: dict[str, FiniteVariable]) -> list[EffectChoice]:
    if not isinstance(proposal, dict) or not isinstance(proposal.get("effects"), list):
        raise ValueError("AI 效果输出无效")
    entries = proposal["effects"]
    if not entries:
        return [EffectChoice(Expr("true"), (), "none")]
    quote = str(proposal.get("source_quote", ""))
    if not quote or quote not in str(action):
        raise ValueError("AI 效果没有动作原文依据")
    options: list[list[EffectChoice]] = []
    for entry in entries:
        slot = str(entry.get("target", "")).removeprefix("slots.")
        if slot not in domains:
            raise ValueError("AI 效果写入未知槽位")
        allowed = entry.get("allowed", [])
        if not isinstance(allowed, list) or not allowed or any(type(item) is not int or item < 0 or item >= len(domains[slot].values) for item in allowed):
            raise ValueError("AI 效果取值超出有限域")
        candidates = _overwrite_choices(slot, _UNKNOWN, domains[slot])
        options.append([choice for choice in candidates if choice.effects[0].value in allowed])
        if not options[-1]:
            raise ValueError("AI 效果没有合法成功写回结果")
    variables = [domains[str(entry["target"]).removeprefix("slots.")].name for entry in entries]
    if len(set(variables)) != len(variables):
        raise ValueError("AI 效果重复写同一有限变量，请提供明确的联合更新")
    choices = []
    for index, combination in enumerate(product(*options)):
        guard = Expr("and", tuple(choice.guard for choice in combination))
        effects = tuple(effect for choice in combination for effect in choice.effects)
        choices.append(EffectChoice(guard, effects, f"semantic_{index}"))
    return choices
