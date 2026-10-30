"""转换、AI 检查生成、PAT 检测和报告的统一入口。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from llm import LLMError
from src.conversion.checks import attach_checks
from src.conversion.compiler import ConversionError, compile_controller
from src.conversion.renderer import render_csp
from src.conversion.symbols import build_symbols
from src.model import CSPIR, Diagnostic
from src.verification import run_pat_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAT = PROJECT_ROOT / "PAT.Console" / "Process-Analysis-Toolkit" / "PAT3.Console.exe"


@dataclass(frozen=True)
class RunOptions:
    output_root: Path = PROJECT_ROOT / "runs" / "controller_ir_to_csp"
    pat_executable: Path = DEFAULT_PAT
    pat_timeout: float = 300.0
    model_only: bool = False


@dataclass
class RunResult:
    status: str
    run_dir: Path
    model: CSPIR | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    draft_model: CSPIR | None = None


def convert_controller_ir(
    raw: dict[str, Any],
    semantic_provider: Any,
    pat_runner: Callable[..., list[dict[str, Any]]] = run_pat_checks,
    options: RunOptions | None = None,
) -> RunResult:
    """唯一程序入口：转换、检测并保存可追溯的运行结果。"""
    settings = options or RunOptions()
    run_dir = _new_run_dir(settings.output_root)
    result = RunResult("INCOMPLETE", run_dir)
    try:
        result.model = compile_controller(raw, semantic_provider)
        result.diagnostics.extend(result.model.conversion_diagnostics)
        # 模型先落盘；后续检查项生成或 PAT 失败也能查看转换结果。
        _write_model(result)
        if settings.model_only:
            result.status = "CONVERTED"
            return _finish(result, semantic_provider)
        result.diagnostics.extend(attach_checks(result.model, raw, semantic_provider))
        if any(item.severity == "error" for item in result.diagnostics):
            return _finish(result, semantic_provider)
        _write_model(result)
        result.verification = pat_runner(result.model, settings.pat_executable, run_dir, settings.pat_timeout)
        _record_verification_diagnostics(result)
        if any(item.get("error") for item in result.verification):
            result.status = "ENGINE_ERROR"
        elif any(not item.get("matches_expected") for item in result.verification):
            result.status = "VIOLATION"
        else:
            result.status = "PASS"
    except ConversionError as exc:
        result.diagnostics.extend(exc.diagnostics)
        result.draft_model = exc.partial_model
    except (LLMError, FileNotFoundError, TimeoutError, OSError) as exc:
        result.status = "ENGINE_ERROR"
        result.diagnostics.append(Diagnostic("ENGINE", "/", str(exc)))
    except (ValueError, TypeError, KeyError) as exc:
        result.diagnostics.append(Diagnostic("SEMANTICS", "/", str(exc)))
    return _finish(result, semantic_provider)


def _new_run_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for index in range(1000):
        path = root / f"{stamp}-{index:03d}"
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise RuntimeError("无法创建独立运行目录")


def _write_model(result: RunResult) -> None:
    model = result.model
    if model is None:
        return
    _write_json(result.run_dir / "model.csp_ir.json", model.to_dict())
    _write_json(result.run_dir / "abstraction_registry.json", model.abstraction_registry)
    _write_json(result.run_dir / "failure_records.json", model.failure_records)
    symbols = build_symbols(model)
    _write_json(result.run_dir / "source_map.json", symbols.sources)
    _write_json(result.run_dir / "render_map.json", symbols.to_dict())
    _write_json(result.run_dir / "checks.json", [asdict(check) for check in model.checks])
    (result.run_dir / "model.csp").write_text(render_csp(model), encoding="utf-8")


def _record_verification_diagnostics(result: RunResult) -> None:
    if result.model is None:
        return
    sources_by_name = build_symbols(result.model).sources
    for item in result.verification:
        trace = str(item.get("trace") or "")
        events = [part.strip(" <>\r\n") for part in trace.split("->")]
        item["trace_sources"] = [sources_by_name[event] for event in events if event in sources_by_name]
        sources = item.get("sources", [])
        path = sources[0] if sources else result.model.source_map.get(item.get("name", ""), "/")
        if item.get("error"):
            result.diagnostics.append(Diagnostic("PAT_ERROR", path, str(item["error"])))
        elif not item.get("matches_expected"):
            message = f"检查 {item.get('name')} 得到 {item.get('actual')}，期望 {item.get('expected')}"
            result.diagnostics.append(Diagnostic("PROPERTY_VIOLATION", path, message))


def _finish(result: RunResult, semantic_provider: Any) -> RunResult:
    if result.draft_model is not None:
        _write_draft(result)
    _write_json(result.run_dir / "diagnostics.json", [asdict(item) for item in result.diagnostics])
    _write_json(result.run_dir / "verification_results.json", result.verification)
    trace = getattr(semantic_provider, "trace", None)
    if trace is not None:
        _write_json(result.run_dir / "llm_trace.json", trace)
    model_file = "model.csp" if result.model is not None else "model.draft.csp" if result.draft_model is not None else None
    _write_json(result.run_dir / "manifest.json", {"status": result.status, "diagnostic_count": len(result.diagnostics), "check_count": len(result.verification), "model_file": model_file, "model_complete": result.model is not None})
    return result


def _write_draft(result: RunResult) -> None:
    """保存未完成的转换草稿，禁止将其作为完整模型送入 PAT。"""
    model = result.draft_model
    if model is None:
        return
    notes = ["转换未完成：本文件仅供人工核对，缺口尚未补全，未执行 PAT 检测。"]
    notes.extend(f"{item.code} {item.path}: {item.message}" for item in result.diagnostics)
    header = "\n".join("// " + line for note in notes for line in note.splitlines()) + "\n\n"
    (result.run_dir / "model.draft.csp").write_text(header + render_csp(model), encoding="utf-8")
    _write_json(result.run_dir / "model.draft.csp_ir.json", model.to_dict())
    _write_json(result.run_dir / "abstraction_registry.draft.json", model.abstraction_registry)
    _write_json(result.run_dir / "failure_records.draft.json", model.failure_records)
    symbols = build_symbols(model)
    _write_json(result.run_dir / "source_map.draft.json", symbols.sources)
    _write_json(result.run_dir / "render_map.draft.json", symbols.to_dict())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
