"""AI 检查项与观察变量的校验和接入。"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from src.model import CSPIR, Check, Diagnostic, Edge, Effect, Expr, FiniteVariable, StateValue
from .guards import compile_guard


NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STAGE_TEST = re.compile(r'current_stage\s*(==|!=)\s*"([^"]+)"')
OBSERVER_TEST = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=)\s*"([^"]+)"')


def attach_checks(model: CSPIR, raw: dict[str, Any], semantic: Any) -> list[Diagnostic]:
    """生成并校验检查内容；校验失败可带反馈重试一次。"""
    if semantic is None:
        return [Diagnostic("CHECKS", "/", "完整检测需要 AI 生成检查项")]
    context = _context(model, raw)
    failures: list[str] = []
    for _ in range(2):
        try:
            proposal = semantic.propose_checks(context)
            _apply_proposal(model, raw, proposal)
            return []
        except (ValueError, KeyError, TypeError) as exc:
            failures.append(str(exc))
            context["previous_error"] = str(exc)
    return [Diagnostic("CHECKS", "/", "AI 检查项未通过校验：" + "; ".join(failures))]


def _context(model: CSPIR, raw: dict[str, Any]) -> dict[str, Any]:
    stages = {name: {"description": stage.get("description", ""), "kind": stage.get("kind"), "targets": [edge.get("target") for edge in stage.get("transitions", [])]} for name, stage in raw["stages"].items()}
    variables = {item.name: [{"index": index, "name": state.name, "sample": state.sample, "missing": state.missing}
                            for index, state in enumerate(item.values)] for item in model.variables}
    diagnostics = [{"code": item.code, "path": item.path, "message": item.message, "severity": item.severity} for item in model.conversion_diagnostics]
    return {"goal": raw.get("goal", raw.get("description", "")), "stages": stages,
            "actions": {name: item.get("description", "") for name, item in raw["actions"].items()},
            "variables": variables, "events": model.source_map, "source_paths": _source_paths(raw),
            "conversion_diagnostics": diagnostics, "abstraction_registry": model.abstraction_registry,
            "guard_grammar": 'current_stage == "stage"; input.slots.name == literal; count(input.slots.name) > 0; all_of/any_of/not'}


def _apply_proposal(model: CSPIR, raw: dict[str, Any], proposal: dict[str, Any]) -> None:
    staged = CSPIR(list(model.variables), list(model.processes), list(model.edges), model.initial_process, dict(model.predicates), list(model.checks), dict(model.source_map), abstraction_registry=model.abstraction_registry)
    _apply_observers(staged, raw, proposal.get("observers", []))
    for item in proposal.get("predicates", []):
        name = _check_name(item.get("name"), _symbols(staged))
        _check_sources(item.get("sources"), raw)
        expression = _predicate(item.get("guard"), staged, raw)
        if expression.op in {"true", "false"}:
            raise ValueError("检查谓词不能是恒真或恒假")
        staged.predicates[name] = expression
        staged.source_map[name] = item["sources"][0]
    checks = proposal.get("checks", [])
    if not isinstance(checks, list) or not checks:
        raise ValueError("AI 没有生成可执行检查项")
    for item in checks:
        staged.checks.append(_check(item, staged, raw))
    model.variables, model.edges = staged.variables, staged.edges
    model.predicates, model.checks = staged.predicates, staged.checks
    model.source_map = staged.source_map


def _apply_observers(model: CSPIR, raw: dict[str, Any], observers: Any) -> None:
    if not isinstance(observers, list):
        raise ValueError("observers 必须是数组")
    events = {edge.event for edge in model.edges}
    for item in observers:
        name = _check_name(item.get("name"), _symbols(model))
        _check_sources(item.get("sources"), raw)
        labels = item.get("values", [])
        if not isinstance(labels, list) or not labels or any(not isinstance(label, str) for label in labels) or len(labels) != len(set(labels)) or len(labels) > 16:
            raise ValueError("observer 的有限取值无效")
        if item.get("initial") not in labels:
            raise ValueError("observer 初值不在有限域中")
        variable = FiniteVariable(name, item["sources"][0], [StateValue(str(label), str(label)) for label in labels], labels.index(item["initial"]))
        updates = item.get("updates", [])
        if not isinstance(updates, list) or any(update.get("event") not in events or update.get("value") not in labels for update in updates):
            raise ValueError("observer 更新引用未知事件或取值")
        if len({update["event"] for update in updates}) != len(updates):
            raise ValueError("observer 对同一事件重复赋值")
        mapping = {update["event"]: labels.index(update["value"]) for update in updates}
        model.edges = [replace(edge, effects=(*edge.effects, Effect(name, mapping[edge.event]))) if edge.event in mapping else edge for edge in model.edges]
        model.variables.append(variable)
        model.source_map[name] = item["sources"][0]


def _predicate(value: Any, model: CSPIR, raw: dict[str, Any]) -> Expr:
    if isinstance(value, dict):
        if len(value) != 1:
            raise ValueError("谓词布尔对象无效")
        key, children = next(iter(value.items()))
        if key in {"all_of", "any_of"} and isinstance(children, list):
            return Expr("and" if key == "all_of" else "or", tuple(_predicate(child, model, raw) for child in children))
        if key == "not":
            return Expr("not", (_predicate(children, model, raw),))
    if isinstance(value, str):
        match = STAGE_TEST.fullmatch(value.strip())
        if match and match.group(2) in raw["stages"]:
            return _variable_test("current_stage", match.group(1), match.group(2), model)
        match = OBSERVER_TEST.fullmatch(value.strip())
        if match and any(item.name == match.group(1) for item in model.variables):
            return _variable_test(match.group(1), match.group(2), match.group(3), model)
    by_name = {item.name: item for item in model.variables}
    slots = {name: by_name[variable] for name, variable in model.abstraction_registry.get("slot_variables", {}).items()}
    slots.update({item.source.removeprefix("/slot_information/"): item for item in model.variables if item.source.startswith("/slot_information/") and not item.joint})
    return compile_guard(value, slots, stage={"entry": None, "doActivity": None, "exit": None})


def _variable_test(name: str, op: str, label: str, model: CSPIR) -> Expr:
    variable = next(item for item in model.variables if item.name == name)
    matches = [index for index, state in enumerate(variable.values) if state.sample == label]
    if not matches:
        raise ValueError(f"谓词引用未知取值 {label}")
    allowed = tuple(matches if op == "==" else [index for index in range(len(variable.values)) if index not in matches])
    return Expr("in", variable=name, allowed=allowed)


def _check(item: dict[str, Any], model: CSPIR, raw: dict[str, Any]) -> Check:
    name = _check_name(item.get("name"), _symbols(model))
    sources = _check_sources(item.get("sources"), raw)
    kind = item.get("kind")
    if kind not in {"deadlock_free", "reachability", "ltl"}:
        raise ValueError("检查类型无效")
    predicate, formula = item.get("predicate"), item.get("formula")
    if kind == "reachability" and predicate not in model.predicates:
        raise ValueError("可达性检查引用未知谓词")
    if kind == "ltl":
        _check_formula(formula, model)
    if item.get("expected") not in {"VALID", "INVALID"}:
        raise ValueError("检查期望结果无效")
    if not str(item.get("description", "")).strip():
        raise ValueError("检查缺少描述")
    model.source_map[name] = sources[0]
    return Check(name, item["description"], kind, predicate if kind == "reachability" else None, formula if kind == "ltl" else None, item["expected"], tuple(sources))


def _check_formula(formula: Any, model: CSPIR) -> None:
    if not isinstance(formula, str) or not formula.strip():
        raise ValueError("LTL 检查缺少公式")
    identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula))
    allowed = set(model.predicates) | {edge.event for edge in model.edges} | {"U", "X", "true", "false"}
    if identifiers - allowed or re.search(r"[^A-Za-z0-9_\s()!&|<>\[\]]", formula):
        raise ValueError("LTL 公式引用未知对象或运算符")


def _check_name(name: Any, existing: set[str]) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in existing:
        raise ValueError("检查对象名称无效或重复")
    return name


def _symbols(model: CSPIR) -> set[str]:
    return {"Controller"} | {item.name for item in model.variables} | {item.name for item in model.processes} | {item.event for item in model.edges} | set(model.predicates) | {item.name for item in model.checks}


def _check_sources(sources: Any, raw: dict[str, Any]) -> list[str]:
    if not isinstance(sources, list) or not sources:
        raise ValueError("检查缺少 IR 来源")
    known = set(_source_paths(raw))
    if any(path not in known for path in sources):
        raise ValueError("检查引用不存在的 IR 来源")
    return sources


def _source_paths(raw: dict[str, Any]) -> list[str]:
    paths = [f"/{key}" for key in ("description", "goal") if key in raw]
    for name, stage in raw["stages"].items():
        paths.extend([f"/stages/{name}", f"/stages/{name}/description"])
        paths.extend(f"/stages/{name}/transitions/{index}" for index, _ in enumerate(stage.get("transitions", [])))
    paths.extend(f"/actions/{name}" for name in raw["actions"])
    paths.extend(f"/slot_information/{name}" for name in raw["slot_information"])
    return paths
