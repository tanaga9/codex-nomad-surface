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
| Connected App Server version | Unknown; not established by this review |
| Reviewed repository revision | `beb3a8d` |
| Scope | App Server transport, initialization, models, thread listing/history, runtime controls, approvals, user input, MCP elicitation, and Dynamic Tools relevant to the existing Surface. Not an exhaustive desktop or CLI feature audit. |
| Verification | 120 focused tests and 10 subtests passed; three local WebSocket peer tests were excluded in the final review. These were automated UI/protocol tests, not a live App Server session. |

Sources:

- [Official changelog](https://learn.chatgpt.com/docs/changelog)
- [App Server documentation](https://learn.chatgpt.com/docs/app-server)
- Local CLI-generated JSON schemas for 0.158.0.

The reviewed revision fixes session-scoped permission responses and adds flat
MCP forms, URL-flow confirmation, decline/cancel responses, and input validation.
Optional empty multiselect fields are omitted; required fields remain validated.
Nested or referenced form schemas are outside the implemented form subset and
cannot be accepted through this UI. Extended-form capability opt-in has not been
enabled.

## Latest Live-Use Verification

**Not recorded.** This review did not establish a dated, actual App Server
session or verify 0.159.2 through a live connection. Record the server version,
date, repository revision, and workflows exercised when that verification occurs.

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
| Runtime verbosity | Compatibility check: the UI sends `turn/start.verbosity`, which is absent from the local 0.158.0 schema. Confirm a supported scoped control or remove the ineffective setting. |
| Asynchronous `agentMessage.questions` | Compatibility check: present in the local schema, but not rendered as answer controls. Verify the display and reply contract before implementation. |
| Bearer-authenticated WebSocket connections | Conditional capability gap: the client does not supply an authorization header. Needed when connecting to an authenticated listener. |
| Permission profiles and automatic approval review controls | Optional UI feature: API support exists, but selection controls are not implemented. This is not an explicit non-adoption decision. |
| Summary history items | Unverified behavior: `thread/turns/list` omits `itemsView` and receives the default summary view. Check whether it retains the information needed by the existing history UI. |
