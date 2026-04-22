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

Implementation note:

- In the current app, the preferred behavior is to keep `st.chat_input` as the
  primary draft widget and append generated form text to the end of the current
  unsent draft when possible, rather than replacing the draft wholesale.

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
- The explanation outside the block should still tell the user what to do if
  the client does not render the form.
- `template` is the plain-text reply template used to build the next user
  prompt from the chosen values.
- The generated result should read naturally as part of the next user prompt.
- Prefer short, editable output text.
- Expect the next turn to contain only the produced text, not hidden metadata.
- Use only structures supported by the current JSON Schema.
- Use `template`; do not use alternate field names for the output template.
- Prefer including `response_example` so the intended reply shape is obvious in
  plain-text logs and non-rendering clients.
- Prefer choice-oriented fields over free-text fields.
- Prefer string options when the stored value and displayed text are the same.
- Use option objects only when you need a different `label` from `value`.
- For `radio` and `select`, set `default` to the recommended option value when
  the agent has a clear recommendation.
- `default` may point to any option, not only the first one.
- Treat `text` and `textarea` as low-priority tools.
- Use free-text fields only when a short supplemental value is useful.
- `label` is optional; omit it when the `id` is already readable enough.
- For option objects, `label` is optional; omit it when the `value` is already
  readable enough.
- Empty-string option values are allowed when they mean "no extra text" in the
  generated reply.
- Match user-facing text to the user's language when practical. This includes
  prose outside the block and fields such as `title`, `description`,
  `submit_label`, field `label`, and option `label`. Keep `id` stable and ASCII
  when practical.
- Format the JSON with normal indentation so it remains readable as text.
- If the user needs to provide substantial prose, ask for it in the normal chat
  flow instead of relying on a form field.

## Minimal Shape

This is a good default when the form is simple:

```json
{
  "template": "{task} for {project}",
  "response_example": "setup for my-app",
  "fields": [
    {
      "id": "task",
      "type": "select",
      "options": ["setup", "migration"]
    },
    {
      "id": "project",
      "type": "text"
    }
  ]
}
```

Add `label`, `description`, `help`, or other fields only when they materially
improve the user interaction.

When the agent recommends a non-first option, set `default` explicitly:

```json
{
  "template": "{mode}",
  "fields": [
    {
      "id": "mode",
      "type": "radio",
      "default": "3",
      "options": ["1", "2", "3", "4", "5"]
    }
  ]
}
```

Use option objects only when the displayed text needs to differ from the stored
value:

```json
{
  "template": "{policy}",
  "fields": [
    {
      "id": "policy",
      "type": "radio",
      "default": "recommended",
      "options": [
        "minimal",
        {
          "value": "recommended",
          "label": "Recommended default"
        }
      ]
    }
  ]
}
```

## Relationship To Other Documents

- `docs/protocols/codex-form.schema.json` is the machine-readable JSON Schema
  for the current protocol version.
- `AGENTS.md` contains project-specific operating instructions for agents.
