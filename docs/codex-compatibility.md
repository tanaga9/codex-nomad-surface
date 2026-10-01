# Codex Compatibility Checkpoint

This is the rolling checkpoint for Codex specification reviews, live-use
verification, and explicit non-adoption decisions. Product scope remains in
[SPEC.md](../SPEC.md); indirect implementations remain in the
[API exception ledger](app-server-api-exceptions.md).

## Maintenance Rules

- Read this checkpoint before a Codex compatibility review. Start release-note
  comparisons after the reviewed upstream version, then revisit the open checks
  below. Recheck current documentation and version-specific API schemas because
  documentation can change independently of releases.
- Record the review date, upstream version, local CLI and connected server
  versions, repository revision, scope, sources, and verification evidence.
  Mark unavailable information as unknown. A scoped review does not establish
  compatibility for unreviewed features.
- Keep specification review, tests with protocol peers, and actual App Server
  use separate. Tests do not advance the live-use checkpoint.
- Record intentional non-adoption with its reason, decision date, and condition
  for reconsideration. Missing functionality alone is not a non-adoption
  decision; unresolved checks and optional features stay separate.
- Update this file after each review or live-use check. Keep the latest scoped
  checkpoint and active decisions concise; Git history retains earlier records.

## Latest Specification Review

| Field | Checkpoint |
| --- | --- |
| Review date | 2026-10-01 |
| Reviewed upstream release | Codex CLI 0.159.2, released 2026-09-29 |
| Local CLI / generated API schemas | 0.159.2 |
| Connected App Server version | 0.159.2, confirmed by WebSocket initialization |
| Reviewed repository revision | Working-tree changes based on `799b3e2` |
| Scope | Asynchronous question presentation, turn-aware panel state, and conservative duplicate-text suppression. Metadata and composition review: `799b3e2`; upgrade review: `f02c805`; broad App Server review: `beb3a8d`. Not an exhaustive desktop or CLI feature audit. |
| Verification | 136 focused tests and 10 subtests passed, including question-only history, stable question controls, recovery deduplication, acknowledged insertion, concurrent receiving, and a loopback WebSocket peer. Local Streamlit 1.63.0 browser checks cover phase-less question text, manual panel state across simulated steer acknowledgements, completion, retained answers, and question-panel closure after confirmed draft insertion. Async payloads and steer acknowledgements were fixtures; actual model emission and reply interpretation remain unverified. |

Sources:

- [Official changelog](https://learn.chatgpt.com/docs/changelog)
- [App Server documentation](https://learn.chatgpt.com/docs/app-server)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- Local CLI-generated JSON schemas for 0.159.2.
- [Streamlit widget identity](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior)
  and [1.63.0 expander API](https://docs.streamlit.io/1.63.0/develop/api-reference/layout/st.expander).

The generated schema exposes `agentMessage.delivery: "async"` and questions
with a `title` and nullable string-array `options`. These are preserved in live output
and history when supplied by the server, including messages with empty text.
Each question has suggested-choice and free-text controls; no choice is selected
automatically. Answer drafts survive a turn finishing within the browser session.
Question panels follow progress notes and precede the final answer. A reserved
container holds auxiliary output in both live and history views, keeping question
inputs in place when output grows or the turn completes.
Progress remains open during a live turn unless manually closed. Progress and
question panels close once at completion and can be reopened. Body text is
omitted only after the matching form renders and the entire text repeats its
question titles and options; extra prose and unmatched text remain visible.
Confirmed insertion closes its question panel once and disables repeat
preparation for that question set. Draft text separates questions and answers
with blank lines.

The reviewed documentation and schema do not establish a dedicated async-answer
RPC or question response ID. The controls append ordinary user text to the main
chat draft using its existing acknowledged insertion bridge. The user reviews
and sends it through the existing `turn/start` or active-turn `turn/steer` path.
Receiving continues; these output items do not create a blocking JSON-RPC
request. This does not claim protocol-level resolution of asynchronous questions.

Verbosity is sent only through `thread/start.config.model_verbosity`.
Before a new chat starts, the UI allows `low`, `medium`, or `high`;
clearing the selection omits the override. Existing chats have no editable
verbosity control, and continuation requests do not send this override.
This uses App Server's thread-scoped config and does not write `config.toml`.

The earlier review at `beb3a8d` covers session-scoped permission responses and flat
MCP forms, URL-flow confirmation, decline/cancel responses, and input validation.
Optional empty multiselect fields are omitted; required fields remain validated.
Nested or referenced form schemas are outside the implemented form subset and
cannot be accepted through this UI. Extended-form capability opt-in has not been
enabled.

## Latest Live-Use Verification

2026-09-30, App Server 0.159.2 over WebSocket, revision `f02c805`:
`config/read` returned the default `gpt-6.1-sol`, which also appeared in
`model/list`. Without a model override, a new thread used `gpt-6.1-sol` and
returned exactly `OK` with low reasoning effort and initial verbosity `low`.
The verification chat was archived; no persistent configuration was changed.
The previous unsupported-model error on 0.158.0 is resolved for this connection.
This checks a short turn, not the complete browser UI, MCP workflows, or
differences in answer length.

An isolated 0.159.2 App Server with a mock Responses API reproduced the
loaded-thread limitation: after starting with `low`, resume with `high` and
resume without the override both still sent `low`. This is observed behavior,
not a documented guarantee. Keep verbosity controls limited to chat creation.

## Intentional Non-Adoption

| Decision | Reason and provenance | Reconsider when |
| --- | --- | --- |
| Do not require exhaustive thread fetching or display in the sidebar **Chat** picker or **New Project** recent-chat list. Preserve bounded candidate lists. | User-confirmed on 2026-09-30: too many entries make these surfaces cumbersome. Limited coverage alone is not a compatibility defect. | The user changes the desired selection experience, or an upstream change prevents the intended bounded selection from working. |

This decision concerns list size. It does not settle which thread sources or
ordering those bounded lists should use. Other product non-goals remain in
`SPEC.md`; do not turn every new official feature into a required implementation.

## Open Checks and Optional Features

| Item | Classification / next step |
| --- | --- |
| Continued-chat verbosity changes | Observed API limitation on loaded 0.159.2 threads: resume config does not update the active verbosity. Offer this control only at creation; reconsider when a supported live override is verified. |
| Asynchronous `agentMessage.questions` reply semantics | Display and ordinary-draft composition implemented and fixture-tested. Actual model emission and interpretation of the follow-up have not been live-tested; verify a dedicated reply contract if upstream documents one. |
| Bearer-authenticated WebSocket connections | Conditional capability gap: the client does not supply an authorization header. Needed when connecting to an authenticated listener. |
| Permission profiles and automatic approval review controls | Optional UI feature: API support exists, but selection controls are not implemented. This is not an explicit non-adoption decision. |
| Summary history items | Unverified behavior: `thread/turns/list` omits `itemsView` and receives the default summary view. Check whether it retains the information needed by the existing history UI. |
