---
name: tldraw-current-maintainer
description: Audit and maintain Codex Nomad Surface against the current stable tldraw SDK, official tldraw documentation, release notes, migrations, schemas, editor APIs, rendering behavior, and licensing. Use for tldraw dependency reviews, upgrade planning or implementation, Canvas SDK compatibility audits, and proposals that depend on current tldraw capabilities. Do not use for ordinary canvas content editing.
---

# tldraw Current Maintainer

Keep the embedded Nomad Canvas aligned with the current tldraw SDK while
preserving the repository's product constraints and file-backed Canvas
architecture. Do not rely on a tldraw version recorded in this skill; discover
the installed and current versions on every maintenance run.

## Establish the maintenance target

Distinguish these tasks before acting:

- **Audit:** inspect and report drift without changing dependencies or code.
- **Stable update:** target the current stable `tldraw` release. This is the
  default when the user asks for the latest or current SDK.
- **Prerelease evaluation:** inspect `next`, beta, or canary only when the user
  explicitly requests prerelease capabilities. Never silently replace the
  stable target with a prerelease.

An audit does not authorize an update. Report material breaking changes,
license implications, architectural changes, or new runtime services before
implementing them.

## Source order

Use current primary sources rather than memory or search snippets:

1. Read `SPEC.md`, `AGENTS.md`, and `docs/canvas-skin-architecture.md` for local
   constraints.
2. Determine the declared and locked versions from
   `components/nomad-canvas/nomad_canvas/frontend/package.json` and
   `components/nomad-canvas/nomad_canvas/frontend/package-lock.json`. Inspect
   imports and SDK use in
   `components/nomad-canvas/nomad_canvas/frontend/src/NomadCanvas.tsx` and
   related Canvas modules.
3. When dependencies are installed, compare the version in
   `components/nomad-canvas/nomad_canvas/frontend/node_modules/tldraw/package.json`
   with the locked version. Only when they match, prefer the package-local
   `DOCS.md` and `RELEASE_NOTES.md`; otherwise treat those installed docs as
   stale and use fetched official documentation for the relevant versions.
4. Fetch the current stable version from the npm registry and open the official
   tldraw release notes and relevant API documentation on `tldraw.dev` or the
   official `tldraw/tldraw` GitHub repository. Use `tldraw.dev/llms.txt` for
   discovery when useful, but open the underlying relevant pages before making
   claims.
5. For an update, read every release note and migration section after the
   locked version through the target version. Consult official examples or
   source only when the public API documentation does not settle the question.

Treat registry metadata as version evidence, release notes as change evidence,
and API documentation or shipped type declarations as API-contract evidence.
Label conclusions based only on local execution as observed behavior.

## Versioning and compatibility rules

- tldraw does not promise conventional semantic-versioning safety. Minor
  releases may contain breaking changes, so never infer compatibility from the
  version number alone.
- Keep the `tldraw` package and directly installed `@tldraw/*` packages on a
  compatible aligned release. Check the resolved lockfile, not only version
  ranges in `package.json`.
- Review Node, React, TypeScript, bundler, browser, and license requirements for
  the target release before proposing an update.
- Prefer documented public Editor, schema, snapshot, export, binding, geometry,
  and migration APIs. Do not deepen dependencies on undocumented internals to
  avoid a documented migration.
- Preserve existing tldraw store migrations. Never rewrite saved Canvas
  snapshots as a blind JSON transformation when the SDK provides a supported
  migration path.
- Do not add a sync server, tldraw Desktop dependency, legacy compatibility
  path, or document-script execution unless the user explicitly expands the
  architecture.

## Audit workflow

1. Record the declared range, locked version, current stable version, and the
   exact date and sources checked.
2. Inventory the tldraw APIs and record shapes used by the Canvas component,
   especially snapshots, rich text, bindings, export, page bounds, transactions,
   undo behavior, and custom component integration.
3. Compare all intervening release notes with that inventory. Separate:
   breaking changes, deprecated APIs, behavior changes, new capabilities,
   performance or security fixes, and licensing changes.
4. Check whether repository documentation claims match the implementation and
   current SDK behavior. Distinguish implemented prototype behavior from the
   intended architecture.
5. Rank findings by user impact and upgrade risk. Recommend the smallest useful
   change, and state when no change is needed.

For Canvas feature proposals, first determine whether tldraw already exposes a
supported primitive. In particular, verify current support for shape and
binding validation, frames, pages, groups, z-order, text measurement, geometry,
layout helpers, arrow routing, partial exports, and store diffs before designing
Nomad-specific equivalents.

## Update workflow

Only when implementation is requested:

1. Update the dependency and lockfile through npm from
   `components/nomad-canvas/nomad_canvas/frontend`; do not hand-edit resolved
   dependency entries.
2. Apply the documented migrations and the smallest required source changes.
   Preserve the current CCv2 boundary, authenticated WebSocket transport,
   file-backed persistence, real arrow bindings, existing logical-reference
   metadata and same-request ref mappings, and bounded visual exports unless the
   user requests an architectural change.
3. Rebuild through the project's existing frontend build so the packaged
   component assets and runtime copy stay synchronized.
4. Run frontend type checking and build validation, then the focused Python
   Canvas tests. Add behavior-level coverage when an SDK change affects patch
   atomicity, bindings, snapshots, migrations, exports, text geometry, or undo.
5. Inspect generated dependency and build changes separately from authored
   source changes. Do not stage or commit them unless explicitly requested.

## Reporting

Report:

- installed, locked, and target versions;
- official sources and release range reviewed;
- relevant breaking changes and newly available capabilities;
- affected local files and saved-document compatibility;
- validation performed and remaining uncertainty;
- license or production-attribution issues separately from technical issues.

Do not call an upgrade safe solely because type checking passes. Visual export,
text layout, arrow bindings, undo behavior, and loading an existing saved Canvas
are runtime behaviors that may need direct verification.
