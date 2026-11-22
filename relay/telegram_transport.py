"""Bot API client and durable, rate-limited message delivery."""
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

LOG = logging.getLogger("telegram")


class APIError(RuntimeError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class Transport:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.stop_event = threading.Event()
        self._api_locks = {agent: threading.Lock() for agent in cfg["tokens"]}
        self.secrets = [cfg["secret"], *cfg["tokens"].values()]

    def redact(self, text):
        for value in self.secrets:
            if value:
                text = str(text).replace(value, "[REDACTED]")
        return text

    def call(self, agent, method, data=None, timeout=40, files=None):
        # Any configured agent uses its own bot; internal subagent ids fall back to MyTerminal.
        agent = agent if agent in self.cfg["tokens"] else "terminal"
        if files:
            boundary = 'relay-' + uuid.uuid4().hex
            parts = []
            for key, value in (data or {}).items():
                value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n' + value + '\r\n').encode('utf-8'))
            for key, (filename, content) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'.encode('utf-8') + content + b'\r\n')
            parts.append(f'--{boundary}--\r\n'.encode())
            body, content_type = b''.join(parts), 'multipart/form-data; boundary=' + boundary
        else:
            body, content_type = json.dumps(data or {}, ensure_ascii=False).encode('utf-8'), 'application/json'
        request = Request(
            f"https://api.telegram.org/bot{self.cfg['tokens'][agent]}/{method}",
            data=body,
            headers={"Content-Type": content_type},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            try:
                payload = json.load(exc)
            except Exception:
                payload = {"description": f"HTTP {exc.code}"}
            raise APIError(
                self.redact(payload.get("description", "Telegram request failed")),
                payload.get("parameters", {}).get("retry_after", 0),
            ) from None
        except (URLError, TimeoutError, OSError):
            raise APIError(f"Telegram {method}: network unavailable") from None
        except ValueError:
            raise APIError(f"Telegram {method}: invalid server response") from None
        if not payload.get("ok"):
            raise APIError(self.redact(payload.get("description", "Telegram request failed")),
                           payload.get("parameters", {}).get("retry_after", 0))
        return payload["result"]

    def download_file(self, file_id, max_bytes=20 * 1024 * 1024):
        """Download an owner-provided Telegram file without exposing the bot token."""
        from urllib.error import HTTPError, URLError

        info = self.call("terminal", "getFile", {"file_id": file_id})
        size = int(info.get("file_size") or 0)
        if size > max_bytes:
            raise ValueError("Файл больше лимита Telegram Bot API (20 МБ).")
        remote_path = str(info.get("file_path") or "")
        parts = remote_path.replace("\\", "/").split("/")
        if not remote_path or remote_path.startswith(("/", "\\")) or ".." in parts:
            raise APIError("Telegram вернул некорректный путь файла")
        token = self.cfg["tokens"]["terminal"]
        request = Request(f"https://api.telegram.org/file/bot{token}/{quote(remote_path, safe='/')}")
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read(max_bytes + 1)
        except HTTPError as exc:
            raise APIError(f"Telegram file download failed (HTTP {exc.code})") from None
        except (URLError, TimeoutError, OSError):
            raise APIError("Telegram file download: network unavailable") from None
        if len(content) > max_bytes:
            raise ValueError("Файл больше лимита Telegram Bot API (20 МБ).")
        name = Path(remote_path).name or "telegram-file"
        return name, content, size or len(content)

    def emit(self, agent, topic, text, reply_markup=None):
        from human_messages import render_html
        from relay_store import chunks
        text = self.redact(text)
        if len(text.encode('utf-16-le')) // 2 > 12000:
            pieces = list(chunks(text, limit=1000000))
            for index, piece in enumerate(pieces):
                self._enqueue_document(agent, topic, piece, reply_markup if index == len(pieces)-1 else None)
            return
        pieces = list(chunks(text))
        for index, piece in enumerate(pieces):
            payload = {"parse_mode": "HTML", "text": render_html(piece)}
            if reply_markup and index == len(pieces) - 1:
                payload["reply_markup"] = reply_markup
            self.store.enqueue(agent, topic, piece, payload)

    def _enqueue_document(self, agent, topic, text, reply_markup=None):
        folder = self.store.path.parent / 'message-attachments'
        folder.mkdir(exist_ok=True)
        path = folder / f'{agent}-output-{uuid.uuid4().hex[:10]}.txt'
        path.write_bytes(text.encode('utf-8'))
        first = next((line.strip() for line in text.splitlines() if line.strip()), 'Подробности')
        caption = f'📎 Подробный вывод\n{first[:220]}\n\n{len(text.splitlines())} строк. Весь текст сохранён в приложенном файле.'
        payload = {'_document': str(path.resolve())}
        if reply_markup:
            payload['reply_markup'] = reply_markup
        self.store.enqueue(agent, topic, caption, payload)

    def archive_backlog(self):
        """Compact the old verbose queue once on startup, retaining every character."""
        marker = None
        preview = self.store.path.parent / 'format-preview.json'
        if preview.exists():
            marker = json.loads(preview.read_text(encoding='utf-8')).get('batch')
        groups = {}
        for row in self.store.rows('SELECT * FROM outbox ORDER BY id'):
            payload = json.loads(row.get('payload') or '{}')
            if (row['agent'] == 'terminal' or payload.get('_document') or payload.get('reply_markup')
                    or (marker and marker in row['text'])):
                continue
            groups.setdefault((row['agent'], row['topic']), []).append(row)
        for (agent, topic), rows in groups.items():
            text = ''.join(row['text'] for row in rows)
            if len(text) < 16000 or len(text.encode('utf-8')) > 3000000:
                continue
            # A fresh outbox row owns a durable file before old text rows are removed.
            self._enqueue_document(agent, topic, text)
            with self.store.connect() as db:
                db.executemany('DELETE FROM outbox WHERE id=?', [(row['id'],) for row in rows])

    def start(self):
        self.archive_backlog()
        threading.Thread(target=self._deliver, name="telegram-outbox", daemon=True).start()

    def _deliver(self):
        while not self.stop_event.is_set():
            rows = self.store.rows("SELECT * FROM outbox WHERE due<=? ORDER BY id LIMIT 1", (time.time(),))
            if not rows:
                self.stop_event.wait(.25)
                continue
            row = rows[0]
            data = {"chat_id": self.cfg["chat_id"], "text": row["text"],
                    "link_preview_options": {"is_disabled": True}}
            data.update(json.loads(row.get("payload") or "{}"))
            document = data.pop('_document', None)
            if row["topic"]:
                data["message_thread_id"] = row["topic"]
            try:
                if document:
                    path = Path(document).resolve()
                    if not path.is_relative_to((self.store.path.parent / 'message-attachments').resolve()):
                        raise APIError('Attachment path is outside the relay output directory')
                    data['caption'] = data.pop('text')
                    data.pop('link_preview_options', None)
                    try:
                        content = path.read_bytes()
                    except OSError:
                        raise APIError('Файл полного вывода временно недоступен') from None
                    self.call(row['agent'], 'sendDocument', data, files={'document': (path.name, content)})
                else:
                    self.call(row["agent"], "sendMessage", data)
                self.store.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
                self.stop_event.wait(3.2)  # aggregate forum rate: below 20 messages/minute
            except APIError as exc:
                if "parse" in str(exc).lower() and "entit" in str(exc).lower() and "parse_mode" in data:
                    payload = json.loads(row.get("payload") or "{}")
                    payload.pop("parse_mode", None)
                    payload.pop("text", None)
                    self.store.execute("UPDATE outbox SET payload=?,due=0 WHERE id=?", (json.dumps(payload), row["id"]))
                    continue
                wait = max(exc.retry_after, min(120, 2 ** min(row["attempts"] + 1, 7)))
                self.store.execute("UPDATE outbox SET attempts=attempts+1,due=? WHERE id=?",
                                   (time.time() + wait, row["id"]))
                LOG.warning("Delivery deferred: %s", exc)
                self.stop_event.wait(min(wait, 5))

