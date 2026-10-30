"""逐项调用 PAT 并保存原始结果与反例路径。"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from src.conversion.renderer import render_csp
from src.model import CSPIR


def run_pat_checks(model: CSPIR, executable: Path, run_dir: Path, timeout: float = 300.0) -> list[dict[str, Any]]:
    """每项检查单独渲染和执行，避免 PAT 结果相互混淆。"""
    if not executable.is_file():
        raise FileNotFoundError(f"PAT 程序不存在：{executable}")
    if not model.checks:
        raise ValueError("没有可执行的 AI 检查项")
    results: list[dict[str, Any]] = []
    for index, check in enumerate(model.checks, 1):
        input_file = run_dir / f"check_{index:03d}.csp"
        output_file = run_dir / f"pat_{index:03d}.txt"
        input_file.write_text(render_csp(model, checks=[check]), encoding="utf-8")
        result = _run_one(executable, input_file, output_file, check.kind, timeout)
        result.update({"name": check.name, "description": check.description, "expected": check.expected, "sources": list(check.sources), "input_file": input_file.name, "output_file": output_file.name})
        result["matches_expected"] = result["actual"] == check.expected
        results.append(result)
    return results


def _run_one(executable: Path, input_file: Path, output_file: Path, kind: str, timeout: float) -> dict[str, Any]:
    command = [str(executable), "-csp"]
    if kind in {"reachability", "deadlock_free"}:
        command.extend(["-engine", "1"])
    command.extend([str(input_file), str(output_file)])
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, cwd=executable.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        stdout, stderr, return_code = completed.stdout, completed.stderr, completed.returncode
        error = None if return_code == 0 else f"PAT 退出码 {return_code}"
    except subprocess.TimeoutExpired:
        stdout, stderr, return_code = "", "", None
        error = f"PAT 超时：{timeout:g} 秒"
    raw = output_file.read_text(encoding="utf-8-sig", errors="replace") if output_file.is_file() else ""
    combined = "\n".join((stdout, stderr, raw))
    actual = _validity(combined)
    if error is None and actual == "UNKNOWN":
        error = "PAT 未返回可识别的检查结果"
    trace = next((line.strip() for line in combined.splitlines() if line.strip().startswith("<") and "->" in line), None)
    return {"actual": actual, "trace": trace, "elapsed_seconds": round(time.perf_counter() - started, 3), "return_code": return_code, "error": error, "stdout": stdout, "stderr": stderr, "raw_output": raw}


def _validity(text: str) -> str:
    if re.search(r"\bis\s+(not\s+valid|invalid)\b", text, re.I):
        return "INVALID"
    if re.search(r"\bis\s+valid\b", text, re.I):
        return "VALID"
    return "UNKNOWN"
