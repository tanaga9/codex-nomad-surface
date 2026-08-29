# Streamlit Audit Checklist

Use these areas to organize repository-wide discovery. Mark each area as used, not found, or not assessable, and attach file locations for material uses.

## Application structure

- Entrypoints and CLI launch options
- Page configuration, navigation, multipage apps, and dialogs
- Reruns, fragments, forms, callbacks, placeholders, and containers
- Session state initialization, ownership, serialization, and cleanup

## Data and execution

- Data/resource caching, invalidation, TTLs, hashing, and mutation boundaries
- Connections, secrets, uploads, downloads, and filesystem access
- Async I/O, threads, background work, polling, and execution-context handling
- Error handling, status displays, progress, logging, and diagnostics

## Interface

- Text, data, chart, media, metric, layout, and input APIs
- Chat elements, streaming output, feedback, and user-response controls
- Themes, configuration, accessibility, responsiveness, and mobile behavior
- HTML/CSS injection and selectors coupled to undocumented DOM structure

## Extension and operations

- Custom components, component protocol/version, frontend assets, and build tooling
- Authentication, authorization, cookies, headers, proxy/base URL, and CORS/XSRF settings
- Server configuration, health checks, telemetry, deployment, and packaging
- Testing APIs, app testing, snapshots, and browser-level coverage

## Evidence quality

- Direct symbol use found statically
- Use through a local wrapper or alias
- Configuration-only behavior
- Runtime-observed behavior
- Potential blind spot requiring manual exercise
