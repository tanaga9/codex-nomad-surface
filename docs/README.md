# Documentation Map

This directory is organized by document role rather than by feature chronology.

## Top-Level Specification

- [SPEC.md](../SPEC.md)
  - Product-level specification and long-lived design constraints.

- [AGENTS.md](../AGENTS.md)
  - Agent-facing instructions for repository-specific output protocols.

## Protocols

- [protocols/codex-form.md](protocols/codex-form.md)
  - Agent-facing behavior notes for `codex-form`.

- [protocols/codex-form.schema.json](protocols/codex-form.schema.json)
  - Machine-readable schema for the current `codex-form` protocol.

## External References

- [OpenAI Codex docs](https://platform.openai.com/docs/codex)
  - High-level Codex product documentation. Check here first for current Codex capabilities and terminology.

- [OpenAI Code generation guide](https://platform.openai.com/docs/guides/code-generation)
  - General coding-model guidance and current Codex positioning across interfaces.

- [OpenAI Projects API reference](https://platform.openai.com/docs/api-reference/projects)
  - Official `Projects` API for organization-level OpenAI projects. This is not the same thing as local repo / `cwd` project selection in this app.

- [OpenAI Docs MCP](https://platform.openai.com/docs/docs-mcp)
  - Official way to search OpenAI developer docs from Codex or other MCP-aware tools when a fresh doc check is needed.

- [OpenAI Local shell guide](https://platform.openai.com/docs/guides/tools-local-shell)
  - Relevant background for local-agent tooling patterns related to Codex CLI style execution.
