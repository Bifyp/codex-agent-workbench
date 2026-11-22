# Adding Agents

This starter assumes Codex only. It intentionally does not install or configure Claude Code, Free-Claude-Code, FreeLLMAPI, Hermes, or other providers automatically.

## Integration checklist

1. Choose the agent runtime and read its current upstream installation and security documentation.
2. Decide whether it runs as a local CLI, a local API, or a remote service.
3. Add a small adapter behind the project's agent interface. Keep provider-specific flags and output parsing inside that adapter.
4. Make configuration opt-in. The default must continue to use Codex only.
5. Read credentials from environment variables or a secrets manager. Never place secrets in source files, command-line arguments that may be logged, or committed config.
6. Document the model identifier, endpoint, free/paid status, quota behavior, telemetry/data handling, and fallback behavior.
7. Add tests for successful output, provider errors, timeouts, cancellation, and file/media payloads where applicable.
8. Verify the free-only configuration cannot route to a paid provider.

## Suggested configuration shape

This is a design example, not a configuration file implemented by this template:

~~~toml
[agents.codex]
enabled = true
default = true

[agents.fcc_claude]
enabled = false
command = "fcc"

[agents.freellmapi]
enabled = false
base_url = "http://127.0.0.1:8082/v1"
~~~

The local FreeLLMAPI catalog also lists optional tools in [freellmapi-agents.md](freellmapi-agents.md). Do not enable a provider until its installation, authentication, license, and billing behavior have been reviewed. For Free-Claude-Code and FreeLLMAPI, see [free-setup.md](free-setup.md).
