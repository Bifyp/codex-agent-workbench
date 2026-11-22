# FreeLLMAPI Agent Catalog Snapshot

**Observed in FreeLLMAPI.exe on 2026-11-22.** The app showed 21 integration cards. This document records the additional catalog entries requested for this Codex-first starter. Catalog contents may change with app updates.

The starter already covers Codex CLI, Claude Code through the Free-Claude-Code (FCC) path, and Hermes Agent through FreeLLMAPI. They are omitted below. The remaining catalog entries are optional candidates; listing them does not mean they are installed, supported by this template, or enabled.

## Additional catalog entries

| Integration | Category shown in app | Description shown in app |
| --- | --- | --- |
| Cline | Editor | VS Code extension that asks for confirmation before edits and commands. |
| OpenCode | CLI / Desktop | Provider-neutral terminal coding agent with a full-screen text interface. |
| Continue | Editor | VS Code and JetBrains extension for autocomplete and chat while coding. |
| Aider | Terminal | Command-line coding partner that creates a Git commit for each change. |
| Goose | CLI / Desktop | Extensible local agent from Block, including MCP server support. |
| Qwen Code | Terminal | Gemini CLI fork configured for Qwen models and large repositories. |
| Roo Code | Editor | VS Code extension with configurable architect, coder, and debugger modes. |
| Kilo Code | Editor / CLI | Roo/Cline-style extension with an orchestrator for splitting work. |
| Crush | Terminal | Charm terminal coding agent with project-oriented sessions. |
| DeepSeek Harness | Terminal | DeepSeek wrapper that represents providers as switchable routes. |
| MiMo Code | Terminal | Xiaomi terminal coding agent based on OpenCode, with OpenAI-compatible providers. |
| AtomCode | Terminal | AtomGit Rust terminal coding agent with OpenAI-compatible endpoint support. |
| OpenClaw | Agent | Local always-on assistant that can work through messaging apps. |
| Pi | Terminal | Minimal terminal agent extended through its own extensions, skills, and prompts. |
| Reasonix | CLI / Desktop | DeepSeek-oriented terminal coding agent. |
| ZCode | Desktop / CLI | Z.ai coding agent for GLM models with provider configuration. |
| Cursor | Editor | AI-focused editor that uses Cursor cloud services and requires a public URL for this gateway connection. |
| Generic OpenAI client | API | Manual connection option for other tools that support the OpenAI API. |

## How to use this catalog

Treat each row as a candidate, not an enabled provider. Before implementing or connecting one:

1. Check whether the tool is already installed and available in the target environment.
2. Read the upstream project's current installation, license, model, and security documentation.
3. Confirm the selected model endpoint is genuinely free under the current provider terms if no-cost use is required.
4. Keep the adapter opt-in and preserve Codex as the default.
5. Never add API keys, OAuth data, session files, or paid fallback credentials to the repository.

For no-provider-billing guidance, see [Free-Only Setup](free-setup.md). The app's card descriptions are paraphrased here; consult the live FreeLLMAPI catalog for current connection steps.
