---
name: codex-current-maintainer
description: Maintain Codex Nomad Surface against current OpenAI Codex behavior and official documentation. Use when asked to audit, update, or self-maintain this repository for Codex App Server, Codex config, AGENTS.md, Prompt Form, Generative UI, Streamlit compatibility, Skills, Automations, model/runtime controls, or related OpenAI developer documentation changes.
---

# Codex Current Maintainer

Use this skill to keep Codex Nomad Surface aligned with current OpenAI Codex behavior while preserving the project's design constraints.

## Sources

Check sources in this order:

1. `SPEC.md` for product design constraints.
2. `AGENTS.md` for repository working rules.
3. Official OpenAI developer docs through the `openaiDeveloperDocs` MCP server.

If OpenAI docs are unavailable, say so and avoid speculative broad changes.

## Workflow

1. Identify the exact maintenance question or drift risk.
2. Fetch the relevant current OpenAI docs before changing behavior or docs that
   depend on Codex config, Codex App Server RPC, OpenAI APIs, model behavior,
   approval behavior, sandbox behavior, or Prompt Form guidance.
3. Compare docs against the local implementation and project specification.
4. Make the smallest useful change.
5. Validate with focused checks.
6. Summarize changed files, docs consulted, verification, and remaining uncertainty.

If a behavior is not documented in official OpenAI docs, verify it through local
execution or direct observation when practical, and label the result as observed
behavior rather than documented behavior.

## Focus Areas

- Codex App Server WebSocket RPC usage.
- Codex config keys and runtime override behavior.
- Model, reasoning, verbosity, approval, and sandbox controls.
- Prompt Form protocol and embedded form rendering.
- Prompt Form as a response-body protocol, separate from interactive tools such
  as `request_user_input`.
- Generative UI compatibility with project scope, especially A2UI,
  Open-JSON-UI, AG-UI, and whether Streamlit can support relevant patterns.

## Guardrails

- Do not hardcode fast-changing Codex model or option lists when discovery is available.
- Prefer session, chat, or turn scoped runtime overrides over writing Codex config files.
- If a docs change implies a large redesign, report the mismatch first instead of rewriting broadly.
- Report before broad changes to RPC contracts, UI protocols, authentication,
  authorization, or multi-module runtime behavior.
