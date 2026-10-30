"""将顺序 ControllerIR Stage 编译为有限控制流程。"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from itertools import product
from typing import Any

from src.model import CSPIR, Diagnostic, Edge, Effect, Expr, FiniteVariable, Process, StateValue
from .registry import build_registry
from .effects import EffectChoice, build_effect_choices
from .guards import GuardError, compile_guard, has_uncovered_state
from .input import validate_controller


PARAMETERS = ("entry_status", "do_status", "exit_status")
STEPS = ("entry", "doActivity", "exit")


class ConversionError(ValueError):
    """承载全部输入或语义诊断，供顶层入口统一报告。"""

    def __init__(self, diagnostics: list[Diagnostic], partial_model: CSPIR | None = None) -> None:
        self.diagnostics = diagnostics
        self.partial_model = partial_model
        super().__init__("; ".join(item.message for item in diagnostics))


class ControllerCompiler:
    """公开入口 compile；辅助方法只维护一次编译的局部状态。"""

    def __init__(self, raw: dict[str, Any], semantic: Any = None) -> None:
        self.raw = raw
        self.semantic = semantic
        self.domains: dict[str, FiniteVariable] = {}
        self.processes: list[Process] = []
        self.edges: list[Edge] = []
        self.source_map: dict[str, str] = {}
        self.diagnostics: list[Diagnostic] = []
        self.warnings: list[Diagnostic] = []
        self.names = {stage: _name(stage) for stage in raw.get("stages", {})}
        self.policies_by_stage: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.control_domain: FiniteVariable | None = None
        self.run_domain = FiniteVariable("run_outcome", "/stages", [StateValue(name, name) for name in ("RUNNING", "COMPLETED", "ABORTED", "PAUSED")], 0)
        self.failure_records: list[dict[str, Any]] = []

    def compile(self) -> CSPIR:
        """先验证和抽象，再依次生成每个 Stage 的控制位置。"""
        self.diagnostics.extend(validate_controller(self.raw))
        if self.diagnostics:
            raise ConversionError(self.diagnostics)
        self._resolve_policies()
        if self.diagnostics:
            raise ConversionError(self.diagnostics)
        self.registry, errors = build_registry(self._abstraction_input(), self.semantic)
        self.domains = self.registry.domains
        self.warnings.extend(self.registry.diagnostics)
        self.diagnostics.extend(errors)
        if self.diagnostics:
            raise ConversionError(self.diagnostics)
        initial = next(name for name, stage in self.raw["stages"].items() if stage["kind"] == "initial")
        stage_names = list(self.raw["stages"])
        states = [StateValue(name, name) for name in stage_names]
        self.control_domain = FiniteVariable("current_stage", "/stages", states, stage_names.index(initial))
        self.source_map.update({domain.name: domain.source for domain in self.domains.values()})
        self.source_map["current_stage"] = "/stages"
        self.source_map["run_outcome"] = "/stages"
        for stage_name, stage in self.raw["stages"].items():
            self._compile_stage(stage_name, stage)
        unique_domains = list({domain.name: domain for domain in self.domains.values()}.values())
        variables = [*unique_domains, self.control_domain, self.run_domain]
        model = CSPIR(variables, self.processes, self.edges, self._first(initial),
                      source_map=self.source_map, conversion_diagnostics=self.warnings)
        model.abstraction_registry = self.registry.to_dict()
        model.failure_records = self.failure_records
        self._validate_model(model)
        if self.diagnostics:
            # 仅保留引用和有限域有效的草稿，供人工检查转换缺口。
            raise ConversionError(self.diagnostics, partial_model=model)
        return model

    def _compile_stage(self, name: str, stage: dict[str, Any]) -> None:
        if stage["kind"] == "termination":
            terminal = self._position(name, "terminal")
            self._add_process(terminal, f"/stages/{name}", terminal=True)
            return
        for step in STEPS:
            if stage.get(step) is not None:
                position = self._position(name, step)
                self._add_process(position, f"/stages/{name}/{step}")
                self._compile_step(name, stage, step)
        for policy_name, proposal in self.policies_by_stage.get(name, []):
            position = self._position(name, "policy_" + policy_name)
            self._add_process(position, f"/policies/{policy_name}")
            self._compile_policy(name, stage, policy_name, proposal)
        position = self._position(name, "transitions")
        self._add_process(position, f"/stages/{name}/transitions")
        self._compile_outgoing(name, stage)
        self._complete_failures(name, stage)

    def _compile_step(self, name: str, stage: dict[str, Any], step_name: str) -> None:
        step = stage[step_name]
        action = self.raw["actions"][step["action"]]
        source = self._position(name, step_name)
        path = f"/stages/{name}/{step_name}"
        try:
            choices = build_effect_choices(step, action, self.domains, self.semantic)
        except ValueError as exc:
            self.diagnostics.append(Diagnostic("EFFECT", path, str(exc)))
            return
        for index, choice in enumerate(choices):
            event = _name(f"{name}_{step_name}_success_{index}")
            target = self._next(name, stage, step_name)
            args = self._updated_args(step_name, 1)
            self._add_edge(source, choice.guard, event, choice.effects, target, path, args)
        failed = _name(f"{name}_{step_name}_failure")
        target = self._position(name, "transitions")
        self._add_edge(source, Expr("true"), failed, (), target, path, self._updated_args(step_name, 2))

    def _compile_outgoing(self, name: str, stage: dict[str, Any]) -> None:
        source = self._position(name, "transitions")
        for index, item in enumerate(stage.get("transitions", [])):
            path = f"/stages/{name}/transitions/{index}"
            try:
                guard = compile_guard(item.get("guard", True), self.domains, stage=stage,
                                      semantic=self.semantic, rego_profile=self.registry.profile)
            except (GuardError, ValueError) as exc:
                self.diagnostics.append(Diagnostic("GUARD", path + "/guard", str(exc)))
                continue
            target = self._first(item["target"])
            event = _name(f"{name}_to_{item['target']}_{index}")
            args = () if self.raw["stages"][item["target"]]["kind"] == "termination" else (0, 0, 0)
            stage_index = list(self.raw["stages"]).index(item["target"])
            effect = (Effect("current_stage", stage_index),)
            if self.raw["stages"][item["target"]]["kind"] == "termination":
                effect += (Effect("run_outcome", 1),)
            self._add_edge(source, guard, event, effect, target, path, args)

    def _complete_failures(self, name: str, stage: dict[str, Any]) -> None:
        """先检查出边覆盖；只在失败无路可走时请求语义补全。"""
        guards = [edge.guard for edge in self.edges if edge.source == self._position(name, "transitions")]
        for step in STEPS:
            if stage.get(step) is None or self._failure_covered(stage, step, guards):
                continue
            path = f"/stages/{name}/{step}"
            decision = self._failure_decision(name, stage, step, path)
            decision = self._normalize_failure(decision)
            record = {"stage": name, "step": step, "source": path, "proposal": decision, "applied": False}
            self.failure_records.append(record)
            if decision is None:
                self._keep_failure_state(path, "原文未提供额外失败去向")
                continue
            if decision.get("decision") not in {"terminate", "pause", "edge", "retry"}:
                self._keep_failure_state(path, "AI 未能确定有依据的额外失败去向")
                continue
            evidence = _failure_quotes(decision.get("evidence"), stage, self.raw["actions"][stage[step]["action"]])
            if not evidence or not _supports_failure_decision(decision, evidence):
                self._keep_failure_state(path, "AI 提议缺少明确的失败处理原文依据")
                continue
            record["applied"] = self._add_failure_continuation(name, stage, step, path, guards, decision)

    def _normalize_failure(self, decision: Any) -> dict[str, Any] | None:
        """兼容旧 stop 返回值，对外统一使用失败处理类型。"""
        if not isinstance(decision, dict):
            return None
        behavior = decision.get("failure_behavior", decision.get("decision", "unresolved"))
        return {**decision, "decision": "terminate" if behavior == "stop" else behavior}

    def _add_failure_continuation(self, name: str, stage: dict[str, Any], step: str, path: str, guards: list[Expr], decision: dict[str, Any]) -> bool:
        """新增分支只覆盖已有条件均不成立的状态。"""
        target = self._failure_target(name, decision, path, step)
        if target is None:
            return False
        missing = Expr("not", (Expr("or", tuple(guards)),)) if guards else Expr("true")
        condition = Expr("and", (Expr("status", step=step, result="failure"), missing))
        try:
            if decision.get("condition") is not None:
                extra = compile_guard(decision["condition"], self.domains, stage=stage, rego_profile=self.registry.profile)
                condition = Expr("and", (condition, extra))
        except ValueError as exc:
            self._keep_failure_state(path, str(exc))
            return False
        args, effects = self._failure_updates(stage, step, decision)
        event = _name(f"{name}_{step}_{decision['decision']}")
        self._add_edge(self._position(name, "transitions"), condition, event, effects, target, path, args)
        if not self._failure_covered(stage, step, [*guards, condition]):
            self._keep_failure_state(path, "失败补全仍未覆盖全部缺口")
        return True

    def _failure_updates(self, stage: dict[str, Any], step: str, decision: dict[str, Any]) -> tuple[tuple, tuple]:
        """失败结束与暂停有不同标记，重试保留之前步骤的状态。"""
        behavior = decision["decision"]
        if behavior in {"terminate", "pause"}:
            return (), (Effect("run_outcome", 2 if behavior == "terminate" else 3),)
        if behavior == "retry":
            values = list(PARAMETERS)
            for index in range(STEPS.index(step), len(STEPS)):
                values[index] = 0
            return tuple(values), ()
        target = decision["target"]
        index = list(self.raw["stages"]).index(target)
        terminal = self.raw["stages"][target]["kind"] == "termination"
        effects = (Effect("current_stage", index),)
        if terminal:
            effects += (Effect("run_outcome", 2),)
        return (() if terminal else (0, 0, 0)), effects

    def _keep_failure_state(self, path: str, reason: str) -> None:
        """没有后继的状态可由 CSP# 表达，不阻止模型生成。"""
        self.warnings.append(Diagnostic("FAILURE_GAP", path, reason + "；保留失败后的现有出边，无可用出边时形成死锁状态，交由 PAT 检查", severity="warning"))

    def _failure_covered(self, stage: dict[str, Any], step: str, guards: list[Expr]) -> bool:
        status = self._failure_status(stage, step)
        try:
            return not has_uncovered_state(guards, self.domains, status)
        except GuardError:
            return False

    def _failure_status(self, stage: dict[str, Any], step: str) -> dict[str, int]:
        """当前步骤失败，之前非空步骤成功，后续步骤未执行。"""
        status = {item: 0 for item in STEPS}
        for item in STEPS:
            if stage.get(item) is not None:
                status[item] = 2 if item == step else 1
            if item == step:
                break
        return status

    def _failure_decision(self, name: str, stage: dict[str, Any], step: str, path: str) -> dict[str, Any] | None:
        if self.semantic is None:
            return None
        context = {"stage": name, "step": step, "description": stage.get("description", ""),
                   "action": self.raw["actions"][stage[step]["action"]], "step_information": stage[step],
                   "transitions": stage.get("transitions", []), "targets": list(self.raw["stages"]), "source": path,
                   "failure_status": self._failure_status(stage, step),
                   "domains": self._failure_domains(), "guard_mappings": self.registry.to_dict()["guards"],
                   "uncovered_condition": "当前步骤失败，且全部已有出边 Guard 均不成立"}
        return self.semantic.resolve_failure(context)

    def _failure_domains(self) -> dict[str, Any]:
        """给失败补全提供状态含义，不能只给没有业务意义的内部名称。"""
        domains = {}
        for slot, domain in self.domains.items():
            states = [{"index": index, "sample": state.sample, "missing": state.missing}
                      for index, state in enumerate(domain.values)]
            domains[slot] = {"variable": domain.name, "states": states}
        return domains

    def _failure_target(self, name: str, decision: dict[str, Any], path: str, step: str) -> str | None:
        choice = decision.get("decision")
        if choice in {"terminate", "pause"}:
            target = self._position(name, "failed" if choice == "terminate" else step + "_paused")
            if not any(process.name == target for process in self.processes):
                self._add_process(target, path, terminal=choice == "terminate", paused=choice == "pause")
            return target
        if choice == "retry":
            return self._position(name, step)
        if choice == "edge" and decision.get("target") in self.raw["stages"]:
            return self._first(decision["target"])
        self._keep_failure_state(path, "AI 提议的失败目标无效")
        return None

    def _first(self, name: str) -> str:
        stage = self.raw["stages"][name]
        if stage["kind"] == "termination":
            return self._position(name, "terminal")
        for step in STEPS:
            if stage.get(step) is not None:
                return self._position(name, step)
        if self.policies_by_stage.get(name):
            return self._position(name, "policy_" + self.policies_by_stage[name][0][0])
        return self._position(name, "transitions")

    def _next(self, name: str, stage: dict[str, Any], current: str) -> str:
        for step in STEPS[STEPS.index(current) + 1:]:
            if stage.get(step) is not None:
                return self._position(name, step)
        if self.policies_by_stage.get(name):
            return self._position(name, "policy_" + self.policies_by_stage[name][0][0])
        return self._position(name, "transitions")

    def _resolve_policies(self) -> None:
        for name, policy in self.raw["policies"].items():
            path = f"/policies/{name}"
            if self.semantic is None:
                self.diagnostics.append(Diagnostic("POLICY", path, "非空 policy 需要 AI 语义补全"))
                continue
            context = {"name": name, "policy": policy, "stages": {key: value.get("description", "") for key, value in self.raw["stages"].items()}, "slots": list(self.raw["slot_information"])}
            proposal = self.semantic.resolve_policy(context)
            stage = proposal.get("stage")
            if stage not in self.raw["stages"] or self.raw["stages"][stage]["kind"] == "termination" or not isinstance(proposal.get("outcomes"), list) or not proposal["outcomes"]:
                self.diagnostics.append(Diagnostic("POLICY", path, "AI 未确定有效的触发 Stage 和有限结果"))
                continue
            quote = str(proposal.get("source_quote", ""))
            if not quote or quote not in str(policy):
                self.diagnostics.append(Diagnostic("POLICY", path, "AI policy 补全缺少原文依据"))
                continue
            self.policies_by_stage.setdefault(stage, []).append((name, proposal))

    def _abstraction_input(self) -> dict[str, Any]:
        """policy 先确定执行位置和判断，再与原 Guard 一起建立有限域。"""
        raw = deepcopy(self.raw)
        for policies in self.policies_by_stage.values():
            for name, proposal in policies:
                raw["policies"][name] = {"original": raw["policies"][name], "resolved": proposal}
        return raw

    def _compile_policy(self, stage_name: str, stage: dict[str, Any], name: str, proposal: dict[str, Any]) -> None:
        source = self._position(stage_name, "policy_" + name)
        policies = self.policies_by_stage[stage_name]
        index = next(index for index, item in enumerate(policies) if item[0] == name)
        target = self._position(stage_name, "policy_" + policies[index + 1][0]) if index + 1 < len(policies) else self._position(stage_name, "transitions")
        for number, outcome in enumerate(proposal["outcomes"]):
            path = f"/policies/{name}/outcomes/{number}"
            try:
                guard = compile_guard(outcome.get("guard", True), self.domains, stage=stage,
                                      semantic=self.semantic, rego_profile=self.registry.profile)
                choices = self._policy_effects(outcome.get("effects", []))
            except (ValueError, GuardError) as exc:
                self.diagnostics.append(Diagnostic("POLICY", path, str(exc)))
                continue
            for index, choice in enumerate(choices):
                event = _name(f"{stage_name}_{name}_{outcome.get('name', number)}_{index}")
                self._add_edge(source, Expr("and", (guard, choice.guard)), event, choice.effects, target, path, PARAMETERS)
        guards = [edge.guard for edge in self.edges if edge.source == source]
        for values in product((0, 1, 2), repeat=3):
            statuses = dict(zip(STEPS, values))
            if has_uncovered_state(guards, self.domains, statuses):
                self.diagnostics.append(Diagnostic("POLICY", f"/policies/{name}", "policy 结果没有覆盖所有有限状态"))
                break

    def _policy_effects(self, proposals: Any) -> list[EffectChoice]:
        """同组槽位同时写回，只更新一个组合变量，保持未写槽位信息。"""
        if not isinstance(proposals, list):
            raise ValueError("policy effects 必须是数组")
        writes: dict[str, Any] = {}
        for item in proposals:
            if not isinstance(item, dict) or "value" not in item:
                raise ValueError("policy effect 必须包含明确的 target 和 value")
            slot = str(item.get("target", "")).removeprefix("slots.")
            if slot not in self.domains:
                raise ValueError(f"policy 写入未知槽位 {slot}")
            if slot in writes:
                raise ValueError("policy 在一条结果中重复写同一槽位")
            writes[slot] = item["value"]
        from .updates import literal_writes

        return [EffectChoice(guard, effects, "policy") for guard, effects in literal_writes(writes, self.domains)]

    def _position(self, name: str, location: str) -> str:
        return _name(name + "_" + location)

    def _updated_args(self, step: str, code: int) -> tuple[int | str, ...]:
        index = STEPS.index(step)
        values: list[int | str] = list(PARAMETERS)
        values[index] = code
        if code == 2:
            for after in range(index + 1, 3):
                values[after] = 0
        return tuple(values)

    def _add_process(self, name: str, path: str, terminal: bool = False, paused: bool = False) -> None:
        self.processes.append(Process(name, () if terminal or paused else PARAMETERS, terminal, paused))
        self.source_map[name] = path

    def _add_edge(self, source: str, guard: Expr, event: str, effects: tuple, target: str, path: str, args: tuple) -> None:
        self.edges.append(Edge(source, guard, event, effects, target, path, args))
        self.source_map[event] = path

    def _validate_model(self, model: CSPIR) -> None:
        processes = {process.name: process for process in model.processes}
        variables = {variable.name: variable for variable in model.variables}
        if len(processes) != len(model.processes):
            raise ConversionError([Diagnostic("PROCESS_NAME", "/stages", "生成的 Process 名称冲突")])
        if len(variables) != len(model.variables):
            raise ConversionError([Diagnostic("VARIABLE_NAME", "/slot_information", "生成的变量名称冲突")])
        for edge in model.edges:
            if edge.source not in processes or edge.target not in processes:
                raise ConversionError([Diagnostic("PROCESS_REF", edge.source_path, "生成的 Process 引用无效")])
            if len(edge.target_args) != len(processes[edge.target].parameters):
                raise ConversionError([Diagnostic("PROCESS_ARGS", edge.source_path, "目标 Process 参数数量不匹配")])
            for effect in edge.effects:
                variable = variables.get(effect.variable)
                if variable is None or not 0 <= effect.value < len(variable.values):
                    raise ConversionError([Diagnostic("EFFECT_DOMAIN", edge.source_path, "写回超出变量有限域")])


