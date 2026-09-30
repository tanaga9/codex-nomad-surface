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
| Review date | 2026-09-30 |
| Reviewed upstream release | Codex CLI 0.159.2, released 2026-09-29 |
| Local CLI / generated API schemas | 0.158.0 |
| Connected App Server version | 0.158.0, confirmed by WebSocket initialization |
| Reviewed repository revision | Working tree based on `1d55dbb` |
| Scope | Runtime verbosity at chat creation: supported choices, clearing before start, and excluding edits/overrides for existing chats. Earlier broad App Server review: `beb3a8d`. Not an exhaustive desktop or CLI feature audit. |
| Verification | 79 focused tests and 10 subtests passed, including UI and protocol checks. Actual App Server verification is recorded separately below. |

Sources:

- [Official changelog](https://learn.chatgpt.com/docs/changelog)
- [App Server documentation](https://learn.chatgpt.com/docs/app-server)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- Local CLI-generated JSON schemas for 0.158.0.

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

2026-09-30, App Server 0.158.0 over WebSocket, working tree based on `1d55dbb`:
thread creation with `low` produced an `OK` response using `gpt-6-sol`;
`config/read` confirmed that file-level verbosity remained unset. Verification
chats were archived. This checks a short turn, not differences in answer length,
MCP workflows, or App Server 0.159.2.

Additional isolated 0.158.0 App Server checks used a mock Responses API.
Although resume accepted `high`, the loaded thread still sent `low`; omitting
the override did not reset it. This is observed behavior, not a documented
guarantee. After restricting controls to creation, outbound requests used the
chosen `low`/`medium` values for new chats, kept the initial value on continuation,
and used the model default when the initial selection was cleared.

The configured `gpt-6.1-sol` was rejected as unsupported for the connected
ChatGPT account and was absent from the returned model catalog. `gpt-6-sol`
was selected only for verification; the app's default model was not changed.

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
| Continued-chat verbosity changes | Observed API limitation on loaded 0.158.0 threads: resume config does not update the active verbosity. Offer this control only at creation; reconsider when a supported live override is verified. |
| Connected runtime/model availability | Operational check: the configured `gpt-6.1-sol` was rejected by the connected 0.158.0 server/account. Verify runtime updates and account availability before using that model; do not add automatic model fallback. |
| Asynchronous `agentMessage.questions` | Compatibility check: present in the local schema, but not rendered as answer controls. Verify the display and reply contract before implementation. |
| Bearer-authenticated WebSocket connections | Conditional capability gap: the client does not supply an authorization header. Needed when connecting to an authenticated listener. |
| Permission profiles and automatic approval review controls | Optional UI feature: API support exists, but selection controls are not implemented. This is not an explicit non-adoption decision. |
| Summary history items | Unverified behavior: `thread/turns/list` omits `itemsView` and receives the default summary view. Check whether it retains the information needed by the existing history UI. |
