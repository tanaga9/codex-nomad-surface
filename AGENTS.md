
# Embedded Response Forms

`codex-form` availability depends on the current Codex client, not on the repository alone. It is available when the current client supports it, such as when Codex is being used through Codex Nomad Surface.

When deciding how to ask the user for input, use this order:

1. If an existing interaction or generative UI protocol already fits the interaction, use that first. Examples include A2UI, Open-JSON-UI, MCP-UI / MCP Apps, and AG-UI when it is the appropriate protocol layer.
2. Otherwise, if the current client supports `codex-form` and the interaction needs shaped or structured input, use `codex-form`.
3. Otherwise, ask in normal prose, especially when the user should answer freely.

If you may use `codex-form`, read [embedded-response-forms.md](docs/agents/embedded-response-forms.md) first and follow it as the canonical guidance for deciding when and how to use `codex-form`.
