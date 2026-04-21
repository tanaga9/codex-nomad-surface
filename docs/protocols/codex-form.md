# `codex-form` Protocol

## What Agents Need To Know

`codex-form` lets an agent ask the user for structured input inside a normal
assistant response.

Use it when free-form prose would work, but a form would make the user's choice
or input easier.

From the agent's point of view:

- you can present a form inside the response
- the user can answer by operating that form
- the form result is turned into plain text and appended into the next chat draft
- you do not receive separate structured state back from the UI
- you infer the user's choice from the text that appears in the next prompt

That means the form is an input aid, not a separate side channel.

## Expected Interaction Model

1. The agent writes normal prose.
2. If structured input would help, the agent appends a `codex-form` block.
3. The UI renders that block as a form.
4. The user fills or selects values.
5. The UI converts the result into plain text and appends it to the chat input.
6. The user may still edit the text before sending.
7. The next user prompt contains the text the form produced.

## When To Use It

Use `codex-form` when:

- the agent wants the user to choose from options
- the agent wants the user to fill a few specific values
- the agent wants to reduce ambiguity in the next prompt
- the agent still wants the final result to remain editable text

Do not use it when:

- plain prose is simpler
- the result should trigger an immediate action without user review
- the UI-specific structure is unnecessary

## Agent-Facing Constraints

- Keep the human-readable explanation outside the fenced block.
- The generated result should read naturally as part of the next user prompt.
- Prefer short, editable output text.
- Expect the next turn to contain only the produced text, not hidden metadata.
- Use only structures supported by the current JSON Schema.
- Prefer choice-oriented fields over free-text fields.
- Treat `text` and `textarea` as low-priority tools.
- Use free-text fields only when a short supplemental value is useful.
- If the user needs to provide substantial prose, ask for it in the normal chat
  flow instead of relying on a form field.

## Relationship To Other Documents

- `docs/protocols/codex-form.schema.json` is the machine-readable JSON Schema
  for the current protocol version.
- `AGENTS.md` is the short operational instruction for agents in this repository.
