"""Model-in-the-loop ReAct orchestration and grounded-citation validation."""

from __future__ import annotations

from typing import Any

from .models import DecisionModel
from .tools import ToolExecutor


FINAL_STATUSES = {"completed", "needs_clarification", "insufficient_information", "tool_error"}


class KnowledgeQAAgent:
    def __init__(self, model: DecisionModel, tools: ToolExecutor, max_steps: int = 8):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.model = model
        self.tools = tools
        self.max_steps = max_steps

    def run(self, question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        request = {"question": question.strip(), "history": history or []}
        if not request["question"]:
            raise ValueError("question must not be empty")
        observations: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []

        for step in range(1, self.max_steps + 1):
            try:
                action = self.model.decide(request, observations, self.max_steps - step + 1)
            except Exception as exc:
                result = {
                    "status": "model_error",
                    "answer": f"模型调用或响应解析失败，任务未完成：{exc}",
                    "citations": [],
                    "gaps": ["需要可用的模型服务及符合协议的 JSON 响应。"],
                    "clarifying_question": "",
                    "steps": step - 1,
                    "model_mode": self.model.mode,
                }
                return {"request": request, "result": result, "trace": trace}

            name = action.get("action", "")
            rationale = str(action.get("rationale", ""))[:240]
            arguments = action.get("args", {})
            if name == "finish":
                candidate = action.get("finish", {})
                errors = self._validate_finish(candidate, observations)
                if errors:
                    observation = {
                        "tool": "finish",
                        "ok": False,
                        "error": {"code": "INVALID_FINISH", "messages": errors},
                        "summary": "结束结果未通过校验：" + "；".join(errors),
                    }
                else:
                    result = self._normalize_finish(candidate)
                    result.update(steps=step, model_mode=self.model.mode)
                    trace.append(
                        {
                            "step": step,
                            "action": "finish",
                            "arguments": {},
                            "rationale": rationale,
                            "observation": {"ok": True, "summary": "结果与引用校验通过。"},
                        }
                    )
                    self._link_trace(trace, "stop")
                    return {"request": request, "result": result, "trace": trace}
            else:
                observation = self.tools.call(str(name), arguments if isinstance(arguments, dict) else {})

            observations.append(observation)
            trace.append(
                {
                    "step": step,
                    "action": name,
                    "arguments": arguments,
                    "rationale": rationale,
                    "observation": observation,
                }
            )

        result = {
            "status": "max_steps_exceeded",
            "answer": f"已达到 {self.max_steps} 步执行上限，任务未完成；未输出未经核验的答案。",
            "citations": [],
            "gaps": ["需要增加执行预算或改进检索与决策后重试。"],
            "clarifying_question": "",
            "steps": self.max_steps,
            "model_mode": self.model.mode,
        }
        self._link_trace(trace, "stop_max_steps")
        return {"request": request, "result": result, "trace": trace}

    def _validate_finish(self, candidate: Any, observations: list[dict[str, Any]]) -> list[str]:
        if not isinstance(candidate, dict):
            return ["finish 必须是对象。"]
        errors = []
        status = candidate.get("status")
        if status not in FINAL_STATUSES:
            errors.append("status 不在允许范围内。")
        if not str(candidate.get("answer", "")).strip():
            errors.append("answer 不能为空。")
        citations = candidate.get("citations", [])
        if not isinstance(citations, list):
            errors.append("citations 必须是数组。")
            citations = []
        if status == "completed" and not citations:
            errors.append("completed 至少需要一条原文引用。")
        if status == "needs_clarification" and not str(candidate.get("clarifying_question", "")).strip():
            errors.append("needs_clarification 必须填写 clarifying_question。")
        if status == "insufficient_information" and not candidate.get("gaps"):
            errors.append("insufficient_information 必须说明资料缺口。")

        read_documents = {
            item["result"]["document"]["doc_id"]: item["result"]["document"]["content"]
            for item in observations
            if item.get("ok") and item.get("tool") == "read_document"
        }
        for citation in citations:
            if not isinstance(citation, dict):
                errors.append("每条 citation 必须是对象。")
                continue
            doc_id = citation.get("doc_id")
            quote = citation.get("quote", "")
            if doc_id not in read_documents:
                errors.append(f"引用了本轮未成功读取的文档 {doc_id}。")
            elif not quote or quote not in read_documents[doc_id]:
                errors.append(f"{doc_id} 的 quote 不是原文连续片段。")
        return errors

    def _normalize_finish(self, candidate: dict[str, Any]) -> dict[str, Any]:
        citations = []
        for item in candidate.get("citations", []):
            document = self.tools.knowledge_base.get(item["doc_id"])
            citations.append(
                {
                    "doc_id": item["doc_id"],
                    "version_date": document["version_date"],
                    "quote": item["quote"],
                }
            )
        gaps = candidate.get("gaps", [])
        return {
            "status": candidate["status"],
            "answer": candidate["answer"],
            "citations": citations,
            "gaps": gaps if isinstance(gaps, list) else [str(gaps)],
            "clarifying_question": candidate.get("clarifying_question", ""),
        }

    @staticmethod
    def _link_trace(trace: list[dict[str, Any]], terminal_action: str) -> None:
        for index, entry in enumerate(trace):
            entry["next_action"] = trace[index + 1]["action"] if index + 1 < len(trace) else terminal_action
