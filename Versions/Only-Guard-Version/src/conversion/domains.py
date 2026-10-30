"""按判断结果合并代表值，建立共享的有限槽位域。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from itertools import combinations
from typing import Any, Iterator

from src.model import Diagnostic, FiniteVariable, StateValue
from .rego import CONTAINS_ATOM, COUNT_ATOM, INDEX_ATOM, LITERAL_ATOM, MEMBER_ATOM, MISSING, SLOT_ATOM


def iter_atoms(value: Any) -> Iterator[str]:
    """从条件树提取完整表达式，保留同一表达式内的变量绑定。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_atoms(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_atoms(item)


def build_domains(raw: dict[str, Any], semantic: Any = None) -> tuple[dict[str, FiniteVariable], list[Diagnostic]]:
    """公开兼容入口；实际抽象和判断映射由统一注册表建立。"""
    from .registry import build_registry

    registry, errors = build_registry(raw, semantic)
    return registry.domains, errors


def build_rule_domain(name: str, slot: dict[str, Any], atoms: list[str], profile: dict[str, Any]) -> FiniteVariable:
    """构造代码能确认的状态，按全部判断结果去掉冗余。"""
    samples = _samples(slot, atoms)
    values = [_state(sample, index, slot["type"]) for index, sample in enumerate(samples)]
    domain = FiniteVariable(_identifier("slot_" + name), f"/slot_information/{name}", values, 0, atoms)
    domain.slot_types, domain.profile = {name: slot}, profile
    domain.reason = "代码按全部判断的真值划分有限状态，合并等价代表值"
    if slot["type"] not in {"boolean", "enum"}:
        _merge_states(domain)
    default = slot.get("default_value", MISSING)
    domain.initial = domain.value_for(default)
    if domain.initial is None:
        raise ValueError("默认值不在有限抽象域中")
    domain.success_values = tuple(i for i, state in enumerate(domain.values) if valid_result(state, slot))
    return domain


def _samples(slot: dict[str, Any], atoms: list[str]) -> list[Any]:
    kind = slot.get("type")
    if kind == "enum":
        samples = list(slot.get("enum_values", []))
        if not samples:
            raise ValueError("enum_values 不能为空")
    elif kind == "boolean":
        samples = [False, True]
    elif kind == "string":
        samples = _string_samples(atoms)
    elif kind == "list":
        samples = _list_samples(atoms, slot.get("element_type"))
    elif kind == "dict":
        samples = [{}, {"_other": True}]
    else:
        raise ValueError(f"暂不支持槽位类型 {kind!r}")
    default = slot.get("default_value", MISSING)
    if default is not MISSING and default is not None and not valid_result(StateValue("default", default), slot):
        raise ValueError("default_value 与槽位类型或枚举声明不一致")
    if default is not MISSING:
        samples.insert(0, default)
    if default is MISSING or slot.get("allow_missing") is True:
        samples.insert(0, MISSING)
    if default is None or slot.get("nullable"):
        samples.insert(0, None)
    return _distinct(samples)


def _string_samples(atoms: list[str]) -> list[str]:
    literals = {""}
    for atom in atoms:
        match = LITERAL_ATOM.fullmatch(atom)
        if match and match.group(3).startswith('"'):
            literals.add(json.loads(match.group(3)))
    lengths = _boundary_lengths(atoms) | {1} | {len(item) for item in literals}
    if max(lengths) > 4096:
        raise ValueError("字符串长度代表值超过 4096，请缩小观察范围")
    samples = sorted(literals)
    for length in sorted(lengths):
        sample = "x" * length
        if sample in literals and length:
            sample = "y" * length
        samples.append(sample)
    return samples


def _list_samples(atoms: list[str], element_type: str | None) -> list[list[Any]]:
    members = _members(atoms)
    if len(members) > 8:
        raise ValueError("列表观察成员超过 8 个")
    lengths = _boundary_lengths(atoms) | {0, 1, 2, len(members), len(members) + 1}
    if max(lengths) > 128:
        raise ValueError("列表长度代表值超过 128")
    samples = []
    for size in range(len(members) + 1):
        for subset in combinations(members, size):
            # 删除一次成员后，既可能消失，也可能因重复出现而仍然存在。
            variants = [list(subset), *[[*subset, member] for member in subset]]
            for variant in variants:
                for length in sorted(lengths | {len(variant), len(variant) + 1}):
                    if length >= len(variant):
                        fillers = [_filler(index, members, element_type) for index in range(length - len(variant))]
                        samples.append([*variant, *fillers])
    if len(samples) > 4096:
        raise ValueError("列表抽象候选超过 4096 个")
    return samples


def _filler(index: int, members: list[Any], element_type: str | None) -> Any:
    if element_type == "dict":
        return {"_other": index}
    value = f"_other_{index}"
    while value in members:
        value += "_"
    return value


def _members(atoms: list[str]) -> list[Any]:
    members = []
    for atom in atoms:
        member = MEMBER_ATOM.fullmatch(atom)
        indexed = INDEX_ATOM.fullmatch(atom)
        legacy = CONTAINS_ATOM.fullmatch(atom)
        token = member.group(1) if member else indexed.group(2) if indexed else legacy.group(2) if legacy else None
        if token is not None and json.loads(token) not in members:
            members.append(json.loads(token))
    return members


def _boundary_lengths(atoms: list[str]) -> set[int]:
    lengths = {0}
    for atom in atoms:
        match = COUNT_ATOM.fullmatch(atom)
        if match:
            boundary = int(match.group(3))
            # 边界外多保留一个样本，增删后可继续留在同一抽象区间。
            lengths.update({max(0, boundary - 1), boundary, boundary + 1, boundary + 2})
    return lengths


def _state(sample: Any, index: int, kind: str) -> StateValue:
    if sample is MISSING:
        return StateValue("MISSING", None, missing=True)
    if sample is None:
        return StateValue("NULL", None)
    label = str(sample) if kind in {"boolean", "enum"} else f"VALUE_{index}"
    length = len(sample) if isinstance(sample, (str, list, dict)) else None
    return StateValue(label, sample, length)


def _distinct(samples: list[Any]) -> list[Any]:
    from .rego import rego_equal

    result = []
    for sample in samples:
        if not any(sample is old or (sample is not MISSING and old is not MISSING and rego_equal(sample, old)) for old in result):
            result.append(sample)
    return result


def _merge_states(domain: FiniteVariable) -> None:
    """同一类型、存在状态和观察真值相同的样本只保留一个。"""
    signatures = {}
    states = []
    for state in domain.values:
        signature = domain.signature(MISSING if state.missing else state.sample)
        if signature not in signatures:
            signatures[signature] = len(states)
            states.append(replace(state, representatives=(state.sample,)))
        else:
            index = signatures[signature]
            states[index] = replace(states[index], representatives=(*states[index].representatives, state.sample))
    domain.values = states


def valid_result(state: StateValue, slot: dict[str, Any]) -> bool:
    """初始化允许空值，不代表动作成功也允许返回空值。"""
    if state.missing:
        return False
    if state.sample is None:
        return None in slot.get("enum_values", []) or slot.get("allow_null_result") is True
    expected = {"string": str, "list": list, "dict": dict, "boolean": bool}
    if slot["type"] == "enum":
        from .rego import rego_equal

        return any(rego_equal(state.sample, value) for value in slot["enum_values"])
    return type(state.sample) is expected[slot["type"]]


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    cleaned = cleaned if re.match(r"[A-Za-z_]", cleaned) else "v_" + cleaned
    return f"{cleaned}_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:6]}"
