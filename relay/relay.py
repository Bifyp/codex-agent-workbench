"""Local Telegram relay for nine agent bots and terminal input."""

from __future__ import annotations

import json
import os
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "topics.json"
AGENTS = ("codex", "claude", "hermes", "opencode", "aider", "goose", "qwen", "pi", "terminal")
TOPIC_LOCK = threading.RLock()
LABELS = {
    "codex": "Codex",
    "claude": "Claude Code · free-claude-code",
    "hermes": "Hermes · FreeLLMAPI",
    "opencode": "OpenCode · FreeLLMAPI",
    "aider": "Aider · FreeLLMAPI",
    "goose": "Goose · FreeLLMAPI",
    "qwen": "Qwen Code · FreeLLMAPI",
    "pi": "Pi · FreeLLMAPI",
    "terminal": "Мой терминал",
}

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

def config() -> dict:
    tokens = {agent: env(f"BOT_{agent.upper()}_TOKEN") for agent in AGENTS}
    missing = [agent for agent, token in tokens.items() if not token]
    if missing:
        raise RuntimeError("Не заданы токены: " + ", ".join(f"BOT_{a.upper()}_TOKEN" for a in missing))
    secret = env("RELAY_SECRET")
    if len(secret) < 24:
        raise RuntimeError("RELAY_SECRET должен содержать не менее 24 символов")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise RuntimeError("Не задан TELEGRAM_CHAT_ID")
    return {
        "tokens": tokens, "chat_id": chat_id, "secret": secret,
        "host": env("RELAY_HOST", "127.0.0.1"),
        "port": int(env("RELAY_PORT", "8765")),
        "topics": {agent: env(f"TOPIC_{agent.upper()}") for agent in AGENTS},
        "auto_topics": env("AUTO_CREATE_TOPICS", "1").lower() in ("1", "true", "yes"),
    }

def telegram(cfg: dict, agent: str, method: str, data: dict) -> dict:
    url = f"https://api.telegram.org/bot{cfg['tokens'][agent]}/{method}"
    req = Request(url, data=json.dumps(data, ensure_ascii=False).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Telegram {method}: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method}: {result.get('description', 'unknown error')}")
    return result["result"]

def read_topics() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def write_topics(topics: dict) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)

def create_topic(cfg: dict, agent: str, title: str, key: str) -> int:
    with TOPIC_LOCK:
        topics = read_topics()
        if key not in topics:
            result = telegram(cfg, "terminal", "createForumTopic", {
                "chat_id": cfg["chat_id"], "name": title[:128]
            })
            topics[key] = result["message_thread_id"]
            write_topics(topics)
        return int(topics[key])

def get_thread(cfg: dict, agent: str, session: str = "") -> int | None:
    topics = read_topics()
    key = f"{agent}:{session}" if session else ""
    value = topics.get(key) or cfg["topics"].get(agent) or topics.get(agent)
    return int(value) if value else None

def post(cfg: dict, agent: str, text: str, topic: int | None = None) -> None:
    for offset in range(0, len(text) or 1, 3900):
        data = {
            "chat_id": cfg["chat_id"],
            "text": text[offset:offset + 3900] or " ",
            "disable_web_page_preview": True,
        }
        if topic:
            data["message_thread_id"] = topic
        telegram(cfg, agent, "sendMessage", data)

def payload_text(payload: dict) -> str:
    value = payload.get("text") or payload.get("message") or payload.get("content") or ""
    if not value:
        value = payload
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)

class Handler(BaseHTTPRequestHandler):
    server: "RelayServer"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {fmt % args}")

    def respond(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.server.cfg['secret']}"

    def do_GET(self) -> None:
        if self.path == "/health":
            return self.respond(200, {"ok": True, "agents": list(AGENTS),
                                     "controller_ready": bool(getattr(getattr(self.server, "controller", None), "ready", False))})
        self.respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self.authorized():
            return self.respond(401, {"error": "unauthorized"})
        route = urlparse(self.path).path.strip("/").split("/")
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if len(route) == 2 and route[0] == "event" and route[1] in AGENTS:
                self.event(route[1], payload)
                return self.respond(200, {"ok": True})
            if self.path == "/terminal":
                self.event("terminal", {"event": "input", "text": payload_text(payload)})
                return self.respond(200, {"ok": True})
            if self.path == "/topic/subagent":
                agent = str(payload.get("agent", ""))
                session = str(payload.get("session_id", ""))
                name = str(payload.get("name") or session or "subagent")
                if agent not in AGENTS or agent == "terminal":
                    return self.respond(400, {"error": "invalid_agent"})
                topic = create_topic(self.server.cfg, agent, f"{LABELS[agent]} · {name}", f"{agent}:{session}")
                return self.respond(200, {"ok": True, "message_thread_id": topic})
            self.respond(404, {"error": "not_found"})
        except (ValueError, RuntimeError, OSError) as exc:
            self.respond(400, {"error": str(exc)[:300]})

    def event(self, agent: str, payload: dict) -> None:
        if getattr(self.server, "event_sink", None):
            self.server.event_sink(agent, payload)
            return
        event_name = str(payload.get("event") or payload.get("type") or "update")[:80]
        subagent = str(payload.get("subagent_id") or "")
        session = str(subagent or payload.get("session_id") or "")[:100]
        normalized_event = event_name.lower()
        if session and "subagent" in normalized_event and any(
            marker in normalized_event for marker in ("start", "spawn", "created")
        ):
            create_topic(
                self.server.cfg,
                agent,
                f"{LABELS[agent]} · {str(payload.get('name') or session)}",
                f"{agent}:{session}",
            )
        text = f"[{LABELS[agent]}] {event_name}"
        if session:
            text += f" · {session}"
        content = payload_text(payload)
        if content:
            text += f"\n{content}"
        post(self.server.cfg, agent, text, get_thread(self.server.cfg, agent, session))

class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address: tuple[str, int], cfg: dict):
        super().__init__(address, Handler)
        self.cfg = cfg

def main() -> None:
    cfg = config()
    if cfg["auto_topics"]:
        topics = read_topics()
        for agent in AGENTS:
            if not cfg["topics"].get(agent) and agent not in topics:
                try:
                    topics[agent] = telegram(cfg, "terminal", "createForumTopic", {
                        "chat_id": cfg["chat_id"], "name": LABELS[agent][:128]
                    })["message_thread_id"]
                except RuntimeError as exc:
                    print(f"Не удалось создать тему {LABELS[agent]}: {exc}")
        write_topics(topics)
    server = RelayServer((cfg["host"], cfg["port"]), cfg)
    print(f"Telegram Agent Relay слушает http://{cfg['host']}:{cfg['port']}")
    server.serve_forever()

if __name__ == "__main__":
    main()

