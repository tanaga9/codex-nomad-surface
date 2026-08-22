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

Prepare the complete Python and frontend development environment from the
repository root:

```bash
python3 scripts/dev.py setup
```

The command creates `.venv`, runs `npm ci` and the production build for every
component in `components.toml`, synchronizes and verifies the generated assets,
and installs the Python package with test dependencies. Activate the prepared
environment, then run Streamlit directly:

```bash
. .venv/bin/activate
streamlit run codex_nomad_surface/app.py
```

Use `python3 scripts/dev.py build-components` after changing multiple frontend
components, or `python3 scripts/dev.py build-component nomad-canvas` after a
change limited to one component. Generated frontend assets are intentionally
not committed.

The macOS `run.command` and Windows `run.cmd` launchers run `build-components`
before starting Streamlit. Pass `--skip-component-build` to bypass that build
explicitly. Pass additional Streamlit arguments after `--`.

Build a wheel and source distribution with
`python3 scripts/dev.py build-package`. This command rebuilds all frontend
components, removes stale Python build output, creates fresh distributions
under `dist/`, and verifies the component entries inside both distributions.
The Python package includes the synchronized assets under
`codex_nomad_surface/ui_components/generated/`.

The project PEP 517 backend applies the same component preparation to standard
build entry points such as `python3 -m build` and `pip install .`. When building
a wheel from an sdist, it validates the runtime assets already stored in the
sdist instead of requiring Node.js or the frontend source tree. A standard
build from a source checkout requires `python3 scripts/dev.py setup` to have
installed the frontend dependencies first.

## Adding A Frontend Component

Start packaged Streamlit components from the official Custom Components v2
template. Keep each component's production build behind `npm run build`, then
add one `[[components]]` entry to `components.toml` with its frontend, build,
runtime, and entry-glob paths. Register the corresponding component and
`asset_dir` in `pyproject.toml`. The project-wide commands will then install,
build, synchronize, and verify it alongside the existing components.

Restart Streamlit when dependencies, environment variables, or launch options
change. A browser reload is usually enough for ordinary Python source edits.

When the connection screen starts local Codex App Server, it runs `codex` by
default. Set `CODEX_APP_SERVER_BIN` before starting Streamlit to use another
command name or executable path.

## Tests And Checks

Use test discovery from the repository root:

```bash
.venv/bin/python -m pytest tests
```

For a quick syntax check of touched Python files:

```bash
python3 -m py_compile codex_nomad_surface/app.py codex_nomad_surface/codex_client.py
```

Avoid plain `python3 -m pytest` for this repository unless the active Python
environment has all app dependencies installed. The repository virtual
environment should be created with `pip install -e ".[test]"`.

## UI Test Mode

Append `?test=1` to the Streamlit URL to open the isolated UI Test screen. This
mode is intended for exercising local UI flows such as approvals and
user-response controls without sending a prompt to Codex or requiring Codex App
Server to be connected.

UI Test state is kept separate from normal Codex chat state. Normal turns use
`pending_turn`; UI tests use `ui_test_pending` and `ui_test_chat`.

## Streamlit Upgrade Smoke Test

After updating Streamlit, verify these browser flows before release:

- Confirm unauthenticated users cannot view the operation screen or file links.
- In UI Test mode, exercise an approval and a multi-question user response.
- At a phone-sized viewport, append a Prompt Form, Skill, and file path to the
  native chat input, then send a message with an image attachment.
