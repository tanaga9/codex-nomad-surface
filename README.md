# Codex Nomad Surface

Codex Nomad Surface is a small Streamlit app for remotely operating a host-side Codex App Server from a mobile browser.

The system is intended for personal use and includes:

- Codex App Server connection checks
- Project selection inferred from Codex App Server threads
- Codex App Server thread listing and project inference
- Recent App Server thread history with lazy loading for older messages
- Prompt submission
- Result display
- Inline approval request display and response
- Project / chat selection in the left sidebar
- A minimal settings screen

There is no CLI fallback. Prompt submission is disabled when Codex App Server is not running.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

## Run

Start Codex App Server first.

```bash
codex app-server --listen ws://127.0.0.1:8080
```

Start the Streamlit app in another terminal.

```bash
NOMAD_AUTH_SECRET='your-secret' streamlit run app.py
```

Open the Streamlit URL in a browser. After authentication, confirm the Codex App Server URL in Settings. The default is `ws://127.0.0.1:8080`.

## Layout

- Left sidebar: project / chat selection, Input Assist, and the Settings dialog.
- Main area: chat history, inline approvals, and the bottom `st.chat_input`.

## Structure

- `app.py`: Streamlit UI.
- `codex_client.py`: Codex App Server WebSocket RPC connection, thread listing, history loading, prompt submission, and approval responses.
- `settings.py`: storage in `.nomad_surface/settings.json`.
- `skins/*.json`: Skin definitions for task-specific UI.
- `pyproject.toml`: Python project metadata and runtime dependencies.

## Notes

Communication with Codex App Server supports WebSocket RPC only. The Settings URL must use `ws://` or `wss://`.

When Codex updates this app's source files through the app itself, a browser reload is usually enough for Streamlit to rerun with the updated code. Restart the Streamlit process when dependencies, environment variables, or launch options change.
