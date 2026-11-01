"""Local web UI for the knowledge QA agent; no third-party dependencies."""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .agent import KnowledgeQAAgent
from .models import OfflineMockModel, OpenAICompatibleModel
from .tools import KnowledgeBase, ToolExecutor


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"


class KnowledgeWebServer(ThreadingHTTPServer):
    def __init__(self, address, handler, model, knowledge_base, max_steps):
        super().__init__(address, handler)
        self.model = model
        self.knowledge_base = knowledge_base
        self.max_steps = max_steps


class WebHandler(BaseHTTPRequestHandler):
    server: KnowledgeWebServer

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/config":
            documents = [
                {
                    "doc_id": item["doc_id"],
                    "title": item["title"],
                    "version_date": item["version_date"],
                }
                for item in self.server.knowledge_base.documents.values()
            ]
            self._json(
                {
                    "model_mode": self.server.model.mode,
                    "model": os.getenv("OPENAI_MODEL", "离线流程 Mock") if self.server.model.mode != "offline_mock" else "离线流程 Mock",
                    "max_steps": self.server.max_steps,
                    "documents": documents,
                }
            )
            return
        files = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}
        if path not in files:
            self._json({"error": "Not found"}, status=404)
            return
        file_path = STATIC_DIR / files[path]
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types[file_path.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/ask":
            self._json({"error": "Not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("请求体为空或超过 64 KB 限制")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            question = str(payload.get("question", "")).strip()
            history = payload.get("history", [])
            if not question:
                raise ValueError("问题不能为空")
            if not isinstance(history, list):
                raise ValueError("history 必须是数组")
            clean_history = [
                {"role": str(turn.get("role", "user")), "content": str(turn.get("content", ""))[:4000]}
                for turn in history[-10:]
                if isinstance(turn, dict)
            ]
            agent = KnowledgeQAAgent(
                self.server.model,
                ToolExecutor(self.server.knowledge_base),
                max_steps=self.server.max_steps,
            )
            self._json(agent.run(question, clean_history))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._json({"error": f"服务处理失败：{exc}"}, status=500)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} {format_string % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web UI for enterprise knowledge QA")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "documents.json")
    parser.add_argument("--mock", action="store_true", help="Offline workflow stub; clearly labeled in the UI")
    parser.add_argument("--max-steps", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mock:
        model = OfflineMockModel()
    else:
        model = OpenAICompatibleModel(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
    server = KnowledgeWebServer(
        (args.host, args.port),
        WebHandler,
        model,
        KnowledgeBase(args.data),
        args.max_steps,
    )
    mode = "offline_mock" if args.mock else os.getenv("OPENAI_MODEL", "openai_compatible")
    print(f"Knowledge QA web UI: http://{args.host}:{args.port} ({mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
