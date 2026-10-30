"""验证可读输出、名称冲突和 PAT 反例来源映射。"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.conversion.compiler import compile_controller
from src.conversion.renderer import render_csp
from src.conversion.symbols import build_symbols
from src.model import Check, Expr, FiniteVariable, StateValue
from src.pipeline import RunOptions, convert_controller_ir
from src.verification import run_pat_checks


ROOT = Path(__file__).resolve().parents[1]
PAT = ROOT / "PAT.Console" / "Process-Analysis-Toolkit" / "PAT3.Console.exe"


def controller():
    return {"description": "收集文档类型", "slot_information": {"comms_type": {"type": "enum", "enum_values": ["3P", "Newsletter"], "default_value": None}}, "participants": {}, "actions": {"ASK": {}}, "policies": {}, "stages": {"initial": {"kind": "initial", "doActivity": {"action": "ASK", "parameters": {}, "when_success_write_to": {"operation": "overwrite", "target": "slots.comms_type"}}, "transitions": [{"target": "done", "guard": {"all_of": ['input.stage_execution.doActivity.execution_status == "success"', 'input.slots.comms_type == "Newsletter"']}}]}, "done": {"kind": "termination", "transitions": []}}}


class RenderingTests(unittest.TestCase):
    def test_final_names_and_constants_preserve_internal_model(self):
        model = compile_controller(controller())
        original = deepcopy(model.to_dict())
        code = render_csp(model)
        self.assertIn("#define COMMS_TYPE_NEWSLETTER 2;", code)
        self.assertIn("var slot_comms_type = COMMS_TYPE_NULL;", code)
        self.assertIn("slot_comms_type == COMMS_TYPE_NEWSLETTER", code)
        self.assertIn("slot_comms_type = COMMS_TYPE_NEWSLETTER;", code)
        self.assertIn("do_status == STATUS_SUCCESS", code)
        self.assertIn("entry_status, STATUS_FAILURE, STATUS_NOT_RUN", code)
        self.assertIn("Controller() = initial_doActivity(STATUS_NOT_RUN, STATUS_NOT_RUN, STATUS_NOT_RUN);", code)
        self.assertNotRegex(code, r"\bP_\w+|_[0-9a-f]{6}\b")
        self.assertEqual(model.to_dict(), original)

    def test_name_collisions_keep_distinct_states_and_ltl_references(self):
        model = compile_controller(controller())
        event = model.edges[0].event
        # 谓词与事件的可读名称、保留字相撞，均需保留独立引用。
        event_name = build_symbols(model).names[event]
        model.predicates[event_name] = Expr("in", variable="current_stage", allowed=(1,))
        model.variables.extend([FiniteVariable("Skip", "/stages/initial", [StateValue("a-b", "a-b"), StateValue("a_b", "a_b")], 0)])
        model.checks = [Check("eventually_done", "最终结束", "ltl", None, f"<> ({event} || {event_name})", "VALID", ("/stages/done",))]
        symbols = build_symbols(model)
        self.assertNotEqual(symbols.names[event], symbols.names[event_name])
        self.assertNotEqual(symbols.names["Skip"], "Skip")
        self.assertNotEqual(symbols.values["Skip", 0], symbols.values["Skip", 1])
        code = render_csp(model)
        self.assertIn(f"#assert Controller |= <> ({symbols.names[event]} || {symbols.names[event_name]});", code)
        self.assertIn(f"#define {symbols.names[event_name]} ", code)
        self.assertEqual(code, render_csp(model))
        self.assertEqual(code, render_csp(model, checks=model.checks))

    def test_abstract_samples_are_named_by_observed_meaning(self):
        model = compile_controller(controller())
        model.variables.extend([
            FiniteVariable("text", "/slot_information/text", [StateValue("UNSET", None), StateValue("VALUE_1", ""), StateValue("VALUE_2", "x")], 1),
            FiniteVariable("items", "/slot_information/items", [StateValue("VALUE_0", []), StateValue("VALUE_1", ["a"]), StateValue("VALUE_2", ["_other_0"])], 0, ['contains(input.slots.items, "a")']),
        ])
        symbols = build_symbols(model)
        self.assertEqual(symbols.values["text", 2], "TEXT_NONEMPTY")
        self.assertEqual(symbols.values["items", 1], "ITEMS_LENGTH_1_CONTAINS_A")
        self.assertEqual(symbols.values["items", 2], "ITEMS_LENGTH_1_NO_TRACKED_MEMBERS")

    def test_readable_pat_trace_is_mapped_back_to_ir(self):
        class Semantic:
            def resolve_failure(self, context):
                return {"decision": "unresolved"}

            def propose_checks(self, context):
                return {"checks": [{"name": "no_deadlock", "description": "无死锁", "kind": "deadlock_free", "expected": "VALID", "sources": ["/stages/initial"]}]}

        def runner(model, executable, run_dir, timeout):
            name = build_symbols(model).names[model.edges[0].event]
            return [{"name": "no_deadlock", "actual": "INVALID", "expected": "VALID", "matches_expected": False, "trace": f"<init -> {name}>", "error": None}]

        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(controller(), Semantic(), runner, RunOptions(Path(folder)))
            self.assertEqual(result.verification[0]["trace_sources"], ["/stages/initial/doActivity"])
            sources = json.loads((result.run_dir / "source_map.json").read_text(encoding="utf-8"))
            mapping = json.loads((result.run_dir / "render_map.json").read_text(encoding="utf-8"))
            self.assertEqual(sources["initial_doActivity"], "/stages/initial/doActivity")
            self.assertEqual(sources["COMMS_TYPE_NEWSLETTER"], "/slot_information/comms_type")
            self.assertEqual(mapping["names"][result.model.initial_process], "initial_doActivity")

    @unittest.skipUnless(PAT.is_file(), "PAT 程序不存在")
    def test_pat_resolves_renamed_events_and_constants(self):
        model = compile_controller(controller())
        event = model.edges[0].event
        predicate_name = build_symbols(model).names[event]
        model.predicates[predicate_name] = Expr("in", variable="current_stage", allowed=(1,))
        model.checks = [Check("event_seen", "动作事件可出现", "ltl", None, f"[] !{event}", "INVALID", ("/stages/initial",)), Check("completed", "结束可达", "reachability", predicate_name, None, "VALID", ("/stages/done",))]
        with tempfile.TemporaryDirectory() as folder:
            results = run_pat_checks(model, PAT, Path(folder), 30.0)
        self.assertEqual([item["actual"] for item in results], ["INVALID", "VALID"], results)
        self.assertTrue(all(item["error"] is None for item in results), results)


if __name__ == "__main__":
    unittest.main()
