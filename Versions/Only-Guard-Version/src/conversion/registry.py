"""先收集全部用途，再保存统一的槽位抽象和 Guard 映射。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.model import Diagnostic, Expr, FiniteVariable
from .domains import iter_atoms, build_rule_domain
from .rego import category, normalize, profile_for, slot_names


@dataclass
class Observation:
    """一个完整判断及其所有使用位置。"""

    id: str
    expression: str
    slots: list[str]
    category: str
    sources: list[str] = field(default_factory=list)


@dataclass
class AbstractionRegistry:
    """跨 Stage 共享状态，防止重复调用或形成不同的抽象。"""

    domains: dict[str, FiniteVariable] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    guards: dict[str, Expr] = field(default_factory=dict)
    guard_trees: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """保存可读注册表，模型文件中仍保留有类型对象。"""
        unique = {domain.name: asdict(domain) for domain in self.domains.values()}
        return {"rego": self.profile, "domains": unique,
                "slot_variables": {name: domain.name for name, domain in self.domains.items()},
                "observations": [asdict(item) for item in self.observations.values()],
                "guard_trees": self.guard_trees, "guards": {name: asdict(expr) for name, expr in self.guards.items()}}


def build_registry(raw: dict[str, Any], semantic: Any = None) -> tuple[AbstractionRegistry, list[Diagnostic]]:
    """公开入口：收集、分组、抽象、校验，最后生成判断映射。"""
    registry = AbstractionRegistry()
    errors = []
    try:
        registry.profile = profile_for(raw)
        _collect(raw, registry)
        for group in _groups(raw, registry):
            _build_group(group, raw, semantic, registry)
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(Diagnostic("ABSTRACTION", "/slot_information", str(exc)))
    return registry, errors


def _collect(raw: dict[str, Any], registry: AbstractionRegistry) -> None:
    """同一表达式共用编号，字符串中的伪槽位引用不会被收集。"""
    for path, tree in _guard_sources(raw):
        registry.guard_trees[path] = tree
        for original in iter_atoms(tree):
            atom = normalize(original, raw["slot_information"])
            names = slot_names(atom)
            unknown = set(names) - set(raw["slot_information"])
            if unknown:
                raise ValueError(f"{path} 引用未知槽位：{sorted(unknown)}")
            registry.aliases[original.strip()] = atom
            if not names:
                continue
            if atom not in registry.observations:
                number = len(registry.observations) + 1
                registry.observations[atom] = Observation(f"observation_{number:03d}", atom, names, category(atom))
            registry.observations[atom].sources.append(path)


def _guard_sources(raw: dict[str, Any]):
    for name, stage in raw["stages"].items():
        for index, edge in enumerate(stage.get("transitions", [])):
            yield f"/stages/{name}/transitions/{index}/guard", edge.get("guard", True)
    for name, policy in raw.get("policies", {}).items():
        yield from _policy_guards(policy, f"/policies/{name}")


def _policy_guards(value: Any, path: str):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "guard":
                yield path + "/guard", child
            else:
                yield from _policy_guards(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _policy_guards(child, path + "/" + str(index))


def _groups(raw: dict[str, Any], registry: AbstractionRegistry) -> list[list[str]]:
    """跨槽位判断的依赖闭包作为一次联合调用的单位。"""
    groups = [{name} for name in raw["slot_information"]]
    for observation in registry.observations.values():
        connected = [group for group in groups if group.intersection(observation.slots)]
        merged = set().union(*connected)
        groups = [group for group in groups if group not in connected] + [merged]
    order = list(raw["slot_information"])
    return [sorted(group, key=order.index) for group in groups]


def _build_group(group: list[str], raw: dict[str, Any], semantic: Any, registry: AbstractionRegistry) -> None:
    observations = [item for item in registry.observations.values() if set(item.slots).intersection(group)]
    atoms = [item.expression for item in observations]
    categories = {item.category for item in observations}
    direct_type = raw["slot_information"][group[0]]["type"] in {"boolean", "enum"}
    needs_llm = len(group) > 1 or "unknown" in categories or (not direct_type and len(categories) > 1)
    if needs_llm:
        from .joint import build_joint_domain

        domain = build_joint_domain(group, observations, raw, semantic, registry.profile)
        registry.diagnostics.append(Diagnostic("ABSTRACTION_APPROXIMATION", domain.source, "LLM 联合抽象已通过代表值校验；业务覆盖依据见抽象 reason", "warning"))
    else:
        name = group[0]
        domain = build_rule_domain(name, raw["slot_information"][name], atoms, registry.profile)
    for name in group:
        registry.domains[name] = domain
    _register_guards(domain, observations, registry)


def _register_guards(domain: FiniteVariable, observations: list[Observation], registry: AbstractionRegistry) -> None:
    from .execution import case_guard, result_cases, status_steps
    from .rego import evaluate

    for item in observations:
        if status_steps(item.expression):
            if item.expression not in domain.execution_results:
                domain.execution_results[item.expression] = result_cases(item.expression, domain)
            cases = domain.execution_results[item.expression]
            registry.guards[item.expression] = case_guard(cases, domain.name)
            continue
        if item.expression not in domain.results:
            domain.results[item.expression] = [evaluate(item.expression, domain.sample_slots(state), registry.profile) for state in domain.values]
        allowed = tuple(index for index, answer in enumerate(domain.results[item.expression]) if answer is True)
        registry.guards[item.expression] = Expr("in", variable=domain.name, allowed=allowed)
