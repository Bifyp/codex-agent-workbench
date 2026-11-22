"""One-time local BotFather setup using your own Telegram API application."""
from __future__ import annotations

import asyncio
import argparse
import getpass
import os
import re
import secrets
from pathlib import Path

from telethon import TelegramClient, errors
from telegram_login import login
from telegram_cooldown import before_send, defer

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
API_ENV = ROOT / ".telegram-api.env"
BOTFATHER_ID = 93372553
BOTS = {
    "codex": "Codex Relay",
    "claude": "Claude Code Relay",
    "hermes": "Hermes Relay",
    "terminal": "My Terminal Relay",
    "opencode": "OpenCode Relay",
    "aider": "Aider Relay",
    "goose": "Goose Relay",
    "qwen": "Qwen Code Relay",
    "pi": "Pi Relay",
}
TOKEN = re.compile(r"(?<![\w-])\d{5,}:[A-Za-z0-9_-]{30,}(?![\w-])")


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def save_env(updates: dict[str, str]) -> None:
    """Update only changed keys; retain comments and unknown settings."""
    lines = ENV.read_text(encoding="utf-8-sig").splitlines() if ENV.exists() else []
    remaining = dict(updates)
    for index, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                lines[index] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    temp = ENV.with_name(".env.tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temp, ENV)


def checked_response(text: str, expected: tuple[str, ...]) -> None:
    lowered = text.lower()
    if not any(word in lowered for word in expected):
        raise RuntimeError(
            "Неожиданный ответ BotFather. Открой его чат и проверь состояние. "
            "Скрипт остановлен; ответы и токены не выводятся в журнал."
        )


async def exchange(conv, message: str) -> str:
    await before_send()
    try:
        sent = await conv.send_message(message)
    except errors.FloodWaitError as exc:
        defer(max(20, exc.seconds))
        raise
    response = await conv.get_response(sent)
    text = response.raw_text or ""
    if "too many attempts" in text.lower():
        delay = re.search(r"(\d+)\s+seconds", text, re.I)
        seconds = int(delay.group(1)) if delay else 300
        defer(max(20, seconds))
        raise RuntimeError(f"BotFather просит подождать {seconds} секунд. Пауза сохранена; повтори запуск позже.")
    return text


async def create_bot(client, father, agent: str, title: str) -> str:
    async with client.conversation(father, timeout=60, max_messages=30) as conv:
        await exchange(conv, "/cancel")
        response = await exchange(conv, "/newbot")
        checked_response(response, ("name", "назван", "имя"))
        response = await exchange(conv, title)
        checked_response(response, ("username", "пользовател"))
        for _ in range(5):
            username = input(
                f"Username для {title} (5–32 символа, окончание bot; Enter = автоматически): "
            ).strip().lstrip("@")
            if not username:
                username = f"relay_{agent}_{secrets.token_hex(5)}_bot"
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,28}bot", username, re.I):
                print("Некорректный username. Попробуй ещё раз.")
                continue
            response = await exchange(conv, username)
            token = TOKEN.search(response)
            if token:
                # Persist immediately: a later interruption must not lose this token.
                save_env({f"BOT_{agent.upper()}_TOKEN": token.group(),
                          f"BOT_{agent.upper()}_USERNAME": username})
                print(f"Создан @{username}. Токен сохранён в .env.")
                return username
            if any(word in response.lower() for word in ("taken", "invalid", "занят", "недопуст")):
                print("BotFather отклонил username. Выбери другой.")
                continue
            checked_response(response, ())
        raise RuntimeError("Username не выбран. Повтори настройку позже.")


async def select_bot(conv, command: str, username: str):
    """Select an existing bot from BotFather's inline picker without logging its data."""
    await before_send()
    sent = await conv.send_message(command)
    menu = await conv.get_response(sent)
    target = username.lstrip("@").casefold()
    choices = [button.text for row in (menu.buttons or []) for button in row
               if target in (button.text or "").casefold()]
    if not choices:
        raise RuntimeError(f"BotFather не показал {username} в меню. Проверь владельца бота.")
    await before_send()
    await menu.click(text=choices[0])
    return await conv.get_response(timeout=60)


async def adopt_bot(client, father, agent: str, title: str, username: str) -> str:
    """Rename an owned bot, issue its API token, and save it without displaying it."""
    async with client.conversation(father, timeout=60, max_messages=30) as conv:
        await select_bot(conv, "/setname", username)
        response = await exchange(conv, title)
        if not response:
            raise RuntimeError(f"BotFather не подтвердил смену имени для {username}.")
        response = await select_bot(conv, "/token", username)
        token = TOKEN.search(response.raw_text or "")
        if not token:
            raise RuntimeError(f"BotFather не выдал токен для {username}; ответ не сохранён.")
        save_env({f"BOT_{agent.upper()}_TOKEN": token.group(),
                  f"BOT_{agent.upper()}_USERNAME": username.lstrip("@")})
        print(f"Подключён @{username} как {title}; токен сохранён в .env.")
        return username


