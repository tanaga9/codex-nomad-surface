# Codex Nomad Surface

Codex Nomad Surface is a small Streamlit app for remotely operating a host-side Codex App Server from a mobile browser.

## Concept

Codex Nomad Surface is not intended to replace the official Codex interface. It is a personal outer operation layer: a Surface placed between the user and a host-side Codex App Server.

Codex is moving from a coding-focused agent toward a broader general-purpose agent, while many interfaces still resemble a traditional chat or coding surface. That mismatch creates repeated input work: task shape, context, constraints, and expected output have to be described again and again. This is not only a mobile problem, but it becomes especially visible on mobile.

## Skin Philosophy

Skins are task-specific input surfaces for reducing repetitive input. A Skin can expose quick prompts and small fields for a particular kind of work, then fold those values into the request sent to Codex.

Skins are not just visual themes, and they do not need to map one-to-one with Skills. A Skin is the user-facing operation shape; a Skill is agent-side procedure or knowledge. The initial implementation keeps Skins small and JSON based, while leaving room for richer declarative UI formats later.

## Features

The system is intended for personal use and includes:

- Codex App Server connection checks
- Project and chat selection inferred from Codex App Server threads
- Recent App Server thread history with lazy loading for older messages
- Prompt submission
- Result display
- Inline approval request display and response
- Input Assist with switchable JSON-based Skins
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

Start Codex App Server first.

```bash
codex app-server --listen ws://127.0.0.1:8080
```

Start the Streamlit app in another terminal.

```bash
NOMAD_AUTH_SECRET='your-secret' streamlit run app.py
```

Set `NOMAD_AUTH_SECRET` to a real local secret before exposing the app on a network. If it is omitted, the app uses the development default `dev-secret`.

Open the Streamlit URL in a browser. After authentication, confirm the Codex App Server URL in Settings. The default App Server URL is `ws://127.0.0.1:8080`.

## Layout

- Left sidebar: project / chat selection, Input Assist, and the Settings dialog.
- Main area: chat history, inline approvals, and the bottom chat input.

## Structure

- `app.py`: Streamlit UI.
- `codex_client.py`: Codex App Server WebSocket RPC connection, thread listing, history loading, prompt submission, and approval responses.
- `settings.py`: storage in `.nomad_surface/settings.json`.
- `skins/*.json`: Skin definitions for task-specific UI.
- `pyproject.toml`: Python project metadata and runtime dependencies.

## Notes

Communication with Codex App Server supports WebSocket RPC only. The Settings URL must use `ws://` or `wss://`.

When Codex updates this app's source files through the app itself, a browser reload is usually enough for Streamlit to rerun with the updated code. Restart the Streamlit process when dependencies, environment variables, or launch options change.
