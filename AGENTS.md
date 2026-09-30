# Project Preferences

When working in this repository, preserve these user preferences unless the user explicitly overrides them:

- Do not run git operations that modify repository state unless the user explicitly asks for them. Read-only git inspection such as `git status` and `git diff` is allowed.
- Follow `SPEC.md` for product design constraints.
- Keep documentation concise and avoid overfitting it to recent events.
- Use generic placeholder paths in documentation, such as `/path/to/...`, instead of real local usernames or machine-specific paths when the path is only illustrative.
- Once per session, read `docs/README.md` before the first development command so local environment and testing notes are not missed.
- Prefer current Codex App Server APIs directly. Do not add fallback or legacy fallback behavior for cases the current App Server API does not support unless the user explicitly asks for it. If a fallback seems worth recommending, explain the tradeoff and ask the user before implementing it. Maintain `docs/app-server-api-exceptions.md` when local state, external APIs, or indirect mechanisms are used instead of current Codex App Server APIs.

## Git Diff Workflow

The user reviews AI-generated edits before staging them. In this repository,
staged changes are user-reviewed candidate changes, while unstaged changes are
in-progress agent edits.

Use `git diff --cached` for staged-review or commit-message requests. Use
`git diff` for current unstaged edits. If both exist, clearly distinguish them.
Do not stage, unstage, revert, or commit unless explicitly asked.

## Codex Compatibility Reviews

Read `docs/codex-compatibility.md` before checking for Codex specification drift.
Use its reviewed version and scope as the starting point, preserve intentional
non-adoption decisions, and revisit open checks. Update the checkpoint after a
review or live-use verification, keeping documentation review, automated tests,
and actual App Server use distinct. Record newly confirmed non-adoption decisions
there rather than repeatedly reporting them as missing features.

## Bundled Skills

- Keep Skills that inspect or modify this repository in `.agents/skills/`.
- Keep project-independent plugin Skills in `plugins/nomad-surface/skills/`.
  They may depend on runtime capabilities, but not on this repository checkout.


# Prompt Templates

Reusable project prompts live in `ops/prompts/*.md`. Follow
`docs/prompt-templates.md` when creating or editing them. Use `{{input.name}}`
for variable values and optional YAML front matter for input metadata.

The app does not render assistant-emitted `promptform` blocks as forms.
Use available purpose-built interaction tools for structured questions, or
ask in ordinary prose. Templates compose user drafts and do not replace
App Server questions or approval requests.
