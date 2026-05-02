# Project Preferences

When working in this repository, preserve these user preferences unless the user explicitly overrides them:

- Do not run git operations that modify repository state unless the user explicitly asks for them. Read-only git inspection such as `git status` and `git diff` is allowed.
- Follow `SPEC.md` for product design constraints.
- Keep documentation concise and avoid overfitting it to recent events.
- Use generic placeholder paths in documentation, such as `/path/to/...`, instead of real local usernames or machine-specific paths when the path is only illustrative.
- Once per session, read `docs/README.md` before the first development command so local environment and testing notes are not missed.
- Prefer current Codex App Server APIs directly. Do not add fallback or legacy fallback behavior for cases the current App Server API does not support unless the user explicitly asks for it. If a fallback seems worth recommending, explain the tradeoff and ask the user before implementing it. Maintain `docs/app-server-api-exceptions.md` when local state, external APIs, or indirect mechanisms are used instead of current Codex App Server APIs.

## Git Diff Workflow

The user reviews AI-generated edits before staging them. In this repository,
staged changes are user-reviewed candidate changes, while unstaged changes are
in-progress agent edits.

Use `git diff --cached` for staged-review or commit-message requests. Use
`git diff` for current unstaged edits. If both exist, clearly distinguish them.
Do not stage, unstage, revert, or commit unless explicitly asked.


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

If you may use `promptform`, read `embedded-response-forms.md` first and follow it as the canonical guidance for deciding when and how to use Prompt Form.

Look for `embedded-response-forms.md` at either of these paths:

- `docs/agents/embedded-response-forms.md`
- `/path/to/codex-nomad-surface/docs/agents/embedded-response-forms.md`

If it does not exist at either path, briefly tell the user that the Prompt Form guidance file could not be found.
