"""CSP# verification and trace-guided repair pipeline.

Input starts from CSP#; the natural-language planning and initial code
generation stages from PAT-Agent are intentionally omitted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llm import LLMError, OpenAICompatibleLLM, load_llm_config


DEFAULT_INPUT_DIR = PROJECT_ROOT / "inputs"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results"
DEFAULT_PAT_EXECUTABLE = (
    PROJECT_ROOT
    / "PAT.Console"
    / "Process-Analysis-Toolkit"
    / "PAT3.Console.exe"
)
ASSERTION_PATTERN = re.compile(
    r"(?m)^(?![ \t]*//)[ \t]*(#assert\b[^\r\n]*?;?)[ \t]*(?:\r?\n|$)"
)
RESULT_START_MARKER = "********Verification Result********"
RESULT_END_MARKER = "********Verification Setting********"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify CSP# with PAT and repair failed assertions from traces."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=(
            "CSP# files. If omitted, all *.csp files directly inside inputs/ "
            "are processed."
        ),
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
        help="LLM configuration file (default: project-root .env).",
    )
    parser.add_argument(
        "--pat-executable", type=Path, default=DEFAULT_PAT_EXECUTABLE
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="PAT timeout in seconds for each assertion (default: 300).",
    )
    parser.add_argument(
        "--no-refine",
        action="store_true",
        help="Only verify; do not call the trace-guided repair agent.",
    )
    parser.add_argument(
        "--max-refine-rounds",
        type=int,
        default=5,
        help="Maximum repair/verification rounds (default: 5).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8192,
        help="Maximum tokens in each repair response (default: 8192).",
    )
    return parser.parse_args(argv)


def absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def discover_inputs(raw_inputs: Sequence[str], input_dir: Path) -> List[Path]:
    input_dir = absolute(input_dir)
    if raw_inputs:
        candidates = []
        for raw in raw_inputs:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute() and not candidate.exists():
                candidate = input_dir / candidate
            candidates.append(absolute(candidate))
    else:
        candidates = sorted(input_dir.glob("*.csp"), key=lambda item: item.name.lower())

    inputs: List[Path] = []
    seen = set()
    for candidate in candidates:
        candidate = absolute(candidate)
        if candidate.suffix.lower() != ".csp":
            raise ValueError(f"Input must have a .csp extension: {candidate}")
        if not candidate.is_file():
            raise FileNotFoundError(f"CSP# input not found: {candidate}")
        key = os.path.normcase(str(candidate))
        if key not in seen:
            inputs.append(candidate)
            seen.add(key)
    return inputs


def create_run_directory(results_dir: Path) -> Path:
    results_dir = absolute(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    prefix = dt.datetime.now().astimezone().strftime("%m%d-%H%M")
    for sequence in range(1, 1000):
        candidate = results_dir / f"{prefix}-{sequence:03d}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"No available run number for {prefix}")


def mask_comments(code: str) -> str:
    """Mask line/block comments while preserving character offsets."""
    masked = list(code)
    index = 0
    state = "code"
    while index < len(code):
        current = code[index]
        following = code[index + 1] if index + 1 < len(code) else ""
        if state == "code":
            if current == "/" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "line"
                index += 2
                continue
            if current == "/" and following == "*":
                masked[index] = masked[index + 1] = " "
                state = "block"
                index += 2
                continue
            if current == '"':
                state = "string"
        elif state == "line":
            if current in "\r\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block":
            if current == "*" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "code"
                index += 2
                continue
            if current not in "\r\n":
                masked[index] = " "
        elif state == "string":
            if current == "\\" and following:
                index += 2
                continue
            if current == '"':
                state = "code"
        index += 1
    return "".join(masked)


def split_code_and_assertions(code: str) -> List[Tuple[str, str]]:
    """Create one standalone CSP# model per active assertion."""
    matches = list(ASSERTION_PATTERN.finditer(mask_comments(code)))
    if not matches:
        return []

    body_parts: List[str] = []
    previous_end = 0
    for match in matches:
        body_parts.append(code[previous_end : match.start()])
        previous_end = match.end()
    body_parts.append(code[previous_end:])
    body = "".join(body_parts).rstrip()

    blocks = []
    for match in matches:
        assertion = code[match.start(1) : match.end(1)].strip()
        blocks.append((assertion, f"{body}\n\n{assertion}\n"))
    return blocks


def normalize_assertions(code: str) -> List[str]:
    return [
        re.sub(r"\s+", " ", assertion).strip()
        for assertion, _ in split_code_and_assertions(code)
    ]


