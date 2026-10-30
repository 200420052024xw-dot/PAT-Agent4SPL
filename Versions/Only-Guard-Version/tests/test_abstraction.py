"""验证特殊值、联合抽象、Rego 绑定和枚举写回的实际行为。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.conversion.compiler import ConversionError, compile_controller
from src.conversion.effects import build_effect_choices
from src.conversion.guards import compile_guard, evaluate_guard
from src.conversion.rego import MISSING, _opa_query, evaluate, evaluate_known, opa_executable
from src.conversion.registry import build_registry
from src.conversion.renderer import render_csp
from src.model import Check, Expr
from src.pipeline import RunOptions, convert_controller_ir
from src.verification import run_pat_checks
from tests.joint_fixtures import JointSemantic, action, controller


ROOT = Path(__file__).resolve().parents[1]
PAT = ROOT / "PAT.Console" / "Process-Analysis-Toolkit" / "PAT3.Console.exe"
SUCCESS = 'input.stage_execution.doActivity.execution_status == "success"'


class AbstractionTests(unittest.TestCase):
    def test_missing_null_empty_and_nonempty_are_distinct(self):
        guards = ['input.roles == null', 'input.roles != null', 'count(input.roles) == 0']
        raw = controller({"roles": {"type": "list", "default_value": None, "allow_missing": True}}, guards)
        registry, errors = build_registry(raw)
        self.assertEqual(errors, [])
        domain = registry.domains["roles"]
        missing, null, empty, filled = [domain.value_for(value) for value in (MISSING, None, [], ["a"])]
        self.assertEqual(len({missing, null, empty, filled}), 4)
        self.assertNotIn(missing, registry.guards['input.roles != null'].allowed)
        self.assertNotIn(null, registry.guards['count(input.roles) == 0'].allowed)
        self.assertIn(empty, registry.guards['count(input.roles) == 0'].allowed)

    def test_string_count_handles_null_without_conversion_error(self):
        raw = controller({"name": {"type": "string", "default_value": None}}, ['count(input.name) > 0'])
        model = compile_controller(raw)
        domain = model.variables[0]
        guard = model.edges[0].guard
        self.assertNotIn(domain.value_for(None), guard.allowed)
        self.assertIn(domain.value_for("Alice"), guard.allowed)

    def test_equal_predicate_results_merge_representative_lengths(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['count(input.items) > 2'])
        registry, errors = build_registry(raw)
        self.assertEqual(errors, [])
        domain = registry.domains["items"]
        self.assertEqual(domain.value_for([1]), domain.value_for([1, 2]))
        self.assertEqual(domain.value_for([1, 2, 3]), domain.value_for(list(range(20))))
        self.assertNotEqual(domain.value_for([1]), domain.value_for([1, 2, 3]))

    def test_cross_categories_use_one_call_across_stages(self):
        states = [{"name": "EMPTY", "sample": ""}, {"name": "SHORT", "sample": "x"}, {"name": "LONG", "sample": "xxx"}]
        raw = controller({"name": {"type": "string", "default_value": ""}}, ['input.name == ""', 'count(input.name) > 2'])
        raw["stages"]["other"] = {**deepcopy(raw["stages"]["initial"]), "kind": "state"}
        semantic = JointSemantic(states)
        model = compile_controller(raw, semantic)
        self.assertEqual(len(semantic.calls), 1)
        self.assertEqual(set(semantic.calls[0]["stages"]), {"initial", "other"})
        domain = model.variables[0]
        self.assertEqual(domain.value_for("ab"), 1)
        self.assertEqual(domain.value_for("abcd"), 2)
        self.assertEqual(len(model.abstraction_registry["observations"]), 2)

    def test_mixed_categories_require_joint_provider(self):
        raw = controller({"name": {"type": "string", "default_value": ""}}, ['input.name == ""', 'count(input.name) > 2'])
        with self.assertRaises(ConversionError):
            compile_controller(raw)

    def test_joint_guard_must_match_the_representative_values(self):
        class WrongGuard(JointSemantic):
            def resolve_abstraction(self, context):
                proposal = super().resolve_abstraction(context)
                proposal["guards"][0]["allowed"] = []
                return proposal

        raw = controller({"items": {"type": "list", "default_value": []}}, ['"a" in input.items', 'count(input.items) >= 2'])
        with self.assertRaises(ConversionError):
            compile_controller(raw, WrongGuard())

    def test_joint_abstraction_cannot_omit_a_known_combination(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['"a" in input.items', 'count(input.items) >= 2'])
        states = JointSemantic().states[:-1]
        with self.assertRaises(ConversionError):
            compile_controller(raw, JointSemantic(states))

    def test_joint_states_keep_length_and_membership_consistent(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['"a" in input.items', 'count(input.items) >= 2'])
        registry, errors = build_registry(raw, JointSemantic())
        self.assertEqual(errors, [])
        domain = registry.domains["items"]
        empty = domain.value_for([])
        self.assertNotIn(empty, registry.guards['"a" in input.items'].allowed)
        self.assertNotIn(empty, registry.guards['count(input.items) >= 2'].allowed)

    def test_literal_append_preserves_members_and_crosses_length_boundary(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['"a" in input.items', 'count(input.items) >= 2'])
        registry, errors = build_registry(raw, JointSemantic())
        domain = registry.domains["items"]
        step = action("a", "items")
        step["when_success_write_to"]["operation"] = "append"
        choices = build_effect_choices(step, {}, registry.domains)
        empty = domain.value_for([])
        short = domain.value_for(["a"])
        long = domain.value_for(["a", "a"])
        self.assertEqual({choice.effects[0].value for choice in choices if choice.guard.allowed == (empty,)}, {short})
        self.assertIn(long, {choice.effects[0].value for choice in choices if choice.guard.allowed == (short,)})
        self.assertTrue(all(choice.effects[0].value in registry.guards['"a" in input.items'].allowed for choice in choices))

    def test_remove_preserves_duplicate_and_unobserved_member_possibilities(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['"a" in input.items'])
        registry, errors = build_registry(raw)
        self.assertEqual(errors, [])
        domain = registry.domains["items"]
        step = action("a", "items")
        step["when_success_write_to"]["operation"] = "remove"
        choices = build_effect_choices(step, {}, registry.domains)
        before = domain.value_for(["a"])
        after = {choice.effects[0].value for choice in choices if choice.guard.allowed == (before,)}
        self.assertEqual(after, {domain.value_for([]), domain.value_for(["x"]), domain.value_for(["a", "a"])})

    def test_remove_can_stay_above_a_length_boundary(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['count(input.items) > 2'])
        registry, errors = build_registry(raw)
        domain = registry.domains["items"]
        step = action(target="items")
        step["parameters"] = {}
        step["when_success_write_to"]["operation"] = "remove"
        choices = build_effect_choices(step, {}, registry.domains)
        before = domain.value_for([1, 2, 3])
        after = {choice.effects[0].value for choice in choices if choice.guard.allowed == (before,)}
        self.assertIn(before, after)
        self.assertIn(domain.value_for([1, 2]), after)

    def test_unknown_enum_write_explores_all_values_without_null(self):
        slots = {"kind": {"type": "enum", "enum_values": ["A", "B"], "default_value": None}}
        step = action(target="kind")
        step["parameters"] = {}
        raw = controller(slots, [True], step)
        model = compile_controller(raw)
        domain = model.variables[0]
        success = [edge for edge in model.edges if "success" in edge.event]
        self.assertEqual({domain.values[edge.effects[0].value].sample for edge in success}, {"A", "B"})
        self.assertIsNone(domain.values[domain.initial].sample)

    def test_known_enum_write_uses_only_the_declared_value(self):
        raw = controller({"kind": {"type": "enum", "enum_values": ["A", "B"], "default_value": None}}, [True], action("B", "kind"))
        model = compile_controller(raw)
        success = [edge for edge in model.edges if "success" in edge.event]
        self.assertEqual(len(success), 1)
        self.assertEqual(model.variables[0].values[success[0].effects[0].value].sample, "B")

    def test_enum_unknown_results_do_not_call_effect_llm(self):
        class NoEffects:
            def resolve_effects(self, context):
                raise AssertionError("未知枚举不需要 LLM 选择返回值")

        raw = controller({"kind": {"type": "enum", "enum_values": ["A", "B"], "default_value": None}}, [True])
        registry, errors = build_registry(raw)
        step = action(target="kind")
        step["parameters"] = {}
        choices = build_effect_choices(step, {}, registry.domains, NoEffects())
        self.assertEqual(len(choices), 2)

    def test_registry_is_saved_with_model(self):
        raw = controller({"name": {"type": "string", "default_value": ""}}, ['input.name == ""'])
        with tempfile.TemporaryDirectory() as folder:
            result = convert_controller_ir(raw, None, options=RunOptions(Path(folder), model_only=True))
            self.assertEqual(result.status, "CONVERTED")
            registry = json.loads((result.run_dir / "abstraction_registry.json").read_text(encoding="utf-8"))
            self.assertIn("name", registry["slot_variables"])
            self.assertEqual(len(registry["observations"]), 1)

    @unittest.skipUnless(opa_executable(), "需要 OPA 验证复杂 Rego 语义")
    def test_same_user_binding_is_not_split_into_two_existence_tests(self):
        query = 'some user in input.users; user.id == 2; user.active == true'
        states = [{"name": "EMPTY", "sample": []},
                  {"name": "NO_MATCH", "sample": [{"id": 2, "active": False}]},
                  {"name": "MATCH", "sample": [{"id": 2, "active": True}]}]
        raw = controller({"users": {"type": "list", "element_type": "dict", "default_value": []}}, [query])
        registry, errors = build_registry(raw, JointSemantic(states))
        self.assertEqual(errors, [])
        domain = registry.domains["users"]
        split = [{"id": 2, "active": False}, {"id": 1, "active": True}]
        self.assertEqual(domain.value_for(split), 1)
        self.assertEqual(registry.guards[query].allowed, (2,))

    @unittest.skipUnless(opa_executable(), "需要 OPA 验证跨槽位判断")
    def test_joint_slot_write_preserves_the_other_boolean(self):
        states = [{"name": label, "sample": {"a": a, "b": b}} for label, a, b in
                  [("FF", False, False), ("FT", False, True), ("TF", True, False), ("TT", True, True)]]
        slots = {name: {"type": "boolean", "default_value": False} for name in ("a", "b")}
        raw = controller(slots, ['input.a == input.b'], action(True, "a"))
        model = compile_controller(raw, JointSemantic(states))
        domain = model.variables[0]
        self.assertTrue(domain.joint)
        choice = next(edge for edge in model.edges if "success" in edge.event and edge.guard.allowed == (0,))
        self.assertEqual(domain.values[choice.effects[0].value].sample, {"a": True, "b": False})

    @unittest.skipUnless(opa_executable(), "需要 OPA 对照语言语义")
    def test_local_comparisons_match_opa_for_special_values(self):
        queries = ['input.roles == null', 'input.roles != null', 'count(input.roles) == 0', '"a" in input.roles', 'input.roles[_] == "a"']
        for slots in ({}, {"roles": None}, {"roles": []}, {"roles": ["a"]}, {"roles": ["b"]}):
            for query in queries:
                payload = json.dumps({"slots": slots, **slots})
                expected = _opa_query(query, payload, json.dumps({"version": "v1", "imports": []}))
                self.assertEqual(evaluate_known(query, slots), expected, (query, slots))

    @unittest.skipUnless(opa_executable(), "需要 OPA 验证状态与槽位的组合")
    def test_rego_body_preserves_status_and_slot_conditions(self):
        query = 'input.stage_execution.doActivity.execution_status == "success"; count(input.items) > 0'
        states = [{"name": "EMPTY", "sample": []}, {"name": "FILLED", "sample": ["x"]}]
        raw = controller({"items": {"type": "list", "default_value": []}}, [query], action())
        semantic = JointSemantic(states)
        model = compile_controller(raw, semantic)
        domain = model.variables[0]
        guard = next(edge.guard for edge in model.edges if edge.source_path.endswith("/transitions/0"))
        self.assertFalse(evaluate_guard(guard, {domain.name: 0}, {"doActivity": 1}))
        self.assertTrue(evaluate_guard(guard, {domain.name: 1}, {"doActivity": 1}))
        self.assertFalse(evaluate_guard(guard, {domain.name: 1}, {"doActivity": 2}))
        self.assertEqual(len(semantic.calls), 1)
        self.assertEqual(len(domain.execution_results[query]), 3)

    def test_new_check_cannot_read_unpreserved_exact_length(self):
        raw = controller({"items": {"type": "list", "default_value": []}}, ['count(input.items) > 0'])
        registry, errors = build_registry(raw)
        with self.assertRaises(ValueError):
            compile_guard('count(input.items) == 1', registry.domains, stage=raw["stages"]["initial"])

    def test_policy_conditions_are_collected_before_abstraction(self):
        class PolicySemantic(JointSemantic):
            def resolve_policy(self, context):
                return {"stage": "initial", "source_quote": "检查列表", "outcomes": [
                    {"name": "empty", "guard": 'count(input.items) == 0', "effects": []},
                    {"name": "filled", "guard": {"not": 'count(input.items) == 0'}, "effects": []}]}

        raw = controller({"items": {"type": "list", "default_value": []}}, ['count(input.items) > 2'])
        raw["policies"] = {"check": "检查列表"}
        states = [{"name": "EMPTY", "sample": []}, {"name": "SHORT", "sample": ["x"]}, {"name": "LONG", "sample": ["x", "y", "z"]}]
        semantic = PolicySemantic(states)
        model = compile_controller(raw, semantic)
        expressions = {item["expression"] for item in model.abstraction_registry["observations"]}
        self.assertEqual(expressions, {'count(input.items) == 0', 'count(input.items) > 2'})
        self.assertEqual(len(semantic.calls), 1)

    @unittest.skipUnless(opa_executable(), "需要 OPA 验证联合 policy 更新")
    def test_policy_writes_two_slots_in_one_joint_state(self):
        class PolicySemantic(JointSemantic):
            def resolve_policy(self, context):
                return {"stage": "initial", "source_quote": "同时设置", "outcomes": [
                    {"name": "set", "guard": True, "effects": [{"target": "slots.a", "value": True}, {"target": "slots.b", "value": True}]}]}

        states = [{"name": label, "sample": {"a": a, "b": b}} for label, a, b in
                  [("FF", False, False), ("FT", False, True), ("TF", True, False), ("TT", True, True)]]
        raw = controller({name: {"type": "boolean", "default_value": False} for name in ("a", "b")}, ['input.a == input.b'])
        raw["policies"] = {"set": "同时设置"}
        model = compile_controller(raw, PolicySemantic(states))
        edges = [edge for edge in model.edges if edge.source_path.startswith("/policies/")]
        self.assertEqual(len(edges), 4)
        self.assertTrue(all(len(edge.effects) == 1 and edge.effects[0].value == 3 for edge in edges))

    @unittest.skipUnless(opa_executable(), "需要 OPA 对照否定语义")
    def test_negation_respects_import_profile(self):
        query = 'not count(input.roles) > 0'
        self.assertIsNone(evaluate(query, {}, {"version": "v1", "imports": []}))
        self.assertTrue(evaluate(query, {}, {"version": "v1", "imports": ["future.keywords.not"]}))

    @unittest.skipUnless(opa_executable(), "需要 OPA 验证无槽位时的完整状态表达式")
    def test_pure_status_rego_body_compiles_without_slot_domains(self):
        query = 'input.stage_execution.entry.execution_status == "success"; input.stage_execution.doActivity.execution_status == "failure"'
        raw = controller({}, [query], action())
        raw["rego"] = {"version": "v0", "imports": []}
        raw["stages"]["initial"]["entry"] = action()
        model = compile_controller(raw)
        guard = next(edge.guard for edge in model.edges if edge.source_path.endswith("/transitions/0"))
        self.assertTrue(evaluate_guard(guard, {}, {"entry": 1, "doActivity": 2}))
        self.assertFalse(evaluate_guard(guard, {}, {"entry": 1, "doActivity": 1}))

    @unittest.skipUnless(PAT.is_file(), "需要 PAT 验证分支探索")
    def test_pat_reaches_each_unknown_enum_result(self):
        step = action(target="kind")
        step["parameters"] = {}
        raw = controller({"kind": {"type": "enum", "enum_values": ["A", "B"], "default_value": None}}, [SUCCESS], step)
        model = compile_controller(raw)
        domain = model.variables[0]
        for value in ("A", "B"):
            name = "got_" + value
            model.predicates[name] = Expr("in", variable=domain.name, allowed=(domain.value_for(value),))
            model.checks.append(Check(name, "枚举结果可达", "reachability", name, None, "VALID", ("/slot_information/kind",)))
        with tempfile.TemporaryDirectory() as folder:
            results = run_pat_checks(model, PAT, Path(folder), 30)
        self.assertEqual([result["actual"] for result in results], ["VALID", "VALID"], results)

    @unittest.skipUnless(opa_executable() and PAT.is_file(), "需要 OPA 和 PAT 验证组合 Guard")
    def test_pat_accepts_joint_guard_and_policy_without_writes(self):
        class PolicySemantic(JointSemantic):
            def resolve_policy(self, context):
                return {"stage": "initial", "source_quote": "允许继续", "outcomes": [
                    {"name": "continue", "guard": True, "effects": []}]}

            def resolve_effects(self, context):
                return {"effects": []}

        query = 'input.stage_execution.doActivity.execution_status == "success"; count(input.items) > 0'
        raw = controller({"items": {"type": "list", "default_value": []}}, [query], action("x", "items"))
        raw["stages"]["initial"]["doActivity"]["when_success_write_to"]["operation"] = "append"
        raw["policies"] = {"continue": "允许继续"}
        states = [{"name": "EMPTY", "sample": []}, {"name": "FILLED", "sample": ["x"]}]
        model = compile_controller(raw, PolicySemantic(states))
        model.predicates["completed"] = Expr("in", variable="run_outcome", allowed=(1,))
        model.checks = [Check("completion_reachable", "组合条件允许完成", "reachability", "completed", None, "VALID", ("/stages/initial",))]
        with tempfile.TemporaryDirectory() as folder:
            results = run_pat_checks(model, PAT, Path(folder), 30)
        self.assertEqual(results[0]["actual"], "VALID", results)


if __name__ == "__main__":
    unittest.main()
