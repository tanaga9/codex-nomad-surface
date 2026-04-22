# Agent Instructions For Embedded Response Forms

This document defines shared agent instructions for the `codex-form`
assistant-to-UI protocol.

Use this file as the canonical reference when another project wants to reuse
the same guidance from its own `AGENTS.md`.

## Purpose

This repository supports an assistant-to-UI protocol named `codex-form`.
Use it when a form would make user choice or short value entry easier than
free-form text alone.

The result still comes back as normal chat text. The UI may render the form,
but the next user prompt is plain text, not hidden structured state.

## Rules

- Keep normal human-readable explanation outside the fenced block.
- That explanation should still tell the user what to do if the client does not
  render the form.
- Emit the structured payload inside a `codex-form` fenced block.
- The fenced block must contain valid JSON.
- Provide `template`.
- Do not use alternate names for `template`.
- Provide `fields`.
- `template` means the plain-text reply template for the next user message.
- Use `{field_id}` placeholders inside `template`.
- Use only structures supported by the current schema for this repository
  version.
- Format the JSON with normal indentation.

## Authoring Guidance

- Emit one form when one form is enough.
- Prefer concise plain-text templates that the user may still edit before
  sending.
- Prefer choice-oriented fields first.
- Treat `text` and `textarea` as low-priority options.
- Use free-text fields only when a short supplemental value is materially
  helpful.
- Prefer including `response_example` so the intended reply shape is obvious in
  plain text.
- Prefer string options when display text and stored value are the same.
- Use option objects only when `label` needs to differ from `value`.
- Empty-string option values are allowed when they intentionally mean "no extra
  text" in the generated reply.
- For `radio` and `select`, set `default` to the recommended option when the
  agent has one, even if that option is not first.
- Omit `label` when the `id` is already readable enough.
- For option objects, omit `label` when the `value` is already readable enough.
- For non-English users, keep `id` stable and ASCII when practical, and put the
  localized wording in user-facing fields.
- Match user-facing text to the user's language when practical. This includes
  prose outside the block, `title`, `description`, `submit_label`, field
  `label`, and option `label`.
- When a standard purpose-built protocol exists for the interaction, prefer
  using that protocol instead of using the form as a substitute.
- If the user is expected to write substantial free text, prefer asking for it
  directly in the chat prompt instead of through a form.

## Reference

- Agent-facing protocol notes: [../protocols/codex-form.md](../protocols/codex-form.md)
- JSON Schema:
  [../protocols/codex-form.schema.json](../protocols/codex-form.schema.json)
