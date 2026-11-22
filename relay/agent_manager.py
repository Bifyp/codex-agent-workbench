"""Serialized agent execution with shared context, sessions and team handoffs."""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from human_messages import text_value, tool_start, tool_result, codex_item, warning
import team_validation
from agent_labels import PHASES, TASKS, model_name

LOG = logging.getLogger("agents")
NAMES = {
    "codex": "Codex", "claude": "Claude Code", "hermes": "Hermes",
    "opencode": "OpenCode", "aider": "Aider", "goose": "Goose",
    "qwen": "Qwen Code", "pi": "Pi",
}
FREE_AGENTS = {"opencode", "aider", "goose", "qwen", "pi"}

def choose_agent(prompt: str) -> str:
    """Pick one worker for an ordinary prompt; /team remains explicit."""
    text = (prompt or "").lower()
    if any(word in text for word in ("mcp", "api интеграц", "интеграц", "webhook")):
        return "goose"
    if any(word in text for word in ("проанализируй репозиторий", "анализ репозитория", "найди по проекту", "исследуй код")):
        return "qwen"
    if any(word in text for word in ("маленьк", "точечн", "замени строк", "переименуй", "quick fix")):
        return "aider"
    if any(word in text for word in ("повторяющ", "скрипт", "список файлов", "простой")):
        return "pi"
    if any(word in text for word in ("отлад", "реализуй", "рефактор", "напиши код", "исправь код")):
        return "opencode"
    return "codex"
TEAM = [
    ("codex", "plan", "Разбери задачу и составь конкретный план для Claude. Код пока не меняй. Укажи критерии готовности."),
    ("claude", "implement", "Выполни задачу по плану Codex. Реализуй изменения и запусти согласованные тесты. Команды python -m unittest и python -m pytest разрешены для этой задачи. После успешных тестов сразу дай краткий итог и передай работу Hermes. Не расширяй требования неоговорёнными случаями. Если инструмент отказал в разрешении, сообщи об этом; не пробуй обходить отказ другой формой команды."),
    ("hermes", "review", "Проверь результат Claude относительно исходной задачи и плана Codex. Найди ошибки, риски и пробелы. Код не меняй. Дай точные замечания."),
    ("codex", "integrate", "Сопоставь реализацию Claude и ревью Hermes. Исправь подтверждённые проблемы, заверши задачу и дай пользователю итог. Явно укажи непроверенное."),
]


def executables(root):
    from backend_profiles import executable
    home = Path.home()
    candidates = {
        "codex": home / "AppData/Local/Programs/OpenAI/Codex/bin/codex.exe",
        "claude": home / ".local/bin/fcc-claude.exe",
        "hermes": home / "AppData/Local/hermes/bin/hermes.exe",
        "opencode": home / "AppData/Local/Microsoft/WinGet/Links/opencode.exe",
        "aider": home / "AppData/Roaming/uv/tools/aider-chat/Scripts/python.exe",
        "goose": home / "AppData/Local/Microsoft/WinGet/Links/goose.exe",
        "qwen": Path(r"C:\nvm4w\nodejs\qwen.ps1"),
        "pi": Path(r"C:\nvm4w\nodejs\pi.ps1"),
    }
    settings = root / "agents.json"
    overrides = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    found = {}
    for agent, path in candidates.items():
        selected = overrides.get(agent) or (executable(root, agent) if agent in ("codex", "claude", "hermes") else None) or str(path)
        if not Path(selected).is_file():
            selected = shutil.which({"claude": "fcc-claude"}.get(agent, agent))
        if selected:
            found[agent] = str(Path(selected).resolve())
    return found


def command(agent, executable, session=None, prompt=None):
    if agent == "aider" and str(executable).lower().endswith("python.exe"):
        executable = [executable, "-m", "aider"]
    elif agent in FREE_AGENTS and str(executable).lower().endswith(".ps1"):
        executable = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", executable]
    else:
        executable = [executable]
    if agent == "codex":
        if session:
            return executable + ["exec", "resume", "--enable", "multi_agent_v2", "--json", "--skip-git-repo-check", session, "-"]
        return executable + ["exec", "--enable", "multi_agent_v2", "--json", "--skip-git-repo-check", "-s", "workspace-write", "-"]
    if agent == "claude":
        args = ["-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--include-hook-events",
                "--forward-subagent-text", "--permission-mode", "acceptEdits",
                "--allowedTools", "Bash(python -m unittest*)", "Bash(python -m pytest*)", "Bash(pytest*)"]
        return executable + args + (["--resume", session] if session else [])
    if agent == "opencode":
        return executable + ["run", "--format", "json"] + (["--session", session] if session else [])
    if agent == "aider":
        # Aider must never auto-commit changes made from a Telegram task.
        return executable + (["--message", prompt] if prompt is not None else ["--message-file", "-"]) + [
            "--no-auto-commits", "--no-git", "--no-check-update", "--no-show-model-warnings"]
    if agent == "goose":
        return executable + ["run", "--no-session", "-i", "-"]
    if agent == "qwen":
        return executable + ["--output-format", "stream-json", "--prompt", prompt or ""]
    if agent == "pi":
        args = executable + ["--print", "--mode", "json", "--provider", "freellmapi"]
        return args + (["--session", session] if session else [])
    args = executable + ["chat", "--query-file", "-", "--oneshot", "--format", "stream-json",
            "--max-turns", "40", "--provider", "custom"]
    return args + (["--resume", session] if session else [])


