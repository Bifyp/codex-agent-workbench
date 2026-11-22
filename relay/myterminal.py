"""Owner-only Telegram command router. Agent messages never trigger new runs."""
import json
import logging
import re
import threading
import time
from pathlib import Path

from telegram_transport import APIError
from agent_manager import NAMES

LOG = logging.getLogger("myterminal")
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
HELP = (
    "/auto задача — автоматически выбрать одного подходящего агента\n"
    "/team задача — Codex план → Claude работа → Hermes ревью → Codex итог\n"
    "/codex задача — запустить Codex\n"
    "/claude задача — запустить fcc-claude\n"
    "/hermes задача — запустить Hermes через FreeLLMAPI\n"
    "/opencode задача — OpenCode через профиль FreeLLMAPI\n"
    "/aider задача — Aider через профиль FreeLLMAPI\n"
    "/goose задача — Goose через профиль FreeLLMAPI\n"
    "/qwen задача — Qwen Code через профиль FreeLLMAPI\n"
    "/pi задача — Pi через профиль FreeLLMAPI\n"
    "Команда без задачи выбирает агента для следующих сообщений.\n"
    "/status — задачи и состояние\n"
    "/models — последние выбранные модели\n"
    "/limits — доступность бесплатных моделей и ограничения\n"
    "/pause ID — остановить с возможностью продолжения\n"
    "/continue ID — продолжить сохранённую задачу\n"
    "/changes ID — показать изменения Git\n"
    "/stop [ID] — остановить задачу\n"
    "/retry ID — повторить неудачную задачу\n"
    "/project [путь] — общая рабочая папка\n"
    "/new — новые сессии в этой теме\n"
    "К задаче можно прикладывать фото, документы, видео и аудио (до 20 МБ).\n"
    "/help — эта справка\n"
    "В Общем чате обычное сообщение по умолчанию запускает команду. "
    "Все участники используют одну папку и передают результаты друг другу."
)


