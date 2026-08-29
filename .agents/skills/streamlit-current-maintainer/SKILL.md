---
name: streamlit-current-maintainer
description: Audit, modernize, and upgrade a Streamlit application against current official Streamlit documentation and releases. Use when Codex needs to inventory all Streamlit features and APIs used by a repository, assess correctness and improvement opportunities, identify the installed and latest stable Streamlit versions, compare their capabilities and breaking changes, plan or implement a Streamlit upgrade, or propose improvements using newer Streamlit features.
---

# Streamlit Current Maintainer

Keep the application aligned with current stable Streamlit while preserving its product constraints and behavior.

## Source policy

1. Read repository instructions, product specifications, dependency manifests, lockfiles, and local development notes first.
2. Use official Streamlit sources for public behavior and release facts:
   - `docs.streamlit.io` for API and conceptual documentation.
   - `docs.streamlit.io/develop/quick-reference/release-notes` for release history.
   - PyPI package metadata for the latest stable published version when needed.
   - The official `streamlit/streamlit` repository only when documentation is insufficient.
3. Treat installed-package inspection and local execution as observed behavior, not public specification.
4. State when official sources are unavailable. Do not invent version differences or API guarantees.

Always fetch current official information during an audit or upgrade. Do not rely on version facts embedded in this skill.

## Choose the task mode

- **Audit and recommend:** inspect and report; do not edit application or dependency files.
- **Upgrade and improve:** perform the audit, implement the approved or requested upgrade and focused improvements, then verify them.
- **Version comparison:** compare the repository's declared/resolved/installed version with the latest stable release without changing files.

If the user asks only to audit, review, compare, or propose, remain read-only. Treat a request to update, migrate, implement, or follow the latest version as authorization to edit within repository rules.

## Workflow

### 1. Establish the version baseline

Determine separately:

- the version constraint declared in project metadata;
- the version resolved in a lockfile, if present;
- the version installed in the active project environment, if available;
- the latest stable version published upstream.

Do not silently conflate these values. Record the Python version and relevant Streamlit extras or companion packages. Prefer the repository's existing package manager and environment.

### 2. Inventory current Streamlit usage

Search the whole repository, excluding generated and vendored directories. Capture direct imports, aliases, re-exports, dynamically accessed APIs, configuration, theme settings, CLI commands, tests, custom components, and HTML/CSS/JavaScript that depends on Streamlit's DOM or runtime behavior.

Classify every discovered use into the feature areas in [audit-checklist.md](references/audit-checklist.md). Trace important wrappers to their call sites so the inventory describes user-visible use, not only symbol counts.

Where practical, complement static inspection with focused runtime observation or existing tests. Never claim exhaustive runtime coverage from static search alone; report blind spots such as dynamic imports, unvisited UI branches, secrets-dependent paths, or unavailable environments.

### 3. Check current usage against official specification

For each material feature:

- verify the documented contract for the repository's current version when versioned docs are available;
- check current stable documentation for changed signatures, defaults, deprecations, replacements, limitations, accessibility, and mobile behavior;
- distinguish correctness issues, upgrade blockers, maintainability risks, and optional enhancements;
- cite the exact official page or release-note section supporting each version-sensitive finding.

Pay special attention to rerun semantics, session state, caching, fragments, forms, navigation, query parameters, async/thread boundaries, custom components, unsafe HTML, and DOM-dependent styling.

### 4. Build the version delta

Review every stable release after the repository's resolved version through the latest stable version. Summarize only changes relevant to the application, but record the full release range examined.

Separate:

- breaking changes and required migrations;
- deprecations and removals;
- behavior or default changes;
- dependency and Python compatibility changes;
- bug fixes likely to affect current workarounds;
- new features that can simplify code or improve the product.

Do not recommend a new feature merely because it exists. Tie it to a concrete current limitation, workaround, product requirement, or measurable maintenance benefit.

### 5. Form recommendations

Rank findings as:

- **Required:** correctness, security, compatibility, or upgrade blockers.
- **Recommended:** clear user experience, reliability, performance, accessibility, or maintenance gains.
- **Optional:** useful experiments with limited immediate payoff.

For each proposal, include evidence, affected files or feature area, expected benefit, compatibility risk, effort, and verification approach. Prefer small direct changes consistent with the repository specification over framework-wide rewrites.

Use [report-template.md](references/report-template.md) when delivering a substantial audit.

### 6. Upgrade safely when requested

1. Update the declared dependency using the repository's existing dependency policy.
2. Refresh the lockfile with the existing package manager when one is tracked.
3. Make required migrations before optional modernization.
4. Remove obsolete workarounds only after verifying the upstream fix and local behavior.
5. Implement high-value new features as focused, reviewable changes. Ask before a broad redesign or behavior change.
6. Preserve unrelated user edits and follow repository rules for generated files and git operations.

Do not add legacy fallbacks unless the user or repository policy explicitly requires them.

### 7. Verify proportionally

Run the repository's focused tests and static checks, then exercise affected Streamlit flows when practical. Include startup/import validation, stateful reruns, navigation, caching, component rendering, and mobile-sensitive UI when relevant.

Report:

- versions before and after;
- checks run and their results;
- UI paths exercised;
- untested paths and remaining uncertainty;
- follow-up work separated from completed changes.

## Guardrails

- Prefer stable releases unless the user explicitly requests a prerelease.
- Do not infer a successful upgrade from dependency resolution alone.
- Do not call Streamlit UI APIs from background threads.
- Do not replace working code solely to use a newer API; require a concrete benefit.
- Flag reliance on undocumented DOM structure or private Streamlit APIs as fragile.
- Keep observed behavior, documented behavior, and inference visibly distinct.
