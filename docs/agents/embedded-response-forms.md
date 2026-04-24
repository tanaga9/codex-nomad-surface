# Agent Instructions For Embedded Response Forms

This document defines shared agent instructions for the Prompt Form
assistant-to-UI protocol.

Use this file as the canonical reference when another project wants to reuse
the same guidance from its own `AGENTS.md`.

## Purpose

Prompt Form is a structured input protocol for embedded response forms inside
an assistant response.
Its availability depends on the current Codex client. Codex Nomad Surface is
one client that supports it.
Use it after first checking whether an existing interaction or generative UI
protocol is the better fit.
If no such protocol fits, use `promptform` when the current client supports it
and the agent needs input that fits a defined response shape.
Use it when a structured UI would make it easier for the user to provide that
input than free-form text alone.
The current UI renders embedded forms, but the decision to use Prompt Form
should be based on the shape of the input needed, not on any one widget type.

The result still comes back as normal chat text. The UI may render the form,
but the next user prompt is plain text, not hidden structured state.

## Rules

- Keep normal human-readable explanation outside the fenced block.
- That explanation should still tell the user what to do if the client does not
  render the form.
- Emit the structured payload inside a `promptform` fenced block.
- The fenced block must contain valid JSON.
- Provide `template`.
- Do not use alternate names for `template`.
- Provide `fields`.
- `template` means the plain-text reply template for the next user message.
- Use `{field_id}` placeholders inside `template`.
- Use only structures supported by the current schema version.
- Format the JSON with normal indentation.

## Authoring Guidance

- Treat Prompt Form as the preferred fallback when no better-fit existing
  interaction or generative UI protocol applies and the interaction still
  benefits from structured input.
- Think in terms of the response shape the agent needs, not the specific input
  widget currently available.
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

- Agent-facing protocol notes: [../protocols/promptform.md](../protocols/promptform.md)
- JSON Schema:
  [../protocols/promptform.schema.json](../protocols/promptform.schema.json)
