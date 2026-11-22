# Codex Agent Workbench

[English](#english) | [Русский](#русский)

<a id="english"></a>
## English

A clean, Codex-first starter repository for building a collaborative agent workspace. Codex is the only assumed/default agent. Optional providers are documented as extensions; this repository does not ship an agent relay implementation or credentials.

### Quick start

1. Clone this repository and open the folder in Codex.
2. Read [AGENTS.md](AGENTS.md) and [the Telegram bootstrap task](tasks/telegram-bootstrap.md).
3. Ask Codex to implement the task. It should delegate independent work to real subagents and integrate their results.
4. Keep credentials in local environment variables or a secrets manager. Never commit tokens, session files, or .env files.

### What's included

- [AGENTS.md](AGENTS.md): repository-specific instructions for Codex.
- [docs/adding-agents.md](docs/adding-agents.md): a provider-neutral extension guide.
- [docs/free-setup.md](docs/free-setup.md): a no-provider-billing setup guide for Free-Claude-Code and FreeLLMAPI.
- [tasks/telegram-bootstrap.md](tasks/telegram-bootstrap.md): a ready-to-send task prompt for Telegram, including actual subagent delegation.
- [.gitignore](.gitignore): common local secrets and generated files.

### Reference projects

These projects are references, not vendored dependencies. Review their current documentation, licenses, security posture, and provider terms before using them:

- [Free-Claude-Code](https://github.com/Alishahryar1/free-claude-code): an open-source multi-provider harness/proxy project. Its supported providers and free quotas can change.
- [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi): an OpenAI-compatible router that aggregates provider endpoints, including free tiers.
- [Claude Code + FreeLLMAPI + LiteLLM guide](https://github.com/shashankpandya/claude-code-freellmapi): a community guide to a compatibility bridge. It is not an official integration and should be checked against current upstream releases.

See [the free setup guide](docs/free-setup.md) before configuring providers. “Free” depends on the selected provider, account plan, limits, and current terms. This template cannot guarantee that a third-party route will stay free.

<a id="русский"></a>
## Русский

Чистый шаблон рабочего пространства с Codex в качестве основного агента. По умолчанию предполагается только Codex. Другие провайдеры описаны как возможные расширения; в репозитории нет готового Telegram-моста и секретов.

### Быстрый старт

1. Склонируй репозиторий и открой папку в Codex.
2. Прочитай [AGENTS.md](AGENTS.md) и [задание для Telegram](tasks/telegram-bootstrap.md).
3. Попроси Codex выполнить задание. Он должен действительно делегировать независимые части субагентам, а затем объединить результаты.
4. Храни ключи только в локальных переменных окружения или менеджере секретов. Не добавляй в Git токены, файлы сессий и .env.

### Что входит

- [AGENTS.md](AGENTS.md): инструкции Codex для этого репозитория.
- [docs/adding-agents.md](docs/adding-agents.md): руководство по добавлению агентов.
- [docs/free-setup.md](docs/free-setup.md): настройка без платных API-маршрутов для Free-Claude-Code и FreeLLMAPI.
- [tasks/telegram-bootstrap.md](tasks/telegram-bootstrap.md): готовое задание для Telegram с реальной работой субагентов.
- [.gitignore](.gitignore): локальные секреты и генерируемые файлы.

### Проекты для изучения

Это ссылки на сторонние проекты, а не включённые зависимости. Перед использованием проверь актуальную документацию, лицензию, безопасность и условия провайдеров:

- [Free-Claude-Code](https://github.com/Alishahryar1/free-claude-code): проект с несколькими CLI и провайдерами. Список моделей и бесплатные лимиты могут меняться.
- [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi): OpenAI-совместимый маршрутизатор, объединяющий API провайдеров, в том числе бесплатные тарифы.
- [Инструкция Claude Code + FreeLLMAPI + LiteLLM](https://github.com/shashankpandya/claude-code-freellmapi): стороннее руководство по совместимости. Это не официальная интеграция; сверяйся с текущими версиями оригинальных проектов.

В [инструкции по бесплатной настройке](docs/free-setup.md) описано, что проверить. Бесплатность зависит от выбранного провайдера, тарифа, квот и актуальных условий. Шаблон не может гарантировать, что сторонний маршрут всегда будет бесплатным.
