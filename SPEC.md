# SPEC

## Project Name

Codex Nomad Surface

## Summary

Codex Nomad Surface is a **Python + Streamlit** web app for remotely operating Codex.

Its main purpose is to make a **Codex App Server** running on a desktop or server usable from a **mobile-friendly web interface**.

It is not intended to replace the official product. It is a customizable personal Surface, meaning an outer operation layer that can be optimized for the user's own workflows.

---

## Background

Codex Nomad Surface is based on the following assumptions:

- Codex started as a coding agent and may continue evolving toward a more general-purpose AI agent.
- A broad all-in-one desktop interface can work on a PC, but the same approach tends to become cumbersome on mobile.
- On mobile, task-specific UI is often easier to use than one large generic interface.

Therefore, Codex Nomad Surface should not aim to be a universal single screen. It should be designed as a Surface with task-specific Skins that can be switched as needed.

For example, when using a scheduling-oriented Skill, it should be possible to use a Skin with calendar-oriented UI.

---

## Skin Policy

- A Skin provides UI optimized for a specific class of tasks.
- Users should be able to switch Skins while working.
- Skills and Skins do not need a strict one-to-one relationship, but compatible combinations should be easy to support.
- Skin definitions should be text based and declarative.
- Skin expression may refer to ideas from Generative UI Specs such as A2UI or Open-JSON-UI.
- Full compliance with any specific external spec is not required in the current scope.
- Codex may decide the details of the Skin structure and switching behavior during implementation.

---

## Skins And Generative UI Specs

In Codex Nomad Surface, a Skin is not just a visual theme. It is a declarative definition of an operation surface optimized for a specific task.

This idea is compatible with recent Generative UI Specs such as A2UI and Open-JSON-UI.

However, in this project, a Skin is a higher-level concept rather than those specs themselves.

The relationship is:

- **Skin**: the full task-specific operation experience.
- **A2UI / Open-JSON-UI**: possible expression formats for agent-provided declarative UI.

In other words, a Skin is a higher-level concept that can include input UI, output UI, transitions, confirmations, and context. A2UI and Open-JSON-UI are candidate technologies for implementing Skins.

The current policy is:

- Skins should be expressible as JSON or similar declarative text.
- The internal representation may resemble A2UI / Open-JSON-UI.
- This project may use a small project-specific Skin schema.
- The design should leave room for future integration with A2UI / Open-JSON-UI / AG-UI.

---

## Assumptions

- The implementation language is **Python**.
- The UI uses **Streamlit**.
- The connection target is **Codex App Server**.
- The current implementation uses **Codex App Server WebSocket RPC only**.
- CLI fallback is outside the current implementation scope.
- Codex is not designed to run directly on mobile devices. A host-side Codex instance is operated remotely through the web app.
- The main usage model is access from a mobile browser over VPN.
- VPN access alone should not grant full operation. The available operation range expands only after authentication.

---

## Goals

- Make Codex comfortable to use from a phone.
- Make the operation surface easy to customize for personal workflows.
- Support task-specific Skins.
- Keep the structure small and easy to understand.
- Avoid unnecessary growth in maintenance burden.
- Keep the app easy for Codex itself to modify.
- Prevent operation from starting before authentication, even over VPN.
- Aim for a mobile experience where biometric authentication can unlock the app quickly.

---

## Required Capabilities

- Run with Streamlit.
- Be usable on phone-sized screens.
- Display Codex connection state.
- Send prompts to Codex.
- Display results and approval-pending state.
- Handle operations that require approval on screen.
- Display user-response requests from Codex App Server even when the request
  format is not yet recognized by this app.
- Switch Skins for task-specific UI.
- Provide a minimal settings screen.
- Assume access over VPN.
- Make the operation screen effectively unavailable before authentication.
- Expand the available operation range only after authentication.
- Avoid showing connection details and work content before authentication.

---

## Screen Requirements

At minimum, the app should provide the following screens or capabilities.

Before authentication, the app may effectively expose only authentication.

- Home
- Authentication
- Project selection
- Skin selection / switching
- Prompt input
- Result display
- Approval operation
- User response operation for non-prompt interaction requests
- Settings

Codex may optimize the screen structure and UI details during implementation.

---

## Design Policy

- Leave detailed design decisions to Codex.
- Prefer a small, direct, readable structure.
- Separate the UI layer from the Codex integration layer.
- Keep the structure easy to extend later.
- Make Skins easy to add or replace.
- The internal Skin representation may be close to declarative JSON Schema.
- If the agent returns UI, it may follow A2UI / Open-JSON-UI style structures.
- Prefer implementation ease and start with a small project-specific schema if needed.
- Avoid excessive abstraction.
- Keep the configuration minimal to reduce maintenance cost.
- Personal use is acceptable.
- Clearly separate available screens and operations before and after authentication.
- Treat Codex Nomad Surface as an outer operation layer around Codex rather
  than as a replacement for the official Codex interface.
- Keep persistent app settings minimal. `settings.json` should be limited to
  stable app-level configuration such as connection settings, not temporary UI
  state or per-run behavior.
- Runtime controls for Codex execution, such as model, reasoning effort,
  reasoning summary, verbosity, approval policy, and sandbox policy, should
  prefer chat/session/turn scoped overrides over modifying Codex config files.
