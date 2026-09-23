# Bert/Ernie Hermes Cell Architecture

Status: approved architecture; implementation and promotion are not authorized by this document
Evidence date: 2026-08-21
Hermes release policy: exact one stable release behind authoritative upstream

## Purpose

This specification defines the Hermes-side architecture for Bert and Ernie. It
is the durable input to the implementation plan at
[`docs/superpowers/plans/2026-08-21-bert-ernie-hermes-hybrid-migration.md`](../superpowers/plans/2026-08-21-bert-ernie-hermes-hybrid-migration.md).

The target is one standardized Hermes platform, codebase, release process, and
update pipeline deployed as two independently operable cells:

- **Ernie cell:** local, private, intermittently offline, and permitted to use
  Nate's local/private context inside the local trust zone.
- **Bert cell:** cloud-resident, normally available, higher-capacity, and
  restricted to task-minimal sanitized inputs.

The cells are not one giant runtime and must not become divergent forks. Each
has independent profiles, credentials, state stores, release pointers, health,
promotion, rollback, and failure boundaries. They share versioned contracts,
not mutable storage.

## Authority and policy pointers

Nate OS remains authoritative for routing, authority, memory, approvals,
evidence, and receipts. This repository owns Hermes runtime contracts and
implementation only. It must not duplicate or silently change Nate OS policy.

Current policy entry points:

- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/index.md`
- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/standing-rules.md`
- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/workflows/bert-hermes.md`
- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/workflows/shared-agent-memory.md`
- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/background-operations.md`
- `/Users/react/Documents/_IK_International/AI/_apps/nate-os/docs/nate-os/security/supply-chain.md`

If these sources conflict with this specification at execution time, stop and
resolve the conflict before implementation or promotion.

## Confirmed decisions

1. Use a **hybrid clean migration**, not an in-place repair or wholesale merge.
   Build an immutable release from the selected upstream tag, then replay only
   declared Bert/Ernie behavior through supported extensions, plugins, skills,
   CLI commands, deterministic workflows, or generic upstream-quality hooks.
2. Preserve the old runtime and profile as an inseparable rollback pair. Never
   point old code at newly migrated state or new code at the original profile.
3. Select the target as the penultimate authoritative stable upstream release.
   On 2026-08-21 the latest verified release was `v2026.8.19` (Hermes 0.20.5)
   and the target was `v2026.8.18` (Hermes 0.20.4), commit
   `e624e9fde561e1add9388384012b295fde669ade`. Discovery must run again at
   execution time; the policy is not a permanent pin to 0.20.4.
4. Use the writable `bitdepth` fork for owned commits. Keep the read-only
   NousResearch `upstream` remote push-disabled and validate the remote contract
   before every candidate build or publication.
5. Candidate discovery, checkout, diffing, supply-chain screening, synthetic
   tests, migration rehearsal, offline evaluation preparation, and CLEAR
   receipts may run quietly. Promotion and every external or privileged action
   remain approval-gated.
6. Stage and canary Ernie first. Bert is a distinct later approval, deployment,
   health check, and rollback decision.

## Current-state evidence summary

The following facts were verified read-only on 2026-08-21. They are evidence
for the migration design, not a claim about uninspected live Bert state.

### Fork and lifecycle

- The isolated branch starts at `dca28dd07b69bfb1747836e716e6b5cb6d85a340`
  on the customized `bert-live-main` line. `pyproject.toml` reports 0.15.1.
- The direct legacy-head to 0.20.4 comparison spans roughly 8,558 files and
  about +1.62 million/-464 thousand lines. Twelve custom operational commits
  follow the merge base. A direct pull, merge, or broad cherry-pick would carry
  accidental architecture and unreviewed conflicts.
- The retired maintenance apply path can fetch/checkout inside a running
  checkout, editable-install, and restart. It is intentionally audit-only and
  is not a valid promotion primitive.
- Upstream's own `hermes update` is also an in-place update primitive. Its
  checks can inform candidate validation, but it must not mutate a production
  release directory.
- Provisional Git metadata already separates the writable `bitdepth` remote
  from push-disabled `upstream`, namespaces upstream tags, and sets
  `remote.pushDefault=bitdepth`. Preserve and validate this metadata; do not
  silently recreate or discard it.

### Orchestration and state

- `shared_core/` models Bert and Ernie but not Codex. It lacks the confirmed
  privacy, approval, expected-result, availability, and anti-loop fields.
- `gateway/shared_core_adapter.py` shadow-ingests messages and does not enforce
  work transfer, personal ownership, mixed decomposition, or completion flow.
- The stated sanitized-only intent conflicts with raw request storage in the
  current task record.
- Daily-goal coordination has useful receipts, outbox, and retry behavior but
  is a separate bespoke path rather than the shared delegation contract.
- Hermes 0.20.4 provides useful building blocks: isolated delegation, durable
  Kanban tasks with idempotency/CAS/heartbeats/retries, independent profiles,
  cron execution ledgers, relay primitives, and additive database migrations.
  These do not by themselves create a secure cross-host queue or prove
  semantic migration.
- Hermes profile export excludes `state.db`; moving an archive is therefore
  not continuity proof.

### Ernie model router

Static configuration currently identifies:

| Route | Configured model | Current behavior/risk |
| --- | --- | --- |
| default/tool/vision | `ernie-gemma4-ctx` from `gemma4:26b` | Every request containing tools is forced here. |
| reasoning | `ernie-deepseek-r1-ctx` from `deepseek-r1:32b` | Reached only by keyword and only when no tools are present. |
| coding | `ernie-qwen-coding-ctx` from `qwen3.6:35b-a3b-coding-nvfp4` | Reached by keyword and only when no tools are present; NVFP4 compatibility/benefit on Apple hardware is unproven. |
| OCR | `qwen2.5vl:latest` | Separate image/OCR keyword route. |

Installed-manifest evidence also lists Devstral, GLM-4.7-Flash, Mistral Small
3.2, Phi-4, Qwen2.5-Coder, and bge-m3. Presence is not health, compatibility,
or quality evidence.

The current router:

- selects models with raw keyword heuristics;
- hides the selected worker behind one public alias;
- sets global reasoning effort to `none` and explicitly disables Qwen
  reasoning;
- forwards OpenAI-style tool history without a model-specific normalization
  contract; and
- can change the model beneath a continuing persona conversation.

This conflicts with prompt/cache continuity and with the target model topology.

### Local resource envelope

The inspected Mac is an Apple M1 Max with 64 GB unified memory and 10 CPU
cores. For official Qwen3.8-27B artifacts:

- BF16 weights are about 53.8-55.6 GB before KV cache, vision projection,
  runtime, and operating-system overhead; this is not a practical target.
- A provenance-linked Q8 GGUF is about 28.6 GB, plus about 0.63 GB for vision
  and optionally 3.16 GB for MTP; long context and concurrency may exhaust the
  remaining memory.
- A provenance-linked Q4_K_M GGUF is about 19 GB, plus about 0.63 GB for vision
  and optionally 1.68 GB for MTP; this is the practical offline candidate, not
  an approved production model.

### Supply chain

The read-only screen found no forbidden package version in an implementation
surface for the current branch or selected 0.20.4 target. Passive safeguard
and test-fixture references were present and are non-actionable. No dependency
command was run. The upstream jump changes large Python and JavaScript lock
surfaces and includes install hooks; all manifests, locks, and hooks must be
reviewed before a separately approved frozen install.

## Intended architecture

```text
                                  Nate OS
                    policy / authority / memory / receipts
                                      |
               versioned contracts and visibility enforcement
                                      |
             +------------------------+------------------------+
             |                                                 |
       Ernie local cell                                  Bert cloud cell
  private personal chief of staff                  cloud personal chief of staff
  full permitted local context                     sanitized task-minimal context
             |                                                 |
     orchestration plane                                  orchestration plane
  tools/scripts/subagents/Kanban                       tools/scripts/subagents/Kanban
             |                                                 |
       model workers                                      model workers
   generalist + gated specialists                    generalist + gated specialists
             |                                                 |
  private inbox/outbox DB ---- secured versioned transport ---- inbox/outbox DB
             |
    local reintegration map
      never leaves Ernie

                 Work task from either persona
                             |
               sanitized exactly-once envelope
                             v
                         Codex owner
                   original persona tracks
