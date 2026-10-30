"""已校验 CSP_IR 的唯一 CSP# 渲染入口。"""

from __future__ import annotations

from collections import defaultdict
import re

from src.model import CSPIR, Check, Edge, Expr
from .symbols import RenderSymbols, build_symbols


def render_csp(model: CSPIR, checks: list[Check] | None = None) -> str:
    """将有限模型一次性渲染为可交给 PAT 的文本。"""
    symbols = build_symbols(model)
    lines: list[str] = []
    for diagnostic in model.conversion_diagnostics:
        note = f"{diagnostic.severity} {diagnostic.code} {diagnostic.path}: {diagnostic.message}"
        lines.extend("// " + part for part in note.splitlines())
    if model.conversion_diagnostics:
        lines.append("")
    lines.extend(f"#define {item['name']} {item['value']};" for item in symbols.constants)
    lines.append("")
    for variable in model.variables:
        lines.append(f"var {symbols.names[variable.name]} = {symbols.values[variable.name, variable.initial]};")
    lines.append("")
    grouped: dict[str, list[Edge]] = defaultdict(list)
    for edge in model.edges:
        grouped[edge.source].append(edge)
    for process in model.processes:
        lines.extend(_render_process(symbols.names[process.name], process.parameters, process.terminal, grouped[process.name], symbols))
    initial = next(process for process in model.processes if process.name == model.initial_process)
    args = ", ".join(symbols.statuses[0] for _ in initial.parameters)
    lines.extend([f"Controller() = {symbols.names[initial.name]}({args});", ""])
    for name, predicate in model.predicates.items():
        lines.append(f"#define {symbols.names[name]} ({render_expr(predicate, symbols)});")
    if model.predicates:
        lines.append("")
    for check in model.checks if checks is None else checks:
        lines.append(_render_check(check, symbols))
    return "\n".join(lines).rstrip() + "\n"


def render_expr(expr: Expr, symbols: RenderSymbols | None = None) -> str:
    """只渲染由有类型条件树生成的表达式。"""
    if expr.op in {"true", "false"}:
        return expr.op
    if expr.op == "and":
        if not expr.args:
            return "true"
        return "(" + " && ".join(render_expr(child, symbols) for child in expr.args) + ")"
    if expr.op == "or":
        if not expr.args:
            return "false"
        return "(" + " || ".join(render_expr(child, symbols) for child in expr.args) + ")"
    if expr.op == "not":
        return "!(" + render_expr(expr.args[0], symbols) + ")"
    if expr.op == "in":
        if not expr.allowed:
            return "false"
        variable = symbols.names[expr.variable] if symbols else expr.variable
        values = [symbols.values[expr.variable, value] if symbols else str(value) for value in expr.allowed]
        return "(" + " || ".join(f"{variable} == {value}" for value in values) + ")"
    parameter = {"entry": "entry_status", "doActivity": "do_status", "exit": "exit_status"}[expr.step]
    code = {"not_run": 0, "success": 1, "failure": 2}[expr.result]
    return f"{parameter} == {symbols.statuses[code] if symbols else code}"


def _render_process(name: str, parameters: tuple[str, ...], terminal: bool, edges: list[Edge], symbols: RenderSymbols) -> list[str]:
    signature = f"{name}({', '.join(parameters)})"
    if terminal:
        return [f"{signature} = Skip;", ""]
    if not edges:
        return [f"{signature} = Stop;", ""]
    lines = [f"{signature} ="]
    for index, edge in enumerate(edges):
        if index:
            lines.append("    []")
        lines.append("    " + _render_edge(edge, symbols))
    lines[-1] += ";"
    return [*lines, ""]


def _render_edge(edge: Edge, symbols: RenderSymbols) -> str:
    effects = ""
    if edge.effects:
        updates = " ".join(f"{symbols.names[effect.variable]} = {symbols.values[effect.variable, effect.value]};" for effect in edge.effects)
        effects = " {" + updates + "}"
    arguments = ", ".join(symbols.statuses[item] if isinstance(item, int) else item for item in edge.target_args)
    return f"[{render_expr(edge.guard, symbols)}] {symbols.names[edge.event]}{effects} -> {symbols.names[edge.target]}({arguments})"


def _render_check(check: Check, symbols: RenderSymbols) -> str:
    if check.kind == "deadlock_free":
        return "#assert Controller deadlockfree;"
    if check.kind == "reachability":
        return f"#assert Controller reaches {symbols.names[check.predicate]};"
    formula = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", lambda match: symbols.names.get(match.group(), match.group()), check.formula)
    return f"#assert Controller |= {formula};"
