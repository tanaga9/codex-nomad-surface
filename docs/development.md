# Development Notes

## Local Environment

For local development, assume the repository root may contain a prepared
`.env` file with useful environment variables for this app and related local
services.

Do not commit `.env`, and do not copy secret values into documentation or test
output. Treat it as a local convenience layer.

Load it before running commands that depend on local configuration:

```bash
set -a
. ./.env
set +a
```

If a command behaves differently from the app you run in a browser, first check
whether the same `.env` has been loaded in that shell.

## Running The App

After installing the package into the virtual environment, this is usually
enough:

```bash
.venv/bin/streamlit run codex_nomad_surface/app.py
```

When running directly from a checkout that has not been installed into the
active environment, include the repository root on `PYTHONPATH`:

```bash
PYTHONPATH=. .venv/bin/streamlit run codex_nomad_surface/app.py
```

Restart Streamlit when dependencies, environment variables, or launch options
change. A browser reload is usually enough for ordinary Python source edits.

## Tests And Checks

Use test discovery from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests
```

For a quick syntax check of touched Python files:

```bash
python3 -m py_compile codex_nomad_surface/app.py codex_nomad_surface/codex_client.py
```

Avoid plain `python3 -m unittest` for this repository unless the active Python
environment has all app dependencies installed. Test discovery can otherwise
attempt to import application packages outside `tests/` and fail before running
the intended test suite.

## UI Test Mode

Append `?test=1` to the Streamlit URL to open the isolated UI Test screen. This
mode is intended for exercising local UI flows such as approvals and
user-response controls without sending a prompt to Codex or requiring Codex App
Server to be connected.

UI Test state is kept separate from normal Codex chat state. Normal turns use
`pending_turn`; UI tests use `ui_test_pending` and `ui_test_chat`.
