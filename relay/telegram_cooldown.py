"""Persist BotFather pacing across normal exits and restarts."""
import asyncio
import json
import os
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent / ".telegram-cooldown.json"
INTERVAL = 20


def _read():
    if not STATE.exists():
        return 0.0
    try:
        return float(json.loads(STATE.read_text(encoding="utf-8"))["next_allowed_at"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Повреждён файл .telegram-cooldown.json; проверь его перед запуском.") from exc


def defer(seconds):
    deadline = max(_read(), time.time() + seconds)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"next_allowed_at": deadline}), encoding="utf-8")
    os.replace(temporary, STATE)


async def before_send():
    remaining = _read() - time.time()
    if remaining > 0:
        print(f"Кулдаун BotFather: {remaining:.0f} сек.")
        while remaining > 0:
            await asyncio.sleep(min(remaining, 1))
            remaining = _read() - time.time()
    # Reserve before submitting: interrupted/unknown sends still count.
    defer(INTERVAL)

