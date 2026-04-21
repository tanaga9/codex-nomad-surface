# Agent Instructions For Embedded Response Forms

This repository supports an assistant-to-UI protocol named `codex-form` for
cases where the agent wants to ask the user for structured input inside a normal
assistant response.

From the agent's point of view, the important behavior is:

- the UI may render the form for the user
- the user's form result is converted into plain text
- that plain text is appended to the next chat draft
- the agent learns the result by reading the next user prompt

Use it when a form would make user choice or value entry easier than free-form
text alone.

Rules:

- Keep normal human-readable explanation outside the fenced block.
- Emit the structured payload inside a `codex-form` fenced block.
- The fenced block must contain valid JSON.
- Provide `template`.
- Provide `fields`.
- Use `{field_id}` placeholders inside `template`.
- Use only structures supported by the current schema for this repository
  version.

Authoring guidance:

- Prefer concise plain-text templates that the user may still edit before
  sending.
- Do not use the form as an action channel that bypasses user confirmation.
- Emit one form when one form is enough.
- Expect the next turn to reveal the result as plain text, not as separate
  structured state.
- Prefer choice-oriented fields first.
- Treat `text` and `textarea` as low-priority options.
- Use `text` or `textarea` only when a short supplemental free-text value is
  materially helpful.
- If the user is expected to write substantial free text, prefer asking for it
  directly in the chat prompt instead of through a form.

Reference:

- Agent-facing protocol notes: [docs/protocols/codex-form.md](docs/protocols/codex-form.md)
- JSON Schema: [docs/protocols/codex-form.schema.json](docs/protocols/codex-form.schema.json)