- Avoid hardcoding fast-changing Codex choices, especially model IDs and option
  lists. Prefer discovering available values from Codex App Server or Codex API,
  with a manual free-text fallback only when discovery is unavailable.
- Avoid UI that only restates implementation details. Redundant tabs, disabled
  fields, and explanatory rows should be removed when a short inline note
  communicates the same thing more clearly.

### App Server Interaction Handling Policy

Codex App Server may send output, approval requests, MCP elicitations, tool
suggestions, or other structured interaction requests that are not plain
assistant text. Codex Nomad Surface should treat these messages as first-class
operation-surface events, not as incidental transport details.

The long-lived policy is:

- Incoming App Server messages should be classified first by user-visible
  behavior: response-required, known output, known silent status, or unknown
  observed event. This keeps protocol drift from turning into invisible waits.
- If Codex App Server sends any assistant-side event or item, the app should
  reflect it somewhere in the UI. Unknown event types should be preserved in a
  generic output area rather than silently ignored. Item lifecycle events should
  follow the same rule at both start and completion.
- If Codex App Server sends any request that appears to require a user response
  outside the prompt body, the app should display an actionable response UI.
  The user should not be left waiting on an invisible request.
- Known request types should get purpose-built UI and response payloads when
  practical.
- When a known request type provides its own response choices, such as App
  Server `tool/requestUserInput` options or approval `availableDecisions`, the
  app should render those choices as given and send back the corresponding
  protocol-shaped answer. Do not replace explicit assistant-provided choices
  with generic labels unless the request lacks usable choices.
- When a request contains multiple questions, each question should keep its own
  visible response control. Do not collapse multi-question requests into a
  single affirmative or declining response.
- Unknown request types should still get a conservative generic UI with at
  least affirmative and declining responses, so the turn does not freeze purely
  because the app does not yet understand the newer protocol shape.
- Generic fallback handling is a safety net, not a substitute for adding
  explicit support once a request type becomes known and stable.

---

## Assistant-Provided Micro UI

Codex Nomad Surface may render small structured UI fragments proposed by the
assistant inside the chat flow.

This is distinct from the higher-level `Skin` concept.

The intended layering is:

- **Skin**: the overall task-specific operation surface
- **Assistant-provided micro UI**: localized structured affordances embedded in a
  response, such as a choice form for the next user message

The initial policy for assistant-provided micro UI is:

- It should be declarative and text-based.
- It should fail safely when the UI does not understand the payload.
- It should assist user input rather than bypass user confirmation.
- It should preserve the chat system's text-first nature.
- It should remain extensible so additional field types and interaction patterns
  can be added later.

The current mechanism supported by Codex Nomad Surface for this is Prompt Form,
where the assistant emits a structured `promptform` block and the Web UI
renders an embedded response form that generates editable draft text for the
user.

This should be treated as a pragmatic fallback mechanism, not as the project's
only long-term structured UI direction. If a better-fit existing interaction or
generative UI protocol is available for a given use case, that protocol may be
preferred.

### Chat Draft Integration Policy

For assistant-provided micro UI that helps compose the next prompt, the app
should keep `st.chat_input` as the primary draft input.

Pre-send composition state should not create a chat or Codex App Server thread.
Actions such as adding a Prompt Form, Skill picker, or File Path picker prepare
the next user turn; they should remain local composer draft state until the app
is about to submit the first turn to Codex. The current implementation may use
a temporary draft `ChatSession` as a bridge, but the cleaner long-term model is
to represent this state separately from persisted chats.

The long-lived policy is:

- Prefer keeping `st.chat_input` rather than replacing it with a custom chat
  composer.
- Prefer appending helper-generated text to the end of the current unsent draft
  instead of overwriting the draft.
- Treat custom code as a thin assist layer around Streamlit's standard chat
  input, not as a replacement for it.

The reasons are:

- In a personal, small-scale project, starting to rebuild a custom
  `st.chat_input`-equivalent main chat composer in custom code would create an
  ongoing maintenance burden that is hard to keep up with.
- Streamlit should remain responsible for the main chat-input behavior, while
  this project maintains only the auxiliary assist behavior around it.
- Appending to the current unsent draft is better for user convenience because
  it preserves in-progress text rather than discarding it.

---

## Non-Goals

The following are not required:

- Feature parity with the official Codex app
- Multi-user support
- Advanced authentication or authorization management
- Full IDE replacement
- Large-scale plugin framework
- Native iPhone app packaging
- Enterprise-scale IAM or complex authentication infrastructure
- Full A2UI / Open-JSON-UI compatibility in the current scope

---

## Implementation Guidance For Codex

Implement according to the following policy:

- Start with the smallest working structure.
- Keep the structure as minimal as possible to avoid increasing maintenance burden.
- Build a foundation that remains easy to extend.
- Use a structure that assumes Skin switching.
- Do not require strict compliance with external specs from the beginning. Start with a small and manageable project-specific expression.
- Codex may decide UI/UX details, file structure, and internal design.
- Preserve the project essence: a mobile-oriented remote operation Surface for Codex.
- Do not treat VPN access as sufficient security. Design the screen around authentication.
- Prioritize authentication on the initial screen and prevent operation until authentication is complete.
