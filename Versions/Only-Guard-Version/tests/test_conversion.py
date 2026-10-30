"""验证转换语义和当前样本的端到端接口。"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.conversion.compiler import compile_controller
from src.conversion.compiler import ConversionError
from src.conversion.checks import attach_checks
from src.conversion.effects import build_effect_choices
from src.conversion.input import load_controller
from src.conversion.renderer import render_csp
from src.pipeline import RunOptions, convert_controller_ir
from src.verification import run_pat_checks
from tests.joint_fixtures import JointSemantic


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "inputs" / "Controller_IR" / "Internal-Comms-IR.json"
PAT = ROOT / "PAT.Console" / "Process-Analysis-Toolkit" / "PAT3.Console.exe"


class FakeSemantic:
    """测试替身只给缺失失败路径与检查内容提供确定回答。"""

    trace: list[dict] = []

    def resolve_failure(self, context):
        return {"decision": "stop", "reason": "失败后结束本次运行", "evidence": context["description"]}

    def resolve_guard(self, atom, domains):
        raise AssertionError(f"样本 guard 应由代码编译：{atom}")

    def resolve_policy(self, context):
        return {"stage": "initial", "source_quote": "审核", "outcomes": [{"name": "pass", "guard": True, "effects": []}]}

    def propose_checks(self, context):
        return {"predicates": [{"name": "reached_completed", "guard": 'current_stage == "completed"', "sources": ["/stages/completed"]}], "checks": [{"name": "completion_reachable", "description": "完成阶段可达", "kind": "reachability", "predicate": "reached_completed", "formula": None, "expected": "VALID", "sources": ["/stages/completed"]}]}


class ConversionTests(unittest.TestCase):
    def test_current_sample_compiles_without_business_name_rules(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        model = compile_controller(raw, FakeSemantic())
        code = render_csp(model)
        self.assertEqual(len(model.variables), 13)
        self.assertGreater(len(model.edges), 100)
        self.assertIn("current_stage", code)
        self.assertTrue(any("/stages/retrieve_emails/doActivity" == path for path in model.source_map.values()))

    def test_pipeline_reports_pat_violation_without_repair(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))

        def pat_stub(model, executable, run_dir, timeout):
            return [{"name": model.checks[0].name, "actual": "INVALID", "expected": "VALID", "matches_expected": False, "trace": "<init -> bad>", "error": None}]

        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, FakeSemantic(), pat_stub, RunOptions(Path(folder)))
            self.assertEqual(result.status, "VIOLATION")
            self.assertTrue((result.run_dir / "model.csp_ir.json").is_file())
            self.assertTrue((result.run_dir / "verification_results.json").is_file())

    def test_pipeline_saves_model_before_check_service_failure(self):
        from llm import LLMError

        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))

        class FailedChecks(FakeSemantic):
            def propose_checks(self, context):
                raise LLMError("检查服务不可用")

        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, FailedChecks(), options=RunOptions(Path(folder)))
            self.assertEqual(result.status, "ENGINE_ERROR")
            self.assertTrue((result.run_dir / "model.csp").is_file())
            self.assertIn("Controller()", (result.run_dir / "model.csp").read_text(encoding="utf-8"))
            self.assertEqual(result.verification, [])

    def test_pipeline_saves_draft_without_verifying_untranslatable_guard(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["stages"]["initial"]["transitions"][0]["guard"] = "unknown_business_condition"

        class UnresolvedSemantic(FakeSemantic):
            def resolve_guard(self, atom, domains):
                return {"error": "无法翻译此条件"}

            def propose_checks(self, context):
                raise AssertionError("草稿不得生成检测项")

        def no_pat(*args):
            raise AssertionError("草稿不得运行 PAT")

        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, UnresolvedSemantic(), no_pat, RunOptions(Path(folder)))
            self.assertEqual(result.status, "INCOMPLETE")
            self.assertIsNone(result.model)
            self.assertIsNotNone(result.draft_model)
            draft = (result.run_dir / "model.draft.csp").read_text(encoding="utf-8")
            self.assertIn("转换未完成", draft)
            self.assertIn("/stages/initial/transitions/0/guard", draft)
            self.assertIn("Controller()", draft)
            self.assertNotIn("#assert", draft)
            self.assertFalse((result.run_dir / "model.csp").exists())
            manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["model_file"], "model.draft.csp")
            self.assertFalse(manifest["model_complete"])

    def test_model_only_saves_complete_model_with_failure_warnings(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))

        class UnresolvedSemantic(FakeSemantic):
            def resolve_failure(self, context):
                return {"decision": "unresolved"}

            def propose_checks(self, context):
                raise AssertionError("只转换时不得生成检查项")

        def no_pat(*args):
            raise AssertionError("只转换时不得调用 PAT")

        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, UnresolvedSemantic(), no_pat, RunOptions(Path(folder), model_only=True))
            self.assertEqual(result.status, "CONVERTED")
            self.assertIsNotNone(result.model)
            self.assertTrue(all(item.severity == "warning" for item in result.diagnostics))
            self.assertTrue((result.run_dir / "model.csp").is_file())
            self.assertFalse((result.run_dir / "model.draft.csp").exists())
            manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["model_complete"])
            self.assertEqual(manifest["check_count"], 0)

    def test_invalid_input_does_not_save_misleading_model(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["stages"]["initial"]["transitions"][0]["target"] = "missing"
        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, FakeSemantic(), options=RunOptions(Path(folder)))
            self.assertEqual(result.status, "INCOMPLETE")
            self.assertFalse((result.run_dir / "model.csp").exists())
            self.assertFalse((result.run_dir / "model.draft.csp").exists())

    def test_missing_step_is_skipped_and_exit_runs_before_transition(self):
        raw = {"description": "顺序流程", "slot_information": {"flag": {"type": "boolean", "default_value": False}}, "participants": {}, "actions": {"SET": {}}, "policies": {}, "stages": {"initial": {"kind": "initial", "entry": None, "doActivity": None, "exit": None, "transitions": [{"target": "work", "guard": True}]}, "work": {"kind": "state", "entry": None, "doActivity": {"action": "SET", "parameters": {"value": True}, "when_success_write_to": {"operation": "overwrite", "target": "slots.flag"}}, "exit": {"action": "SET", "parameters": {"value": False}, "when_success_write_to": {"operation": "overwrite", "target": "slots.flag"}}, "transitions": [{"target": "done", "guard": True}]}, "done": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}
        model = compile_controller(raw)
        paths = model.source_map
        self.assertFalse(any(path == "/stages/work/entry" for path in paths.values()))
        do_edge = next(edge for edge in model.edges if paths[edge.event] == "/stages/work/doActivity" and "success" in edge.event)
        self.assertEqual(paths[do_edge.target], "/stages/work/exit")

    @unittest.skipUnless(PAT.is_file(), "PAT 程序不存在")
    def test_pat_compiles_generated_model(self):
        raw = {"description": "可以完成", "slot_information": {}, "participants": {}, "actions": {}, "policies": {}, "stages": {"initial": {"kind": "initial", "entry": None, "doActivity": None, "exit": None, "transitions": [{"target": "completed", "guard": True}]}, "completed": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}
        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, FakeSemantic(), run_pat_checks, RunOptions(Path(folder), PAT, 30.0))
            self.assertEqual(result.status, "PASS", result.verification)

    @unittest.skipUnless(PAT.is_file(), "PAT 程序不存在")
    def test_current_sample_runs_in_pat(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, FakeSemantic(), run_pat_checks, RunOptions(Path(folder), PAT, 120.0))
            self.assertIn(result.status, {"PASS", "VIOLATION"}, result.verification)

    def test_fixed_list_member_and_length_share_one_domain(self):
        raw = {"description": "列表条件", "slot_information": {"items": {"type": "list", "default_value": []}}, "participants": {}, "actions": {}, "policies": {}, "stages": {"initial": {"kind": "initial", "entry": None, "doActivity": None, "exit": None, "transitions": [{"target": "done", "guard": {"all_of": ['contains(input.slots.items, "a")', "count(input.slots.items) >= 2"]}}]}, "done": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}
        model = compile_controller(raw, JointSemantic())
        domain = next(item for item in model.variables if item.source == "/slot_information/items")
        self.assertIsNotNone(domain.value_for(["a", "b", "c"]))
        self.assertTrue(any('"a" in' in atom for atom in domain.observations))

    def test_nonempty_policy_has_explicit_sequential_position(self):
        raw = {"description": "先审核", "slot_information": {}, "participants": {}, "actions": {}, "policies": {"rule": {"description": "审核"}}, "stages": {"initial": {"kind": "initial", "entry": None, "doActivity": None, "exit": None, "transitions": [{"target": "done", "guard": True}]}, "done": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}
        model = compile_controller(raw, FakeSemantic())
        self.assertEqual(model.source_map[model.initial_process], "/policies/rule")
        self.assertTrue(any(path.startswith("/policies/rule/outcomes/") for path in model.source_map.values()))

    def test_policy_cannot_be_silently_attached_to_terminal_stage(self):
        raw = {"description": "先审核", "slot_information": {}, "participants": {}, "actions": {}, "policies": {"rule": {"description": "审核"}}, "stages": {"initial": {"kind": "initial", "entry": None, "doActivity": None, "exit": None, "transitions": [{"target": "done", "guard": True}]}, "done": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}

        class TerminalPolicy(FakeSemantic):
            def resolve_policy(self, context):
                return {"stage": "done", "source_quote": "审核", "outcomes": [{"name": "pass", "guard": True, "effects": []}]}

        with self.assertRaises(ConversionError) as caught:
            compile_controller(raw, TerminalPolicy())
        self.assertEqual(caught.exception.diagnostics[0].path, "/policies/rule")

    def test_unresolved_failure_preserves_graph_and_reports_warning(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))

        class UnresolvedSemantic(FakeSemantic):
            def resolve_failure(self, context):
                if context["stage"] == "revise_slot":
                    return {"decision": "unresolved", "evidence": ["成功结果"]}
                return super().resolve_failure(context)

        model = compile_controller(raw, UnresolvedSemantic())
        self.assertTrue(any(item.path == "/stages/revise_slot/doActivity" and "未能确定" in item.message and item.severity == "warning" for item in model.conversion_diagnostics))
        self.assertFalse(any("revise_slot_failed" in process.name for process in model.processes))

    @unittest.skipUnless(PAT.is_file(), "PAT 程序不存在")
    def test_failure_without_successor_is_detectable_by_pat(self):
        from src.model import Check

        raw = {"description": "验证失败路径", "slot_information": {}, "participants": {}, "actions": {"DO": {"description": "执行操作。"}}, "policies": {}, "stages": {"initial": {"kind": "initial", "description": "执行操作，成功后结束。", "entry": None, "doActivity": {"action": "DO", "parameters": {}, "when_success_write_to": None}, "exit": None, "transitions": [{"target": "done", "guard": 'input.stage_execution.doActivity.execution_status == "success"'}]}, "done": {"kind": "termination", "entry": None, "doActivity": None, "exit": None, "transitions": []}}}
        # 相同动作的两种来源约定：没有失败后继时应死锁，明确终止时应结束。
        for description, actual in [("执行操作，成功后结束。", "INVALID"), ("动作失败后停止本次运行。", "VALID")]:
            with self.subTest(description=description):
                raw["stages"]["initial"]["description"] = description
                model = compile_controller(raw, FakeSemantic())
                model.checks.append(Check("no_deadlock", "无死锁", "deadlock_free", None, None, "VALID", ("/stages/initial",)))
                with tempfile.TemporaryDirectory() as folder:
                    results = run_pat_checks(model, PAT, Path(folder), 30.0)
                self.assertEqual(results[0]["actual"], actual, results)
                self.assertIsNone(results[0]["error"])

    def test_failure_stop_accepts_multiple_exact_quotes(self):
        from src.conversion.compiler import _failure_quotes, _supports_failure_decision

        stage = {"description": "失败后停止本次运行。"}
        action = {"description": "失败时不继续生成。"}
        for evidence in ["失败后停止本次运行。失败时不继续生成。", [stage["description"], action["description"]]]:
            quotes = _failure_quotes(evidence, stage, action)
            self.assertTrue(_supports_failure_decision({"decision": "stop"}, quotes))
        self.assertEqual(_failure_quotes("失败后跳转到不存在的目标。", stage, action), [])
        self.assertFalse(_supports_failure_decision({"decision": "stop"}, ["展示说明并等待回复。", "失败时不得停止本次运行。", "失败时停止本次运行吗？"]))

    def test_renaming_stages_does_not_change_graph_shape(self):
        original = json.loads(SAMPLE.read_text(encoding="utf-8"))
        renamed = deepcopy(original)
        renamed["stages"] = {"s_" + name: stage for name, stage in renamed["stages"].items()}
        for stage in renamed["stages"].values():
            for edge in stage["transitions"]:
                edge["target"] = "s_" + edge["target"]
        left = compile_controller(original, FakeSemantic())
        right = compile_controller(renamed, FakeSemantic())
        self.assertEqual(len(left.processes), len(right.processes))
        self.assertEqual(len(left.edges), len(right.edges))

    def test_invalid_target_is_reported_at_ir_location(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["stages"]["initial"]["transitions"][0]["target"] = "missing"
        with self.assertRaises(ConversionError) as caught:
            compile_controller(raw, FakeSemantic())
        self.assertEqual(caught.exception.diagnostics[0].path, "/stages/initial/transitions/0")

    def test_invalid_stage_reports_diagnostic(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        raw["stages"]["initial"] = None
        with self.assertRaises(ConversionError) as caught:
            compile_controller(raw, FakeSemantic())
        self.assertTrue(any(item.path == "/stages/initial" for item in caught.exception.diagnostics))

    def test_jsonc_loader_keeps_comment_markers_in_strings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonc"
            path.write_text('{"url":"https://example.test/a", // comment\n"value": 1,}', encoding="utf-8")
            self.assertEqual(load_controller(path)["url"], "https://example.test/a")

    def test_ai_observer_updates_event_and_check_source(self):
        raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
        model = compile_controller(raw, FakeSemantic())
        event = next(edge.event for edge in model.edges if edge.source_path == "/stages/initial/transitions/0")

        class ObserverSemantic:
            def propose_checks(self, context):
                return {"observers": [{"name": "started_mark", "values": ["no", "yes"], "initial": "no", "updates": [{"event": event, "value": "yes"}], "sources": ["/stages/initial"]}], "predicates": [{"name": "started", "guard": 'started_mark == "yes"', "sources": ["/stages/initial"]}], "checks": [{"name": "starts", "description": "初始出边可执行", "kind": "reachability", "predicate": "started", "expected": "VALID", "sources": ["/stages/initial"]}]}

        self.assertEqual(attach_checks(model, raw, ObserverSemantic()), [])
        updated = next(edge for edge in model.edges if edge.event == event)
        self.assertEqual(updated.effects[-1].variable, "started_mark")
        self.assertEqual(model.source_map["starts"], "/stages/initial")

    def test_ai_effect_cannot_override_explicit_target(self):
        from src.model import FiniteVariable, StateValue

        domain = FiniteVariable("flag", "/slot_information/flag", [StateValue("false", False), StateValue("true", True)], 0)
        step = {"when_success_write_to": {"operation": "overwrite", "target": "slots.flag"}}
        action = {"description": "设置标志"}

        class EffectSemantic:
            def resolve_effects(self, context):
                return {"source_quote": "设置标志", "effects": [{"target": "slots.other", "allowed": [1]}]}

        with self.assertRaisesRegex(ValueError, "明确写回目标冲突"):
            build_effect_choices(step, action, {"flag": domain}, EffectSemantic())


if __name__ == "__main__":
    unittest.main()
