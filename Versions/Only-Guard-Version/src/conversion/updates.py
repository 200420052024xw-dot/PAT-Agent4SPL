"""依据原值和更新操作生成一致的抽象写回。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.model import Effect, Expr, FiniteVariable, StateValue
from .domains import valid_result
from .rego import rego_equal


def joint_overwrites(slot: str, domain: FiniteVariable, value: Any, unknown: bool) -> list[tuple[Expr, int]]:
    """联合状态写一个槽位时，保留其他槽位已经观察到的信息。"""
    choices = []
    spec = domain.slot_types[slot]
    for before, old in enumerate(domain.values):
        if not unknown:
            after = domain.value_for({**old.sample, slot: value})
            if after is None:
                raise ValueError(f"联合抽象未覆盖槽位 {slot} 的具体写回")
            choices.append((Expr("in", variable=domain.name, allowed=(before,)), after))
            continue
        for after, candidate in enumerate(domain.values):
            if slot not in candidate.sample:
                continue
            result = candidate.sample[slot]
            if not valid_result(StateValue("result", result), spec):
                continue
            if _unchanged_components(slot, old.sample, candidate.sample, domain):
                choices.append((Expr("in", variable=domain.name, allowed=(before,)), after))
    if not choices:
        raise ValueError(f"联合抽象未覆盖槽位 {slot} 的写回结果")
    return choices


def _unchanged_components(changed: str, before: dict[str, Any], after: dict[str, Any], domain: FiniteVariable) -> bool:
    from .rego import evaluate, slot_names

    for name, spec in domain.slot_types.items():
        if name == changed:
            continue
        if (name in before) != (name in after):
            return False
        if spec["type"] in {"boolean", "enum"} and not rego_equal(before.get(name), after.get(name)):
            return False
        if domain._presence(name, before) != domain._presence(name, after):
            return False
        for atom in domain.observations:
            if slot_names(atom) == [name] and evaluate(atom, before, domain.profile) != evaluate(atom, after, domain.profile):
                return False
    return True


def literal_writes(writes: dict[str, Any], domains: dict[str, FiniteVariable]) -> list[tuple[Expr, tuple[Effect, ...]]]:
    """按组合变量分组，再将独立变量的结果组合为一次原子写回。"""
    from itertools import product

    grouped = {}
    for slot, value in writes.items():
        grouped.setdefault(domains[slot].name, {})[slot] = value
    options = []
    for group in grouped.values():
        domain = domains[next(iter(group))]
        options.append(_literal_group(group, domain))
    choices = []
    for combination in product(*options):
        guard = Expr("and", tuple(guard for guard, _ in combination))
        effects = tuple(effect for _, effect in combination)
        choices.append((guard, effects))
    return choices


def _literal_group(writes: dict[str, Any], domain: FiniteVariable) -> list[tuple[Expr, Effect]]:
    """联合域保留其余槽位；单槽位只需直接映射明确值。"""
    if not domain.joint:
        after = domain.value_for(next(iter(writes.values())))
        if after is None:
            raise ValueError("policy 的字面写回超出有限抽象域")
        return [(Expr("true"), Effect(domain.name, after))]
    options = set()
    for before, state in enumerate(domain.values):
        for sample in state.representatives or (state.sample,):
            after = domain.value_for({**sample, **writes})
            if after is None:
                raise ValueError("policy 写回未被联合抽象覆盖")
            options.add((before, after))
    return [(Expr("in", variable=domain.name, allowed=(before,)), Effect(domain.name, after)) for before, after in sorted(options)]


def list_updates(slot: str, domain: FiniteVariable, value: Any, operation: str, unknown: bool) -> list[tuple[Expr, int]]:
    """用代表值执行增删，再映射结果；不凭长度随意改成员信息。"""
    choices = set()
    for before, state in enumerate(domain.values):
        for sample in state.representatives or (state.sample,):
            original = sample.get(slot) if domain.joint else sample
            if not isinstance(original, list):
                continue
            for element in _elements(original, domain, slot, value, unknown):
                updated = _update_list(original, element, operation)
                if domain.joint:
                    updated = {**sample, slot: updated}
                after = domain.value_for(updated)
                if after is None:
                    raise ValueError(f"{operation} 结果未被有限域覆盖，需完善槽位 {slot} 的抽象")
                choices.add((before, after))
    if not choices:
        raise ValueError(f"{operation} 没有可用的列表状态")
    return [(Expr("in", variable=domain.name, allowed=(before,)), after) for before, after in sorted(choices)]


def _elements(original: list[Any], domain: FiniteVariable, slot: str, value: Any, unknown: bool) -> list[Any]:
    if not unknown:
        return [value]
    from .domains import _members

    elements = [*original, *_members(domain.observations)]
    other = {"_other": True} if domain.slot_types.get(slot, {}).get("element_type") == "dict" else "_unobserved_element"
    while any(rego_equal(other, element) for element in elements):
        other = {"_other": str(other)} if isinstance(other, dict) else other + "_"
    return [*elements, other]


def _update_list(original: list[Any], element: Any, operation: str) -> list[Any]:
    updated = deepcopy(original)
    if operation == "append":
        updated.append(element)
    else:
        index = next((i for i, item in enumerate(updated) if rego_equal(item, element)), None)
        if index is not None:
            del updated[index]
    return updated
