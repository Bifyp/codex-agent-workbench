"""Create or reuse the private forum, add configured agent bots and apply avatars."""
import argparse
import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from telethon import TelegramClient, errors, functions, types, utils
from setup_telegram import read_env, save_env, ROOT, API_ENV, ENV, BOTS
from telegram_cooldown import before_send, defer

STATE = ROOT / ".telegram-branding.json"
ASSETS = ROOT / "avatars"


def bot_call(token, method, data=None, image=None):
    data = data or {}
    headers = {}
    if image:
        boundary = secrets.token_hex(20)
        parts = []
        for key, value in data.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="avatar"; filename="avatar.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
        parts.extend([Path(image).read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
        body = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = Request(f"https://api.telegram.org/bot{token}/{method}", data=body, headers=headers)
    try:
        with urlopen(request, timeout=45) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            description = json.load(exc).get("description", "request rejected")
        except Exception:
            description = "request rejected"
        raise RuntimeError(f"Bot API {method}: {description}") from None
    except (URLError, TimeoutError):
        raise RuntimeError(f"Bot API {method}: network error") from None
    if not result.get("ok"):
        raise RuntimeError(f"Bot API {method}: request rejected")
    return result["result"]


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def persist(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE)


async def main(avatars_only=False):
    cfg = read_env(ENV)
    api = read_env(API_ENV)
    state = load_state()
    bots = {}
    for agent in BOTS:
        token = cfg.get(f"BOT_{agent.upper()}_TOKEN")
        if not token:
            print(f"Skipped {agent}: token not configured yet.", flush=True)
            continue
        bots[agent] = await asyncio.to_thread(bot_call, token, "getMe")
        print(f"Verified {agent}: @{bots[agent]['username']}", flush=True)
    client = TelegramClient(str(ROOT / "telegram-user.session"),
                            int(api["TELEGRAM_API_ID"]), api["TELEGRAM_API_HASH"],
                            flood_sleep_threshold=0, device_model="Agent Relay Setup")
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Run setup_telegram.py --check to connect first.")
        chat_id = cfg.get("TELEGRAM_CHAT_ID")
        if chat_id:
            group = await client.get_entity(int(chat_id))
        else:
            # Recover an interrupted create/save using a matching group owned by this account.
            matches = [d.entity async for d in client.iter_dialogs()
                       if d.title == "Agent Relay" and getattr(d.entity, "creator", False)
                       and getattr(d.entity, "forum", False)]
            if len(matches) > 1:
                raise RuntimeError("Multiple Agent Relay groups found; set TELEGRAM_CHAT_ID.")
            if matches:
                group = matches[0]
            else:
                await before_send()
                created = await client(functions.channels.CreateChannelRequest(
                    title="Agent Relay",
                    about="Codex, Claude Code, Hermes and terminal activity.",
                    megagroup=True, forum=True,
                ))
                group = next(c for c in created.chats if isinstance(c, types.Channel))
            save_env({"TELEGRAM_CHAT_ID": str(utils.get_peer_id(group))})
        if not getattr(group, "forum", False) or not group.megagroup:
            raise RuntimeError("Configured chat must be a forum supergroup.")
        print(f"Forum ready: {group.title} ({utils.get_peer_id(group)})", flush=True)
        state["chat_id"] = utils.get_peer_id(group)
        persist(state)
        if not avatars_only:
            for agent, bot in bots.items():
                user = await client.get_entity(bot["username"])
                if user.id != bot["id"] or not user.bot:
                    raise RuntimeError(f"Bot identity mismatch: {agent}")
                try:
                    await client.get_permissions(group, user)
                except errors.UserNotParticipantError:
                    await before_send()
                    result = await client(functions.channels.InviteToChannelRequest(group, [user]))
                    if getattr(result, "missing_invitees", []):
                        raise RuntimeError(f"Could not add {agent} to group.")
                permissions = await client.get_permissions(group, user)
                if agent == "terminal" and not getattr(permissions.participant, "admin_rights", None):
                    await before_send()
                    await client(functions.channels.EditAdminRequest(
                        group, user, types.ChatAdminRights(manage_topics=True), "Topics",
                    ))
                print(f"Group member ready: {agent}", flush=True)
        # Missing avatars are permitted during the initial group provisioning phase.
        for agent in bots:
            image = ASSETS / f"{agent}.jpg"
            if not image.exists():
                continue
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            if state.get(f"avatar_{agent}") != digest:
                await before_send()
                await asyncio.to_thread(bot_call, cfg[f"BOT_{agent.upper()}_TOKEN"],
                    "setMyProfilePhoto",
                    {"photo": json.dumps({"type": "static", "photo": "attach://avatar"})},
                    image)
                state[f"avatar_{agent}"] = digest
                persist(state)
                print(f"Avatar installed: {agent}", flush=True)
        image = ASSETS / "group.jpg"
        if image.exists():
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            if state.get("avatar_group") != digest:
                await before_send()
                upload = await client.upload_file(image)
                await client(functions.channels.EditPhotoRequest(group, types.InputChatUploadedPhoto(upload)))
                state["avatar_group"] = digest
                persist(state)
                print("Group avatar installed.", flush=True)
        print("Done.", flush=True)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--avatars-only", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.avatars_only))
    except errors.FloodWaitError as exc:
        defer(max(20, exc.seconds))
        print(f"Telegram FloodWait: retry after {exc.seconds} seconds.")
        raise SystemExit(1)
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1)
    except Exception as exc:
        print(f"Stopped: {type(exc).__name__}")
        raise SystemExit(1)

