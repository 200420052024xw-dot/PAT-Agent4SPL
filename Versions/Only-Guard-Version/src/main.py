"""命令行公开入口。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from llm import LLMConfigurationError
from src.conversion.input import load_controller
from src.pipeline import PROJECT_ROOT, RunOptions, convert_controller_ir
from src.semantic import OpenAISemanticProvider


def main(argv: Sequence[str] | None = None) -> int:
    """执行一次完整转换和检测，并返回稳定的状态码。"""
    parser = argparse.ArgumentParser(description="ControllerIR → CSP_IR → CSP# → AI 检查 → PAT")
    parser.add_argument("command", choices=["convert"])
    parser.add_argument("input", nargs="?", type=Path, default=PROJECT_ROOT / "inputs" / "Controller_IR" / "Internal-Comms-IR.json")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "runs" / "controller_ir_to_csp")
    parser.add_argument("--pat-executable", type=Path, default=RunOptions().pat_executable)
    parser.add_argument("--pat-timeout", type=float, default=300.0)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model-only", action="store_true", help="只生成完整 CSP#，不生成检查项或运行 PAT")
    args = parser.parse_args(argv)
    try:
        raw = load_controller(args.input)
        semantic = OpenAISemanticProvider.from_env(args.env_file)
    except (OSError, ValueError, LLMConfigurationError) as exc:
        print(f"ENGINE_ERROR: {exc}")
        return 3
    options = RunOptions(args.output_root, args.pat_executable, args.pat_timeout, args.model_only)
    result = convert_controller_ir(raw, semantic, options=options)
    print(f"结果：{result.status}")
    print(f"运行目录：{result.run_dir}")
    if result.model is not None:
        print(f"CSP# 文件：{result.run_dir / 'model.csp'}")
    elif result.draft_model is not None:
        print(f"CSP# 草稿（转换未完成）：{result.run_dir / 'model.draft.csp'}")
    for item in result.diagnostics:
        print(f"{item.severity} {item.code} {item.path}: {item.message}")
    return {"CONVERTED": 0, "PASS": 0, "VIOLATION": 1, "INCOMPLETE": 2, "ENGINE_ERROR": 3}[result.status]