```

### Cell isolation

Each cell owns:

- a `current` release pointer and immutable release directories;
- an independent `HERMES_HOME` profile root;
- credentials and secret stores with no cross-cell copying;
- session, Kanban, cron, transport, and receipt stores;
- service definitions, ports, health evidence, and lifecycle lock;
- a staged candidate root and rollback manifest; and
- cell-specific private/sanitized policy adapters.

The cells share only:

- the same selected Hermes release and internal extension revision;
- versioned delegation/result schemas;
- Nate OS policy identifiers and visibility rules;
- evaluation suites and receipt formats; and
- a secured, authenticated, replay-resistant transport contract.

No mutable SQLite database is mounted, synchronized, copied, or used by both
cells. Canonical Nate OS records synchronize through Git; each agent rebuilds
its own disposable index.

## Persona and ownership model

- Bert and Ernie are peer **personal chief-of-staff interfaces**. Each appears
  as one persistent, responsive personality while delegating work to tools,
  deterministic workflows, subagents, and durable workers.
- Codex is the **work chief of staff**. Substantive work execution, building,
  development, operations, efficiency, and sustained work coordination transfer
  to Codex exactly once.
- The persona that received a work request remains the conversational interface,
  tracks the handoff, and reports status; it does not duplicate execution.
- Personal scheduling, reminders, household/local-file operations, and personal
  research remain with Bert/Ernie unless Nate explicitly assigns them elsewhere.
- Mixed requests decompose into work and personal children with stable parent
  provenance. The user receives one coherent status from the originating persona.

## Execution ladder

The orchestrator chooses the least expensive safe rung that can complete the
task:

1. simple conversational response inline;
2. bounded existing tool;
3. deterministic script or workflow;
4. ephemeral subagent for bounded isolated work;
5. durable specialist or recurring automation.

The chosen rung, owner, approval boundary, and background status are recorded.
Background execution must not make the persona unavailable for new conversation.
Moving up the ladder never grants new authority.

## Delegation envelope

The wire and persistence schema is versioned and rejects unknown required
semantics. Every cross-owner or cross-cell task carries:

| Field | Contract |
| --- | --- |
| `schema_version` | Exact supported major/minor version. Unknown major fails closed. |
| `task_id` | Stable UUID for the logical task. |
| `parent_task_id` | Stable parent for decomposition, or null. |
| `owner` | `bert`, `ernie`, or `codex`; changes require an ownership event. |
| `requester_persona` | Persona responsible for user-facing continuity. |
| `task_class` | `personal`, `work`, or `mixed-child`. |
| `privacy_class` | `public`, `sanitized-cloud`, `local-private`, or `secret-prohibited`. |
| `payload` | Task-minimal content permitted for the recipient. |
| `local_payload_ref` | Opaque Ernie-local reference; never dereferenceable by Bert. |
| `provenance` | Source channel/session/message IDs, evidence timestamps, and sanitizer version. |
| `constraints` | User constraints, forbidden actions, deadlines, and output limits. |
| `approval` | Required, granted, denied, expired, and scope fields. |
| `expected_result` | Versioned result schema and acceptance description. |
| `completion` | Pending, accepted, running, waiting, completed, failed, expired, or cancelled. |
| `idempotency_key` | Stable semantic dedupe key. |
| `lineage` | Hop count, visited owners, prior envelope digest, and loop ceiling. |
| `retry` | Attempt, next attempt, expiry, last acknowledged sequence, and escalation state. |
| `integrity` | Envelope digest, sender identity, transport sequence, and signature metadata. |

Raw Ernie-private content is represented by `local_payload_ref` plus a sanitized
projection. It is never embedded in the cloud envelope, log, receipt, shared
memory proposal, or model evaluation artifact.

## Privacy, sanitization, and reintegration

1. Ernie classifies outbound fields using deterministic rules before any cloud
   or Codex handoff.
2. The sanitizer removes secrets, direct identifiers, private paths, private
   raw content, and unnecessary mappings. It emits a safe projection, a local
   mapping reference, and a sanitizer receipt containing hashes/counts only.
3. A policy check confirms recipient, purpose, minimum necessary content,
   approval state, and visibility ceiling.
4. Bert processes only the safe projection and binds its result to the task ID,
   payload digest, expected schema, and provenance.
5. Ernie validates the result and safely re-personalizes it using the local
   mapping. That mapping never returns to Bert.

Bert cannot write canonical Nate OS shared memory. Ernie may create sourced
proposals under Nate OS policy but may not silently change policy.

## Availability and offline-Ernie behavior

When Bert owns or tracks a task requiring Ernie's local/private action:

1. Create exactly one durable pending handoff keyed by task ID and idempotency
   key. Duplicate enqueue returns the existing record.
2. Keep the task pending; Bert must not substitute cloud execution.
3. Check Ernie availability on a configurable 25-minute base heartbeat. The
   value is the midpoint of the confirmed 20-30-minute operating window and is
   a default to validate from receipts, not an immutable constant.
4. Use compare-and-swap state transitions, monotonic transport sequence numbers,
   deterministic jitter, and acknowledged delivery. Acknowledgement is required
   before retry stops.
5. Keep transport retries within each heartbeat bounded. Keep one pending task
   across heartbeat failures; do not create duplicate work or notifications.
6. Apply configured expiry/escalation policy. Missing or ambiguous expiry or
   escalation authority blocks activation rather than inventing a value.
7. When Ernie returns, transfer the original owner, constraints, privacy class,
   approval state, provenance, and idempotency key intact.

CLEAR availability checks and unchanged pending states remain quiet. Only an
expired task, security failure, conflicting acknowledgement, required approval,
or policy-defined escalation enters the approval inbox.

## Self-improvement lifecycle

Three substantially similar successful requests may create a **candidate**;
three occurrences are not automatic proof and grant no authority. The lifecycle
is:

1. detect a repeated pattern from safe receipts;
2. draft a bounded skill, workflow, deterministic script, or routing rule;
3. sandbox it against historical/synthetic cases;
4. evaluate correctness, safety, privacy, latency, and maintenance cost;
5. promote only within its permitted authority;
6. monitor real receipts and compare with the prior path; and
7. merge, revise, or retire it.

A validated, read-only, local capability may auto-enable with a CLEAR receipt
only when it adds no dependency, permission, schedule, write, external effect,
cloud exposure, privacy expansion, or authority. Every other promotion requires
approval. The evidence detector must exclude near-duplicates produced by retry,
automation, or the same parent task.

## Web and work routing

- Ernie may perform lightweight web lookups through the existing bounded web
  tool when the request fits its privacy and tool policy.
- Heavy personal online research can be delegated to Bert only through the
  sanitizer and minimum-necessary envelope.
- Substantive work research or execution transfers to Codex exactly once.
- Personal and work children of a mixed request keep a common parent and one
  requester persona, but have separate owners and completion states.

## Model topology

Models are replaceable workers. They are not Bert/Ernie's persona, identity,
canonical memory, task owner, or source of policy.

The target topology is:

- one primary agentic generalist per cell after it passes the cell's gates;
- gated specialists invoked at task/subagent boundaries only when a capability
  registry and evaluation justify them; and
- deterministic scripts before model workers for stable repeatable operations.

Do not keyword-swap models within one continuing conversation. A specialist
receives a bounded task envelope and returns a typed result to the persistent
persona. Router fallback must preserve ownership, approvals, privacy, and
idempotency.

### Qwen3.8-27B candidate

The main candidate is the official `Qwen/Qwen3.8-27B`, released 2026-08-14
under Apache-2.0. It is a dense 27B vision-language model with 262,144 native
context, configurable reasoning effort, and preserved thinking. It is an
offline candidate only.

Evaluation must freeze:

- the official source repository and exact revision;
- license and model-card digest;
- quantizer/converter identity, source revision link, file size, and SHA-256;
- runtime version and chat/tool parser configuration; and
- public/synthetic prompt-set revision and seed/configuration.

Known blocking issues before tool-bearing use:

- the current router disables Qwen reasoning;
- the current history path does not normalize model-specific tool history;
- the shipped Qwen3.8 template has documented failures when historical
  OpenAI-compatible tool arguments are JSON strings; and
- native 262K context and concurrent workers are not proven feasible on the
  64 GB Mac.

Required comparative baselines are current Gemma, DeepSeek, Qwen3.6, and any
installed candidate that passes provenance and runtime-health preflight.
Third-party modified or so-called "uncensored" derivatives are excluded from
primary use and cannot substitute for the official candidate. Open-ended
behavior is evaluated with safety, privacy, and authorization gates rather than
treated as a model identity claim.

## Continuity and semantic migration

Migration operates only on cloned profile homes and immutable source backups.
It preserves:

- identity/persona files and their provenance;
- memory records and retrieval behavior;
- session and conversation history, including tool-call/result relationships;
- task IDs, ownership, status, approvals, comments, handoffs, and provenance;
- schedules, disabled state, next-fire semantics, time zones, and delivery
  targets;
- Kanban and cron execution ledgers;
- skills/plugins and their source/configuration manifests; and
- Nate OS agent identity and visibility ceiling.

For every SQLite store, use the SQLite online backup API from a consistent
source or stop writers for the final snapshot. Record `PRAGMA integrity_check`,
`foreign_key_check`, schema/user version, row counts, timestamp spans, stable
ID sets/digests, and application-specific invariants. Copying a database file,
archive, or profile directory is never proof.

Semantic validation includes:

- retrieval cases that return the expected safe memory record;
- persona/identity continuity rubrics without exposing private content;
- session role alternation and tool-call/result pairing;
- task ownership, completion, approval, and provenance equivalence;
- cron next-fire and disabled-state equivalence plus execution-ledger continuity;
- profile and cross-cell isolation;
- canary exclusion from Bert, receipts, shared memory, and model artifacts; and
- restart and rollback rehearsals using cloned state.

## Immutable release lifecycle

### Discovery and target selection

1. Query authoritative NousResearch GitHub releases.
2. Accept only releases with `draft=false`, `prerelease=false`, a valid release
   tag, and a resolvable tag commit.
3. Order by authoritative publication time with deterministic tie handling.
4. Select the immediately previous stable release, not a compatibility fallback
   inferred from issue titles.
5. Record latest tag/SHA, target tag/SHA, discovery time, source URL, and reason.
6. Fewer than two stable releases, duplicate/ambiguous ordering, missing tag,
   remote-contract failure, or moving tag is `BLOCKED`.

### Candidate layout

Each cell uses equivalent cell-local roots:

```text
cell-root/
  releases/<tag>-<sha>/          # immutable code + frozen environment + manifest
  profiles/<profile-release-id>/ # cloned/migrated cell profile
  candidates/<receipt-id>/       # temporary build and rehearsal workspace
  backups/<receipt-id>/          # old release/profile/config manifests
  receipts/<receipt-id>.json     # non-secret lifecycle evidence
  current -> releases/...        # atomic release pointer
  current-profile -> profiles/...# atomic profile pointer
