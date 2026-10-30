"""LLM 语义补全接口；所有输出仍需由转换代码校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from llm import OpenAICompatibleLLM, load_llm_config
from src.model import FiniteVariable


class SemanticProvider(Protocol):
    """转换器依赖的公开语义服务接口。"""

    def resolve_abstraction(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def resolve_guard(self, atom: str, domains: dict[str, FiniteVariable]) -> dict[str, Any]: ...
    def resolve_failure(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def resolve_effects(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def resolve_policy(self, context: dict[str, Any]) -> dict[str, Any]: ...
    def propose_checks(self, context: dict[str, Any]) -> dict[str, Any]: ...


class OpenAISemanticProvider:
    """一个明确的 from_env 入口负责建立所有语义请求。"""

    def __init__(self, client: OpenAICompatibleLLM) -> None:
        self.client = client
        self.trace: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "OpenAISemanticProvider":
        """从项目统一的 LLM 配置建立服务。"""
        return cls(OpenAICompatibleLLM(load_llm_config(env_file)))

    def resolve_guard(self, atom: str, domains: dict[str, FiniteVariable]) -> dict[str, Any]:
        """解释当前规则不认识的原子条件。"""
        context = {"atom": atom, "domains": _domain_view(domains)}
        instruction = "把条件编译为有限布尔树。输出 JSON：op 为 true/false/and/or/not/in；in 使用 slot 和 allowed 整数索引。只能使用给出的槽位和值，不确定则返回 error。"
        return self._request("guard", instruction, context)

    def resolve_abstraction(self, context: dict[str, Any]) -> dict[str, Any]:
        """一次返回统一有限状态及所有判断映射，避免前后调用不一致。"""
        instruction = (
            "依据全部槽位、Stage、动作和 Rego 判断联合设计有限状态。只输出 JSON 对象："
            "states 数组，每项有 name、sample、可选 missing；guards 数组，每项有 id、allowed、undefined；reason 为非空说明。"
            "单槽位 sample 是原类型代表值；缺失用 sample=null,missing=true；联合槽位组 sample 是槽位名到值的对象，缺失键省略。"
            "allowed/undefined 是 states 中判断成立/未定义的索引，其余索引表示不成立。每个 observation.id 必须恰好出现一次。"
            "若判断还读取 stage_execution，guard 项改用 cases 数组，按 observation.status_cases 逐项返回 status、allowed、undefined。"
            "执行状态由 Process 参数表示，不能把这些状态复制成槽位状态，也不能当作缺失输入。"
            "保留缺失、null、空内容的区别；保持绑定在同一元素上的多个条件，不得拆成独立存在判断。"
            "先确定状态，再根据状态设计 Guard 映射；合并相同判断组合，覆盖 candidate_states 及所有有依据的业务结果。"
            "candidate_states 只是代码提供的种子，可补充新代表值。不能发明槽位、枚举值或不可能的组合。"
            "reason 说明每类状态的含义、合并依据、原文依据及近似；无法可靠抽象时返回 unresolved 和 reason。"
        )
        return self._request("abstraction", instruction, context)

    def resolve_failure(self, context: dict[str, Any]) -> dict[str, Any]:
        """只在代码确认失败路径缺少去向后提出候选。"""
        instruction = (
            "仅分析给定步骤在 transitions 中未覆盖的失败状态。返回 JSON："
            "failure_behavior 为 terminate、pause、edge、retry 或 unresolved；target 为已有 Stage，condition 为可选附加 Guard；"
            "给 reason 和 evidence 原文片段。terminate 表示失败结束本次运行；pause 表示阻塞，不能把暂停当结束。"
            "retry 仅在原文明确要求重试当前步骤时使用；edge 必须有明确目标依据。"
            "确认描述针对当前 step，不能将 doActivity 的失败说明套给 entry。不得覆盖已有有效出边，不得编造恢复事件。"
        )
        return self._request("failure", instruction, context)

    def resolve_effects(self, context: dict[str, Any]) -> dict[str, Any]:
        """解释文字中明确但未结构化的成功写回约束。"""
        instruction = "分析动作成功后的数据影响。返回 JSON：effects 数组，每项含 target=slots.<槽位>、allowed=给定有限域索引数组；source_quote 为动作或 Stage 描述中的原文片段。只有原文明确限制结果时才能缩小 allowed；不确定时用全部索引。若没有状态写回，effects 为空。不能创造槽位或删去未被原文排除的结果。"
        return self._request("effects", instruction, context)

    def resolve_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        """将非空 policy 提议为有明确触发位置的有限步骤。"""
        instruction = "将 policy 转成有限状态评估步骤。返回 JSON：stage、outcomes 数组、每项包含 name、guard、effects；给 source_quote。stage 必须在候选中，guard 必须使用输入条件语法，effects 只能写已知 slots。无法可靠确定触发位置或有限结果则返回 decision=unresolved。"
        return self._request("policy", instruction, context)

    def propose_checks(self, context: dict[str, Any]) -> dict[str, Any]:
        """从 IR 目标和已建模型提出可追溯的检查项。"""
        instruction = "自动提出有实际意义的 PAT 检查。返回 JSON：predicates 数组(name,guard,sources)，checks 数组(name,description,kind,predicate,formula,expected,sources)，可选 observers 数组(name,values,initial,updates,sources)。谓词 guard 用 current_stage == \"阶段名\"、input.slots.槽位 == 值、count(input.slots.槽位) 比较整数或 all_of/any_of/not。observer 的 updates 为 event,value 数组。kind 为 reachability/deadlock_free/ltl；expected 为 VALID/INVALID。sources 必须是输入给出的 JSON 路径。只能引用现有对象。不要生成恒真或没有依据的检查；无法表达时放入 uncheckable 数组并说明原因。至少提出一项可执行检查。"
        return self._request("checks", instruction, context)

    def _request(self, kind: str, instruction: str, context: dict[str, Any]) -> dict[str, Any]:
        safe_context = _redact(context)
        messages = [{"role": "system", "content": instruction}, {"role": "user", "content": json.dumps(safe_context, ensure_ascii=False)}]
        failures: list[str] = []
        for attempt in range(2):
            response = self.client.complete(messages=messages, temperature=0)
            try:
                payload = json.loads(_json_text(response))
                if isinstance(payload, dict):
                    self.trace.append({"kind": kind, "context": safe_context, "response": payload})
                    return payload
            except json.JSONDecodeError as exc:
                failures.append(str(exc))
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "只输出有效 JSON 对象。"})
        raise ValueError(f"LLM {kind} 输出无效：{failures}")


def _domain_view(domains: dict[str, FiniteVariable]) -> dict[str, Any]:
    return {name: [{"index": index, "name": state.name, "sample": state.sample, "missing": state.missing} for index, state in enumerate(domain.values)] for name, domain in domains.items()}


def _json_text(response: str) -> str:
    value = response.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0]
    return value.strip()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items() if key not in {"participant_config", "api_key", "token", "credentials"}}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
