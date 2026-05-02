# Documentation Map

This directory is organized by document role rather than by feature chronology.

## Top-Level Specification

- [SPEC.md](../SPEC.md)
  - Product-level specification and long-lived design constraints.

- [AGENTS.md](../AGENTS.md)
  - Agent-facing instructions for repository-specific output protocols.

- [app-server-api-exceptions.md](app-server-api-exceptions.md)
  - Management ledger and policy for implementations that use local state,
    external APIs, or indirect mechanisms instead of current Codex App Server
    APIs.

## Protocols

- [protocols/promptform.md](protocols/promptform.md)
  - Agent-facing behavior notes for Prompt Form, an embedded structured-input fallback available in supporting clients such as Codex Nomad Surface.

- [protocols/promptform.schema.json](protocols/promptform.schema.json)
  - Machine-readable schema for the current `promptform` protocol.

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

- [Streamlit multithreading guide](https://docs.streamlit.io/develop/concepts/design/multithreading)
  - Reference for Streamlit's threading model and the tradeoff between
    concurrency for I/O-bound work and multiprocessing for compute-bound work.
