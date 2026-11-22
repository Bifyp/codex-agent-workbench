# Telegram Agent Relay

This folder contains the reusable Telegram relay core from the Codex Agent Workbench.

## What it does

- Receives agent events over a local authenticated HTTP endpoint.
- Publishes readable progress, tool activity, file changes, and results to Telegram forum topics.
- Keeps durable jobs and Telegram delivery in SQLite.
- Supports separate task IDs, pause/continue/stop/retry controls, attachments up to 20 MB, and per-agent or shared topics.
- Uses a terminal bot as the owner-only command controller.

The relay core is provider-neutral. Agent CLIs and provider credentials stay outside this public template.

## Files

- `service.py`: starts the relay, controller, durable store, and HTTP server.
- `relay.py`: Telegram topics, event routes, and health endpoint.
- `relay_store.py`: SQLite state, jobs, history, and outbox.
- `telegram_transport.py`: Bot API calls, retries, file downloads, and message delivery.
- `myterminal.py`: owner-only Telegram commands.
- `agent_manager.py`: task execution, sessions, subagent topics, and readable progress.
- `human_messages.py`: converts tool and file activity into readable Telegram text.
- `setup_telegram.py`: local BotFather setup with a persistent user session and 20-second cooldown.
- `provision_telegram.py`: verifies and adds configured bots to a forum group.
- `.env.example`: configuration template without credentials.

## Setup

1. Install Python 3.12+ and create a virtual environment.
2. Copy `.env.example` to `.env` and fill in bot tokens, group ID, owner ID, and a long relay secret.
3. Install setup dependencies:

   ```powershell
   python -m pip install -r requirements-setup.txt
   ```

4. Use `setup_telegram.py` locally to create or adopt bots through BotFather.
5. Run `provision_telegram.py` to verify and invite them to the forum group.
6. Start with `run.ps1`. Install optional Windows logon autostart with `install-autostart.ps1`.

Never commit `.env`, Telegram session files, SQLite databases, task folders, provider keys, or local model profiles.

## Commands

```text
/auto task       choose one suitable agent
/codex task      run Codex
/claude task     run Claude Code
/hermes task     run Hermes
/opencode task   run OpenCode
/aider task      run Aider
/goose task      run Goose
/qwen task       run Qwen Code
/pi task         run Pi
/team task       explicit multi-agent workflow
/status           show task IDs and states
/pause ID         pause one task
/continue ID     continue one task
/stop ID         stop one task
/retry ID         retry one task
/project [path]   choose the shared workspace
```

Ordinary messages in the common topic use `/auto` behavior. They do not start every agent. Use `/team` only when a coordinated multi-agent workflow is wanted.

## Readable progress format

The relay renders progress as short blocks such as:

```text
📌 Task
⚙️ Agent
👥 Subagent
🛠 Action
📁 File
✅ Result
⚠️ Error
```

Raw JSON remains in local task journals; Telegram receives the human-readable view.

## Security

The HTTP endpoint binds to localhost by default and requires `Authorization: Bearer RELAY_SECRET`. Bot tokens and user sessions stay local. Review provider terms and current quotas before enabling external CLIs.
