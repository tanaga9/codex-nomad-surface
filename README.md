# Codex Nomad Surface

Codex Nomad Surface is a small Streamlit app for remotely operating a host-side Codex App Server from a mobile browser.

## Concept

Codex Nomad Surface is not intended to replace the official Codex interface. It is a personal outer operation layer: a Surface placed between the user and a host-side Codex App Server.

Codex is moving from a coding-focused agent toward a broader general-purpose agent, while many interfaces still resemble a traditional chat or coding surface. That mismatch creates repeated input work: task shape, context, constraints, and expected output have to be described again and again. This is not only a mobile problem, but it becomes especially visible on mobile.

## Prompt Form Defs

Reusable Prompt Form definitions are stored in `promptform-defs/*.json`.
They are intended to be reused and refined over time for recurring work shapes.
The Add Prompt Form picker lists `promptform-defs/` forms in this order:
project-specific forms from the selected project first, then shared general
forms.

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
- Generic display and response handling for App Server requests that require
  user input outside the prompt body
- Reusable Prompt Form defs loaded from JSON files
- User-triggered Prompt Form insertion from the sidebar
- User-triggered Skill picker insertion from the sidebar
- User-triggered File Path picker insertion from the sidebar
- A minimal settings screen

There is no CLI fallback. Prompt submission is disabled when Codex App Server is not running.

## Setup

Python 3.12 or newer is required.
Node.js 22.12.0 or newer and npm are required to build the frontend
components from source. Installing a wheel or sdist does not require Node.js.

macOS / Linux:

```bash
python3 scripts/dev.py setup
```

Windows PowerShell:

```powershell
py -3.12 scripts/dev.py setup
```

The setup command creates `.venv`, restores each registered frontend
component's locked npm dependencies, builds and verifies its assets, and
installs the Python project with its test dependencies. Component definitions
are kept in `components.toml`.

After changing a frontend component, rebuild all components or one named
component:

```bash
python scripts/dev.py build-components
python scripts/dev.py build-component nomad-canvas
```

Build clean, verified Python distributions under `dist/` with:

```bash
python scripts/dev.py build-package
```

GitHub Actions also builds and verifies the wheel for pull requests, updates
to `main`, and manual workflow runs. Download the resulting `.whl` from the
workflow run's artifacts.

The wheel includes the bundled frontend dependencies and their required
license notices. Project code is MIT-licensed; bundled dependencies remain
under the notices in `components/licenses/`, including the separate tldraw
license. During packaging, these notices are copied into
`codex_nomad_surface/licenses/` inside the wheel. A tldraw production license
key is not embedded in the wheel and is a deployment-time concern. After
changing frontend dependencies, optionally run
`python scripts/generate_third_party_licenses.py` to refresh the notices.

Standard PEP 517 builds, including `python -m build` and `pip install .`, also
prepare or validate the registered frontend components automatically. Source
distributions include the generated runtime assets, so installing from an
sdist does not require Node.js. Standard builds from a source checkout require
the development setup to have installed the frontend dependencies first.

The macOS `run.command` and Windows `run.cmd` launchers rebuild all frontend
components before starting the app. Skip that step only when explicitly
requested:

macOS:

```bash
./run.command --skip-component-build
```

Windows:

```bat
run.cmd --skip-component-build
```

## Run

Start the Streamlit app.

macOS / Linux:

```bash
. .venv/bin/activate
export NOMAD_AUTH_SECRET='your-secret'
streamlit run codex_nomad_surface/app.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
$env:NOMAD_AUTH_SECRET = "your-secret"
$env:CODEX_APP_SERVER_BIN = "$env:APPDATA\npm\codex.cmd"
streamlit run codex_nomad_surface/app.py
```

If the configured Codex App Server URL points to `127.0.0.1` and the app cannot connect, the connection screen can start Codex App Server for you.
The launcher includes an optional OpenAI API key field. If filled, the key is
passed only to the launched Codex App Server process as `OPENAI_API_KEY`.
By default, the launcher runs `codex app-server`. Set
`CODEX_APP_SERVER_BIN` before starting Streamlit to use another command name or
executable path in place of `codex`.
On Windows with npm-installed Codex, use the command wrapper such as
`/path/to/codex.cmd` rather than the PowerShell wrapper `codex.ps1`.

