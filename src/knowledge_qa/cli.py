"""CLI entry point for real-model and explicitly labeled offline-mock runs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .agent import KnowledgeQAAgent
from .models import OfflineMockModel, OpenAICompatibleModel
from .tools import KnowledgeBase, ToolExecutor


ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grounded enterprise knowledge QA Agent")
    parser.add_argument("--question", required=True, help="Current employee question")
    parser.add_argument("--history", default="[]", help="JSON array of {role, content} turns")
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "documents.json")
    parser.add_argument("--mock", action="store_true", help="Offline workflow stub; not a real-model evaluation")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--trace", type=Path, help="Optional path for the complete JSON run record")
    parser.add_argument("--fail-tool", choices=["search_documents", "read_document"])
    parser.add_argument("--fail-doc-id", default="*")
    parser.add_argument("--fail-count", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    history = json.loads(args.history)
    if not isinstance(history, list):
        raise ValueError("--history must be a JSON array")
    if args.mock:
        model = OfflineMockModel()
    else:
        model = OpenAICompatibleModel(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
    failures = {}
    if args.fail_tool:
        failures[f"{args.fail_tool}:{args.fail_doc_id}"] = args.fail_count
    agent = KnowledgeQAAgent(
        model=model,
        tools=ToolExecutor(KnowledgeBase(args.data), failures=failures),
        max_steps=args.max_steps,
    )
    record = agent.run(args.question, history)
    rendered = json.dumps(record, ensure_ascii=False, indent=2)
    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
