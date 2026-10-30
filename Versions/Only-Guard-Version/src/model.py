"""转换器内部的有类型模型及统一诊断。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Literal


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class StateValue:
    name: str
    sample: Any
    length: int | None = None
    members: tuple[str, ...] = ()
    missing: bool = False
    representatives: tuple[Any, ...] = ()


@dataclass
class FiniteVariable:
    name: str
    source: str
    values: list[StateValue]
    initial: int
    observations: list[str] = field(default_factory=list)
    slot_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    results: dict[str, list[bool | None]] = field(default_factory=dict)
    execution_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    joint: bool = False
    success_values: tuple[int, ...] | None = None

    def value_for(self, raw: Any) -> int | None:
        """将字面值映射到一个抽象值；无法唯一映射时返回空。"""
        from src.conversion.rego import MISSING, rego_equal

        if raw is MISSING:
            return next((index for index, state in enumerate(self.values) if state.missing), None)
        for index, state in enumerate(self.values):
            if not state.missing and rego_equal(state.sample, raw):
                return index
        if isinstance(raw, (str, list, dict)):
            signature = self.signature(raw)
            for index, state in enumerate(self.values):
                if not state.missing and type(state.sample) is type(raw) and self.signature(state.sample) == signature:
                    return index
        return None

    def signature(self, raw: Any) -> tuple[Any, ...]:
        """用全部观察结果定位状态，同时保留缺失和空值的区别。"""
        from src.conversion.rego import MISSING, evaluate
        from src.conversion.execution import status_cases

        names = list(self.slot_types) or [self.source.rsplit("/", 1)[-1]]
        slots = raw if self.joint else ({} if raw is MISSING else {names[0]: raw})
        presence = tuple(self._presence(name, slots) for name in names)
        answers = []
        for atom in self.observations:
            results = [evaluate(atom, slots, self.profile, status) for status in status_cases(atom)]
            answers.append(tuple(results))
        return presence, tuple(answers)

    def _presence(self, name: str, slots: dict[str, Any]) -> tuple[Any, ...]:
        """布尔和枚举保留实际值，容器额外区分空内容。"""
        value = slots.get(name)
        direct = self.slot_types.get(name, {}).get("type") in {"boolean", "enum"}
        literal = json.dumps(value, ensure_ascii=False, sort_keys=True) if direct else None
        return name in slots, value is None, type(value).__name__, not bool(value), literal

    def sample_slots(self, state: StateValue) -> dict[str, Any]:
        """把一个代表状态还原为 OPA 所需的输入槽位。"""
        if self.joint:
            return state.sample
        name = next(iter(self.slot_types), self.source.rsplit("/", 1)[-1])
        return {} if state.missing else {name: state.sample}


@dataclass(frozen=True)
class Expr:
    op: Literal["true", "false", "and", "or", "not", "in", "status"]
    args: tuple["Expr", ...] = ()
    variable: str = ""
    allowed: tuple[int, ...] = ()
    step: str = ""
    result: str = ""


@dataclass(frozen=True)
class Effect:
    variable: str
    value: int


@dataclass(frozen=True)
class Edge:
    source: str
    guard: Expr
    event: str
    effects: tuple[Effect, ...]
    target: str
    source_path: str
    target_args: tuple[int | str, ...] = ()


@dataclass(frozen=True)
class Process:
    name: str
    parameters: tuple[str, ...] = ()
    terminal: bool = False
    paused: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    kind: Literal["deadlock_free", "reachability", "ltl"]
    predicate: str | None
    formula: str | None
    expected: Literal["VALID", "INVALID"]
    sources: tuple[str, ...]


@dataclass
class CSPIR:
    variables: list[FiniteVariable]
    processes: list[Process]
    edges: list[Edge]
    initial_process: str
    predicates: dict[str, Expr] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    source_map: dict[str, str] = field(default_factory=dict)
    conversion_diagnostics: list[Diagnostic] = field(default_factory=list)
    abstraction_registry: dict[str, Any] = field(default_factory=dict)
    failure_records: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """输出可检查、可保存的结构化中间模型。"""
        from dataclasses import asdict

        return asdict(self)
