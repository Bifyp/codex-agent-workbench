# Repository Instructions

- Keep Codex as the only default agent. Treat other agents as optional, explicitly configured integrations.
- Keep source-code comments in English.
- Keep the root README bilingual (English and Russian) with working language links.
- Do not commit API keys, bot tokens, OAuth data, CLI sessions, private logs, or local environment files.
- Never silently choose a paid provider or enable a paid fallback. Require explicit provider configuration and document its cost and data handling.
- When a task has independent workstreams, create and use real subagents, give each a bounded deliverable, and integrate their actual results. Do not claim delegation that did not occur.
- Keep provider integrations behind small adapters and document required environment variables.
- Preserve upstream licenses and attribution when adding third-party dependencies or code.
- Before reporting completion, inspect the changed files and run only the verification steps requested by the user or required by the task.
