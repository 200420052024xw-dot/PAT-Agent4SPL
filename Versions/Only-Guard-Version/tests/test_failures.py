"""验证先检查迁移覆盖、再补全失败缺口的转换行为。"""

import json
import tempfile
import unittest
from pathlib import Path

from src.conversion.compiler import compile_controller
from src.conversion.guards import evaluate_guard
from src.conversion.renderer import render_csp
from src.model import Check, Expr
from src.pipeline import RunOptions, convert_controller_ir
from src.verification import run_pat_checks
from tests.joint_fixtures import action, controller


PAT = Path(__file__).resolve().parents[1] / "PAT.Console" / "Process-Analysis-Toolkit" / "PAT3.Console.exe"
SUCCESS = 'input.stage_execution.doActivity.execution_status == "success"'
FAILURE = 'input.stage_execution.doActivity.execution_status == "failure"'


class FailureSemantic:
    """只提供有明确描述依据的失败去向，记录实际调用上下文。"""

    def __init__(self, behavior="pause", evidence="doActivity失败后暂停等待人工处理。", condition=None):
        self.behavior, self.evidence, self.condition = behavior, evidence, condition
        self.calls = []

    def resolve_failure(self, context):
        self.calls.append(context)
        return {"failure_behavior": self.behavior, "evidence": self.evidence,
                "condition": self.condition, "reason": "按描述处理未覆盖的失败状态"}


