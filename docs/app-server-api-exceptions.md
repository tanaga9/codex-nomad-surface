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

| ID       | Area                                     | Mechanism                                                                                         | Why It Exists                                                                                         | Direction                                                                                                      |
| -------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `EX-001` | Project discovery from archived sessions | Scans `~/.codex/sessions/**/*.jsonl` and merges discovered `cwd` values into the project list.    | Supplements `thread/list` project discovery with local session archives.                              | Prefer App Server thread/project data only. Remove this if App Server coverage is sufficient.                  |
| `EX-002` | Archived session `cwd` extraction        | Reads the first line of each session JSONL and extracts `payload.cwd`.                            | Supports `EX-001`.                                                                                    | Remove together with `EX-001`; do not expand JSONL parsing.                                                    |
| `EX-003` | Provider model discovery                 | Calls a configured provider `base_url + "/models"` with an optional API key from the environment. | Supplements App Server model discovery with direct provider API discovery.                            | Prefer App Server model APIs. Keep only if direct provider discovery remains an explicit product feature.      |
| `EX-004` | Local file content serving               | Serves local file bytes through the Streamlit middleware route.                                   | Allows file links shown in the app to open local content from the host.                               | Prefer an App Server file-read capability if it can satisfy the same auth and UX requirements.                 |
| `EX-005` | Chat input append bridge                 | Finds Streamlit's chat input textarea in the DOM and appends generated text.                      | Streamlit does not expose a first-class Python API for appending to the unsent `st.chat_input` draft. | Keep small and isolated until Streamlit or the app has a supported composer API.                               |
| `EX-006` | Prompt Form and starter append fallback  | Falls back to local DOM append logic when the shared chat input bridge is unavailable.            | Keeps Prompt Form and starter buttons functional if the bridge script has not initialized.            | Prefer the shared bridge. Avoid adding more independent DOM fallbacks.                                         |
| `EX-007` | Local App Server launcher                | Starts `codex app-server` as a local subprocess from the web app.                                 | Convenience for local use when the configured App Server endpoint is localhost.                       | Keep as an explicit local convenience, not as a prompt-submission fallback. Do not add CLI execution fallback. |

