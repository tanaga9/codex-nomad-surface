
# Embedded Response Forms

`promptform` availability depends on the current Codex client, not on the repository alone. It is available when the current client supports it, such as when Codex is being used through Codex Nomad Surface.

Prompt Form is a protocol embedded in the assistant response body. It is
separate from interactive tools such as `request_user_input`.
Do not treat tool availability as a proxy for `promptform` availability.
If a tool is `unavailable`, that does not by itself mean Prompt Form cannot be
used.
Unless client support has been explicitly rejected or ruled out, read the
related Prompt Form materials first and try `promptform` before falling back
to plain prose when structured input would help.
Do not claim "this client does not support Prompt Form" unless the user has
explicitly confirmed that, or there is an explicit specification stating it.

When deciding how to ask the user for input, use this order:

1. If an existing interaction or generative UI protocol already fits the interaction, use that first. Examples include A2UI, Open-JSON-UI, MCP-UI / MCP Apps, and AG-UI when it is the appropriate protocol layer.
2. Otherwise, if the current client supports `promptform` and the interaction needs shaped or structured input, use `promptform`.
3. Otherwise, ask in normal prose, especially when the user should answer freely.

If you may use `promptform`, read [embedded-response-forms.md](docs/agents/embedded-response-forms.md) first and follow it as the canonical guidance for deciding when and how to use Prompt Form.
