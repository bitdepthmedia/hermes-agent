# Hermes Legacy Customization and Automation Inventory

Evidence refreshed: 2026-08-21 (America/Detroit)

This inventory is subordinate to the committed
[cell architecture](bert-ernie-hermes-cell-architecture.md) and the
[hybrid migration plan](../superpowers/plans/2026-08-21-bert-ernie-hermes-hybrid-migration.md).
It records what may be replayed into the clean upstream-based platform. It does
not authorize profile access, automation changes, dependency execution,
promotion, service changes, or live access.

## Refreshed baseline

- Official GitHub release metadata still lists `v2026.8.19` as the latest
  non-draft, non-prerelease release and `v2026.8.18` as the immediately prior
  stable release.
- Two read-only `git ls-remote` observations resolved the selected target tag to
  commit `e624e9fde561e1add9388384012b295fde669ade`.
- The legacy customization range is the twelve non-merge commits after merge
  base `a6b6afdff4cb3dc8b0a45d9ceabbb30942227cea` through legacy tip
  `dca28dd07b69bfb1747836e716e6b5cb6d85a340`.
- `bitdepth` remains the writable/default remote. `upstream` fetches
  `NousResearch/hermes-agent`, tracks `upstream/main`, namespaces upstream tags
  below `refs/upstream/tags/*`, and has a disabled push URL.
- Static review of manifests, lockfiles, installed-package surfaces, Docker/CI,
  and executable install/update surfaces found no implementation evidence for
  a forbidden package version. The only exact forbidden strings are test
  fixtures that verify the existing scanner.

## Legacy commit disposition

| Commit | Legacy intent | Disposition | Evidence and replay rule |
| --- | --- | --- | --- |
| `9ce39545` | Recover Codex auxiliary text after a null-output parser failure. | Upstream-superseded | The target consumes raw Codex Responses events through `agent.codex_runtime._consume_codex_event_stream`, explicitly avoiding `response.output=null`, and has focused null-output tests. Do not replay the old exception patch. |
| `b030111f` | Add the orchestrator-access-node profile, wrapper, Last30Days workflow, and a core orchestrator tool. | Adapt | Preserve declared profile, scheduled-research, and health semantics at profile/plugin/script boundaries. Map the legacy profile to the two-cell role contract, migrate state semantically, and reject raw private memory copying. Do not restore the core `call_orchestrator` patch. |
| `0bfd5b30` | Harden cron heartbeat behavior and forbidden-version screening. | Adapt | Use upstream `no_agent` script jobs for deterministic heartbeats. Retain the supply-chain classifier as a lifecycle preflight at the supported edge; do not replay the legacy direct-tool scheduler branch. |
| `40a21462` | Add an in-tree `shared_core` shadow router/server/worker system. | Reject | The target already supplies `delegate_task`, Kanban worker lifecycle, plugin hooks, profile-scoped plugin state, and gateway extension points. Re-express only the confirmed Nate OS delegation/transport contracts as thin extensions; do not merge this parallel core. |
| `08ba06b0` | Add daily-goal contracts, coordinator, sources, execution, profiles, and read-only API behavior. | Adapt | Preserve the proven daily-goal, Kanban/cron ledger, evidence, and fallback semantics as deterministic workflows and thin plugins. Replace legacy policy and large core/API patches with the confirmed chief-persona, Codex-work-owner, privacy, and approval contracts. |
| `d7a1229d` | Bound output from the read-only Bert router. | Replay at supported edge | Output limits and fail-closed behavior remain invariants. Enforce them in the versioned delegation envelope and secured transport adapter, with canary-leak and oversize tests, not in core API-server code. |
| `b1846187` | Bind Bert status to server-derived evidence and an output digest. | Replay at supported edge | Preserve evidence-derived status, provenance, and digest fields in health/lifecycle receipts and transport acknowledgements. The model may summarize but cannot overwrite authoritative status. |
| `889a3507` | Run the daily-goal coordinator directly from cron without a model hop. | Upstream-superseded | The target has first-class `no_agent=True` script jobs with direct stdout delivery, silent success, failure alerts, claim heartbeat, and focused tests. Use that primitive with the adapted workflow. |
| `8606fae6` | Block agent writes and mutating terminal commands in a protected live checkout. | Adapt | Replace the mutable-checkout premise with immutable releases and atomic per-cell pointers. Keep a thin read-only runtime-root guard only as defense in depth; promotion remains outside the agent tool plane. |
| `a9246834` | Generalize direct cron support to deterministic scripts. | Upstream-superseded | The target's `no_agent` scheduler path is more complete and explicitly avoids agent/session construction. Do not replay the custom `direct_tool=script` path. |
| `18a41579` | Continue a reasoning-only/incomplete Codex response. | Upstream-superseded | The target has Codex runtime assembly, retry classification for null output, empty-response guards, and reasoning-only recovery tests. Validate the local regression fixture, but do not replay the old synthetic prompt. |
| `dca28dd` | Ignore dashboard bytecode and web-build guard files. | Upstream-superseded | The selected target already ignores `.bytecode-fingerprint`, `.bytecode-fingerprint.tmp`, and `.web_ui_build.lock`. |