class Controller:
    def __init__(self, store, transport, manager, cfg, common_topic, agent_topics):
        self.store, self.transport, self.manager = store, transport, manager
        self.cfg, self.common_topic, self.agent_topics = cfg, common_topic, agent_topics
        self.owner = int(cfg["owner"])
        self.username = ""
        self.stop_event = threading.Event()
        self.ready = False

    def start(self):
        me = self.transport.call("terminal", "getMe")
        self.username = me["username"].lower()
        webhook = self.transport.call("terminal", "getWebhookInfo")
        if webhook.get("url"):
            raise RuntimeError("У MyTerminal настроен webhook. Сначала выбери один способ получения сообщений.")
        commands = [
            ("auto", "Автовыбор одного агента"), ("team", "Совместная работа всех агентов"), ("codex", "Запустить Codex"),
            ("claude", "Запустить fcc-claude"), ("hermes", "Запустить Hermes"),
            ("opencode", "Запустить OpenCode"), ("aider", "Запустить Aider"),
            ("goose", "Запустить Goose"), ("qwen", "Запустить Qwen Code"),
            ("pi", "Запустить Pi"),
            ("status", "Задачи и состояние"), ("stop", "Остановить задачу"),
            ("models", "Автовыбор моделей и бесплатный режим"),
            ("limits", "Доступность бесплатных моделей и лимиты"),
            ("pause", "Приостановить задачу"), ("continue", "Продолжить задачу"),
            ("changes", "Показать изменения задачи"),
            ("project", "Рабочая папка"), ("new", "Новые сессии"),
            ("retry", "Повторить задачу"), ("help", "Справка"),
        ]
        self.transport.call("terminal", "setMyCommands",
                            {"commands": [{"command": c, "description": d} for c, d in commands]})
        self.ready = True
        threading.Thread(target=self._poll, daemon=True, name="telegram-poll").start()
        threading.Thread(target=self._dispatch, daemon=True, name="telegram-dispatch").start()

    def _poll(self):
        while not self.stop_event.is_set():
            try:
                updates = self.transport.call("terminal", "getUpdates",
                    {"offset": self.store.get("update_offset", 0), "timeout": 25,
                     "allowed_updates": ["message", "callback_query"]}, timeout=35)
                for update in updates:
                    self.store.accept_update(update)
            except APIError as exc:
                LOG.warning("Polling deferred: %s", exc)
                self.stop_event.wait(max(3, min(exc.retry_after or 5, 60)))

    def _dispatch(self):
        while not self.stop_event.is_set():
            rows = self.store.rows("SELECT * FROM inbox WHERE done=0 ORDER BY id LIMIT 1")
            if not rows:
                self.stop_event.wait(.3)
                continue
            row = rows[0]
            try:
                self.handle(json.loads(row["payload"]))
                self.store.execute("UPDATE inbox SET done=1 WHERE id=?", (row["id"],))
            except Exception:
                LOG.exception("Command handling failed")
                self.stop_event.wait(3)

    def handle(self, update):
        if update.get("callback_query"):
            self.callback(update)
            return
        message = update.get("message", {})
        sender = message.get("from", {})
        if (sender.get("is_bot") or sender.get("id") != self.owner
                or str(message.get("chat", {}).get("id")) != str(self.cfg["chat_id"])):
            return
        text = (message.get("text") or message.get("caption") or "").strip()
        attachment = self._attachment(message)
        if not text and not attachment:
            return
        topic = int(message.get("message_thread_id") or self.common_topic)
        selected_key = f"selected:{topic}"
        source = f"telegram:{update['update_id']}"
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            head, argument = parts[0], parts[1] if len(parts) > 1 else ""
            command, _, mention = head[1:].partition("@")
            if mention and mention.lower() != self.username:
                return
            command, argument = command.lower(), argument.strip()
            if command in ("help", "start"):
                self.manager.say(HELP, topic)
            elif command in (*NAMES, "team", "all", "auto"):
                agent = "team" if command == "all" else command
                self.store.set(selected_key, agent)
                if argument or attachment:
                    prompt = self._with_attachment(message, argument, topic, source) if attachment else argument
                    if prompt is not None:
                        self.manager.submit(agent, prompt, topic, source)
                else:
                    self.manager.say(f"Выбран {'совместный режим' if agent == 'team' else NAMES[agent]}. Напиши задачу.", topic)
            elif command == "status":
                self.manager.say(self.manager.status(), topic)
            elif command in ("limits", "changes", "pause", "continue"):
                if command == "limits":
                    from model_router import availability_report
                    answer = availability_report(self.manager.root, self.store)
                elif command == "changes":
                    answer = self.manager.changes(argument)
                elif command == "pause":
                    answer = self.manager.cancel_job(argument or None, pause=True)
                else:
                    answer = self.manager.continue_job(argument)
                self.manager.say(answer, topic)
            elif command == "models":
                if argument == "reset":
                    self.store.execute("DELETE FROM kv WHERE key LIKE 'model_health:%'")
                    self.manager.say("Ограничения совместимости сброшены. Следующий запуск заново выберет модель; бесплатный режим сохранён.", topic)
                    return
                lines = ["Автовыбор включён. Только бесплатные маршруты; платный fallback заблокирован."]
                for name, label in NAMES.items():
                    if name == "codex":
                        lines.append("Codex: модель из настроек Codex CLI; используется авторизация ChatGPT")
                        continue
                    choice = self.store.get("last_selected_model:" + name)
                    lines.append(f"{label}: {choice['model']} ({choice['task_type']})" if choice
                                 else f"{label}: модель будет выбрана при запуске задачи")
                self.manager.say("\n".join(lines), topic)
            elif command == "project":
                if argument:
                    if self.manager.active or self.store.pending():
                        self.manager.say("Сначала заверши или останови задачи в очереди.", topic)
                        return
                    path = Path(argument.strip('"')).expanduser().resolve()
                    if not path.is_dir():
                        self.manager.say("Папка не найдена.", topic)
                        return
                    self.store.set("workspace", str(path))
                self.manager.say(f"Общая папка: {self.manager.workspace()}", topic)
            elif command == "stop":
                self.manager.say(self.manager.cancel_job(argument or None), topic)
            elif command == "new":
                for agent in NAMES:
                    key = f"session:{self.manager.workspace()}:{topic}:{agent}"
                    self.store.execute("DELETE FROM kv WHERE key=? OR substr(key,1,?)=?", (key, len(key)+1, key+":"))
                self.manager.say("Новые сессии для этой темы. Общие файлы и журнал сохранены.", topic)
            elif command == "retry":
                rows = self.store.rows("SELECT * FROM jobs WHERE id=?", (argument,))
                if rows and rows[0]["status"] in ("failed", "interrupted", "cancelled"):
                    old = rows[0]
                    self.manager.submit(old["agent"], old["prompt"], topic, source,
                                        workspace=old["workspace"], metadata=json.loads(old.get("metadata") or "{}"))
                else:
                    self.manager.say("Укажи ID остановленной или неудачной задачи из /status.", topic)
            else:
                self.manager.say("Неизвестная команда. /help — список команд.", topic)
            return
        delegate = self.store.get(f"delegate:{topic}")
        if delegate:
            if not delegate.get("session_id") and not (delegate.get("native_id") and delegate.get("parent_session")):
                self.manager.say("Субагент ещё запускается; дождись его ID и отправь сообщение снова.", topic)
                return
            prompt = self._with_attachment(message, text, topic, source, delegate["workspace"]) if attachment else text
            if prompt is not None:
                self.manager.submit(delegate["agent"], prompt, topic, source, workspace=delegate["workspace"],
                                    metadata={"delegate": delegate})
            return
        route = self.store.rows("SELECT * FROM routes WHERE topic=?", (topic,))
        if route:
            self.manager.say("Для этой темы не сохранён ID дочерней сессии. Автопродолжение работает для новых субагентов, запущенных через MyTerminal. Перезапусти делегирование там.", topic)
            return
        else:
            default_agent = next((agent for agent, tid in self.agent_topics.items()
                                  if tid == topic and agent in NAMES), "auto")
        agent = self.store.get(selected_key, default_agent)
        prompt = self._with_attachment(message, text, topic, source) if attachment else text
        if prompt is not None:
            self.manager.submit(agent, prompt, topic, source)

    @staticmethod
    def _attachment(message):
        """Return Telegram's largest photo or the first supported file attachment."""
        if message.get("photo"):
            photo = max(message["photo"], key=lambda item: int(item.get("file_size") or 0))
            return {"file_id": photo["file_id"], "name": "photo.jpg", "mime_type": "image/jpeg",
                    "size": int(photo.get("file_size") or 0), "kind": "фото"}
        for field, kind, fallback in (
            ("document", "файл", "telegram-file"), ("video", "видео", "video.mp4"),
            ("animation", "анимация", "animation.mp4"), ("audio", "аудио", "audio.bin"),
            ("voice", "голосовое сообщение", "voice.ogg"),
            ("video_note", "видеосообщение", "video-note.mp4"),
            ("sticker", "стикер", "sticker.webp"),
        ):
            item = message.get(field)
            if item and item.get("file_id"):
                return {"file_id": item["file_id"], "name": item.get("file_name") or fallback,
                        "mime_type": item.get("mime_type") or "application/octet-stream",
                        "size": int(item.get("file_size") or 0), "kind": kind}
        return None

    def _with_attachment(self, message, text, topic, source, workspace=None):
        attachment = self._attachment(message)
        if not attachment:
            return text
        if attachment["size"] > MAX_ATTACHMENT_BYTES:
            self.manager.say("Файл превышает лимит 20 МБ Telegram Bot API; задача не запущена.", topic)
            return None
        try:
            remote_name, content, actual_size = self.transport.download_file(
                attachment["file_id"], max_bytes=MAX_ATTACHMENT_BYTES)
        except ValueError as exc:
            self.manager.say(str(exc), topic)
            return None
        original = attachment["name"] or remote_name
        basename = re.split(r"[/\\]", str(original))[-1]
        basename = re.sub(r"[^\w.() -]", "_", basename, flags=re.UNICODE).strip(" .") or "telegram-file"
        if basename in (".", ".."):
            basename = "telegram-file"
        basename = basename[:120]
        if Path(basename).stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            basename = "file_" + basename
        base = Path(workspace) if workspace else self.manager.workspace()
        folder = base / ".telegram-attachments" / str(source.rsplit(":", 1)[-1])
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / basename
        target.write_bytes(content)
        instruction = (
            f"Вложение из Telegram: {attachment['kind']}; MIME: {attachment['mime_type']}; "
            f"размер: {actual_size} байт. Файл: {target.resolve()}\n"
            "Изучи вложение как входные данные задачи. Не запускай и не исполняй его."
        )
        return (text + "\n\n" if text else "") + instruction

    def callback(self, update):
        query = update["callback_query"]
        sender, message = query.get("from", {}), query.get("message", {})
        if (sender.get("is_bot") or sender.get("id") != self.owner
                or str(message.get("chat", {}).get("id")) != str(self.cfg["chat_id"])):
            return
        parts = query.get("data", "").split(":")
        if len(parts) != 3 or parts[0] != "relay" or parts[1] not in ("pause", "stop", "continue", "retry", "changes", "limits"):
            return
        action, job_id = parts[1:]
        rows = self.store.rows("SELECT * FROM jobs WHERE id=?", (job_id,))
        topic = int(message.get("message_thread_id") or self.common_topic)
        if not rows or rows[0]["topic"] != topic:
            return
        try:
            self.transport.call("terminal", "answerCallbackQuery", {"callback_query_id": query["id"], "text": "Принято"})
        except APIError:
            pass
        old = rows[0]
        if action in ("pause", "stop"):
            answer = self.manager.cancel_job(job_id, pause=action == "pause")
        elif action == "continue":
            answer = self.manager.continue_job(job_id)
        elif action == "retry":
            if old["status"] not in ("failed", "interrupted", "cancelled", "paused", "completed"):
                answer = "Сначала заверши или останови задачу."
            else:
                meta = json.loads(old.get("metadata") or "{}")
                meta.pop("resume", None)
                self.manager.submit(old["agent"], old["prompt"], topic, "callback:" + query["id"],
                                    workspace=old["workspace"], metadata=meta)
                return
        elif action == "changes":
            answer = self.manager.changes(job_id)
        else:
            from model_router import availability_report
            answer = availability_report(self.manager.root, self.store)
        self.manager.say(answer, topic)

