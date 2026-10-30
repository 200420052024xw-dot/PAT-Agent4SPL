"""新版 ControllerIR 的读取和结构检查。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.model import Diagnostic


def load_controller(path: Path) -> dict[str, Any]:
    """读取 JSON/JSONC，并给调用方一个独立的字典。"""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonc":
        text = _strip_jsonc(text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("ControllerIR 顶层必须是对象")
    return value


def _strip_jsonc(source: str) -> str:
    """保留字符串及行号，删除行注释、块注释和尾逗号。"""
    output: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            output.append(char)
            index += 1
            continue
        if char == "/" and following in {"/", "*"}:
            end = source.find("\n", index) if following == "/" else source.find("*/", index + 2)
            end = len(source) if end < 0 else end + (2 if following == "*" else 0)
            output.extend("\n" if item == "\n" else " " for item in source[index:end])
            index = end
            continue
        output.append(char)
        index += 1
    return _strip_trailing_commas("".join(output))


def _strip_trailing_commas(source: str) -> str:
    output: list[str] = []
    quoted = escaped = False
    for index, char in enumerate(source):
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        if char == "," and source[index + 1:].lstrip().startswith(("}", "]")):
            output.append(" ")
        else:
            output.append(char)
    return "".join(output)


def validate_controller(raw: dict[str, Any]) -> list[Diagnostic]:
    """只校验可从结构确定的事实，不猜测业务动作效果。"""
    errors: list[Diagnostic] = []
    for key in ("slot_information", "actions", "stages", "participants", "policies"):
        if not isinstance(raw.get(key), dict):
            errors.append(Diagnostic("INVALID_SECTION", f"/{key}", "字段必须是对象"))
    if errors:
        return errors
    stages = raw["stages"]
    initial = [name for name, stage in stages.items() if isinstance(stage, dict) and stage.get("kind") == "initial"]
    if len(initial) != 1:
        errors.append(Diagnostic("INITIAL_STAGE", "/stages", "必须恰有一个 initial Stage"))
    for name, stage in stages.items():
        errors.extend(_validate_stage(name, stage, raw))
    return errors


def _validate_stage(name: str, stage: Any, raw: dict[str, Any]) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    path = f"/stages/{name}"
    if not isinstance(stage, dict):
        return [Diagnostic("INVALID_STAGE", path, "Stage 必须是对象")]
    if stage.get("kind") not in {"initial", "state", "termination"}:
        errors.append(Diagnostic("STAGE_KIND", path + "/kind", "未知 Stage 类型"))
    if stage.get("kind") == "termination" and any(stage.get(step) is not None for step in ("entry", "doActivity", "exit")):
        errors.append(Diagnostic("TERMINAL_ACTION", path, "终止 Stage 不应包含动作"))
    for step in ("entry", "doActivity", "exit"):
        value = stage.get(step)
        if value is None:
            continue
        if not isinstance(value, dict) or value.get("action") not in raw["actions"]:
            errors.append(Diagnostic("ACTION_REF", path + "/" + step, "动作引用不存在"))
    transitions = stage.get("transitions", [])
    if not isinstance(transitions, list):
        return errors + [Diagnostic("TRANSITIONS", path + "/transitions", "出边必须是数组")]
    for index, edge in enumerate(transitions):
        if not isinstance(edge, dict) or edge.get("target") not in raw["stages"]:
            errors.append(Diagnostic("TARGET_REF", f"{path}/transitions/{index}", "目标 Stage 不存在"))
    if stage.get("kind") == "termination" and transitions:
        errors.append(Diagnostic("TERMINAL_EDGES", path, "终止 Stage 不应有普通出边"))
    if stage.get("kind") != "termination" and not transitions:
        errors.append(Diagnostic("MISSING_TRANSITIONS", path, "普通 Stage 缺少出边"))
    return errors
