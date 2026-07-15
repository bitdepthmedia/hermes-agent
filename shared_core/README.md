# Bert & Ernie Shared Core

`shared_core` is the local source of truth for cross-system task coordination.

- It binds only to loopback when exposed over HTTP.
- It records task, worker, handoff, workflow, policy, and audit state in SQLite WAL mode.
- Handoffs persist only sanitized content; audit records contain finding categories, never source text.
- A workflow auto-activates only after three completed matching tasks across two sessions and only when every action is read-only or reversible.
- Proposed privacy rules remain inactive until reviewed and approved.

The gateway adapter is deliberately shadow-only. It records routing decisions without changing Telegram delivery until an explicit activation and end-to-end review.
