"""为最终 CSP# 分配可读名称，内部 ID 和有限值编码保持不变。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.model import CSPIR, FiniteVariable, StateValue
from .domains import LITERAL_ATOM, _members
from .rego import COUNT_ATOM, COMPARATORS


RESERVED = set((
    "Controller Skip Stop true false var channel if ifa else while do case default "
    "atomic assert define tau hvar call new enum interleave parallel interrupt "
    "within timeout wait hiding refines reaches deadlockfree divergencefree "
    "deterministic nonterminating U X entry_status do_status exit_status"
).split())


@dataclass
class RenderSymbols:
    names: dict[str, str] = field(default_factory=dict)
    values: dict[tuple[str, int], str] = field(default_factory=dict)
    statuses: dict[int, str] = field(default_factory=dict)
    constants: list[dict[str, Any]] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """记录内部名称、有限值编码到最终名称的对应关系。"""
        return {"names": self.names, "constants": self.constants}


def build_symbols(model: CSPIR) -> RenderSymbols:
    """统一分配名称；清洗后实际重名或撞上保留字时才加序号。"""
    symbols = RenderSymbols()
    used = set(RESERVED)

    def allocate(base: str, fallback: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_") or fallback
        if base[0].isdigit():
            base = fallback + "_" + base
        candidate, number = base, 2
        while candidate in used:
            candidate = f"{base}_{number}"
            number += 1
        used.add(candidate)
        return candidate

    for variable in model.variables:
        base = "slot_" + variable.source.removeprefix("/slot_information/") if variable.source.startswith("/slot_information/") else variable.name
        if variable.joint:
            base = "slots_" + "__".join(variable.slot_types)
        symbols.names[variable.name] = allocate(base, "variable")
    for process in model.processes:
        symbols.names[process.name] = allocate(_control_name(process.name), "process")
    for event in sorted({edge.event for edge in model.edges}):
        symbols.names[event] = allocate(_control_name(event), "event")
    for name in [*model.predicates, *(check.name for check in model.checks)]:
        symbols.names[name] = allocate(name, "check")
    for code, label in enumerate(("NOT_RUN", "SUCCESS", "FAILURE")):
        name = allocate("STATUS_" + label, "STATUS")
        symbols.statuses[code] = name
        symbols.constants.append({"name": name, "value": code, "source": "/stages", "status": label.lower()})
    for variable in model.variables:
        prefix = symbols.names[variable.name].removeprefix("slot_").upper()
        if variable.name == "current_stage":
            prefix = "STAGE"
        for index, state in enumerate(variable.values):
            name = allocate(prefix + "_" + _state_label(variable, state).upper(), "VALUE")
            symbols.values[variable.name, index] = name
            symbols.constants.append({"name": name, "value": index, "source": variable.source, "variable": variable.name, "state": state.name})
    symbols.sources = {symbols.names.get(name, name): path for name, path in model.source_map.items()}
    symbols.sources.update({item["name"]: item["source"] for item in symbols.constants})
    return symbols


def _control_name(name: str) -> str:
    """仅移除转换器生成的进程、事件名称包装。"""
    match = re.fullmatch(r"P_(.+)_[0-9a-f]{6}", name)
    return match.group(1) if match else name


def _state_label(variable: FiniteVariable, state: StateValue) -> str:
    """以抽象含义命名，不把字符串和列表的代表样本当成真实数据。"""
    sample = state.sample
    if state.missing:
        return "MISSING"
    if sample is None:
        return "NULL" if state.name == "NULL" else "UNSET"
    if state.name == str(sample) or not re.fullmatch(r"VALUE_\d+", state.name):
        return state.name
    if not sample:
        return "EMPTY"
    if isinstance(sample, dict):
        return "NONEMPTY"
    if isinstance(sample, str):
        literals = {json.loads(match.group(3)) for atom in variable.observations if (match := LITERAL_ATOM.fullmatch(atom.strip())) and match.group(3).startswith('"')}
        if sample in literals:
            return "TEXT_" + sample
        others = [item for item in variable.values if isinstance(item.sample, str) and item.sample and item.sample not in literals]
        return "NONEMPTY" if len(others) == 1 else _length_label(variable, sample)
    if isinstance(sample, list):
        members = _members(variable.observations)
        if not members and sum(isinstance(item.sample, list) and bool(item.sample) for item in variable.values) == 1:
            return "NONEMPTY"
        present = [str(member) for member in members if member in sample]
        return _length_label(variable, sample) + ("_CONTAINS_" + "_".join(present) if present else "_NO_TRACKED_MEMBERS" if members else "")
    return state.name


def _length_label(variable: FiniteVariable, sample: Any) -> str:
    """用判断区间命名，避免把代表长度误当成唯一长度。"""
    labels = {"==": "EQ", "!=": "NE", ">": "GT", ">=": "GE", "<": "LT", "<=": "LE"}
    opposite = {"==": "!=", "!=": "==", ">": "<=", ">=": "<", "<": ">=", "<=": ">"}
    tests = []
    for atom in variable.observations:
        match = COUNT_ATOM.fullmatch(atom)
        if match:
            _, comparison, number = match.groups()
            if int(number) == 0:
                continue
            true = COMPARATORS[comparison](len(sample), int(number))
            tests.append(labels[comparison if true else opposite[comparison]] + number)
    return "LENGTH_" + "_AND_".join(tests) if tests else f"LENGTH_{len(sample)}"
