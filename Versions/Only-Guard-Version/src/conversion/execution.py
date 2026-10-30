"""联合处理 Rego 中的执行状态与槽位判断，保留完整表达式。"""

from itertools import product
import re

from src.model import Expr, FiniteVariable
from .rego import evaluate


STATUS_PATH = re.compile(r"input\.stage_execution\.(entry|doActivity|exit)\.execution_status")
STATUS_VALUES = ("not_run", "success", "failure")


def status_steps(query: str) -> list[str]:
    """忽略字符串中的伪引用，按固定执行顺序返回依赖步骤。"""
    query = re.sub(r'"(?:[^"\\]|\\.)*"', '""', query)
    used = set(STATUS_PATH.findall(query))
    return [step for step in ("entry", "doActivity", "exit") if step in used]


def status_cases(query: str) -> list[dict[str, str]]:
    """最多枚举三个步骤的 27 种状态，没有依赖时仅返回空条件。"""
    steps = status_steps(query)
    return [dict(zip(steps, values)) for values in product(STATUS_VALUES, repeat=len(steps))]


def result_cases(query: str, domain: FiniteVariable) -> list[dict]:
    """每种执行状态保存一张槽位判断表，禁止将状态当作缺失输入。"""
    cases = []
    for status in status_cases(query):
        answers = [evaluate(query, domain.sample_slots(state), domain.profile, status) for state in domain.values]
        cases.append({"status": status, "results": answers})
    return cases


def case_guard(cases: list[dict], variable: str) -> Expr:
    """转换为合法组合：执行状态条件与有限变量取值的析取。"""
    branches = []
    for case in cases:
        allowed = tuple(index for index, answer in enumerate(case["results"]) if answer is True)
        if not allowed:
            continue
        conditions = [Expr("status", step=step, result=value) for step, value in case["status"].items()]
        conditions.append(Expr("in", variable=variable, allowed=allowed))
        branches.append(Expr("and", tuple(conditions)))
    return Expr("or", tuple(branches)) if branches else Expr("false")


def pure_status_guard(query: str, profile: dict | None = None) -> Expr:
    """纯执行状态的完整 Rego 表达式也由官方引擎解释。"""
    branches = []
    for status in status_cases(query):
        if evaluate(query, {}, profile, status) is True:
            conditions = tuple(Expr("status", step=step, result=value) for step, value in status.items())
            branches.append(Expr("and", conditions))
    return Expr("or", tuple(branches)) if branches else Expr("false")