async def main(check_only=False) -> None:
    credentials = {**read_env(API_ENV), **os.environ}
    api_id = credentials.get("TELEGRAM_API_ID") or input("API ID с my.telegram.org/apps: ").strip()
    api_hash = credentials.get("TELEGRAM_API_HASH") or getpass.getpass("API hash (скрытый ввод): ").strip()
    if not api_id.isdigit() or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise RuntimeError("Проверь api_id и api_hash.")
    # Save app credentials locally so the persistent session is usable on restart.
    api_settings = read_env(API_ENV)
    api_settings.update(TELEGRAM_API_ID=api_id, TELEGRAM_API_HASH=api_hash)
    api_temp = API_ENV.with_name(".telegram-api.env.tmp")
    api_temp.write_text("\n".join(f"{k}={v}" for k, v in api_settings.items()) + "\n", encoding="utf-8")
    os.replace(api_temp, API_ENV)
    existing = read_env(ENV)
    pending = [agent for agent in BOTS if not existing.get(f"BOT_{agent.upper()}_TOKEN")]
    if not pending and not check_only:
        print("Все токены уже заданы в .env. Новые боты не создаются.")
        return
    if not check_only:
        print("Будут подключены боты: " + ", ".join(BOTS[a] for a in pending))
    print("Телефон, код входа и пароль вводятся здесь локально.")
    print("Сессия сохраняется в telegram-user.session для повторных подключений.")
    client = TelegramClient(
        str(ROOT / "telegram-user.session"), int(api_id), api_hash,
        flood_sleep_threshold=0, device_model="Agent Relay Setup",
    )
    try:
        await login(client)
        me = await client.get_me()
        if me.bot:
            raise RuntimeError("Нужен пользовательский аккаунт, а не бот.")
        save_env({"TELEGRAM_OWNER_ID": str(me.id)})
        if check_only:
            print("Подключение работает. Сессия сохранена. Сообщения не отправлялись.")
            return
        father = await client.get_entity("BotFather")
        if father.id != BOTFATHER_ID or not father.verified:
            raise RuntimeError("Не удалось проверить официальный аккаунт BotFather.")
        for agent in pending:
            username = existing.get(f"BOT_{agent.upper()}_USERNAME", "").strip()
            if username:
                await adopt_bot(client, father, agent, BOTS[agent], username)
            else:
                await create_bot(client, father, agent, BOTS[agent])
        settings = read_env(ENV)
        updates = {}
        if not settings.get("RELAY_SECRET") or settings["RELAY_SECRET"].startswith("replace-"):
            updates["RELAY_SECRET"] = secrets.token_urlsafe(32)
        if not settings.get("TELEGRAM_CHAT_ID"):
            group = input("ID форум-группы (-100…); Enter = заполнить .env позже: ").strip()
            if group:
                if not re.fullmatch(r"-100\d+", group):
                    raise RuntimeError("Ожидается числовой ID супергруппы, начинающийся с -100.")
                updates["TELEGRAM_CHAT_ID"] = group
        if updates:
            save_env(updates)
        print("Готово. Запусти provision_telegram.py, чтобы пригласить настроенных ботов и подготовить форум.")
        print("После настройки токенов запусти .\\run.ps1. Hooks агентов подключаются отдельно.")
    finally:
        # Disconnect the network while retaining the authorization on disk.
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Настройка ботов и проверка Telegram-сессии")
    parser.add_argument("--check", action="store_true", help="Проверить вход без создания ботов")
    arguments = parser.parse_args()
    try:
        asyncio.run(main(check_only=arguments.check))
    except errors.FloodWaitError as exc:
        defer(max(20, exc.seconds))
        print(f"Telegram просит подождать {exc.seconds} секунд. Повтори запуск позже.")
    except (KeyboardInterrupt, EOFError):
        print("\nНастройка прервана. Уже полученные токены сохранены.")
    except asyncio.TimeoutError:
        print("Истекло время ожидания Telegram. Проверь этап входа или чат BotFather.")
    except RuntimeError as exc:
        print(str(exc))
    except Exception as exc:
        # Do not print raw Telegram errors or payloads containing credentials.
        print(f"Настройка остановлена ({type(exc).__name__}). Проверь чат BotFather и локальные настройки.")

