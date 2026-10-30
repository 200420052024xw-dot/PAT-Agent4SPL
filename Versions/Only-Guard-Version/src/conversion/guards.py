"""把 ControllerIR 条件树编译为有限模型条件。"""

from __future__ import annotations

import re
from itertools import product
from typing import Any

from src.model import Expr, FiniteVariable
from .domains import COUNT_ATOM, LITERAL_ATOM
from .execution import case_guard, pure_status_guard, status_steps
from .rego import evaluate, normalize, slot_names


STATUS_ATOM = re.compile(r'input\.stage_execution\.(entry|doActivity|exit)\.execution_status\s*(==|!=)\s*"(success|failure|not_run)"')


class GuardError(ValueError):
    """条件无法安全解释时由公开编译入口报告。"""


def compile_guard(value: Any, domains: dict[str, FiniteVariable], *, stage: dict[str, Any], semantic: Any = None, rego_profile: dict | None = None) -> Expr:
    """编译一个条件；未知原子只接受通过有限域校验的 AI 提议。"""
    if value is True:
        return Expr("true")
    if value is False:
        return Expr("false")
    if isinstance(value, dict):
        return _compile_group(value, domains, stage, semantic, rego_profile)
    if not isinstance(value, str):
        raise GuardError("guard 只能是布尔值、条件对象或表达式")
    atom = value.strip()
    if atom in {"true", "false"}:
        return Expr(atom)
    for step in status_steps(atom):
        if stage.get(step) is None:
            raise GuardError(f"guard 读取空步骤 {step} 的执行状态")
    match = STATUS_ATOM.fullmatch(atom)
    if match:
        step, compare, result = match.groups()
        if stage.get(step) is None:
            raise GuardError(f"guard 读取空步骤 {step} 的执行状态")
        status = Expr("status", step=step, result=result)
        return status if compare == "==" else Expr("not", (status,))
    if slot_names(atom):
        return _slot_guard(atom, domains)
    if status_steps(atom):
        profile = rego_profile or next((domain.profile for domain in domains.values()), None)
        return pure_status_guard(atom, profile)
    if semantic is not None:
        proposal = semantic.resolve_guard(atom, domains)
        return _validate_proposal(proposal, domains)
    raise GuardError(f"无法解释的条件：{atom}")


def _slot_guard(atom: str, domains: dict[str, FiniteVariable]) -> Expr:
    """优先复用联合调用保存的判断表，不再单独调用 LLM。"""
    names = slot_names(atom)
    if any(name not in domains for name in names):
        raise GuardError(f"未知槽位：{names}")
    domain = domains[names[0]]
    if any(domains[name].name != domain.name for name in names):
        raise GuardError("跨槽位判断尚未建立联合抽象")
    specs = {name: variable.slot_types.get(name, {}) for name, variable in domains.items()}
    query = normalize(atom, specs)
    if query in domain.execution_results:
        return case_guard(domain.execution_results[query], domain.name)
    answers = domain.results.get(query)
    if answers is None:
        if domain.slot_types and query not in domain.observations:
            _check_new_observation(query, domain)
        answers = [evaluate(query, domain.sample_slots(state), domain.profile) for state in domain.values]
    allowed = tuple(index for index, answer in enumerate(answers) if answer is True)
    return Expr("in", variable=domain.name, allowed=allowed)


def _check_new_observation(query: str, domain: FiniteVariable) -> None:
    """检查项不能引入原抽象无法区分的新长度或业务条件。"""
    count = COUNT_ATOM.fullmatch(query)
    literal = LITERAL_ATOM.fullmatch(query)
    if literal and domain.slot_types.get(literal.group(1), {}).get("type") in {"enum", "boolean"}:
        return
    empty_tests = {("==", "0"), ("!=", "0"), (">", "0"), ("<=", "0"), (">=", "1"), ("<", "1")}
    if count and (count.group(2), count.group(3)) in empty_tests:
        return
    if literal and literal.group(3) in {"null", "true", "false", '""'}:
        return
    raise GuardError("新增判断不在已收集的抽象中，必须重新建立有限域")


def _compile_group(value: dict[str, Any], domains: dict[str, FiniteVariable], stage: dict[str, Any], semantic: Any, profile: dict | None) -> Expr:
    if len(value) != 1:
        raise GuardError("布尔条件对象必须只有一个运算符")
    key, children = next(iter(value.items()))
    if key in {"all_of", "any_of"} and isinstance(children, list):
        op = "and" if key == "all_of" else "or"
        if not children:
            return Expr("true" if op == "and" else "false")
        args = tuple(compile_guard(child, domains, stage=stage, semantic=semantic, rego_profile=profile) for child in children)
        return Expr(op, args)
    if key == "not":
        child = compile_guard(children, domains, stage=stage, semantic=semantic, rego_profile=profile)
        return Expr("not", (child,))
    raise GuardError(f"不支持的布尔结构：{key}")


def _validate_proposal(proposal: dict[str, Any], domains: dict[str, FiniteVariable]) -> Expr:
    """AI 仅能从现有有限变量及其整数取值构造表达式。"""
    if not isinstance(proposal, dict):
        raise GuardError("AI guard 补全不是对象")
    op = proposal.get("op")
    if op in {"true", "false"}:
        return Expr(op)
    if op in {"and", "or", "not"}:
        args = proposal.get("args", [])
        if not isinstance(args, list) or (op == "not" and len(args) != 1):
            raise GuardError("AI guard 布尔参数无效")
        return Expr(op, tuple(_validate_proposal(item, domains) for item in args))
    if op == "in":
        name = proposal.get("slot")
        if name not in domains:
            raise GuardError("AI guard 引用了未知槽位")
        allowed = proposal.get("allowed", [])
        if not isinstance(allowed, list) or any(type(item) is not int or item < 0 or item >= len(domains[name].values) for item in allowed):
            raise GuardError("AI guard 的有限取值无效")
        return Expr("in", variable=domains[name].name, allowed=tuple(allowed))
    raise GuardError("AI guard 使用了未知运算符")


def has_uncovered_state(guards: list[Expr], domains: dict[str, FiniteVariable], status: dict[str, int]) -> bool:
    """只枚举出边实际读取的变量，检查是否有无路可走的状态。"""
    used = sorted({expr.variable for guard in guards for expr in _walk(guard) if expr.op == "in"})
    by_name = {domain.name: domain for domain in domains.values()}
    if any(name not in by_name for name in used):
        raise GuardError("guard 使用未知有限变量")
    possibilities = (range(len(by_name[name].values)) for name in used)
    for combination in product(*possibilities):
        values = dict(zip(used, combination))
        if not any(evaluate_guard(guard, values, status) for guard in guards):
            return True
    return False


def evaluate_guard(expr: Expr, values: dict[str, int], status: dict[str, int]) -> bool:
    """在有限赋值上求值，供覆盖性检查和测试共用。"""
    if expr.op == "true":
        return True
    if expr.op == "false":
        return False
    if expr.op == "and":
        return all(evaluate_guard(item, values, status) for item in expr.args)
    if expr.op == "or":
        return any(evaluate_guard(item, values, status) for item in expr.args)
    if expr.op == "not":
        return not evaluate_guard(expr.args[0], values, status)
    if expr.op == "in":
        return values[expr.variable] in expr.allowed
    result_code = {"not_run": 0, "success": 1, "failure": 2}[expr.result]
    return status[expr.step] == result_code


def _walk(expr: Expr):
    yield expr
    for child in expr.args:
        yield from _walk(child)
