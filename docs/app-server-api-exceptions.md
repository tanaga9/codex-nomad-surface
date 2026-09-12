# Codex App Server API Exception Ledger

This ledger tracks implementations that provide functionality by reading local
state, calling external APIs directly, or using other indirect mechanisms
instead of a current Codex App Server API.

## Policy

- Prefer current Codex App Server APIs directly.
- Do not add fallback or legacy fallback behavior for unsupported App Server
  cases unless the user explicitly asks for it.
- Before adding a new exception, check whether Codex App Server already exposes
  a current API for the behavior.
- If an exception still seems necessary, explain the tradeoff to the user before
  implementing it.
- When an exception is added, changed, or removed, update this ledger in the
  same change.
- Treat each exception as temporary unless there is a clear product reason to
  keep it independent from Codex App Server.

## Current Exceptions

New-project selection keeps the entered path in session-local draft state until
the first message creates an App Server thread. It does not create a server
thread or filesystem directory just to populate the project picker. Refreshing
the browser before the first message may require entering the path again.

`EX-010` — Canvas document persistence and live editor bridge: tldraw
snapshots and previews are stored as local files, and the live editor is
brokered over an authenticated same-origin WebSocket. A local draft ID keeps a
new canvas addressable until its first App Server turn returns a durable thread
ID. Validated Canvas images are optimized and stored as content-addressed local
assets. An image-create Dynamic Tool operation may read only a file inside the
Canvas project directory, because App Server Dynamic Tools carry structured
arguments but do not transfer asset bytes. This operation therefore requires
Nomad Surface and Codex App Server to share the project filesystem; Codex image
insertion from a separate host is not supported. A future remote-host design
would need an authenticated, bounded binary upload and asset-reference
protocol, not Base64 data inside Dynamic Tool JSON. Canvas documents and assets
are Nomad Surface product state; App Server Dynamic Tools carry Codex calls but
do not store or edit tldraw documents. Keep this layer small, continue using
the current Dynamic Tools API directly, and do not add a legacy Codex transport
fallback.

`EX-011` — Text and Document persistence and live editor bridge: CodeMirror
content and revisions are stored as local files, and the live editor is
brokered over an authenticated same-origin WebSocket. Before a user turn is
sent, Nomad Surface persists the browser's current snapshot; if that sync
cannot be confirmed, the turn is not delivered. Text content is Nomad Surface
product state. App Server Dynamic Tools carry Codex calls but do not store the
managed document. The manifest's revision pointer is the local commit point;
immutable revision files are canonical and `current.*` is a repairable
projection. Live requests belong to one WebSocket connection. Requests still
awaiting a response fail immediately when that connection disconnects or is
replaced. Agent responses, human checkpoints, and other server-side mutations
share one per-text session lease; replacement connections wait for all claimed
operations before loading their initial snapshot. The bounded timeout applies
while awaiting a browser response; once claimed, the request waits for server
processing to finish. Keep the bridge fail-closed and do not add a legacy Codex
transport fallback.

| ID       | Area                                     | Mechanism                                                                                         | Why It Exists                                                                                         | Direction                                                                                                      |
| -------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `EX-001` | Project discovery from archived sessions | Scans `~/.codex/sessions/**/*.jsonl` and merges discovered `cwd` values into the project list.    | Supplements `thread/list` project discovery with local session archives.                              | Prefer App Server thread/project data only. Remove this if App Server coverage is sufficient.                  |
| `EX-002` | Archived session `cwd` extraction        | Reads the first line of each session JSONL and extracts `payload.cwd`.                            | Supports `EX-001`.                                                                                    | Remove together with `EX-001`; do not expand JSONL parsing.                                                    |
| `EX-003` | Provider model discovery                 | Calls a configured provider `base_url + "/models"` with an optional API key from the environment. | Supplements App Server model discovery with direct provider API discovery.                            | Prefer App Server model APIs. Keep only if direct provider discovery remains an explicit product feature.      |
| `EX-004` | Local file content serving               | Serves local file bytes through the Streamlit middleware route.                                   | Allows file links shown in the app to open local content from the host.                               | Prefer an App Server file-read capability if it can satisfy the same auth and UX requirements.                 |
| `EX-005` | Chat input append bridge                 | Finds Streamlit's chat input textarea in the DOM and appends generated text.                      | Streamlit does not expose a first-class Python API for appending to the unsent `st.chat_input` draft. | Keep small and isolated until Streamlit or the app has a supported composer API.                               |
| `EX-006` | Prompt Form and starter append fallback  | Falls back to local DOM append logic when the shared chat input bridge is unavailable.            | Keeps Prompt Form and starter buttons functional if the bridge script has not initialized.            | Prefer the shared bridge. Avoid adding more independent DOM fallbacks.                                         |
| `EX-007` | Local App Server launcher                | Starts `codex app-server`, or `$CODEX_APP_SERVER_BIN app-server` when configured, as a local subprocess from the web app. | Convenience for local use when the configured App Server endpoint is localhost.                       | Keep as an explicit local convenience, not as a prompt-submission fallback. Do not add CLI execution fallback. |
| `EX-008` | File Path picker candidates              | Reads project file paths through bounded local filesystem scanning.                               | Keeps the browser candidate list small while helping users reference project files from the composer. | Prefer an App Server project-file listing API if one becomes available with equivalent performance and auth.   |
| `EX-009` | Uploaded chat image temp files           | Saves browser-uploaded chat images to temporary local files and passes their paths as App Server `local_images`. | App Server image input expects host-local paths, while Streamlit receives browser uploads as bytes.    | Prefer direct App Server upload or attachment support if it becomes available.                                |
