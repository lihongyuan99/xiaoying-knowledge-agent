"""Regenerate deterministic offline workflow examples; not real-model evidence."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_qa.agent import KnowledgeQAAgent
from knowledge_qa.models import OfflineMockModel
from knowledge_qa.tools import KnowledgeBase, ToolExecutor


@dataclass
class Case:
    name: str
    question: str
    expected_status: str
    history: list[dict[str, str]] = field(default_factory=list)
    data: str = "data/documents.json"
    failures: dict[str, int] = field(default_factory=dict)
    covers: list[str] = field(default_factory=list)


CASES = [
    Case("01_clear_answer", "年假需要提前几天申请？", "completed", covers=["明确答案"]),
    Case("02_conflict", "报销期限多久", "needs_clarification", covers=["文档冲突", "双执行路径之一"]),
    Case("03_out_of_scope", "公司是否允许带宠物上班？", "insufficient_information", covers=["资料外问题"]),
    Case("04_paraphrase", "在北京出差，酒店每晚最多能报多少钱？", "completed", covers=["改写后的相似问题"]),
    Case(
        "05_followup",
        "那上海呢？",
        "completed",
        history=[
            {"role": "user", "content": "北京出差住宿报销标准是多少？"},
            {"role": "assistant", "content": "北京每间每晚不超过 500 元。"},
        ],
        covers=["上下文追问", "检索改写"],
    ),
    Case(
        "06_tool_failure",
        "北京住宿标准是多少？",
        "tool_error",
        failures={"read_document:D03": 1},
        covers=["工具失败", "失败路径"],
    ),
    Case(
        "07_replaced_document",
        "上海住宿报销上限是多少？",
        "completed",
        data="data/documents_replaced.json",
        covers=["替换文档后答案改变", "引用同步改变"],
    ),
]


def main() -> None:
    manifest = []
    for case in CASES:
        agent = KnowledgeQAAgent(
            OfflineMockModel(),
            ToolExecutor(KnowledgeBase(ROOT / case.data), failures=case.failures),
            max_steps=8,
        )
        record = agent.run(case.question, case.history)
        assert record["result"]["status"] == case.expected_status, case.name
        record["example_metadata"] = {
            "covers": case.covers,
            "data": case.data,
            "injected_failures": case.failures,
            "evidence_boundary": "离线 Mock 仅验证流程与约束，不证明真实模型决策效果。",
        }
        output_path = ROOT / "examples" / f"{case.name}.json"
        output_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.append(
            {
                "file": output_path.name,
                "question": case.question,
                "status": record["result"]["status"],
                "steps": record["result"]["steps"],
                "model_mode": record["result"]["model_mode"],
                "covers": case.covers,
            }
        )
    (ROOT / "examples" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Generated {len(CASES)} offline-mock examples.")


if __name__ == "__main__":
    main()
