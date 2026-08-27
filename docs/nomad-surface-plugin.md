# Nomad Surface Plugin

## Initial Scope

The first release contains one skill: `mindmap`.

Its purpose is deliberately narrow: help Codex operate the embedded Nomad
Canvas reliably for one concrete visual workflow: creating and revising mind
maps. The skill may use Nomad Canvas concepts directly, while Canvas-wide
operating rules remain outside the plugin.

The plugin complements the Canvas runtime and the short Canvas-scoped developer
message. It does not replace either one.

## Architecture

```text
Nomad Surface
├── Canvas runtime
│   ├── canvas.read_scene
│   ├── canvas.apply_patch
│   └── canvas.export
├── Canvas-scoped developer message
│   └── mandatory interaction rules
└── bundled nomad-surface plugin
    └── mindmap skill for Nomad Canvas
```

- The runtime owns authorization, persistence, revisions, validation, and
  deterministic document operations. Dynamic Tool schemas are the authoritative
  source for detailed capabilities and arguments.
- The developer message carries a short set of rules that must apply to every
  Canvas turn.
- The plugin carries focused operating guidance that can be loaded when a task
  involves a mind map in the embedded Canvas.

Do not duplicate the Canvas Dynamic Tools in a plugin MCP server. Add another
runtime or UI capability only when the existing Canvas boundary cannot support
a demonstrated use case.

## Repository Layout

```text
.agents/plugins/marketplace.json
plugins/nomad-surface/
├── .codex-plugin/
│   └── plugin.json
└── skills/
    └── mindmap/
        ├── SKILL.md
        ├── agents/openai.yaml
        └── references/mindmap-basics.md
```

## `mindmap`

The skill provides Tony Buzan-style Mind Map guidance for Nomad Canvas. It is
organized around the five laws described by Tony Buzan Training: branches,
keywords, colour, pictures, and structure.

The skill is scoped to tasks that expose Nomad Canvas Dynamic Tools. If those
tools are unavailable, it explains that a Canvas Skin task is required and
does not switch to an external or offline canvas.

- represent the subject with a strong central image;
- radiate connected, organic branches from the center;
- place one keyword or key image on each branch;
- use color throughout to reinforce branch associations;
- use pictures and symbols as part of the thinking vocabulary;
- maintain a connected radial hierarchy and show cross-associations when useful.

It can use Nomad Canvas concepts directly and does not need to be written as a
portable, tool-neutral guide. For a new map, the skill uses built-in ImageGen
for the central illustration unless it is unavailable, cannot produce a
Canvas-accessible asset, or the user requests otherwise. When revising a map, it
preserves a suitable existing central image and generates one only when the
image is missing or unsuitable, or when the user requests a replacement.
Important labels remain editable Canvas text and may sit below the illustration.
Selected branch images remain optional.

The combined flow is:

```text
structure the mind map -> read Canvas -> apply bounded patch -> inspect result
```

The plugin does not contain a copy of the Canvas tool contract. Canvas-wide
operating invariants are injected when the Canvas Skin starts, while detailed
capabilities and argument shapes remain in the current Dynamic Tool schemas.

## Deferred Skills

Do not include separate generic diagram-design or critique-and-repair skills in
the first release. Their responsibilities are broad and not yet supported by
enough Nomad Surface usage evidence. Essential visual checks remain small
references inside the initial skill.

Add a new skill only when all of the following are true:

1. A distinct task recurs in real use.
2. The task needs guidance beyond the existing `mindmap` skill.
3. The guidance is stable enough to test and maintain.
4. A separate trigger would load it more precisely than extending the existing
   skill.

Until then, improve the existing skill or its references with narrowly verified
guidance.

## Distribution and Installation

The repository exposes the plugin through:

```text
.agents/plugins/marketplace.json
```

When this repository is the active project, ChatGPT desktop reads the repo
marketplace directly. The user does not need to run
`codex plugin marketplace add .` for the desktop flow. CLI-configured
marketplaces are a separate source list.

The desktop installation flow is:

1. Restart ChatGPT desktop after the marketplace or plugin is first added.
2. Open the Plugins Directory and choose **Nomad Surface Local**.
3. Install **Nomad Surface**.
4. Start a new task so the installed plugin contents are loaded.

Register the marketplace explicitly only when making it available to the Codex
CLI or another flow that does not use ChatGPT desktop's repo discovery:

```sh
codex plugin marketplace add /path/to/codex-nomad-surface
```

## Development Updates

Keep the plugin source beside Nomad Surface and review both in the same change.
A strict Nomad Surface-to-plugin compatibility table is unnecessary while this
is a single-person project with a loosely versioned application.

During development:

1. Edit the plugin source under `plugins/nomad-surface/`.
2. Validate the manifest and the skill.
3. Reinstall or refresh the plugin only when testing the installed copy.
4. Start a new task before judging changed skill behavior.

Use the manifest version for meaningful releases. For repeated local
install-and-test cycles where the host caches the same version, use the
plugin-development cachebuster and reinstall flow rather than inventing a new
public version for every edit.

## Initial Validation

Before expanding the plugin, verify these cases:

- create a small mind map using current Canvas Dynamic Tools;
- revise an existing mind map without replacing unrelated content;
- inspect the changed area and respond correctly to revision or semantic-lint
  issues;
- avoid exporting or changing external files unless requested.

Evaluate failures before adding more instructions. A runtime limitation belongs
in the runtime; a universal invariant belongs in the developer message; only
reusable task guidance belongs in the plugin.

## Related Documentation

- [Product specification](../SPEC.md)
- [Canvas architecture](canvas-skin-architecture.md)
- [Documentation map](README.md)
