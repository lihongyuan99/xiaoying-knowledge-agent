from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_qa.agent import KnowledgeQAAgent
from knowledge_qa.models import OfflineMockModel, OpenAICompatibleModel
from knowledge_qa.tools import KnowledgeBase, ToolExecutor


class SequenceModel:
    mode = "test_sequence"

    def __init__(self, responses):
        self.responses = list(responses)

    def decide(self, request, observations, remaining_steps):
        return self.responses.pop(0)


class AlwaysSearchModel:
    mode = "test_loop"

    def decide(self, request, observations, remaining_steps):
        return {
            "action": "search_documents",
            "args": {"query": "年假", "top_k": 3},
            "rationale": "测试模型持续检索，用于验证执行上限。",
        }


class KnowledgeQAAgentTests(unittest.TestCase):
    def setUp(self):
        self.data_path = ROOT / "data" / "documents.json"

    def run_agent(self, question, history=None, data_path=None, failures=None, max_steps=8):
        agent = KnowledgeQAAgent(
            OfflineMockModel(),
            ToolExecutor(KnowledgeBase(data_path or self.data_path), failures=failures),
            max_steps=max_steps,
        )
        return agent.run(question, history)

    def assert_supported_citations(self, record, data_path=None):
        knowledge_base = KnowledgeBase(data_path or self.data_path)
        for citation in record["result"]["citations"]:
            document = knowledge_base.get(citation["doc_id"])
            self.assertIsNotNone(document)
            self.assertIn(citation["quote"], document["content"])
            self.assertEqual(citation["version_date"], document["version_date"])

    def test_clear_answer(self):
        record = self.run_agent("年假需要提前几天申请？")
        self.assertEqual(record["result"]["status"], "completed")
        self.assertIn("3 个工作日", record["result"]["answer"])
        self.assertEqual(record["result"]["citations"][0]["doc_id"], "D04")
        self.assert_supported_citations(record)

    def test_conflict_lists_both_sources_and_asks(self):
        record = self.run_agent("报销期限多久")
        result = record["result"]
        self.assertEqual(result["status"], "needs_clarification")
        self.assertIn("30 天", result["answer"])
        self.assertIn("15 天", result["answer"])
        self.assertIn("不能擅自选择", result["answer"])
        self.assertEqual({item["doc_id"] for item in result["citations"]}, {"D01", "D02"})
        self.assertTrue(result["clarifying_question"])
        self.assertGreaterEqual(len(result["gaps"]), 2)
        self.assert_supported_citations(record)
        self.assertEqual(
            [step["action"] for step in record["trace"]],
            ["search_documents", "read_document", "read_document", "finish"],
        )

    def test_out_of_scope_question(self):
        record = self.run_agent("公司是否允许带宠物上班？")
        self.assertEqual(record["result"]["status"], "insufficient_information")
        self.assertEqual(record["result"]["citations"], [])
        self.assertTrue(record["result"]["gaps"])

    def test_paraphrased_question(self):
        record = self.run_agent("在北京出差，酒店每晚最多能报多少钱？")
        self.assertEqual(record["result"]["status"], "completed")
        self.assertIn("500 元", record["result"]["answer"])
        self.assertEqual(record["result"]["citations"][0]["doc_id"], "D03")
        self.assert_supported_citations(record)

    def test_contextual_followup_rewrites_search(self):
        history = [
            {"role": "user", "content": "北京出差住宿报销标准是多少？"},
            {"role": "assistant", "content": "北京每间每晚不超过 500 元。"},
        ]
        record = self.run_agent("那上海呢？", history=history)
        self.assertEqual(record["result"]["status"], "completed")
        self.assertIn("上海", record["result"]["answer"])
        self.assertIn("500 元", record["result"]["answer"])
        self.assertEqual(record["trace"][0]["arguments"]["query"], "上海 出差 住宿 报销标准")
        self.assert_supported_citations(record)

    def test_tool_failure_is_explicit_and_not_fabricated(self):
        record = self.run_agent(
            "北京住宿标准是多少？", failures={"read_document:D03": 1}
        )
        self.assertEqual(record["result"]["status"], "tool_error")
        self.assertIn("工具调用失败", record["result"]["answer"])
        self.assertEqual(record["result"]["citations"], [])
        failed = [step for step in record["trace"] if not step["observation"]["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["observation"]["error"]["code"], "INJECTED_TOOL_FAILURE")

    def test_single_search_result_can_answer_without_invented_conflict(self):
        documents = json.loads(self.data_path.read_text(encoding="utf-8"))
        only_d01 = [item for item in documents if item["doc_id"] == "D01"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "documents.json"
            path.write_text(json.dumps(only_d01, ensure_ascii=False), encoding="utf-8")
            record = self.run_agent("报销期限多久", data_path=path)
            self.assertEqual(record["result"]["status"], "completed")
            self.assertIn("30 天", record["result"]["answer"])
            self.assertEqual(len(record["result"]["citations"]), 1)
            self.assertNotIn("15 天", record["result"]["answer"])
            self.assert_supported_citations(record, data_path=path)

    def test_replacing_document_changes_grounded_answer(self):
        replaced_path = ROOT / "data" / "documents_replaced.json"
        record = self.run_agent("上海住宿报销上限是多少？", data_path=replaced_path)
        self.assertEqual(record["result"]["status"], "completed")
        self.assertIn("600 元", record["result"]["answer"])
        self.assertNotIn("500 元", record["result"]["answer"])
        self.assert_supported_citations(record, data_path=replaced_path)

    def test_invalid_citation_is_rejected_then_corrected(self):
        valid_quote = "员工申请年假，应至少提前 3 个工作日提交申请。"
        responses = [
            {"action": "search_documents", "args": {"query": "年假"}, "rationale": "检索。"},
            {"action": "read_document", "args": {"doc_id": "D04"}, "rationale": "读原文。"},
            {
                "action": "finish",
                "rationale": "故意提交错误引用以测试防护。",
                "finish": {
                    "status": "completed",
                    "answer": "提前 3 个工作日。",
                    "citations": [{"doc_id": "D04", "quote": "至少提前 30 天申请。"}],
                    "gaps": [],
                    "clarifying_question": "",
                },
            },
            {
                "action": "finish",
                "rationale": "根据校验反馈改用逐字原文。",
                "finish": {
                    "status": "completed",
                    "answer": "提前 3 个工作日。",
                    "citations": [{"doc_id": "D04", "quote": valid_quote}],
                    "gaps": [],
                    "clarifying_question": "",
                },
            },
        ]
        agent = KnowledgeQAAgent(
            SequenceModel(responses), ToolExecutor(KnowledgeBase(self.data_path)), max_steps=6
        )
        record = agent.run("年假提前多久申请？")
        self.assertEqual(record["result"]["status"], "completed")
        self.assertEqual(record["result"]["steps"], 4)
        self.assertEqual(record["trace"][2]["observation"]["error"]["code"], "INVALID_FINISH")
        self.assertEqual(record["result"]["citations"][0]["quote"], valid_quote)

    def test_step_limit_returns_explicit_incomplete_status(self):
        agent = KnowledgeQAAgent(
            AlwaysSearchModel(), ToolExecutor(KnowledgeBase(self.data_path)), max_steps=3
        )
        record = agent.run("年假需要提前几天申请？")
        self.assertEqual(record["result"]["status"], "max_steps_exceeded")
        self.assertEqual(record["result"]["steps"], 3)
        self.assertEqual(record["result"]["citations"], [])
        self.assertEqual(record["trace"][-1]["next_action"], "stop_max_steps")

    def test_multiple_json_actions_execute_only_first(self):
        content = (
            '{"action":"search_documents","args":{"query":"年假"}}\n'
            '{"action":"read_document","args":{"doc_id":"D04"}}'
        )
        parsed = OpenAICompatibleModel._parse_json(content)
        self.assertEqual(parsed["action"], "search_documents")
        self.assertEqual(parsed["args"]["query"], "年假")

    def test_non_json_trailing_content_is_rejected(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleModel._parse_json('{"action":"finish"}\n这不是 JSON')


if __name__ == "__main__":
    unittest.main()
