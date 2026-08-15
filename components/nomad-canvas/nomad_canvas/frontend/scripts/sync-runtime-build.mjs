import { cpSync, mkdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../build/", import.meta.url));
const target = fileURLToPath(
  new URL(
    "../../../../../codex_nomad_surface/ui_components/nomad_canvas/",
    import.meta.url,
  ),
);

rmSync(target, { force: true, recursive: true });
mkdirSync(target, { recursive: true });
cpSync(source, target, { recursive: true });
