"""检查真实语义服务接口的请求格式，不调用在线模型。"""

import json
import unittest

from src.conversion.compiler import compile_controller
from src.semantic import OpenAISemanticProvider
from tests.joint_fixtures import controller


class RecordingClient:
    """按约定返回一份回答，同时保存完整请求以便核对。"""

    def __init__(self, response):
        self.response, self.calls = response, []

    def complete(self, **request):
        self.calls.append(request)
        return json.dumps(self.response)


class SemanticTests(unittest.TestCase):
    def test_joint_request_returns_states_and_guards_in_one_call(self):
        response = {"states": [{"name": "EMPTY", "sample": ""}, {"name": "SHORT", "sample": "x"}, {"name": "LONG", "sample": "xxx"}],
                    "guards": [{"id": "observation_001", "allowed": [0], "undefined": []},
                               {"id": "observation_002", "allowed": [2], "undefined": []}],
                    "reason": "统一按判空和长度区间编码"}
        client = RecordingClient(response)
        provider = OpenAISemanticProvider(client)
        raw = controller({"name": {"type": "string", "default_value": ""}}, ['input.name == ""', 'count(input.name) > 2'])
        model = compile_controller(raw, provider)
        self.assertEqual(len(client.calls), 1)
        context = json.loads(client.calls[0]["messages"][1]["content"])
        self.assertEqual(set(context["slots"]), {"name"})
        self.assertIn("initial", context["stages"])
        self.assertEqual(provider.trace[0]["kind"], "abstraction")
        self.assertEqual(model.variables[0].reason, response["reason"])

    def test_semantic_trace_excludes_credentials_from_context(self):
        client = RecordingClient({"failure_behavior": "unresolved", "reason": "信息不足"})
        provider = OpenAISemanticProvider(client)
        provider.resolve_failure({"step": "entry", "participant_config": {"secret": "value"},
                                  "action": {"api_key": "value", "description": "动作说明"}})
        context = provider.trace[0]["context"]
        self.assertNotIn("participant_config", context)
        self.assertNotIn("api_key", context["action"])
        self.assertEqual(context["action"]["description"], "动作说明")


if __name__ == "__main__":
    unittest.main()
