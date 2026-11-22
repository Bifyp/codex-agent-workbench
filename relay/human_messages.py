"""Human-readable Telegram views; native JSON remains in local task journals."""
import html
import json
import re

LABELS = {"stdout": "Вывод", "stderr": "Ошибки", "output": "Результат", "result": "Результат",
          "error": "Ошибка", "message": "Сообщение", "text": "Текст", "content": "Содержимое",
          "summary": "Итог", "path": "Файл", "file_path": "Файл", "command": "Команда",
          "exit_code": "Код завершения", "status": "Состояние", "success": "Успешно"}
INTERNAL = {"tool_use_id", "tool_call_id", "session_id", "uuid", "timestamp", "api_message_id",
            "request_id", "cache_control", "usage", "parent_tool_use_id", "agentId"}


def text_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        if "usage limit" in value.lower():
            return "⏳ Лимит использования Codex. Продолжение сохранено; платный резерв отключён."
        # Claude's hand-back envelope is not part of the child's answer.
        match = re.search(r"(?:The report follows:|report follows:)\s*\n(.*?)(?:\nagentId:|\n<usage>|$)", value, re.S)
        if match:
            return "\n".join(line[2:] if line.startswith("  ") else line for line in match.group(1).splitlines()).strip()
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
        if isinstance(decoded, list) and decoded and all(isinstance(item, dict) and item.get("type") == "text" for item in decoded):
            return text_value(decoded)
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text_value(item) for item in value)))
    if isinstance(value, dict):
        if value.get("type") == "text":
            return text_value(value.get("text", ""))
        if value.get("type") in ("thinking", "redacted_thinking", "tool_reference"):
            return ""
        lines = []
        for key, item in value.items():
            if key in INTERNAL or item is None:
                continue
            rendered = text_value(item)
            if rendered:
                lines.append(f"{LABELS.get(key, key.replace('_', ' '))}: {rendered}")
        return "\n".join(lines)
    return str(value)


def code(value):
    return "```\n" + str(value).rstrip() + "\n```" if value else ""


def tool_start(name, arguments=None):
    args = arguments if isinstance(arguments, dict) else {}
    lower = (name or "действие").lower()
    path = args.get("file_path") or args.get("path") or args.get("filename") or ""
    if lower in ("bash", "powershell", "exec_command", "terminal", "shell"):
        command = args.get("command") or args.get("cmd") or text_value(arguments)
        return "🛠 Выполняю команду\n\n" + code(command)
    if lower in ("read", "read_file"):
        return "📖 Читаю файл\n" + str(path)
    if lower in ("write", "write_file"):
        return "📝 Записываю файл\n" + str(path) + "\n\n" + code(args.get("content") or args.get("text"))
    if lower in ("edit", "edit_file", "apply_patch"):
        details = code(args.get("patch") or args.get("input"))
        if not details:
            details = "Было:\n" + code(args.get("old_string") or args.get("old_text")) + "\nСтало:\n" + code(args.get("new_string") or args.get("new_text"))
        return "✏️ Изменяю файл\n" + str(path) + "\n\n" + details
    if lower in ("agent", "task", "spawn_agent", "delegate_task"):
        tasks = args.get("tasks") or [args]
        goals = [item.get("description") or item.get("goal") or item.get("task_name") or "Задача помощнику" for item in tasks]
        return "👥 Передаю задачу субагенту\n" + "\n".join("• " + str(goal) for goal in goals)
    if lower == "sendmessage":
        return "💬 Передаю ваше сообщение прежнему субагенту"
    if lower in ("glob", "grep", "rg", "web_search", "websearch", "search"):
        return "🔎 Ищу\n" + str(args.get("pattern") or args.get("query") or text_value(args))
    return "🛠 " + str(name or "Действие") + ("\n" + text_value(args) if args else "")


def tool_result(name, output, error=False, exit_code=None):
    if isinstance(output, str) and str(name).lower() in ('terminal', 'read_file', 'write_file', 'edit_file'):
        try:
            decoded = json.loads(output)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            output = decoded
    heading = ("❌ Ошибка" if error or exit_code not in (None, 0) else "✅ Готово")
    if name:
        heading += ": " + str(name)
    result = text_value(output).strip()
    if exit_code is not None:
        heading += f" · код {exit_code}"
    return heading + ("\n\n" + result if result else "")


def codex_item(item, completed=True):
    kind = item.get("type", "")
    if kind == "command_execution" or kind == "CommandExecution":
        if not completed:
            return tool_start("terminal", {"command": item.get("command", "")})
        return tool_result("Команда", item.get("aggregated_output") or item.get("output") or "", exit_code=item.get("exit_code"))
    if kind in ("file_change", "FileChange"):
        changes = item.get("changes", [])
        if isinstance(changes, dict):
            changes = [{"path": path, **(value if isinstance(value, dict) else {})} for path, value in changes.items()]
        return "✏️ Файлы изменены\n" + "\n".join("• " + str(change.get("path", "")) +
                 ("\n" + code(change["diff"]) if change.get("diff") else "") for change in changes)
    if kind in ("collab_tool_call", "CollabAgentToolCall"):
        name = str(item.get("tool", ""))
        if "wait" in name:
            return "⏳ Жду ответа субагента" if not completed else "✅ Получен ответ субагента"
        return "👥 Субагент: " + {"spawn_agent": "запуск", "send_input": "передача сообщения", "close_agent": "завершение"}.get(name, name)
    if kind in ("mcp_tool_call", "McpToolCall"):
        return tool_result(item.get("tool"), item.get("result") or item.get("error"), bool(item.get("error"))) if completed else tool_start(item.get("tool"), item.get("arguments"))
    if kind in ("web_search", "WebSearch"):
        return "🔎 Поиск: " + str(item.get("query") or text_value(item.get("action", {})))
    if kind == "SubAgentActivity":
        return "👥 " + str(item.get("agent_path") or "Субагент") + " · " + str(item.get("kind") or "работает")
    return text_value({key: value for key, value in item.items() if key not in ("id", "type")})


def warning(raw):
    if any(phrase in raw for phrase in ("ignoring interface.icon_", "Shell snapshot not supported", "claude-code:unrecognized_model")):
        return ""
    clean = re.sub(r"^\d{4}-\d{2}-\d{2}T\S+\s+(?:WARN|ERROR|INFO)\s+\S+:\s*", "", raw)
    return "⚠️ " + clean


def render_html(text):
    """Safe Telegram HTML; markup never changes the underlying stored text."""
    output, buffer, fenced = [], [], False
    def flush():
        if buffer:
            output.append("<pre>" + html.escape("\n".join(buffer), quote=False) + "</pre>")
            buffer.clear()
    for line in str(text).split("\n"):
        if line.strip().startswith("```"):
            if fenced:
                flush()
            fenced = not fenced
            continue
        if fenced:
            buffer.append(line)
        else:
            heading = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading:
                output.append("<b>" + html.escape(heading.group(1).replace("`", ""), quote=False) + "</b>")
            else:
                parts = []
                for part in re.split(r"(`[^`]+`)", line):
                    if part.startswith("`") and part.endswith("`") and len(part) >= 2:
                        parts.append("<code>" + html.escape(part[1:-1], quote=False) + "</code>")
                    else:
                        parts.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(part, quote=False)))
                output.append("".join(parts))
    flush()
    return "\n".join(output) or " "

