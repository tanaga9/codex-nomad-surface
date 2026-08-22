# nomad-canvas

The packaged Streamlit Custom Component v2 source for the Nomad Surface
tldraw editor. It was generated from Streamlit's official v2 component
template and is kept as a normal React and TypeScript component project.

From `nomad_canvas/frontend`:

```sh
npm ci
npm run build
```

The standalone component build remains under `frontend/build`. To also copy it
into the Nomad Surface runtime package, run this from the repository root:

```sh
python scripts/dev.py build-component nomad-canvas
```

Generated assets are not committed. The project-level command reads
`components.toml`, builds the component, copies its complete build directory
into the application package, and verifies that the Streamlit entry globs each
match exactly one file.

The standalone `example.py` mounts the editor without a Canvas Runtime; its
connection status is therefore expected to remain disconnected.