class StreamDecoder:
    def __init__(self, agent, emit, session, child, identity=None):
        self.agent, self.emit, self.session, self.child = agent, emit, session, child
        self.final, self.text, self.error = "", "", ""
        self.partial = False
        self.children = set()
        self.delegate_calls = {}
        self.aliases = {}
        self.child_results = set()
        self.identity = identity or (lambda child_id, **metadata: None)
        self.hermes_native = {}
        self.continuation_sent = False
        self.completed_children = set()
        self.latest_child_text = {}
        self.last_activity = time.monotonic()
        self.tool_names = {}

    def register_child(self, child_id, name):
        child_id = self.aliases.get(child_id, child_id)
        if child_id and child_id not in self.children:
            self.children.add(child_id)
            self.child(child_id, name)

    def child_result(self, child_id, text):
        child_id = self.aliases.get(child_id, child_id)
        if child_id:
            text = text_value(text)
            if not text.strip():
                return
            fingerprint = (str(child_id), str(text))
            if fingerprint not in self.child_results:
                self.child_results.add(fingerprint)
                self.latest_child_text[child_id] = str(text)
                self.emit("\n[Результат субагента]\n" + str(text) + "\n", child_id)

    def line(self, raw):
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            if raw.strip():
                if self.agent in FREE_AGENTS:
                    self.text += raw + "\n"
                    self.final = self.text
                match = re.search(r"session_id:\s*([A-Za-z0-9_-]+)", raw)
                if match:
                    self.session(match.group(1))
                else:
                    self.emit(raw + "\n", None)
            return
        if not isinstance(event, dict):
            self.emit(raw + "\n", None)
            return
        if self.agent in FREE_AGENTS:
            sid = event.get("sessionID") or event.get("session_id") or event.get("thread_id")
            if sid:
                self.session(str(sid))
            kind = str(event.get("type") or event.get("event") or "")
            text = event.get("text")
            if not isinstance(text, str) and isinstance(event.get("part"), dict):
                text = event["part"].get("text")
            if not isinstance(text, str) and isinstance(event.get("message"), dict):
                text = event["message"].get("content")
                if isinstance(text, list):
                    text = "".join(str(item.get("text", "")) for item in text if isinstance(item, dict))
            if isinstance(text, str) and text:
                if kind not in ("message_update", "text_delta"):
                    self.text += text
                    self.final = self.text
                    self.emit(text + ("" if text.endswith("\n") else "\n"), None)
                return
        kind = event.get("type", "")
        if kind in ("tool_use", "tool_result", "subagent_event", "text"):
            self.last_activity = time.monotonic()
        elif kind in ("assistant", "user") and any(isinstance(block, dict) and block.get("type") in
                ("text", "tool_use", "tool_result") for block in event.get("message", {}).get("content", [])):
            self.last_activity = time.monotonic()
        child_id = self.aliases.get(event.get("parent_tool_use_id"), event.get("parent_tool_use_id"))
        if child_id and event.get("agent_id"):
            self.identity(child_id, native_id=event["agent_id"])
        if event.get("session_id") and not child_id:
            self.session(event["session_id"])
        if event.get("thread_id") and kind == "thread.started":
            self.session(event["thread_id"])
        if not child_id and (kind in ("error", "turn.failed") or (kind == "result" and
                (event.get("is_error") or event.get("exit_code", 0)))):
            self.error = str(event.get("error") or event.get("result") or event)
        if kind == "stream_event":
            inner = event.get("event", {})
            delta = inner.get("delta", {})
            if delta.get("type") in ("text_delta", "input_json_delta"):
                self.last_activity = time.monotonic()
            if delta.get("type") == "text_delta":
                self.emit(delta.get("text", ""), child_id)
                self.partial = True
            return
        if kind == "text":
            text = event.get("text", "")
            if not child_id:
                self.text += text
            self.emit(text, child_id)
            return
        if kind == "result":
            if child_id:
                self.child_result(child_id, event.get("result") or event.get("text") or "")
                return
            self.final = str(event.get("result") or event.get("text") or self.text)
            if not self.partial and not self.text and self.final:
                self.emit(self.final + "\n", child_id)
            if event.get("permission_denials"):
                self.emit("\nНекоторые действия требуют разрешения и не выполнены:\n" +
                          text_value(event["permission_denials"]) + "\n", child_id)
            return
        if kind in ("assistant", "user"):
            for block in event.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                blocktype = block.get("type")
                if blocktype == "text":
                    if kind == "assistant" and not child_id:
                        self.final = block.get("text", "")
                    if child_id and kind == "assistant":
                        self.child_result(child_id, block.get("text", ""))
                    elif not self.partial:
                        self.emit(block.get("text", "") + "\n", child_id)
                elif blocktype == "tool_use":
                    self.tool_names[block.get("id")] = block.get("name")
                    if block.get("name") == "SendMessage":
                        target = block.get("input", {}).get("to")
                        if target in self.aliases:
                            self.aliases[block.get("id")] = self.aliases[target]
                            self.continuation_sent = True
                    if block.get("name", "").lower() in ("task", "agent", "delegate_task", "spawn_agent"):
                        self.register_child(block.get("id"), block.get("input", {}).get("description") or block["name"])
                    self.emit("\n\n" + tool_start(block.get("name"), block.get("input")) + "\n\n", child_id)
                elif blocktype == "tool_result":
                    call = self.aliases.get(block.get("tool_use_id"), block.get("tool_use_id"))
                    if call in self.children:
                        content = json.dumps(block.get("content", ""), ensure_ascii=False)
                        match = re.search(r"agentId:\s*([A-Za-z0-9_-]+)", content)
                        if match:
                            self.identity(call, native_id=match.group(1))
                        self.child_result(call, content)
                    if call not in self.children:
                        self.emit("\n\n" + tool_result(self.tool_names.get(block.get("tool_use_id")), block.get("content"), block.get("is_error", False)) + "\n\n", child_id)
            return
        if kind == "subagent_event":
            native = event.get("subagent_id")
            if not native:
                return
            key = self.hermes_native.get(native)
            if not key:
                index = event.get("task_index", 0)
                candidates = [ids[index] for ids in self.delegate_calls.values()
                              if isinstance(index, int) and 0 <= index < len(ids)
                              and ids[index] not in self.hermes_native.values()]
                key = candidates[-1] if candidates else native
                self.hermes_native[native] = key
                self.aliases[native] = key
                self.register_child(key, event.get("goal") or "Hermes subagent")
            self.identity(key, native_id=native, session_id=event.get("child_session_id"),
                          status=event.get("status"), model=event.get("model"))
            action = event.get("event")
            if action == "subagent.text":
                self.emit(str(event.get("text") or ""), key)
            elif action == "subagent.complete":
                self.child_result(key, event.get("summary") or event.get("text") or "Завершено")
            elif action == "subagent.tool":
                self.emit("\n\n" + tool_start(event.get("name"), event.get("input") or {"command": event.get("text")}) + "\n\n", key)
            elif action == "subagent.tool_result":
                self.emit("\n\n" + tool_result(event.get("name"), event.get("result") or event.get("text")) + "\n\n", key)
            elif action in ("subagent.start", "subagent.spawn_requested"):
                if action == "subagent.start":
                    self.emit("\n👥 Субагент начал работу\nЗадача: " + str(event.get("goal") or "") + "\n\n", key)
            else:
                self.emit("\n" + str(event.get("text") or "") + "\n", key)
            return
        if kind.startswith("item."):
            item = event.get("item", {})
            if item.get("type") == "reasoning":
                return
            if item.get("type") == "agent_message":
                if kind == "item.completed":
                    self.final = item.get("text", "")
                    self.emit(self.final + "\n", child_id)
                return
            if "spawn" in str(item.get("tool", "")).lower():
                for receiver in item.get("receiver_thread_ids", []):
                    self.register_child(receiver, "Codex subagent")
            for receiver, state in (item.get("agents_states") or {}).items():
                self.register_child(receiver, "Codex subagent")
                if isinstance(state, dict) and state.get("message"):
                    self.child_result(receiver, state["message"])
            self.emit("\n\n" + codex_item(item, kind == "item.completed") + "\n\n", child_id)
            return
        if kind in ("tool_use", "tool_result"):
            if kind == "tool_use":
                self.tool_names[event.get("tool_call_id")] = event.get("name")
            if kind == "tool_use" and event.get("name") == "delegate_task":
                call = event.get("tool_call_id") or f"delegate-{len(self.delegate_calls)}"
                tasks = event.get("input", {}).get("tasks") or [event.get("input", {})]
                ids = [call if len(tasks) == 1 else f"{call}:{index}" for index in range(len(tasks))]
                self.delegate_calls[call] = ids
                for index, task in enumerate(tasks):
                    self.register_child(ids[index], str(task.get("goal") or "Hermes delegate")[:70])
            if kind == "tool_result" and event.get("name") == "delegate_task":
                call = event.get("tool_call_id")
                try:
                    output = json.loads(event.get("output") or "{}")
                except (ValueError, TypeError):
                    output = {}
                ids = self.delegate_calls.get(call, [])
                for index, result in enumerate(output.get("results") or []):
                    task_index = result.get("task_index", index)
                    key = ids[task_index] if isinstance(task_index, int) and 0 <= task_index < len(ids) else result.get("subagent_id") or f"{call}:{index}"
                    self.register_child(key, "Hermes subagent")
                    if result.get("subagent_id"):
                        self.aliases[result["subagent_id"]] = key
                    self.child_result(key, result.get("summary") or json.dumps(result, ensure_ascii=False))
            if kind == "tool_use":
                self.emit("\n\n" + tool_start(event.get("name"), event.get("input")) + "\n\n", child_id)
            elif event.get("name") != "delegate_task":
                self.emit("\n\n" + tool_result(event.get("name"), event.get("output"), event.get("is_error", False)) + "\n\n", child_id)
            return
        if kind == "system" and event.get("subtype") == "task_started":
            call = event.get("tool_use_id") or event.get("task_id")
            self.register_child(call, event.get("description") or "Claude subagent")
            if event.get("task_id"):
                call = self.aliases.get(call, call)
                self.aliases[event["task_id"]] = call
                self.identity(call, native_id=event["task_id"])
            return
        if kind == "system" and event.get("subtype") == "task_notification":
            call = self.aliases.get(event.get("task_id"), self.aliases.get(event.get("tool_use_id"), event.get("tool_use_id")))
            if call in self.children and event.get("status") == "completed":
                self.completed_children.add(call)
                self.identity(call, status="completed")
                if event.get("summary") and event.get("summary_is_report"):
                    self.child_result(call, event["summary"])
            return
        if kind in ("system", "thread.started", "turn.started", "turn.completed"):
            return
        if "reasoning" not in kind:
            self.emit("\n" + text_value({key: value for key, value in event.items() if key != "type"}) + "\n", child_id)


