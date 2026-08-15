# nomad-canvas

The packaged Streamlit Custom Component v2 source for the Nomad Surface
tldraw editor. It was generated from Streamlit's official v2 component
template and is kept as a normal React and TypeScript component project.

From `nomad_canvas/frontend`:

```sh
npm install
npm run typecheck
npm run build
```

The build remains under `frontend/build` for standalone component development
and is also copied into Nomad Surface's Python package asset directory. The
application registers that copied build as `codex-nomad-surface.nomad_canvas`.

`frontend/build` is currently committed because it is the component package's
declared `asset_dir`, and no release hook guarantees a frontend build before
packaging. It may be gitignored later only after CI always runs `npm run build`
before packaging and verifies that the component asset globs each match one
generated file.

The standalone `example.py` mounts the editor without a Canvas Runtime; its
connection status is therefore expected to remain disconnected.