You can also start Codex App Server manually.

```bash
codex app-server --listen ws://127.0.0.1:8080
```

Set `NOMAD_AUTH_SECRET` to a real local secret before exposing the app on a network. If it is omitted, the app uses the development default `dev-secret`.

Set `NOMAD_AUTH_DUMMY_USERNAME_FIELD=1` to show a fixed, dummy username
(`codex`) field on the login page for browsers or password managers that only
autofill username / password pairs. The username value is ignored by
authentication.

Open the Streamlit URL in a browser. After authentication, confirm the Codex App Server URL in Settings. The default App Server URL is `ws://127.0.0.1:8080`.

Authentication uses a signed browser cookie that persists for 180 days by
default, including across app-process restarts, provided `NOMAD_AUTH_SECRET`
remains the same. Set `NOMAD_AUTH_SESSION_DAYS` to a value from 1 to 365 to
shorten that period. Failed password attempts are rate-limited in memory: five
failures from the same client within one minute temporarily block login for one
minute.

## Layout

- Left sidebar: project / chat selection, Prompt Form, Skill, and File Path insertion, and the Settings dialog.
- Main area: chat history, inline approvals or other user-response requests,
  and the bottom chat input.

## Structure

- `codex_nomad_surface/app.py`: Streamlit UI.
- `codex_nomad_surface/ui_components/`: reusable UI helpers and static assets for embedded forms and custom chat-input integrations.
- `codex_nomad_surface/codex_client.py`: Codex App Server WebSocket RPC connection, thread listing, history loading, prompt submission, and approval or user-response requests.
- `codex_nomad_surface/settings.py`: storage in `.nomad_surface/settings.json`.
- `promptform-defs/*.json`: reusable Prompt Form definitions for this project or shared general forms.
- `codex_nomad_surface/promptform_defs.py`: loader for Prompt Form definition files.
- `.agents/skills/`: Skills that maintain this repository's implementation.
- `plugins/nomad-surface/skills/`: project-independent Skills distributed with
  the Nomad Surface plugin.
- `codex_nomad_surface/skill_defs.py`: adapter for Skill metadata returned by
  Codex App Server.
- `components.toml`: registry of buildable frontend components and their
  generated-asset destinations.
- `scripts/dev.py`: project-wide setup, component build, and verification
  commands.
- `pyproject.toml`: Python project metadata and runtime dependencies.

The embedded Prompt Form UI is assembled at runtime by the `ui_components`
package, which loads its static CSS/JS assets and injects them into the
Streamlit app.

## Notes

Communication with Codex App Server supports WebSocket RPC only. The Settings URL must use `ws://` or `wss://`.

Codex Nomad Surface should not silently drop assistant-side App Server events.
Known response requests, such as approvals and MCP elicitations, are rendered
with specific inline controls. Unknown App Server requests that carry a JSON-RPC
`id` and `method` are still shown with a generic response UI so the user can
answer instead of leaving the turn blocked on an invisible prompt.

When the Settings Codex App Server URL host is exactly `127.0.0.1`, HTTP paths
that look like absolute host file paths are served as local file previews. For
example, `/path/to/file.py:67` reads `/path/to/file.py` and treats `67` as a
line number. Windows drive-letter links are converted to an authenticated,
same-origin preview URL before the browser sees them, so Codex can use its
normal absolute-path Markdown links without browser-specific handling. File
previews require the same signed authentication cookie as the
main app. File contents are returned directly, and directory paths return an
empty response. This exposes files readable by the web server process and is
intended for local-host operation only.

`promptform` availability depends on the current Codex client rather than on a
repository by itself. Codex Nomad Surface supports it, so other repositories
can also rely on the same guidance when Codex is being used through this
client.

When Codex Nomad Surface starts Codex App Server, it checks shortly after launch whether the process is still running. If the process exits during startup, the launch status dialog shows the exit code. Codex App Server stdout and stderr are written to the web server logs.

Codex App Server processes started from the connection screen are stopped when this web server exits normally. If this web server is force-killed, the launched Codex App Server process may remain running.

When Codex updates this app's source files through the app itself, a browser reload is usually enough for Streamlit to rerun with the updated code. Restart the Streamlit process when dependencies, environment variables, or launch options change.
