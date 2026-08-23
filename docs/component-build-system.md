# Component Build System

## Decision

Frontend components such as `nomad-canvas` and `nomad-text` remain
internal to Codex Nomad Surface. They are developed in separate source
directories but distributed together in one Python package.

Generated frontend assets are not committed. The custom PEP 517 backend in
`nomad_build_backend.py` ensures that normal Python build entry points cannot
silently produce a package without the registered components.

## Sources Of Truth

- `components.toml`: frontend source, build output, runtime destination, and
  entry-file patterns for every component.
- Root `pyproject.toml`: Python package metadata and Streamlit component names.
- `scripts/dev.py`: developer-facing setup, component build, and verified
  package build commands.
- `nomad_build_backend.py`: integration with `pip` and other PEP 517 tools.

Do not edit or commit the generated
`codex_nomad_surface/pyproject.toml` or
`codex_nomad_surface/ui_components/generated/` files.

## Build Flow

- A source-checkout build requires Node.js 22.12.0 or newer and npm, rebuilds
  every registered component, and packages the synchronized runtime assets.
- An sdist contains verified runtime assets but not frontend sources. Building
  its wheel therefore does not require Node.js.
- `run.command` and `run.cmd` rebuild components before starting Streamlit
  unless `--skip-component-build` is specified.

Use `python scripts/dev.py setup` for initial setup and
`python scripts/dev.py build-package` to create and verify distributions.

## Adding A Component

Create the component from the Streamlit Custom Components v2 template, keep
its production build behind `npm run build`, and then:

1. Add one entry to `components.toml`.
2. Add its unique Streamlit name and `asset_dir` to the root `pyproject.toml`.
3. Run setup, the full test suite, and `build-package`.

Keep this single-package architecture unless component packages are
intentionally made independently versioned or reusable outside Codex Nomad
Surface.