def build_pat_command(
    pat_executable: Path,
    assertion: str,
    input_file: Path,
    output_file: Path,
) -> List[str]:
    command = [str(pat_executable)] if os.name == "nt" else ["mono", str(pat_executable)]
    command.append("-csp")
    lowered = assertion.lower()
    if "reaches" in lowered or "deadlockfree" in lowered:
        command.extend(["-engine", "1"])
    command.extend([str(input_file), str(output_file)])
    return command


def read_text_if_present(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def parse_pat_output(output: str) -> Dict[str, Any]:
    start = output.find(RESULT_START_MARKER)
    end = output.find(RESULT_END_MARKER)
    if start == -1:
        verification_text = ""
    elif end == -1 or end <= start:
        verification_text = output[start + len(RESULT_START_MARKER) :].strip()
    else:
        verification_text = output[start + len(RESULT_START_MARKER) : end].strip()

    if re.search(
        r"\bis\s+(?:not\s+valid|invalid)\b", verification_text, re.IGNORECASE
    ):
        validity = "INVALID"
    elif re.search(r"\bis\s+valid\b", verification_text, re.IGNORECASE):
        validity = "VALID"
    else:
        validity = "UNKNOWN"

    trace = None
    for line in verification_text.splitlines():
        line = line.strip()
        if line.startswith("<") and "->" in line:
            trace = line
            break
    return {
        "validity": validity,
        "trace": trace,
        "verification_text": verification_text,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def verify_code(
    code: str,
    round_dir: Path,
    pat_executable: Path,
    timeout: float,
) -> Dict[str, Any]:
    """Run PAT for all assertions; every assertion is expected to be VALID."""
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "candidate.csp").write_text(code, encoding="utf-8")
    blocks = split_code_and_assertions(code)
    started = time.perf_counter()
    results = []

    for index, (assertion, block) in enumerate(blocks, start=1):
        assertion_file = round_dir / f"assertion_{index:03d}.csp"
        output_file = round_dir / f"pat_output_{index:03d}.txt"
        assertion_file.write_text(block, encoding="utf-8")
        command = build_pat_command(
            pat_executable, assertion, assertion_file, output_file
        )
        assertion_started = time.perf_counter()
        try:
            process = subprocess.run(
                command,
                cwd=str(pat_executable.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            stdout = process.stdout or ""
            stderr = process.stderr or ""
            raw_output = read_text_if_present(output_file)
            parsed = parse_pat_output(raw_output)
            error = None
            if process.returncode != 0:
                error = f"PAT exited with code {process.returncode}."
            elif "Error occurred:" in stdout:
                error = next(
                    line.strip()
                    for line in stdout.splitlines()
                    if "Error occurred:" in line
                )
            elif not raw_output:
                error = "PAT produced an empty output file."
            elif parsed["validity"] == "UNKNOWN":
                error = "PAT output has no recognizable verification result."
            return_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            raw_output = read_text_if_present(output_file)
            parsed = parse_pat_output(raw_output)
            error = f"PAT timed out after {timeout:g} seconds."
            return_code = None

        result = {
            "index": index,
            "assertion": assertion,
            "expected": "VALID",
            "actual": parsed["validity"],
            "matches_expected": parsed["validity"] == "VALID",
            "trace": parsed["trace"],
            "verification_text": parsed["verification_text"],
            "elapsed_seconds": round(time.perf_counter() - assertion_started, 6),
            "command": command,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
            "input_file": assertion_file.name,
            "output_file": output_file.name,
            "error": error,
        }
        results.append(result)
        print(f"      {assertion} -> {result['actual']}")

    counts = {
        "total": len(results),
        "valid": sum(item["actual"] == "VALID" for item in results),
        "invalid": sum(item["actual"] == "INVALID" for item in results),
        "unknown": sum(item["actual"] == "UNKNOWN" for item in results),
    }
    if not blocks:
        status = "NO_ASSERTIONS"
    elif counts["unknown"] or any(item["error"] for item in results):
        status = "ERROR"
    elif counts["invalid"]:
        status = "MISMATCH"
    else:
        status = "PASSED"

    mismatches = [
        {
            "assertion": item["assertion"],
            "trace": item["trace"] or "<init>",
            "current_result": item["actual"],
            "desired_result": "VALID",
            "verification_text": item["verification_text"],
        }
        for item in results
        if item["actual"] == "INVALID"
    ]
    document = {
        "status": status,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "counts": counts,
        "assertions": results,
        "mismatches": mismatches,
    }
    write_json(round_dir / "verification_results.json", document)
    write_json(round_dir / "mismatch_traces.json", mismatches)
    return document


def process_mismatch_traces(mismatches: Sequence[Dict[str, Any]]) -> str:
    """Convert PAT failures into the repair guidance used by PAT-Agent."""
    messages = []
    for mismatch in mismatches:
        assertion = mismatch["assertion"]
        trace = mismatch.get("trace") or "<init>"
        if "deadlockfree" in assertion.lower():
            message = (
                f"The model violates {assertion}. PAT found a deadlock through "
                f"this trace: {trace}. Inspect the final state and the guards/effects "
                "of actions in the trace, then make the smallest model change that "
                "gives every unintended deadlock state a valid outgoing transition."
            )
        elif trace == "<init>":
            message = (
                f"The model violates {assertion} from the initial state. Check the "
                "initial values, constants, guards, and property-related state."
            )
        else:
            actions = [part.strip() for part in trace.strip("<>").split("->")]
            last_action = actions[-1]
            earlier_actions = ", ".join(actions[:-1])
            message = (
                f"The model violates {assertion}. Counterexample: {trace}. The last "
                f"action is {last_action}; first inspect whether its guard is too weak "
                f"or its effect is wrong, then inspect earlier actions: {earlier_actions}."
            )
        messages.append(message)
    return "\n\n".join(messages)


def build_refinement_prompt(code: str, mismatches: Sequence[Dict[str, Any]]) -> str:
    guidance = process_mismatch_traces(mismatches)
    return f"""You are an expert in CSP# and PAT (Process Analysis Toolkit).

Repair the CSP# model using the PAT counterexamples below. Make the smallest
relevant changes to model behavior. Preserve the intended domain behavior in
comments and do not introduce unrelated transitions.

Strict constraints:
1. Return the complete revised CSP# model in one fenced code block.
2. Do not add, remove, weaken, strengthen, or rewrite any #assert statement.
3. Keep all original assertions in their original order.
4. Do not repair a failure by changing the property being checked.
5. Preserve required semicolons and valid CSP# syntax.

Current CSP# model:
```csp
{code}
```

PAT-guided repair information:
{guidance}
"""


def call_refinement_agent(
    prompt: str, llm_client: OpenAICompatibleLLM, max_tokens: int
) -> str:
    return llm_client.complete(prompt, max_tokens=max_tokens)


def extract_csp_code(response: str) -> str:
    blocks = re.findall(r"```(?:[^\n`]*)\n(.*?)```", response, re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip()
    return response.strip()


def verify_and_refine_model(
    source_file: Path,
    model_index: int,
    run_dir: Path,
    pat_executable: Path,
    timeout: float,
    refine: bool,
    max_refine_rounds: int,
    llm_client: Optional[OpenAICompatibleLLM],
    max_tokens: int,
) -> Dict[str, Any]:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_file.stem).strip("._")
    model_dir = run_dir / f"{model_index:03d}-{safe_name or 'model'}"
    model_dir.mkdir()
    shutil.copy2(source_file, model_dir / "input.csp")
    current_code = source_file.read_text(encoding="utf-8-sig")
    original_assertions = normalize_assertions(current_code)

    print("    verification round 0")
    current_result = verify_code(
        current_code, model_dir / "round_000", pat_executable, timeout
    )
    rounds = [{"round": 0, "status": current_result["status"]}]
    stop_reason = "INITIAL_VERIFICATION_PASSED"

    if current_result["status"] == "MISMATCH" and not refine:
        stop_reason = "REFINEMENT_DISABLED"
    elif current_result["status"] == "MISMATCH":
        for round_number in range(1, max_refine_rounds + 1):
            print(f"    refinement round {round_number}")
            round_dir = model_dir / f"round_{round_number:03d}"
            round_dir.mkdir()
            prompt = build_refinement_prompt(
                current_code, current_result["mismatches"]
            )
            (round_dir / "refinement_prompt.txt").write_text(prompt, encoding="utf-8")
            try:
                if llm_client is None:
                    raise LLMError("LLM client is unavailable while refinement is enabled.")
                response = call_refinement_agent(prompt, llm_client, max_tokens)
            except LLMError as exc:
                write_json(round_dir / "refinement_error.json", {"error": str(exc)})
                rounds.append(
                    {"round": round_number, "status": "AGENT_ERROR", "error": str(exc)}
                )
                stop_reason = "REFINEMENT_AGENT_ERROR"
                break

            (round_dir / "refinement_response.txt").write_text(
                response, encoding="utf-8"
            )
            candidate_code = extract_csp_code(response)
            (round_dir / "candidate.csp").write_text(candidate_code, encoding="utf-8")

            if normalize_assertions(candidate_code) != original_assertions:
                error = "Repair candidate changed the original #assert statements."
                write_json(round_dir / "candidate_rejected.json", {"error": error})
                rounds.append(
                    {"round": round_number, "status": "CANDIDATE_REJECTED", "error": error}
                )
                stop_reason = "ASSERTIONS_CHANGED"
                break
            if candidate_code.strip() == current_code.strip():
                rounds.append({"round": round_number, "status": "NO_CHANGE"})
                stop_reason = "NO_MODEL_CHANGE"
                break

            current_code = candidate_code
            current_result = verify_code(
                current_code, round_dir, pat_executable, timeout
            )
            rounds.append({"round": round_number, "status": current_result["status"]})
            if current_result["status"] == "PASSED":
                stop_reason = "ALL_ASSERTIONS_VALID"
                break
            if current_result["status"] != "MISMATCH":
                stop_reason = "PAT_VERIFICATION_ERROR"
                break
        else:
            stop_reason = "MAX_REFINEMENT_ROUNDS_REACHED"
    elif current_result["status"] == "NO_ASSERTIONS":
        stop_reason = "NO_ASSERTIONS"
    elif current_result["status"] == "ERROR":
        stop_reason = "PAT_VERIFICATION_ERROR"

    (model_dir / "final.csp").write_text(current_code, encoding="utf-8")
    if current_result["status"] == "PASSED":
        (model_dir / "verified.csp").write_text(current_code, encoding="utf-8")

    summary = {
        "source": str(source_file),
        "status": current_result["status"],
        "counts": current_result["counts"],
        "refinement_enabled": refine,
        "refinement_model": llm_client.config.model_id if llm_client else None,
        "rounds": rounds,
        "stop_reason": stop_reason,
        "final_file": "final.csp",
    }
    write_json(model_dir / "model_summary.json", summary)
    return summary


def run_pipeline(args: argparse.Namespace, input_files: Sequence[Path]) -> Tuple[Path, Dict[str, Any]]:
    pat_executable = absolute(args.pat_executable)
    if not pat_executable.is_file():
        raise FileNotFoundError(f"PAT executable not found: {pat_executable}")
    if args.timeout <= 0 or args.max_refine_rounds < 1 or args.max_tokens < 1:
        raise ValueError("timeout, max-refine-rounds, and max-tokens must be positive.")

    llm_client = None
    llm_public_config = None
    if not args.no_refine:
        llm_config = load_llm_config(args.env_file)
        llm_client = OpenAICompatibleLLM(llm_config)
        llm_public_config = llm_config.public_dict()

    run_dir = create_run_directory(args.results_dir)
    started_at = dt.datetime.now().astimezone()
    started = time.perf_counter()
    models = []
    print(f"Run: {run_dir.name}")
    for index, input_file in enumerate(input_files, start=1):
        print(f"  [{index}/{len(input_files)}] {input_file}")
        try:
            result = verify_and_refine_model(
                input_file,
                index,
                run_dir,
                pat_executable,
                args.timeout,
                not args.no_refine,
                args.max_refine_rounds,
                llm_client,
                args.max_tokens,
            )
        except Exception as exc:
            result = {
                "source": str(input_file),
                "status": "ERROR",
                "counts": {"total": 0, "valid": 0, "invalid": 0, "unknown": 0},
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"    ERROR: {result['error']}", file=sys.stderr)
        models.append(result)

    summary = {
        "models": len(models),
        "passed": sum(item["status"] == "PASSED" for item in models),
        "unrepaired": sum(item["status"] == "MISMATCH" for item in models),
        "errors": sum(item["status"] in {"ERROR", "NO_ASSERTIONS"} for item in models),
        "assertions": sum(item["counts"]["total"] for item in models),
        "valid": sum(item["counts"]["valid"] for item in models),
        "invalid": sum(item["counts"]["invalid"] for item in models),
        "unknown": sum(item["counts"]["unknown"] for item in models),
    }
    manifest = {
        "schema_version": 2,
        "run_id": run_dir.name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "pat_executable": str(pat_executable),
        "refinement": {
            "enabled": not args.no_refine,
            "max_rounds": args.max_refine_rounds,
            "llm": llm_public_config,
        },
        "summary": summary,
        "models": models,
    }
    write_json(run_dir / "manifest.json", manifest)
    return run_dir, manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.input_dir.mkdir(parents=True, exist_ok=True)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    try:
        input_files = discover_inputs(args.inputs, args.input_dir)
        if not input_files:
            print(f"No CSP# files found in {absolute(args.input_dir)}", file=sys.stderr)
            return 2
        run_dir, manifest = run_pipeline(args, input_files)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = manifest["summary"]
    print(
        f"Completed: {summary['passed']} passed, {summary['unrepaired']} unrepaired, "
        f"{summary['errors']} errors."
    )
    print(f"Results: {run_dir}")
    return 0 if summary["unrepaired"] == 0 and summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
