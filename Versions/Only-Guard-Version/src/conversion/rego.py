"""解释常见 Rego 判断；复杂表达式交给官方 OPA 求值。"""

from __future__ import annotations

import json
import operator
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any


MISSING = object()
FIELD = r"input\.(?:slots\.)?([A-Za-z_][A-Za-z0-9_]*)"
TOKEN = r'(null|true|false|-?\d+|"(?:[^"\\]|\\.)*"|\[\])'
SLOT_ATOM = re.compile(FIELD)
COUNT_ATOM = re.compile(r"(?:count|length)\(\s*" + FIELD + r"\s*\)\s*(==|!=|<=|>=|<|>)\s*(\d+)")
LITERAL_ATOM = re.compile(FIELD + r"\s*(==|!=)\s*" + TOKEN)
CONTAINS_ATOM = re.compile(r'contains\(\s*' + FIELD + r',\s*("(?:[^"\\]|\\.)*")\s*\)')
MEMBER_ATOM = re.compile(r'("(?:[^"\\]|\\.)*"|-?\d+)\s+in\s+' + FIELD)
INDEX_ATOM = re.compile(FIELD + r'\[_\]\s*==\s*("(?:[^"\\]|\\.)*"|-?\d+)')
COMPARATORS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}


def slot_names(query: str) -> list[str]:
    """忽略字符串常量里的伪引用，只收集真实的输入槽位。"""
    without_strings = re.sub(r'"(?:[^"\\]|\\.)*"', '""', query)
    return sorted(set(SLOT_ATOM.findall(without_strings)) - {"stage_execution"})


def normalize(query: str, slots: dict[str, Any]) -> str:
    """统一函数写法，兼容旧列表 contains 写法，不拆散变量绑定。"""
    query = query.strip()
    query = re.sub(r"\blength\(", "count(", query)
    match = CONTAINS_ATOM.fullmatch(query)
    if match and slots.get(match.group(1), {}).get("type") == "list":
        return f"{match.group(2)} in input.slots.{match.group(1)}"
    return query


def category(query: str) -> str:
    """根据判断含义决定是否需要跨类型联合抽象。"""
    match = COUNT_ATOM.fullmatch(query)
    if match:
        return "empty" if int(match.group(3)) == 0 or (match.group(2) == ">=" and match.group(3) == "1") else "length"
    match = LITERAL_ATOM.fullmatch(query)
    if match:
        return "empty" if match.group(3) in {'""', "[]", "null"} else "literal"
    if MEMBER_ATOM.fullmatch(query) or INDEX_ATOM.fullmatch(query):
        return "member"
    return "unknown"


def evaluate_known(query: str, slots: dict[str, Any]) -> bool | None:
    """返回真、假或未定义；不认识的语法明确报错。"""
    query = query.strip()
    if query in {"true", "false"}:
        return query == "true"
    match = LITERAL_ATOM.fullmatch(query)
    if match:
        name, comparison, token = match.groups()
        value = slots.get(name, MISSING)
        if value is MISSING:
            return None
        equal = rego_equal(value, json.loads(token))
        return equal if comparison == "==" else not equal
    match = COUNT_ATOM.fullmatch(query)
    if match:
        name, comparison, number = match.groups()
        value = slots.get(name, MISSING)
        if not isinstance(value, (str, list, dict)):
            return None
        return COMPARATORS[comparison](len(value), int(number))
    return _known_membership(query, slots)


def _known_membership(query: str, slots: dict[str, Any]) -> bool | None:
    """区分 in 运算的假与下标匹配失败后的未定义。"""
    match = MEMBER_ATOM.fullmatch(query)
    indexed = match is None
    if match:
        token, name = match.groups()
    else:
        match = INDEX_ATOM.fullmatch(query)
        if not match:
            raise ValueError("需要 OPA 解释此 Rego 表达式")
        name, token = match.groups()
    value = slots.get(name, MISSING)
    if value is MISSING:
        return None
    if not isinstance(value, (list, dict)):
        return None if indexed else False
    members = value.values() if isinstance(value, dict) else value
    found = any(rego_equal(member, json.loads(token)) for member in members)
    # 下标匹配没有满足条件的绑定时未定义，in 运算则返回 false。
    return None if indexed and not found else found


def rego_equal(left: Any, right: Any) -> bool:
    """防止 Python 将 true 与数字 1 错认成相等。"""
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(rego_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(rego_equal(left[key], right[key]) for key in left)
    return left == right


def opa_executable() -> str | None:
    """优先使用显式配置，其次查找系统和项目内的 OPA。"""
    configured = os.getenv("OPA_EXECUTABLE")
    if configured:
        if not Path(configured).is_file():
            raise ValueError("OPA_EXECUTABLE 指向的文件不存在")
        return configured
    local = Path(__file__).resolve().parents[2] / "tmp" / "opa.exe"
    return shutil.which("opa") or (str(local) if local.is_file() else None)


def evaluate(query: str, slots: dict[str, Any], profile: dict[str, Any] | None = None, status: dict[str, str] | None = None) -> bool | None:
    """简单判断直接解释，完整 Rego 表达式由官方引擎解释。"""
    try:
        return evaluate_known(query, slots)
    except ValueError:
        settings = profile or {"version": "v1", "imports": []}
        payload = {"slots": slots, **slots}
        if status:
            payload["stage_execution"] = {step: {"execution_status": value} for step, value in status.items()}
        return _opa_query(query, json.dumps(payload, ensure_ascii=False, sort_keys=True), json.dumps(settings, sort_keys=True))


@lru_cache(maxsize=8192)
def _opa_query(query: str, payload: str, settings_json: str) -> bool | None:
    """缓存相同输入的求值结果，避免反复启动 OPA。"""
    executable = opa_executable()
    if executable is None:
        raise ValueError("复杂 Rego 判断需要 OPA，请配置 OPA_EXECUTABLE")
    settings = json.loads(settings_json)
    command = [executable, "eval", "--format=json", "--stdin-input", "--timeout=5s"]
    command.append("--v0-compatible" if settings["version"] == "v0" else "--v1-compatible")
    for name in settings.get("imports", []):
        command.extend(["--import", name])
    command.append(query)
    try:
        result = subprocess.run(command, input=payload, capture_output=True, text=True, encoding="utf-8", timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("OPA 求值超时，未生成该判断的近似结果") from exc
    response = json.loads(result.stdout or "{}")
    if result.returncode or response.get("errors"):
        messages = [item.get("message", "") for item in response.get("errors", [])]
        raise ValueError("Rego 语法或类型错误：" + "; ".join(messages))
    rows = response.get("result", [])
    if not rows:
        return None
    return any(all(item.get("value") is not False for item in row["expressions"]) for row in rows)


def profile_for(raw: dict[str, Any]) -> dict[str, Any]:
    """固定方言和导入设置，不猜测否定表达式的版本语义。"""
    profile = raw.get("rego", {"version": "v1", "imports": []})
    if not isinstance(profile, dict) or profile.get("version", "v1") not in {"v0", "v1"}:
        raise ValueError("rego.version 必须是 v0 或 v1")
    imports = profile.get("imports", [])
    if not isinstance(imports, list) or any(not isinstance(name, str) for name in imports):
        raise ValueError("rego.imports 必须是字符串数组")
    return {"version": profile.get("version", "v1"), "imports": imports}
