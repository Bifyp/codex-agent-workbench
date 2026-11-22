# Free-Only Setup: Free-Claude-Code and FreeLLMAPI

This guide explains how to configure a workflow to avoid provider API charges. It cannot guarantee zero cost: third-party free tiers, quotas, terms, and billing controls can change. Check them again before each provider is used.

## Projects

- [Free-Claude-Code (FCC)](https://github.com/Alishahryar1/free-claude-code) is an independent multi-provider coding harness/proxy. Follow its current upstream installation guide and select only providers explicitly marked free.
- [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) is an OpenAI-compatible router for multiple provider endpoints. It can route to free tiers, but routing through it does not itself make a provider free.
- [Community Claude Code + FreeLLMAPI + LiteLLM guide](https://github.com/shashankpandya/claude-code-freellmapi) describes one compatibility-bridge approach. It is not endorsed by either upstream project. Verify commands and versions against current official documentation before running it.

## Free-only checklist

1. **Use free endpoints only.** In FCC or FreeLLMAPI, inspect each selected model/provider and its current pricing/quota. Do not rely only on a model name or a “free” label cached from an older catalog.
2. **Do not add paid API credentials.** A paid provider key can make paid requests possible. If a service offers spend limits, set the limit to zero or the minimum you can accept, and turn off auto-recharge. Do not assume every provider supports a hard zero-cost cap.
3. **Disable paid fallbacks.** Automatic failover can silently send a request to another provider. Keep only explicitly free providers enabled, and test the behavior when their quota is exhausted. Prefer a clear failure over a paid fallback.
4. **Check account plans and terms.** A free endpoint can have a request/token cap, queue, model restriction, or usage policy. FCC and FreeLLMAPI do not waive the upstream provider's rules.
5. **Keep routing local where possible.** A locally hosted router such as FreeLLMAPI can simplify configuration, but remote provider requests still send prompts and code to those providers. For code that must not leave the machine, use a local model endpoint and verify that the selected route stays local.
6. **Protect secrets.** Store credentials locally in environment variables or the operating system's secret store. Keep .env, OAuth/session files, and logs out of Git. Do not paste secrets into Telegram or agent prompts.
7. **Recheck after updates.** Provider catalogs, defaults, quotas, and fallback behavior can change after FCC or FreeLLMAPI upgrades. Review the active route before using it on important work.

## Claude Code compatibility

Claude Code speaks Anthropic's API protocol; FreeLLMAPI exposes an OpenAI-compatible API. Direct compatibility may therefore require an adapter such as LiteLLM. The community guide above documents that architecture, but it may become outdated. Use current upstream documentation for FCC, FreeLLMAPI, Claude Code, and LiteLLM; do not copy commands blindly.

## What “free” means here

“Free-only” means this setup is intended to avoid per-token provider charges by using local inference or provider endpoints that currently have no monetary charge within their limits. It does not promise unlimited usage or permanent availability. Hosting, electricity, hardware, optional subscriptions, and provider terms may still matter. If no provider can confirm a no-charge route, stop instead of falling back to a paid model.
