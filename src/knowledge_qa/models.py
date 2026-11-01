"""Real OpenAI-compatible model adapter and an explicitly labeled offline stub."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Protocol


SYSTEM_PROMPT = """你是企业知识问答 Agent。目标是用合成内部资料可靠回答员工问题。
每一轮只返回一个 JSON 对象，不得输出 Markdown 或额外文字。

可用动作：
1. {"action":"search_documents","args":{"query":"检索词","top_k":3},"rationale":"简短依据"}
2. {"action":"read_document","args":{"doc_id":"D01"},"rationale":"简短依据"}
3. {"action":"finish","rationale":"简短依据","finish":{"status":"completed|needs_clarification|insufficient_information|tool_error","answer":"面向用户的回答","citations":[{"doc_id":"D01","quote":"原文连续片段"}],"gaps":["资料缺口"],"clarifying_question":"需要追问时填写，否则为空字符串"}}

决策规则：
- 根据每轮工具观察自主选择补充检索、读取原文、追问或结束，不要机械执行固定流程。
- 最终事实只能来自本轮已成功 read_document 的原文；search 的 snippet 仅用于发现资料，不能作为最终引用。
- quote 必须是该 doc_id 原文中逐字连续的片段，不得改写、拼接或编造。completed 至少需要一条有效引用。
- 多份资料冲突且未给适用范围或优先级时，读取相关原文，分别列出冲突事实和引用，状态用 needs_clarification，并询问适用制度；不得按日期新、ID 顺序或个人偏好任选一个。
- 只检索到一份相关资料且无冲突时，可以依据该资料回答，同时指出适用范围未明等实际缺口（如有）。
- 问题依赖对话历史时，先将检索 query 改写成可独立理解的查询。资料不足时可换关键词补充检索，再决定追问或报告缺口。
- 工具失败时可根据剩余步数换参数、补充检索或停止；无法完成任务时状态用 tool_error，不得假装已读取。
- 不回答资料之外的事实。资料外问题用 insufficient_information，并填写 gaps。
- gaps 只能说明原文未提供哪些信息，不得断言原文没有说明的适用范围、例外或限制。
- rationale 只写一句简短的行动依据，不输出隐藏思维过程。
"""


class DecisionModel(Protocol):
    mode: str

    def decide(
        self,
        request: dict[str, Any],
        observations: list[dict[str, Any]],
        remaining_steps: int,
    ) -> dict[str, Any]: ...


class OpenAICompatibleModel:
    mode = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: int = 60):
        if not api_key or not model:
            raise ValueError("OPENAI_API_KEY and OPENAI_MODEL are required in real-model mode")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def decide(
        self,
        request: dict[str, Any],
        observations: list[dict[str, Any]],
        remaining_steps: int,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request,
                            "tool_observations": observations,
                            "remaining_steps": remaining_steps,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        http_request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Model API HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Model API connection failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        return self._parse_json(content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Some compatible services return several candidate actions together.
            # Execute only the first complete object; later decisions must wait for
            # a new tool observation instead of being replayed from this response.
            parsed, end = json.JSONDecoder().raw_decode(content)
            trailing = content[end:].strip()
            if trailing and not trailing.startswith(("{", "```")):
                raise ValueError("Unexpected non-JSON content after the first action")
        if not isinstance(parsed, dict):
            raise ValueError("Model response must be a JSON object")
        return parsed


class OfflineMockModel:
    """Deterministic workflow stub. It does not demonstrate real model capability."""

    mode = "offline_mock"

    @staticmethod
    def _action(name: str, rationale: str, **kwargs: Any) -> dict[str, Any]:
        return {"action": name, "rationale": rationale, **kwargs}

    @staticmethod
    def _finish(
        status: str,
        answer: str,
        citations: list[dict[str, str]] | None = None,
        gaps: list[str] | None = None,
        clarifying_question: str = "",
    ) -> dict[str, Any]:
        return {
            "action": "finish",
            "rationale": "现有观察已满足该流程分支的结束条件。",
            "finish": {
                "status": status,
                "answer": answer,
                "citations": citations or [],
                "gaps": gaps or [],
                "clarifying_question": clarifying_question,
            },
        }

    def decide(
        self,
        request: dict[str, Any],
        observations: list[dict[str, Any]],
        remaining_steps: int,
    ) -> dict[str, Any]:
        question = request["question"]
        query = question
        if "上海" in question and any("住宿" in turn.get("content", "") for turn in request["history"]):
            query = "上海 出差 住宿 报销标准"

        failed = next((item for item in reversed(observations) if not item.get("ok")), None)
        if failed:
            return self._finish(
                "tool_error",
                f"工具调用失败，无法可靠完成任务：{failed.get('summary', '未知错误')}",
                gaps=["需要恢复文档工具后重试。"],
            )

        searches = [item for item in observations if item.get("tool") == "search_documents" and item.get("ok")]
        reads = [item for item in observations if item.get("tool") == "read_document" and item.get("ok")]
        read_ids = {item["result"]["document"]["doc_id"] for item in reads}

        if not searches:
            return self._action(
                "search_documents",
                "先检索与当前问题相关的资料；追问已改写为独立查询。",
                args={"query": query, "top_k": 3},
            )

        hits = searches[-1]["result"]["hits"]
        hit_ids = {item["doc_id"] for item in hits}
        if not hits:
            return self._finish(
                "insufficient_information",
                "现有企业资料中没有找到支持该问题的内容，无法给出可靠答案。",
                gaps=[f"缺少与“{question}”相关的制度或产品资料。"],
            )

        is_deadline_question = "报销" in question and ("期限" in question or "多久" in question)
        if is_deadline_question and {"D01", "D02"}.issubset(hit_ids) and not {"D01", "D02"}.issubset(read_ids):
            doc_id = next(doc_id for doc_id in ("D01", "D02") if doc_id not in read_ids)
            return self._action(
                "read_document",
                "检索发现两份报销期限资料，需要读取双方原文核对是否冲突。",
                args={"doc_id": doc_id},
            )

        documents = {item["result"]["document"]["doc_id"]: item["result"]["document"] for item in reads}
        if is_deadline_question and {"D01", "D02"}.issubset(read_ids):
            return self._finish(
                "needs_clarification",
                "资料存在冲突：D01 规定报销期限为费用发生后 30 天，D02 规定为 15 天。两份资料均未说明适用范围或优先级，因此不能擅自选择其中一个。",
                [
                    {"doc_id": "D01", "quote": "员工应在费用发生后 30 天内提交报销申请。"},
                    {"doc_id": "D02", "quote": "员工应在费用发生后 15 天内提交报销申请。"},
                ],
                ["缺少 D01 与 D02 的适用范围。", "缺少冲突制度的优先级或废止关系。"],
                "请确认您要查询的是 D01《费用报销管理规范》还是 D02《费用报销操作细则》所适用的制度？",
            )
        if is_deadline_question and read_ids.intersection({"D01", "D02"}):
            doc_id = next(doc_id for doc_id in ("D01", "D02") if doc_id in read_ids)
            days = "30" if doc_id == "D01" else "15"
            document = documents[doc_id]
            return self._finish(
                "completed",
                f"根据目前仅检索到的 {doc_id}，报销期限为费用发生后 {days} 天；现有检索结果未发现冲突。",
                [{"doc_id": doc_id, "quote": document["content"]}],
                ["该制度未进一步说明特殊费用类型是否另有规定。"],
            )
        if "D03" in read_ids:
            content = documents["D03"]["content"]
            if any(city in query for city in ("北京", "上海", "深圳")):
                city = next(city for city in ("北京", "上海", "深圳") if city in query)
                amount = re.search(r"北京、上海、深圳每间每晚不超过\s*(\d+)\s*元", content).group(1)
                answer = f"根据 D03，{city}出差住宿报销上限为每间每晚 {amount} 元。"
                quote = re.search(r"出差住宿报销标准：北京、上海、深圳每间每晚不超过\s*\d+\s*元", content).group(0)
            else:
                amount = re.search(r"其他城市每间每晚不超过\s*(\d+)\s*元", content).group(1)
                answer = f"根据 D03，其他城市出差住宿报销上限为每间每晚 {amount} 元。"
                quote = re.search(r"其他城市每间每晚不超过\s*\d+\s*元", content).group(0)
            return self._finish("completed", answer, [{"doc_id": "D03", "quote": quote}])
        if "D04" in read_ids:
            document = documents["D04"]
            return self._finish(
                "completed",
                "根据 D04，员工申请年假应至少提前 3 个工作日提交申请。",
                [{"doc_id": "D04", "quote": document["content"]}],
            )

        unread = next((item["doc_id"] for item in hits if item["doc_id"] not in read_ids), None)
        if unread:
            return self._action(
                "read_document",
                "检索片段只用于定位，需读取原文后才能形成有依据的回答。",
                args={"doc_id": unread},
            )
        doc_id = next(iter(read_ids))
        document = documents[doc_id]
        return self._finish(
            "completed",
            f"根据 {doc_id}《{document['title']}》：{document['content']}",
            [{"doc_id": doc_id, "quote": document["content"]}],
        )