def compile_controller(raw: dict[str, Any], semantic: Any = None) -> CSPIR:
    """模块公开入口。"""
    return ControllerCompiler(raw, semantic).compile()


def _failure_quotes(evidence: Any, stage: dict[str, Any], action: dict[str, Any]) -> list[str]:
    """兼容多处原文引用，每段都必须能在描述中逐字找到。"""
    quotes = [evidence] if isinstance(evidence, str) else evidence if isinstance(evidence, list) else []
    sources = [str(stage.get("description", "")), str(action.get("description", ""))]
    verified: list[str] = []
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            return []
        if any(quote in source for source in sources):
            verified.append(quote)
            continue
        parts = [part.strip() for part in re.findall(r"[^。！？\n]+[。！？]?", quote) if part.strip()]
        if not parts or any(not any(part in source for source in sources) for part in parts):
            return []
        verified.extend(parts)
    return verified


def _supports_failure_decision(decision: dict[str, Any], quotes: list[str]) -> bool:
    """引用成功行为不足以支持新增失败分支；不确定时保留原图。"""
    for quote in quotes:
        if "？" in quote or "?" in quote:
            continue
        clauses = re.split(r"[。！？\n]", quote)
        for clause in clauses:
            if not re.search(r"失败|异常|出错|无结果|fail|error", clause, re.I):
                continue
            if decision.get("decision") in {"stop", "terminate"}:
                if re.search(r"(?:不|不得|不能|无需|不应|不必)\s*(?:停止|终止|结束)", clause):
                    continue
                if re.search(r"(?:失败|异常|出错|无结果).*?(?:停止|终止|结束)", clause):
                    return True
            elif decision.get("decision") == "pause":
                if re.search(r"(?:失败|异常|出错|无结果).*?(?:暂停|等待)", clause) and not re.search(r"不(?:得|应|必)?(?:暂停|等待)", clause):
                    return True
            elif decision.get("decision") == "retry":
                if re.search(r"(?:失败|异常|出错|无结果).*?重试", clause) and not re.search(r"不(?:得|应|必)?重试", clause):
                    return True
            elif decision.get("decision") == "edge":
                target = decision.get("target")
                if isinstance(target, str) and target and target in clause:
                    return True
    return False


def _name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not normalized or normalized[0].isdigit():
        normalized = "p_" + normalized
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]
    return f"P_{normalized}_{digest}"