```

The service starts only when release SHA, receipt, profile generation, expected
branch/tag policy, and service command agree. Runtime/code SHA parity is a
continuous health invariant.

### Promotion and rollback

- Candidate creation and validation never mutate `current` or a running profile.
- Promotion requires explicit cell-specific approval, a proven backup, a proven
  rollback command, zero unresolved WARN/BLOCKED/CRITICAL gates, and a quiesced
  final state snapshot.
- Switch release/profile pointers atomically as one promotion record. Run
  closed health checks before reopening mutating traffic.
- A pre-traffic health failure automatically restores both prior pointers and
  preserves failed artifacts/logs.
- After the candidate accepts writes, freeze traffic and reconcile the
  append-only delta before rollback. Ambiguous reverse migration or ownership
  divergence requires approval; never silently discard new work.

## Health, drift, and receipts

Steady-state evidence must prove:

- latest stable upstream tag/SHA and selected target tag/SHA;
- writable fork versus push-disabled upstream contract;
- deployed release/profile IDs for Ernie and Bert;
- service/runtime SHA parity and profile-generation parity;
- candidate stage and gate results;
- approval/promotion/rollback state;
- backup and rollback artifact existence;
- gateway, messaging, dashboard, router, tools, Kanban, cron, Nate OS identity,
  and visibility health appropriate to the cell; and
- next scheduled discovery and health check.

Identical CLEAR evidence is hash-suppressed and retained in background receipts.
Only decisions, blockers, risk, expiry/escalation, or approval-required actions
surface.

## Approval boundaries

### Quiet background work

- release discovery and exact target calculation;
- read-only remote, manifest, lockfile, hook, source, and customization diffs;
- isolated candidate checkout and static validation;
- synthetic fixture tests and migration rehearsals;
- provenance collection and offline-evaluation plan preparation;
- drift/health evidence and CLEAR receipts; and
- read-only local self-improvement candidate detection/drafting.

### Approval required

- any dependency install, refresh, or script execution;
- model weight or quant download, runtime configuration, or model selection;
- private-profile access/export or credential/config migration;
- creation/activation of schedules or services;
- local Ernie restart, promotion, or live profile mutation;
- SSH, live Bert access, deployment, restart, or promotion;
- external messages/writes, new permissions, cloud exposure, or authority;
- post-write rollback or reverse migration; and
- every unresolved WARN, BLOCKED, CRITICAL, or material ambiguity.

## Test invariants

The implementation is not acceptable unless focused tests prove:

1. Bert work request transfers to Codex exactly once and Bert does not execute it.
2. Ernie work request transfers with private content redacted or retained only
   behind a local opaque reference.
3. Personal scheduling remains with Bert/Ernie.
4. Mixed requests decompose without losing conversational intent.
5. Envelope owner, requester, provenance, privacy, constraints, approval,
   expected result, completion, and idempotency survive every hop.
6. Bert cannot write canonical shared memory or receive a local/private canary.
7. Ernie's private reintegration mapping is never exported.
8. Codex remains work owner after accepting the handoff.
9. Deterministic scripts, subagents, and durable workers follow the execution
   ladder and report through one persona.
10. Failure, retry, acknowledgement, expiry, escalation, and anti-loop behavior
    are bounded and deduplicated.
11. Ernie-offline produces one pending handoff, quiet heartbeats, no substitute
    cloud work, and exactly-once delivery after acknowledgement.
12. Model routing happens only at task boundaries and the Qwen tool-history
    regression passes before Qwen handles tools.
13. Memory/history/task/schedule/ledger migration passes integrity and semantic
    continuity checks.
14. Candidate promotion never mutates a running checkout; runtime and release
    SHAs match after restart; rollback restores the old release/profile pair.

## Stop conditions

Stop the affected phase and preserve evidence when:

- authoritative release order or tag identity is ambiguous;
- the writable/push-disabled remote contract is wrong;
- a forbidden dependency version appears in an implementation surface;
- manifest/lock/hook review is incomplete before dependency execution;
- a required customization cannot be mapped to a supported extension boundary;
- transport authentication, replay resistance, acknowledgement, or privacy
  behavior cannot be proven;
- a private canary crosses the Ernie trust boundary;
- semantic migration loses or changes identity, ownership, approval, provenance,
  schedules, or ledger state;
- model artifacts lack provenance or tool/history behavior is incompatible;
- memory pressure, context, concurrency, latency, or failure recovery exceeds
  the fixed evaluation gate;
- rollback is not independently rehearsed;
- live or private access lacks current explicit authorization; or
- any gate remains WARN/BLOCKED/CRITICAL at promotion time.

## Explicit non-goals

- No in-place Git pull or checkout under a running service.
- No autonomous production promotion or restart.
- No shared mutable profile or SQLite store between Bert and Ernie.
- No merger of Ernie-private context into Bert or Nate OS shared memory.
- No model identity as persona or canonical memory.
- No keyword model swapping inside a continuing conversation.
- No presumption that Qwen3.8 replaces Gemma or any specialist.
- No third-party modified model in the primary system.
- No Nate OS policy rewrite in this repository.
- No preservation of accidental legacy architecture merely because it exists.

## Unresolved decisions to prove in sandbox

These do not require a user preference until evidence produces a material
trade-off:

1. Whether Hermes 0.20.4 relay satisfies authenticated, versioned,
   acknowledged cross-host delivery. Prefer it if contract tests pass; otherwise
   implement a standalone transport adapter without widening Hermes core.
2. Which of the twelve legacy custom commits are fully superseded upstream.
   Decide by current-target reproduction tests, not commit-message similarity.
3. Whether Qwen3.8, Gemma, DeepSeek, Qwen3.6, or another installed candidate
   earns primary/specialist status. Decide only from frozen comparative gates.
4. The maximum safe context and concurrency on the 64 GB Mac. Measure memory
   pressure, swap delta, time-to-first-token, throughput, and recovery.
5. The expiry/escalation values for offline-Ernie tasks. Derive from observed
   task/availability receipts and Nate OS authority; absence blocks activation.
6. The smallest generic upstream hook, if any, needed by the orchestration
   extension. First attempt plugin, CLI, skill, relay, and existing Kanban APIs.

Production paths, service managers, credentials, ports, and private profile
contents are execution-time evidence to capture only under the matching
approval. They are not guessed in this specification.
