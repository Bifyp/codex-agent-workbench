# Telegram Task: Build a Codex-First Agent Workbench

Copy the task below into the Telegram group connected to MyTerminal.

---

/team Implement the initial Codex-first agent workbench in the current repository. Read AGENTS.md and docs before editing. Keep the starter clean; do not copy files, credentials, session data, or private implementation from another relay.

You are the lead. Create and actually run three bounded subagent tasks, each in its own Telegram topic if the relay supports topics:
1. Architecture subagent: propose and implement the smallest useful Codex-only project skeleton and adapter boundary. Do not activate other agents.
2. Documentation subagent: review and improve the bilingual README plus the agent-extension and free-only setup guides, including Free-Claude-Code (FCC) and FreeLLMAPI.
3. Review subagent: independently check secrets hygiene, licensing/attribution, free-vs-paid routing risks, and consistency; report findings with file/line references.\n\nUse docs/freellmapi-agents.md as the requested optional integration catalog. Include its entries as disabled/opt-in profiles or clearly documented candidates; do not install tools or enable all providers by default.

Coordinate shared files to avoid conflicting edits. Integrate the subagents' actual changes, resolve conflicts, and verify the final tree. If subagents or Telegram topics are unavailable, report that limitation instead of claiming they were created or used.

Requirements:
- Codex is the only default agent.
- All code comments must be in English; keep README English and Russian sections with a language selector.
- Optional providers are opt-in. Never silently enable paid routes or paid fallbacks.
- Use environment variables or a secrets manager. Do not commit API keys, bot tokens, OAuth/session files, local logs, or .env.
- Use only clean, generic starter files. Do not reproduce third-party source; link to upstream projects and retain attribution/licenses if adding dependencies.
- Do not install packages, run agents, or contact external services as part of implementation unless I explicitly ask.
- Add tests only if the implementation task requires them or I ask.
- Finish with a concise summary of files changed, the real subagents/topics used, verification performed, and any remaining limitations.

---

Subagent topics should be created by the Telegram relay when this command runs; do not pre-create them manually.
