"""Local document tools with deterministic failure injection for tests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class KnowledgeBase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.documents = {item["doc_id"]: item for item in raw}
        if len(self.documents) != len(raw):
            raise ValueError("Document IDs must be unique")

    def get(self, doc_id: str) -> dict[str, Any] | None:
        return self.documents.get(doc_id)

    @staticmethod
    def _terms(text: str) -> list[tuple[str, float]]:
        text = text.lower()
        terms: list[tuple[str, float]] = []
        for word in re.findall(r"[a-z0-9]+", text):
            terms.append((word, 4.0))
        for segment in re.findall(r"[\u4e00-\u9fff]+", text):
            for index in range(len(segment) - 1):
                terms.append((segment[index : index + 2], 2.0))
            for char in segment:
                terms.append((char, 0.15))
        return terms

    @classmethod
    def _score(cls, query: str, text: str) -> float:
        normalized_text = text.lower()
        score = sum(weight for term, weight in cls._terms(query) if term in normalized_text)
        compact_query = re.sub(r"\s+", "", query.lower())
        if compact_query and compact_query in re.sub(r"\s+", "", normalized_text):
            score += 10.0
        return score

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        top_k = max(1, min(int(top_k), 5))
        ranked = []
        for document in self.documents.values():
            searchable = f"{document['title']} {document['content']}"
            score = self._score(query, searchable)
            if score < 1.0:
                continue
            sentences = re.split(r"(?<=[。！？；])", document["content"])
            snippet = max(
                (item for item in sentences if item),
                key=lambda item: self._score(query, item),
                default=document["content"],
            )
            ranked.append(
                {
                    "doc_id": document["doc_id"],
                    "title": document["title"],
                    "version_date": document["version_date"],
                    "score": round(score, 2),
                    "snippet": snippet,
                }
            )
        ranked.sort(key=lambda item: (-item["score"], item["doc_id"]))
        return ranked[:top_k]


class ToolExecutor:
    """Expose exactly two business tools to the model."""

    def __init__(self, knowledge_base: KnowledgeBase, failures: dict[str, int] | None = None):
        self.knowledge_base = knowledge_base
        self.failures = dict(failures or {})

    def _should_fail(self, name: str, args: dict[str, Any]) -> bool:
        keys = [f"{name}:*"]
        if name == "read_document":
            keys.insert(0, f"{name}:{args.get('doc_id', '')}")
        for key in keys:
            if self.failures.get(key, 0) > 0:
                self.failures[key] -= 1
                return True
        return False

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        observation: dict[str, Any] = {"tool": name}
        if name not in {"search_documents", "read_document"}:
            observation.update(
                ok=False,
                error={"code": "UNKNOWN_TOOL", "message": f"Unsupported tool: {name}"},
                summary="工具不存在，请选择可用工具或结束任务。",
            )
            return observation
        if self._should_fail(name, args):
            observation.update(
                ok=False,
                error={"code": "INJECTED_TOOL_FAILURE", "message": f"Injected failure in {name}"},
                summary=f"{name} 调用失败，可调整动作或报告工具失败。",
            )
            return observation
        try:
            if name == "search_documents":
                hits = self.knowledge_base.search(str(args.get("query", "")), args.get("top_k", 3))
                observation.update(
                    ok=True,
                    result={"hits": hits},
                    summary=f"检索到 {len(hits)} 篇文档：" + ", ".join(hit["doc_id"] for hit in hits),
                )
            else:
                doc_id = str(args.get("doc_id", ""))
                document = self.knowledge_base.get(doc_id)
                if document is None:
                    observation.update(
                        ok=False,
                        error={"code": "DOCUMENT_NOT_FOUND", "message": f"Unknown doc_id: {doc_id}"},
                        summary=f"未找到 {doc_id}，请使用检索结果中的文档 ID。",
                    )
                else:
                    observation.update(
                        ok=True,
                        result={"document": document},
                        summary=f"已读取 {doc_id}（版本 {document['version_date']}）。",
                    )
        except (TypeError, ValueError) as exc:
            observation.update(
                ok=False,
                error={"code": "INVALID_ARGUMENTS", "message": str(exc)},
                summary=f"工具参数错误：{exc}",
            )
        return observation