## Codex automation migration surface

Prompts were inspected only for operational metadata and path/service markers.
No raw prompt or private content is reproduced here. Automation mutation is an
external-state action and remains approval-required.

| Automation | Current contract | Drift and risk | Disposition |
| --- | --- | --- | --- |
| `daily-bert-health-and-ops-repair` — Daily Bert Health And Ops Repair | Cron; `ACTIVE`; daily at 03:00 local; notification policy not explicitly set; local execution; project `4115059e-8360-4b66-9bdd-ac568b0de0e7`; cwd `/Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent`; legacy local profile/wrapper plus cloud Bert; state in `/Users/react/.codex/automations/daily-bert-health-and-ops-repair/memory.md`; latest safe summary at 2026-08-21 03:04 EDT reported healthy local and cloud checks. Its authority is status-first health observation plus narrowly documented gateway, dashboard, or wrapper restarts after evidence proves a fault; Git, dependency, update, and broader repair actions are forbidden. | The prompt still contains a `main` branch requirement while current evidence uses `bert-live-main`. It inspects a mutable local checkout and `/home/bert/.hermes/hermes-agent`, mixes local and cloud health/repair, names current and legacy service/wrapper surfaces, and assumes one operational lifecycle. An active replacement would duplicate status, notification, and repair work. | Replace with separate exception-only cell-health checks plus a read-only platform lifecycle drift check. Keep it unchanged during isolated candidate construction. Pause it, with approval, before the final pre-Ernie-promotion snapshot; never activate replacements until the old job is provably paused. Use explicit manual/approved Bert checks during the staged interval, then retire it only after both replacement cells have current receipts. |
| `computer-history-to-nate-os` — Computer History to Nate OS | Heartbeat; `ACTIVE`; every 15 minutes; failed-runs-only; target thread `01a02498-1468-72e1-b76b-9d8b33d4dc94`; no configured cwd; points to `/Users/react/.codex/worktrees/4ec6/nate-os` and state root `/Users/react/.codex/state/nate-os/computer-history`; expected owner is Codex/Nate OS, not either Hermes cell; no per-automation outcome memory was available. Its authority is redacted local observation, idempotent checkpoints, and exception delivery only; approval records are non-executing and it cannot change policy, routing, privacy, live Bert, dependencies, or external state. | It is not a Hermes runtime job, but it can create adjacent Nate OS evidence for Bert. The referenced Nate OS worktree currently exists at `34eb2908c3bb5922dec9dbe88c8d758f479a925d`; a disposable worktree path is not a durable production contract. A future memory-sync replacement could duplicate records if it reuses neither the task identity nor state root. | Retain unchanged through Hermes candidate and cell promotion because it does not address a Hermes runtime. Adapt separately, with approval, to a stable canonical Nate OS release/path and preserve its state/idempotency contract. Do not create a second history-ingest automation while it is active. |

## Automation cutover and rollback checklist

The following checks are mandatory before any cell cutover:

1. Capture every relevant automation ID, status, cadence, target, cwd/path,
   notification policy, expected cell, state/receipt location, authority, and
   last safe outcome in the candidate receipt.
2. Prove candidate tests use temporary roots and cannot be selected by the
   active legacy health automation.
3. Before the final Ernie snapshot, obtain approval to pause the legacy combined
   health/repair job. Record its prior status and next scheduled run.
4. Prove the legacy job is paused before any replacement is activated. A test
   must reject simultaneous `ACTIVE` status for overlapping old/new ownership.
5. Promote and verify Ernie independently. Do not activate a Bert replacement
   as part of Ernie promotion.
6. Promote and verify Bert only through its later approval. Each cell gets its
   own release pointer, health receipt, rollback pointer, and automation owner.
7. Activate new automations only after explicit approval, with exception-only
   notifications and stable task/idempotency keys.
8. Rollback first disables the new automation, restores the prior cell pointer,
   verifies health, and then restores the prior automation state only when that
   state is still valid. Never leave both old and new jobs active.
9. Post-cutover verification must cover at least one scheduled tick or approved
   dry run, receipt deduplication, service/runtime SHA parity, notification
   suppression on `CLEAR`, and a forced failure that reaches the exception path.

## Stop conditions

- Any unresolved automation owner, target cell, state root, or idempotency key.
- An old and replacement automation would overlap while both are active.
- A prompt path resolves inside a candidate or running mutable checkout.
- Notification policy would surface routine `CLEAR` evidence.
- Automation mutation lacks current explicit approval.
- A rollback cannot restore both the prior cell pointer and a non-duplicating
  automation state.
