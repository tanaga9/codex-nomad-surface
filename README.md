# Codex Nomad Surface

Codex Nomad Surface is a small Streamlit app for remotely operating a host-side Codex App Server from a mobile browser.

## Concept

Codex Nomad Surface is not intended to replace the official Codex interface. It is a personal outer operation layer: a Surface placed between the user and a host-side Codex App Server.

Codex is moving from a coding-focused agent toward a broader general-purpose agent, while many interfaces still resemble a traditional chat or coding surface. That mismatch creates repeated input work: task shape, context, constraints, and expected output have to be described again and again. This is not only a mobile problem, but it becomes especially visible on mobile.

## Prompt Form Defs

Reusable Prompt Form definitions are stored in `promptform-defs/*.json`.
They are intended to be reused and refined over time for recurring work shapes.

The app supports two paths:

- Assistant-triggered Prompt Forms: normal `promptform` blocks embedded in assistant responses.
- User-triggered Prompt Forms: the sidebar button inserts a selector message into the chat history, then the selected definition is rendered below it.

## Features

The system is intended for personal use and includes:

- Codex App Server connection checks
- Project and chat selection inferred from Codex App Server threads
- Recent App Server thread history with lazy loading for older messages
- Prompt submission
- Result display
- Inline approval request display and response
- Reusable Prompt Form defs loaded from JSON files
- User-triggered Prompt Form insertion from the sidebar
- A minimal settings screen

There is no CLI fallback. Prompt submission is disabled when Codex App Server is not running.

## Setup

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Run

Start the Streamlit app.

```bash
NOMAD_AUTH_SECRET='your-secret' streamlit run app.py
```

If the configured Codex App Server URL points to `127.0.0.1` and the app cannot connect, the connection screen can start Codex App Server for you.

You can also start Codex App Server manually.

```bash
codex app-server --listen ws://127.0.0.1:8080
```

Set `NOMAD_AUTH_SECRET` to a real local secret before exposing the app on a network. If it is omitted, the app uses the development default `dev-secret`.

Open the Streamlit URL in a browser. After authentication, confirm the Codex App Server URL in Settings. The default App Server URL is `ws://127.0.0.1:8080`.

## Layout

- Left sidebar: project / chat selection, Prompt Form insertion, and the Settings dialog.
- Main area: chat history, inline approvals, and the bottom chat input.

## Structure

- `app.py`: Streamlit UI.
- `ui_components/`: reusable UI helpers and static assets for embedded forms and custom chat-input integrations.
- `codex_client.py`: Codex App Server WebSocket RPC connection, thread listing, history loading, prompt submission, and approval responses.
- `settings.py`: storage in `.nomad_surface/settings.json`.
- `promptform-defs/*.json`: reusable Prompt Form definitions.
- `promptform_defs.py`: loader for Prompt Form definition files.
- `pyproject.toml`: Python project metadata and runtime dependencies.

The embedded Prompt Form UI is assembled at runtime by the `ui_components`
package, which loads its static CSS/JS assets and injects them into the
Streamlit app.

## Notes

Communication with Codex App Server supports WebSocket RPC only. The Settings URL must use `ws://` or `wss://`.

`promptform` availability depends on the current Codex client rather than on a
repository by itself. Codex Nomad Surface supports it, so other repositories
can also rely on the same guidance when Codex is being used through this
client.

When Codex Nomad Surface starts Codex App Server, it checks shortly after launch whether the process is still running. If the process exits during startup, the launch status dialog shows the exit code. Codex App Server stdout and stderr are written to the web server logs.

Codex App Server processes started from the connection screen are stopped when this web server exits normally. If this web server is force-killed, the launched Codex App Server process may remain running.

When Codex updates this app's source files through the app itself, a browser reload is usually enough for Streamlit to rerun with the updated code. Restart the Streamlit process when dependencies, environment variables, or launch options change.
