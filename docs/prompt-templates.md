# Prompt Templates

Store project prompts in `ops/prompts/**/*.md`. **Use Prompt Template** in the
sidebar is available in new and existing chats. Like Use Skill and Add File
Path, it is disabled without a selected project or while a turn is pending.
Each press adds a local picker entry, like the Skill and File Path
helpers. Its searchable selection shows template titles and relative paths
from the selected project. Each history render shares one fresh template
listing across its pickers. Chat, Canvas,
and Text share the same entry point.

Choose a template, fill any inputs, and optionally preview the result.
**Add to draft** appends the expanded text to the existing chat input. Each
picker remains available after insertion for reuse; additional presses create
independent pickers. Their selections and inputs are local UI state, not
messages sent to Codex. Insertion never sends a turn or creates an App Server
thread. During turn recovery, inputs remain usable and insertion waits until
the chat input becomes available. Normal send/interrupt behavior is unchanged.

## Format

Plain Markdown needs no metadata. Use `{{input.name}}` for a variable; names
start with an ASCII letter or underscore and continue with letters, digits, or
underscores. Repeated names share one input. Undeclared inputs are required
text fields. Single braces and other template namespaces are literal.

```markdown
---
title: Review changes
description: Review a target with a chosen focus
inputs:
  target:
    label: Target
    required: true
  focus:
    label: Focus
    type: select
    options: [Correctness, Tests, Readability]
    default: Correctness
  notes:
    label: Additional context
    type: textarea
    required: false
---
Review {{input.target}} with a focus on {{input.focus}}.

{{input.notes}}

List findings in order of importance and explain the evidence.
```

Front matter is optional and is removed before insertion. Supported keys are
`title`, `description`, and `inputs`. Each input supports `label`, `type`
(`text`, `textarea`, or `select`), `required` (default true), `default`, `help`,
and string `options` for selects. Quote numeric or boolean-looking text values.
A select without a default starts unselected. Unknown settings, duplicate YAML
keys, unused input definitions, and malformed input markers are reported.
Invalid templates do not prevent other templates or normal chat from working.

Markers are substituted even inside code blocks. Write `\{{input.name}}` to
insert the literal marker without the backslash. Input values are inserted
once, never evaluated or recursively expanded. Required blanks prevent insertion;
optional blank values become empty text. The body otherwise retains its spacing
and line breaks. No shell execution, environment expansion, file inclusion, or
conditional syntax is supported. Symlinked template files/directories are skipped.

## State and lifecycle

Unfinished input values live only in the browser's Streamlit session, scoped to
the picker entry and template. Each new picker starts with template defaults. Pending insertions retain their text and inputs until the
browser confirms insertion. A failed insertion offers **Retry insertion**;
**Cancel insertion** cancels delivery while preserving inputs. A receipt token
prevents duplicate insertion when a component remounts before acknowledgement.
Confirmation clears only the pending delivery; selections and inputs remain
available in that picker. Switching projects or
chats does not share values. Reloading the session may lose unfinished inputs.
Editing the template resets its inputs to current defaults on the next rerun.
A new chat acquiring its App Server thread ID keeps the same template state.
History refreshes preserve picker positions relative to surrounding messages.
If those messages have been trimmed, the picker stays at the older edge of the
visible history; loading its older context restores its original position.
New pickers do not inherit values from earlier pickers; there are no
context-derived defaults.

Prompt Templates replace saved JSON Prompt Forms. The bundled examples have
been migrated; convert other `promptform-defs/*.json` files manually by moving
the body to Markdown and field metadata to front matter. There is no legacy
loader. Assistant-emitted `promptform` blocks are ordinary Markdown code blocks,
not interactive UI. App Server questions and approvals remain independent.