class FailureTests(unittest.TestCase):
    def test_covered_failure_does_not_call_llm(self):
        semantic = FailureSemantic()
        model = compile_controller(controller({}, [SUCCESS, FAILURE], action()), semantic)
        self.assertEqual(semantic.calls, [])
        self.assertEqual(model.failure_records, [])

    def test_true_guard_uses_current_assumption_without_extra_analysis(self):
        semantic = FailureSemantic()
        compile_controller(controller({}, [True], action()), semantic)
        self.assertEqual(semantic.calls, [])

    def test_partial_gap_does_not_override_existing_valid_edge(self):
        semantic = FailureSemantic()
        slots = {"allowed": {"type": "boolean", "default_value": False}}
        raw = controller(slots, [SUCCESS, {"all_of": [FAILURE, 'input.allowed == true']}], action())
        raw["stages"]["initial"]["description"] = semantic.evidence
        model = compile_controller(raw, semantic)
        domain = model.variables[0]
        extra = next(edge for edge in model.edges if "pause" in edge.event)
        original = next(edge for edge in model.edges if edge.source_path.endswith("/transitions/1"))
        status = {"entry": 0, "doActivity": 2, "exit": 0}
        true_value, false_value = domain.value_for(True), domain.value_for(False)
        self.assertTrue(evaluate_guard(original.guard, {domain.name: true_value}, status))
        self.assertFalse(evaluate_guard(extra.guard, {domain.name: true_value}, status))
        self.assertTrue(evaluate_guard(extra.guard, {domain.name: false_value}, status))
        self.assertEqual(len(semantic.calls), 1)
        self.assertIn("states", semantic.calls[0]["domains"]["allowed"])

    def test_pause_is_stop_with_its_own_outcome(self):
        semantic = FailureSemantic()
        raw = controller({}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = semantic.evidence
        model = compile_controller(raw, semantic)
        pause = next(edge for edge in model.edges if "pause" in edge.event)
        process = next(process for process in model.processes if process.name == pause.target)
        self.assertTrue(process.paused)
        self.assertFalse(process.terminal)
        self.assertEqual([(effect.variable, effect.value) for effect in pause.effects], [("run_outcome", 3)])
        self.assertIn("doActivity_paused() = Stop;", render_csp(model))

    def test_terminate_marks_aborted_and_keeps_normal_completion_distinct(self):
        description = "doActivity失败后终止本次运行。"
        raw = controller({}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = description
        model = compile_controller(raw, FailureSemantic("terminate", description))
        abort = next(edge for edge in model.edges if "terminate" in edge.event)
        success = next(edge for edge in model.edges if edge.source_path.endswith("/transitions/0"))
        self.assertEqual(abort.effects[0].value, 2)
        self.assertEqual(next(effect.value for effect in success.effects if effect.variable == "run_outcome"), 1)
        self.assertTrue(next(process.terminal for process in model.processes if process.name == abort.target))

    def test_failure_does_not_write_success_result_or_run_later_step(self):
        raw = controller({"flag": {"type": "boolean", "default_value": False}}, [SUCCESS], action(True, "flag"))
        stage = raw["stages"]["initial"]
        stage["entry"], stage["exit"] = action(True, "flag"), action(False, "flag")
        model = compile_controller(raw)
        failure = next(edge for edge in model.edges if edge.source_path.endswith("/doActivity") and "failure" in edge.event)
        self.assertEqual(failure.effects, ())
        self.assertEqual(failure.target_args, ("entry_status", 2, 0))
        self.assertTrue(failure.target == next(process.name for process in model.processes if model.source_map[process.name].endswith("/transitions")))

    def test_retry_preserves_earlier_status_and_slot_updates(self):
        description = "doActivity失败后重试当前步骤。"
        semantic = FailureSemantic("retry", description)
        raw = controller({"flag": {"type": "boolean", "default_value": False}}, [SUCCESS], action())
        raw["stages"]["initial"]["entry"] = action(True, "flag")
        raw["stages"]["initial"]["description"] = description
        model = compile_controller(raw, semantic)
        retry = next(edge for edge in model.edges if "doActivity_retry" in edge.event)
        original = next(process.name for process in model.processes if model.source_map[process.name].endswith("/doActivity"))
        self.assertEqual(retry.target, original)
        self.assertEqual(retry.target_args, ("entry_status", 0, 0))
        self.assertEqual(retry.effects, ())
        call = next(call for call in semantic.calls if call["step"] == "doActivity")
        self.assertEqual(call["failure_status"], {"entry": 1, "doActivity": 2, "exit": 0})

    def test_additional_condition_keeps_remaining_gap_visible(self):
        semantic = FailureSemantic(condition='input.allowed == true')
        raw = controller({"allowed": {"type": "boolean", "default_value": False}}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = semantic.evidence
        model = compile_controller(raw, semantic)
        self.assertTrue(any("仍未覆盖" in item.message for item in model.conversion_diagnostics))

    def test_unsupported_evidence_cannot_create_a_termination(self):
        raw = controller({}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = "成功后终止本次运行。"
        model = compile_controller(raw, FailureSemantic("terminate", "成功后终止本次运行。"))
        self.assertFalse(any("terminate" in edge.event for edge in model.edges))
        self.assertTrue(any(item.code == "FAILURE_GAP" for item in model.conversion_diagnostics))

    def test_failure_decisions_are_saved_in_run_artifacts(self):
        semantic = FailureSemantic()
        raw = controller({}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = semantic.evidence
        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, semantic, options=RunOptions(Path(folder), model_only=True))
            records = json.loads((result.run_dir / "failure_records.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["step"], "doActivity")
        self.assertEqual(records[0]["proposal"]["decision"], "pause")

    @unittest.skipUnless(PAT.is_file(), "需要 PAT 验证暂停的阻塞语义")
    def test_pat_observes_pause_as_reachable_deadlock(self):
        semantic = FailureSemantic()
        raw = controller({}, [SUCCESS], action())
        raw["stages"]["initial"]["description"] = semantic.evidence
        model = compile_controller(raw, semantic)
        model.predicates["paused"] = Expr("in", variable="run_outcome", allowed=(3,))
        model.checks = [Check("pause_reachable", "暂停可达", "reachability", "paused", None, "VALID", ("/stages/initial",)),
                        Check("pause_blocks", "暂停使执行阻塞", "deadlock_free", None, None, "INVALID", ("/stages/initial",))]
        with tempfile.TemporaryDirectory() as folder:
            results = run_pat_checks(model, PAT, Path(folder), 30)
        self.assertEqual([result["actual"] for result in results], ["VALID", "INVALID"], results)


if __name__ == "__main__":
    unittest.main()