class AgentManager:
    def __init__(self, root, store, transport, topics, common_topic):
        self.root, self.store, self.transport = Path(root), store, transport
        self.topics, self.common_topic = topics, common_topic
        self.paths = executables(self.root)
        self.default_workspace = self.root / "work"
        self.default_workspace.mkdir(exist_ok=True)
        self.tasks_dir = self.root / "tasks"
        self.tasks_dir.mkdir(exist_ok=True)
        self.stop_event = threading.Event()
        self.cancel = threading.Event()
        self.process = None
        self.active = None
        self.lock = threading.RLock()
        self.child_topics = {}
        self.pause_requested = False

    def start(self):
        for row in self.store.rows("SELECT id,topic FROM jobs WHERE status='running'"):
            self.store.finish(row["id"], "interrupted", error="Контроллер был перезапущен")
            self.say(f"Задача {row['id']} прервана перезапуском. Повтори запрос, если нужно.", row["topic"])
        threading.Thread(target=self._worker, daemon=True, name="agent-jobs").start()

    def say(self, text, topic=None, buttons=None):
        if buttons:
            self.transport.emit("terminal", topic or self.common_topic, text, reply_markup=buttons)
        else:
            self.transport.emit("terminal", topic or self.common_topic, text)

    @staticmethod
    def buttons(job_id):
        return {"inline_keyboard": [
            [{"text": "⏸ Пауза", "callback_data": f"relay:pause:{job_id}"},
             {"text": "⏹ Стоп", "callback_data": f"relay:stop:{job_id}"}],
            [{"text": "▶ Продолжить", "callback_data": f"relay:continue:{job_id}"},
             {"text": "↻ Повторить", "callback_data": f"relay:retry:{job_id}"}],
            [{"text": "Изменения", "callback_data": f"relay:changes:{job_id}"},
             {"text": "Модели и лимиты", "callback_data": f"relay:limits:{job_id}"}],
        ]}

    def workspace(self):
        return Path(self.store.get("workspace", str(self.default_workspace)))

    def submit(self, agent, prompt, topic, source, workspace=None, metadata=None):
        if agent == "auto":
            agent = choose_agent(prompt)
        if agent not in (*NAMES, "team"):
            raise ValueError("Неизвестный агент")
        job_id = uuid.uuid5(uuid.NAMESPACE_URL, str(source)).hex[:10]
        job = dict(id=job_id, source=str(source), agent=agent, prompt=prompt,
                   workspace=str(workspace or self.workspace()), topic=int(topic or self.common_topic), metadata=metadata or {})
        if self.store.new_job(job):
            self.store.history(job["workspace"], "user", prompt)
            self.say(f"Задача {job_id} → {'команда' if agent == 'team' else NAMES[agent]}. В очереди.", job["topic"], self.buttons(job_id))
        return job_id

    def status(self):
        from datetime import datetime
        rows = self.store.rows("SELECT id,agent,status,phase,available_at,metadata FROM jobs ORDER BY created DESC LIMIT 8")
        status_ru = {"queued": "в очереди", "waiting": "ждёт", "running": "выполняется",
                     "completed": "завершена", "failed": "ошибка", "cancelled": "отменена", "paused": "приостановлена"}
        phase_ru = {"plan": "план", "implement": "реализация", "review": "ревью", "integrate": "интеграция", "answer": "ответ"}
        lines = [f"Папка: {self.workspace()}"]
        for r in rows:
            st = status_ru.get(r['status'], r['status'])
            ph = phase_ru.get(r['phase'], r['phase'])
            line = f"{r['id']} · {r['agent']} · {st} {ph}"
            if r['status'] == 'waiting' and r['available_at']:
                wait_sec = int(r['available_at'] - time.time())
                if wait_sec > 0:
                    meta = json.loads(r['metadata']) if r['metadata'] else {}
                    if meta.get('codex_limit_until'):
                        until = meta['codex_limit_until']
                    else:
                        until = r['available_at']
                    dt = datetime.fromtimestamp(until).strftime("%d.%m %H:%M")
                    line += f" (следующая проверка до {dt})"
            lines.append(line)
        pending = self.store.rows("SELECT count(*) n FROM outbox")[0]["n"]
        lines.append(f"Сообщений в очереди Telegram: {pending}")
        return "\n".join(lines)

    def cancel_job(self, job_id=None, pause=False):
        with self.lock:
            if self.active and (not job_id or self.active["id"] == job_id):
                self.pause_requested = pause
                self.cancel.set()
                return ("Приостанавливаю задачу " if pause else "Останавливаю задачу ") + self.active["id"]
        if job_id:
            changed = self.store.execute("UPDATE jobs SET status=? WHERE id=? AND status IN ('queued','waiting')", ("paused" if pause else "cancelled", job_id))
            return ("Задача приостановлена." if pause else "Задача отменена.") if changed else "Активная задача с таким ID не найдена."
        return "Сейчас нет выполняемой задачи."

    def continue_job(self, job_id):
        rows = self.store.rows("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows or rows[0]["status"] not in ("paused", "cancelled", "interrupted", "waiting", "failed"):
            return "Продолжить можно остановленную задачу."
        meta = json.loads(rows[0].get("metadata") or "{}")
        meta["resume"] = True
        changed = self.store.execute("UPDATE jobs SET status='queued',error='',available_at=0,metadata=? "
                                    "WHERE id=? AND status IN ('paused','cancelled','interrupted','waiting','failed')",
                                    (json.dumps(meta), job_id))
        return f"Задача {job_id} продолжится с сохранённой сессией и завершёнными этапами." if changed else "Состояние задачи уже изменилось."

    def changes(self, job_id):
        rows = self.store.rows("SELECT workspace FROM jobs WHERE id=?", (job_id,))
        if not rows:
            return "Задача не найдена."
        workspace = Path(rows[0]["workspace"])
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            diff = subprocess.run(["git", "diff", "HEAD", "--", ".", ":(exclude)*.env*", ":(exclude)*.session*"],
                                  cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=15, creationflags=flags)
            if diff.returncode:
                return "В этой папке нет Git с исходным коммитом. История действий и результаты сохранены в журнале задачи."
            status = subprocess.run(["git", "status", "--short"], cwd=workspace, capture_output=True,
                                    text=True, encoding="utf-8", timeout=10, creationflags=flags)
            untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=workspace,
                                       capture_output=True, text=True, encoding="utf-8", timeout=10, creationflags=flags)
            import difflib
            added = []
            allowed = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".md", ".txt", ".json", ".toml", ".yaml", ".yml"}
            for name in untracked.stdout.split("\0")[:20]:
                if not name or any(part in name.lower() for part in (".env", ".session", "credential", "token", "auth.json")):
                    continue
                path = (workspace / name).resolve()
                if not path.is_relative_to(workspace.resolve()) or not path.is_file() or path.suffix not in allowed or path.stat().st_size > 100000:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeError, OSError):
                    continue
                added.extend(difflib.unified_diff([], text.splitlines(keepends=True), fromfile="/dev/null", tofile="b/" + name))
            result = status.stdout + diff.stdout + "".join(added)
            return self.transport.redact(result[:20000] or "Изменений нет.")
        except (OSError, subprocess.TimeoutExpired):
            return "Не удалось прочитать изменения Git."

    def _worker(self):
        while not self.stop_event.is_set():
            pending = self.store.pending()
            if not pending:
                self.stop_event.wait(.5)
                continue
            job = pending[0]
            with self.lock:
                if not self.store.execute("UPDATE jobs SET status='running' WHERE id=? AND status IN ('queued','waiting')", (job["id"],)):
                    continue
                self.active, self.cancel = job, threading.Event()
                self.pause_requested = False
            try:
                self._job(job)
            except Exception as exc:
                message = self.transport.redact(str(exc))
                from model_router import NoFreeModel
                if isinstance(exc, NoFreeModel) and not self.cancel.is_set():
                    # Reload latest metadata from DB to avoid overwriting persisted codex_limit_until
                    fresh = self.store.rows("SELECT metadata FROM jobs WHERE id=?", (job["id"],))
                    meta = json.loads(fresh[0]["metadata"]) if fresh and fresh[0]["metadata"] else {}
                    notify = not meta.get("waiting_notified")
                    meta.update(resume=True, waiting_notified=True)
                    self.store.execute("UPDATE jobs SET status='waiting',error=?,available_at=?,metadata=? WHERE id=?",
                                       (message, time.time() + exc.retry_after, json.dumps(meta), job["id"]))
                    if notify:
                        retry_at = datetime.fromtimestamp(time.time() + exc.retry_after).strftime("%d.%m %H:%M")
                        self.say(f"⏳ Задача {job['id']} ожидает доступа.\n{message}\nСледующая проверка: {retry_at}.", job["topic"], self.buttons(job["id"]))
                    continue
                if not self.cancel.is_set():
                    LOG.exception("Job failed")
                status = ("paused" if self.pause_requested else "cancelled") if self.cancel.is_set() else "failed"
                self.store.finish(job["id"], status, error=message)
                self.say(f"Задача {job['id']}: {status}\n{message}", job["topic"])
            finally:
                with self.lock:
                    self.active, self.process = None, None

    def _job(self, job):
        folder = self.tasks_dir / job["id"]
        folder.mkdir(exist_ok=True)
        (folder / "task.txt").write_text(self.transport.redact(job["prompt"]), encoding="utf-8")
        meta = job.get("metadata") or {}
        meta = json.loads(meta) if isinstance(meta, str) else meta
        if meta.get("delegate"):
            result = self._run(job["agent"], job["prompt"], job, folder, "answer")
            self.store.finish(job["id"], "completed", result=result)
            self.say(f"Субагент ответил. Задача {job['id']} завершена.", job["topic"], self.buttons(job["id"]))
            return
        stages = TEAM if job["agent"] == "team" else [(job["agent"], "answer", "Выполни запрос пользователя.")]
        handoffs = []
        result = ""
        for agent, phase, instruction in stages:
            if self.cancel.is_set():
                raise RuntimeError("Отменено пользователем")
            saved = folder / f"{phase}.md"
            if meta.get("resume") and saved.exists():
                result = saved.read_text(encoding="utf-8")
                handoffs.append(f"=== {NAMES[agent]} / {phase} ===\n{result}")
                continue
            self.store.execute("UPDATE jobs SET phase=? WHERE id=?", (phase, job["id"]))
            self.say(f"▶ Задача {job['id']}\n{NAMES[agent]} · {PHASES.get(phase, phase)}", job["topic"])
            context = self.store.context(job["workspace"])
            prompt = (
                "Ты участник команды Codex / Claude Code / Hermes. Отвечай по-русски.\n"
                f"Общая рабочая папка: {job['workspace']}\n"
                "Не читай секреты или настройки Telegram-моста в родительской папке. "
                "Не меняй чужие незавершённые изменения. Не утверждай, что проверки выполнены, если их не запускал.\n"
                f"Роль на этом этапе: {instruction}\n\n"
                f"Исходная задача пользователя:\n{job['prompt']}\n\n"
                f"Общий журнал последних сообщений:\n{context}\n\n"
                "Результаты предыдущих участников этой задачи:\n" + "\n\n".join(handoffs)[-60000:]
            )
            result = self._run(agent, prompt, job, folder, phase)
            handoffs.append(f"=== {NAMES[agent]} / {phase} ===\n{result}")
            (folder / f"{phase}.md").write_text(result, encoding="utf-8")
            self.store.history(job["workspace"], f"{agent}/{phase}", result)
            if phase != stages[-1][1]:
                self.say(f"{job['id']}: результат {NAMES[agent]} передан следующему участнику.", job["topic"])
        # Validation hook for team jobs with team-duration-v1
        meta = job.get("metadata") or {}
        meta = json.loads(meta) if isinstance(meta, str) else meta
        if job["agent"] == "team" and meta.get("validation") == "team-duration-v1":
            report = team_validation.validate(self.root, job)
            if not report.get("passed"):
                raise RuntimeError("Независимая проверка не прошла. Подробности сохранены в report.json.")
            self.say("🧪 Независимые тесты программы и 24 контрольных сценария прошли.", job["topic"])
        self.store.finish(job["id"], "completed", result=result)
        self.say(f"Задача {job['id']} завершена. Результат выше, журнал сохранён.", job["topic"], self.buttons(job["id"]))

    def _run(self, agent, prompt, job, folder, phase):
        if agent in FREE_AGENTS:
            marker = self.root / ".agent-data" / "free-agent-profiles.json"
            try:
                configured = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                configured = {}
            expected_url = "http://127.0.0.1:31415/v1"
            if (configured.get("base_url") != expected_url
                    or agent not in configured.get("agents", [])):
                raise RuntimeError(
                    f"{NAMES[agent]} пока не подключён к локальному FreeLLMAPI. "
                    "Сначала выполни .\\setup_free_agents.ps1 и проверь CLI в README."
                )
        if agent not in self.paths:
            raise RuntimeError(f"Команда {NAMES.get(agent, agent)} не найдена. Установи CLI и повтори задачу.")
        if agent == "codex":
            # Check global limit first (account-wide)
            global_limit = self.store.get("codex:global_limit_until")
            if global_limit and global_limit > time.time():
                from model_router import NoFreeModel
                retry_after = int(global_limit - time.time()) + 1
                raise NoFreeModel(
                    "Лимит аккаунта Codex. Задача сохранена; платный резерв отключён.",
                    retry_after=retry_after
                )
            # Then check job-specific limit
            metadata = job.get("metadata") or {}
            metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
            limit_until = metadata.get("codex_limit_until")
            if limit_until and limit_until > time.time():
                from model_router import NoFreeModel
                retry_after = int(limit_until - time.time()) + 1
                raise NoFreeModel(
                    "Лимит Codex. Выполненные этапы сохранены.",
                    retry_after=retry_after
                )
        if agent == "hermes" or agent in FREE_AGENTS:
            from provider_runtime import ensure_freellmapi
            ensure_freellmapi(self.root, self.cancel, lambda text: self.say(text, job["topic"]))
        metadata = job.get("metadata") or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
        delegate = metadata.get("delegate")
        from backend_profiles import profile
        current_profile = profile(self.root, agent)
        profile_name = current_profile["name"] if current_profile else "legacy"
        if delegate and delegate.get("profile", profile_name) != profile_name:
            raise RuntimeError("Субагент принадлежит другому CLI-профилю. Его сессия не переносится автоматически.")
        model_file = self.root / "agent-models.json"
        model = json.loads(model_file.read_text(encoding="utf-8")).get(agent) if model_file.exists() else None
        policy_file = self.root / "cost-policy.json"
        if delegate:
            model = delegate.get("model")
        elif policy_file.exists() and json.loads(policy_file.read_text(encoding="utf-8")).get("adaptive_models"):
            from model_router import select
            choice = select(self.root, agent, job.get("prompt") or prompt, phase, self.store)
            model = choice.model
            if model:
                self.store.set("last_selected_model:" + agent,
                               {"model": model, "task_type": choice.task_type, "reason": choice.reason})
                self.say(f"⚙ {NAMES[agent]} · {TASKS.get(choice.task_type, choice.task_type)}\nМодель: {model_name(model)}\nПочему выбрана: {choice.reason}.", job["topic"])
        # A session belongs to the workspace + conversation, never another user's CLI.
        session_key = f"session:{job['workspace']}:{job['topic']}:{agent}"
        if job["agent"] == "team":
            session_key = f"team-session:{job['id']}:{agent}"
        if model:
            session_key += f":model:{model}"
        if agent == "hermes" or agent in FREE_AGENTS:
            session_key += ":via:freellmapi-custom"
        session = self.store.get(session_key)
        if delegate:
            session_key = f"delegate-session:{job['topic']}:{profile_name}"
            session = (delegate.get("parent_session") if agent in ("claude", "codex") else
                       self.store.get(session_key) or delegate.get("session_id") or delegate.get("native_id"))
            if not session:
                raise RuntimeError("ID сохранённой дочерней сессии ещё не получен. Повтори после её запуска.")
            if agent == "claude":
                native = delegate.get("native_id")
                if not native:
                    raise RuntimeError("Claude ещё не сообщил ID субагента.")
                prompt = (f"Deliver the following USER message only to the existing subagent {native} using SendMessage "
                          f"with to={native!r}, summary='Telegram user follow-up', message containing the user text. "
                          "Do not spawn or replace any subagent. Wait for its reply and return that reply. "
                          "Preserve the existing model and free provider route. If continuation fails, report the failure.\nUSER TEXT:\n" + prompt)
            elif agent == "codex":
                target = delegate.get("name")
                if not target or not target.startswith("/root/"):
                    raise RuntimeError("Не сохранён путь дочернего Codex-агента; продолжение требует повторного определения его ID.")
                prompt = (f"Deliver the following USER message only to the existing subagent {target} via collaboration.followup_task "
                          f"with target={target!r}. Do not create, replace, or fork an agent. Wait for the same child's actual reply. "
                          "Return its reply and preserve the current model and authorization. Report failure if continuation fails.\nUSER TEXT:\n" + prompt)
        argv = command(agent, self.paths[agent], session)
        if agent == "aider" and prompt is not None:
            try:
                marker = argv.index("--message-file")
                argv[marker:marker + 2] = ["--message", prompt]
            except ValueError:
                pass
        elif agent == "qwen" and prompt is not None and "--prompt" in argv:
            argv[argv.index("--prompt") + 1] = prompt
        if model:
            cli_model = model
            if agent == "opencode":
                cli_model = "freellmapi/" + model
            elif agent == "aider":
                cli_model = "openai/" + model
            elif agent == "goose":
                argv.extend(["--provider", "freellmapi"])
            argv.extend(["--model", cli_model])
        if agent == "hermes":
            argv.extend(["--in", job["workspace"]])
            if (self.root / "hermes_bridge.py").exists():
                from backend_profiles import hermes_stream_command
                argv = hermes_stream_command(self.paths[agent], argv[1:], self.root / "hermes_bridge.py")
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("BOT_", "TELEGRAM_", "RELAY_"))
                       and k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODEX_THREAD_ID")}
        environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NO_COLOR="1")
        if agent == "opencode":
            # OpenCode's OpenAI-compatible provider prefers the explicit local key;
            # an inherited OPENAI_API_KEY can override its profile auth.
            environment.pop("OPENAI_API_KEY", None)
        if agent == "claude":
            environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "8192"
            environment["MAX_THINKING_TOKENS"] = "1024"
            if delegate:
                environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "4096"
                environment["MAX_THINKING_TOKENS"] = "1024"
        from cost_policy import validate_free_route
        validate_free_route(self.root, agent, model, environment)
        if agent in FREE_AGENTS:
            local_key = environment.get("FREELLMAPI_API_KEY", "")
            if local_key and local_key not in self.transport.secrets:
                self.transport.secrets.append(local_key)
        if agent == "hermes":
            environment["HERMES_HOME"] = str(Path.home() / ".hermes")
        proxy = None
        if agent == "claude" and (self.root / "fcc_compat_proxy.py").exists():
            from fcc_compat_proxy import CompatibilityProxy
            proxy = CompatibilityProxy(reasoning="off" if delegate or phase == "implement" else "bounded",
                                       model_guard=lambda requested: validate_free_route(self.root, "claude", requested, dict(environment)))
            environment["ANTHROPIC_BASE_URL"] = proxy.url
            fcc_python = Path.home() / "AppData/Roaming/uv/tools/free-claude-code/Scripts/python.exe"
            if not fcc_python.is_file():
                proxy.close()
                raise RuntimeError("Не найден интерпретатор установленного FCC для адаптера совместимости.")
            argv = [str(fcc_python), str(self.root / "fcc_bridge.py"), proxy.url, *argv[1:]]
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            process = subprocess.Popen(argv, cwd=job["workspace"], env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=flags, bufsize=0)
        except Exception:
            if proxy:
                proxy.close()
            raise
        with self.lock:
            self.process = process
        buffers, buffer_lock, log_lock = {}, threading.Lock(), threading.Lock()
        last_flush = time.monotonic()
        output_log = folder / f"{phase}.jsonl"

        def child(child_id, name):
            if not child_id:
                return
            key = (job["id"], agent, str(child_id))
            with self.lock:
                if key in self.child_topics:
                    return
                from relay import create_topic
                topic = job["topic"] if delegate else create_topic(self.transport.cfg, agent, f"{NAMES[agent]} · {name}"[:128],
                                     f"child:{job['id']}:{agent}:{child_id}")
                self.child_topics[key] = topic
                note = f"Сообщение пользователя в теме делегированной задачи {child_id} ({name})."
                self.store.execute("INSERT OR REPLACE INTO routes VALUES(?,?,?)", (topic, agent, note))
                if not delegate:
                    self.store.merge(f"delegate:{topic}", {"agent": agent, "child_id": str(child_id), "name": name,
                                     "workspace": job["workspace"], "model": model, "profile": profile_name,
                                     "parent_session": self.store.get(session_key), "status": "running"})
                if not delegate:
                    self.say(f"Создана тема субагента {name}. Напиши здесь, чтобы продолжить его собственный диалог после текущего запуска.", topic)

        def identity(child_id, **details):
            topic = self.child_topics.get((job["id"], agent, str(child_id)))
            if topic:
                self.store.merge(f"delegate:{topic}", {key: value for key, value in details.items() if value is not None})

        def emit(text, child_id):
            topic = self.child_topics.get((job["id"], agent, str(child_id)), job["topic"])
            with buffer_lock:
                buffers[topic] = buffers.get(topic, "") + text

        decoder_lock = threading.RLock()
        from codex_subagents import CodexSubagents
        watcher = None

        def save_session(sid):
            self.store.set(session_key, sid)
            if not delegate:
                for key, topic in tuple(self.child_topics.items()):
                    if key[:2] == (job["id"], agent):
                        self.store.merge(f"delegate:{topic}", {"parent_session": sid})
            if watcher:
                watcher.bind(sid)

        decoder = StreamDecoder(agent, emit, save_session, child, identity)
        if delegate:
            key = delegate["child_id"]
            decoder.register_child(key, delegate.get("name", "subagent"))
            if delegate.get("native_id"):
                decoder.aliases[delegate["native_id"]] = key
        if agent == "codex":
            watcher = CodexSubagents(decoder, time.time() - 2)
            if session:
                watcher.bind(session)
            if delegate:
                watcher.children.add(delegate["session_id"])
                watcher.expected_target = delegate["name"]

        def consume(stream, stderr=False):
            try:
                for binary in iter(stream.readline, b""):
                    raw = self.transport.redact(binary.decode("utf-8", errors="replace").rstrip("\r\n"))
                    with log_lock:
                        with output_log.open("a", encoding="utf-8") as log:
                            log.write(json.dumps({"stderr": stderr, "line": raw}, ensure_ascii=False) + "\n")
                    if stderr:
                        match = re.search(r"session_id:\s*([A-Za-z0-9_-]+)", raw)
                        if match and agent == "hermes":
                            self.store.set(session_key, match.group(1))
                        elif raw.strip():
                            message = warning(raw)
                            if message:
                                emit("\n" + message + "\n", None)
                    else:
                        with decoder_lock:
                            decoder.line(raw)
            except Exception as exc:
                emit(f"\nОшибка чтения потока: {type(exc).__name__}\n", None)
                LOG.exception("Reader failed")

        readers = [threading.Thread(target=consume, args=(process.stdout,), daemon=True),
                   threading.Thread(target=consume, args=(process.stderr, True), daemon=True)]
        for reader in readers:
            reader.start()

        def flush():
            with buffer_lock:
                pending = dict(buffers)
                buffers.clear()
            for topic, text in pending.items():
                self.transport.emit(agent, topic, text)

        started = time.monotonic()
        child_completed = False
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.close()
            while process.poll() is None:
                if agent == "claude" and model and time.monotonic() - decoder.last_activity > 180:
                    from model_router import NoFreeModel
                    self.store.set(f"model_health:{agent}:{model}",
                                   {"blocked_until": time.time() + 600, "reason": "No visible progress for 180 seconds"})
                    raise NoFreeModel("Модель не показала действий или ответа за 180 секунд. Проверю другой бесплатный вариант.", retry_after=20)
                if (delegate and agent == "claude" and decoder.continuation_sent
                        and delegate["child_id"] in decoder.completed_children):
                    child_completed = True
                    self._kill(process)
                    break
                if watcher:
                    with decoder_lock:
                        watcher.poll()
                if self.cancel.is_set() or self.stop_event.is_set() or time.monotonic() - started > 3600:
                    self._kill(process)
                    raise RuntimeError("Запуск остановлен: отмена или превышение времени ожидания.")
                if time.monotonic() - last_flush >= 5:
                    flush()
                    last_flush = time.monotonic()
                time.sleep(.25)
            for reader in readers:
                reader.join(timeout=10)
            if watcher:
                with decoder_lock:
                    watcher.poll()
            flush()
            if self.cancel.is_set():
                raise RuntimeError("Отменено пользователем")
            if not child_completed and (process.returncode != 0 or decoder.error):
                if model:
                    self.store.set(f"model_health:{agent}:{model}",
                                   {"blocked_until": time.time() + 3600, "reason": "CLI run failed"})
                error = f"{NAMES[agent]}: {decoder.error or 'код выхода ' + str(process.returncode)}"
                if re.search(r"429|rate.?limit|quota|usage limit|too many requests|resource.exhausted", error, re.I):
                    from model_router import NoFreeModel
                    if agent == "codex":
                        active_session = self.store.get(session_key) or session
                        from codex_limits import retry_after
                        wait_seconds = retry_after(active_session)
                        limit_until = time.time() + wait_seconds
                        meta = job.get("metadata") or {}
                        meta = json.loads(meta) if isinstance(meta, str) else meta
                        meta["codex_limit_until"] = limit_until
                        self.store.execute(
                            "UPDATE jobs SET metadata=? WHERE id=?",
                            (json.dumps(meta), job["id"])
                        )
                        # Also persist globally so ALL Codex jobs wait
                        self.store.set("codex:global_limit_until", limit_until)
                        raise NoFreeModel(
                            "Лимит Codex. Продолжу с сохранённого этапа, когда доступ восстановится.",
                            retry_after=wait_seconds
                        )
                    raise NoFreeModel(error)
                raise RuntimeError(error)
            if delegate and agent in ("claude", "codex") and (not decoder.continuation_sent or not decoder.child_results):
                raise RuntimeError(f"{NAMES[agent]} не подтвердил адресованное продолжение и ответ прежнего субагента; ответ родителя не засчитан.")
            result = decoder.latest_child_text.get(delegate["child_id"], "") if child_completed else decoder.final or decoder.text
            if not result.strip():
                raise RuntimeError(f"{NAMES[agent]} завершился без ответа. Проверь вывод выше.")
            return self.transport.redact(result)
        finally:
            if process.poll() is None:
                self._kill(process)
            for stream in (process.stdout, process.stderr):
                stream.close()
            if proxy:
                proxy.close()
            with self.lock:
                self.process = None
            flush()

    @staticmethod
    def _kill(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

