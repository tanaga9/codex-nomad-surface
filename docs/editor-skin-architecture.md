# Nomad Editor Architecture

This document describes the current Text and Document editor architecture.

## Product Model

Nomad Surface has two text editors and one separate visual surface:

```text
Editor
├── Text       plain-text notes, prompts, drafts, and logs
└── Document   Markdown documents

Canvas         diagrams and visual editing
```

`Nomad Editor` is the product category. Text and Document currently share the
same internal `nomad-text` component because they use the same editor engine,
sync protocol, persistence, and Codex patch flow.

The stable names are:

```text
component source:    components/nomad-text/
Streamlit component: codex-nomad-surface.nomad_text
Python mount:        nomad_text()
surface value:       "text"
editor_kind:         "text" | "document"
Dynamic Tools:       text.read | text.apply_patch | text.export
storage root:        .nomad_surface/texts/
```

## Editing Modes

The editor stores plain text as its canonical content. `editor_kind` determines
the product role, while `format` and `presentation` determine storage and
display behavior.

```ts
type EditorKind = "text" | "document"
type TextFormat = "plain" | "markdown"
type MarkdownPresentation = "raw" | "assisted"
```

- Text uses `plain`, forces `raw`, and exports `.txt`.
- Document uses `markdown`, exports `.md`, and can switch between `raw` and
  `assisted` without changing the stored Markdown.

Assisted presentation currently adds lightweight line decoration for headings,
quotes, task-list lines, and fenced code markers. CodeMirror's Markdown parser
and syntax highlighting provide the remaining source-oriented presentation. It
is not a WYSIWYG document model.

## Component and Workspace

The browser editor is a packaged Streamlit Custom Component v2 using
CodeMirror 6. It owns:

- text input, selection, and local Undo/Redo;
- plain-text and Markdown extension configuration;
- Raw/Assisted reconfiguration;
- revision-aware application of Codex patches;
- authenticated WebSocket synchronization.

Streamlit owns task creation and selection, authentication, chat history,
Codex output and interactions, download controls, and the split Editor/Chat
workspace. Desktop layouts place the editor beside Chat; narrow layouts stack
bounded editor and Chat regions.

The component sends live checkpoints over an authenticated same-origin
WebSocket. It does not send every keystroke through Streamlit state and reruns.

## Persistence

Each editor task manages one text identity under:

```text
.nomad_surface/texts/<text-id>/
├── manifest.json
├── current.txt or current.md
└── revisions/
    └── <revision>.txt or <revision>.md
```

The manifest records the text identity, draft or App Server thread association,
project path, editor kind, format, presentation, current revision, and content
hash. Immutable revisions are authoritative; `current.*` is a repairable
projection for convenient file access.

Writes use atomic replacement. Revisions increase monotonically and the store
retains the latest 20 revisions. Editor-specific state such as cursor,
selection, scroll position, and decorations is not persisted as document data.

## Codex Integration

An Editor task is associated with one non-ephemeral Codex App Server thread.
Before its first user turn, Nomad Surface supplies a short developer context
that tells Codex to use the embedded text tools, read before editing, make
targeted changes, and retry after revision conflicts.

The `text` namespace is supplied through App Server Dynamic Tools. Dynamic
Tools are experimental in the current App Server protocol, so Nomad Surface
reports an unsupported state when they are unavailable and does not add a
legacy transport fallback. Manual editing remains usable without Codex tools.

### `text.read`

Reads one bounded scope:

- `all`
- `selection`
- `lines`
- `section`
- `outline`

`section` and `outline` are Document-only conveniences. A read returns the
current revision with the requested content. When the live editor is not
connected, the runtime can read the committed snapshot for supported scopes.

### `text.apply_patch`

Applies a revision-checked batch as one browser transaction and one Undo step.
Supported operations are:

- `replace`
- `insert_before`
- `insert_after`
- `delete`
- `replace_document`

Targeted operations use line ranges plus `old_text` as a precondition. The
browser rejects stale revisions, overlapping operations, ambiguous matches,
and mismatched old content. `replace_document` is reserved for an explicitly
requested whole-document rewrite.

### `text.export`

Saves the current managed content to the editor-controlled export directory.
The tool does not accept an arbitrary destination path. Device download remains
a separate UI action.

## Consistency Boundary

The system provides the consistency needed for a single-user editor:

- revision checks for Codex edits;
- old-content preconditions;
- one transaction and one Undo entry per Codex batch;
- debounced checkpoints;
- atomic committed revisions;
- explicit conflict responses followed by read and retry.

It intentionally does not provide CRDT collaboration, remote cursors,
multi-user presence, tracked changes, or a second headless rich-text model.

## Key Implementation Files

- `codex_nomad_surface/text_component.py`
- `codex_nomad_surface/text_runtime.py`
- `codex_nomad_surface/text_store.py`
- `codex_nomad_surface/text_authoring.py`
- `components/nomad-text/nomad_text/frontend/src/`

## External References

- [Codex App Server](https://developers.openai.com/codex/app-server)
- [CodeMirror reference manual](https://codemirror.net/docs/ref/)
- [Streamlit Custom Components v2](https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component)
