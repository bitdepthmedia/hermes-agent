# Bert/Ernie Hermes Hybrid Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one maintainable Hermes platform and immutable lifecycle, deployed as isolated Ernie-local/private and Bert-cloud/sanitized cells with durable role-safe orchestration, semantic continuity migration, and approval-gated promotion.

**Architecture:** Start each candidate from the exact penultimate authoritative stable NousResearch release. Keep internal behavior in a non-core overlay using supported Hermes plugin, skill, CLI, relay, Kanban, and profile boundaries; add a generic core hook only after a failing integration test proves no supported boundary works. Build, migrate, test, and receipt candidates in isolation, then promote Ernie and Bert separately by atomically switching paired immutable release/profile pointers.

**Tech Stack:** Python 3.11+, standard-library `dataclasses`, `sqlite3`, `hashlib`, `hmac`, `json`, `pathlib`, and `urllib`; Hermes plugin/CLI/Kanban/cron/relay APIs; pytest; JSON Schema documents; shell wrappers limited to safe process entry points.

**Spec:** `docs/architecture/bert-ernie-hermes-cell-architecture.md`

**Execution checkpoint (2026-08-21):** Tasks 1–2 and the scripts-disabled V2
dependency audit are complete. Before Task 3 implementation or any build/test
script, use the fail-closed
[`Hermes candidate build and sealing plan`](2026-08-21-hermes-candidate-build-and-sealing-plan.md).
It records that the current candidate is upstream-only and that the sealer does
not yet bind the overlay, runtime environment, or built assets. Its next gate is
the local code-only prerequisite approval, not script execution.

## Global Constraints

- Re-read repo `AGENTS.md`, the spec, current Nate OS index/standing rules/Bert-Hermes/background/supply-chain/shared-memory workflows, and the current upstream release before each execution phase.
- Do not implement from the legacy fork by wholesale merge. Start from the selected upstream release and replay declared contracts only.
- Exact target policy is one authoritative stable release behind, recalculated at execution time; do not hard-code Hermes 0.20.4.
- `bitdepth` is writable. NousResearch `upstream` is read-only and push-disabled. Never push to NousResearch.
- Preserve prompt-prefix stability, strict message-role alternation, independent profiles, and the Hermes narrow-waist/edge-extension design.
- Never mutate a running checkout or profile. Candidate build and migration use isolated directories and cloned profile homes.
- Never share or copy a mutable SQLite database between cells. Use SQLite online backup for consistent snapshots and semantic validation after migration.
- Never expose credentials, private Ernie context, direct identifiers, re-personalization mappings, or raw private records in source, tests, logs, reports, receipts, or cloud envelopes.
- Never add, install, pin, resolve, or execute an install for `axios@1.14.1`, `axios@0.30.4`, or `plain-crypto-js@4.2.1`.
- Before any dependency execution: compare manifests/committed locks, inspect all changed `preinstall`, `install`, `postinstall`, and `prepare` hooks, then obtain approval and use a frozen/reproducible install.
- Qwen/model work is evaluation-only until a separate approval authorizes artifact download and runtime configuration. Third-party modified derivatives are excluded from primary use.
- Live Bert access, SSH, service/schedule activation, deployment, restart, private-profile migration, model download, and production promotion each require current explicit approval.
- CLEAR background evidence stays quiet. WARN/BLOCKED/CRITICAL conditions stop the affected phase and enter the approval path.

---

## Planned file structure

The clean upstream base remains importable without the IK overlay. The overlay
is packaged and staged into each profile independently.

| Area | Planned files | Responsibility |
| --- | --- | --- |
| Lifecycle | `ik_lifecycle/*.py`, `scripts/ik-hermes-lifecycle` | Discover releases, validate remotes/supply chain, build candidates, migrate cloned profiles, verify health, promote/rollback, and write receipts. |
| Contracts | `ik_extensions/persona_orchestration/contracts/*.json`, `ik_extensions/persona_orchestration/envelope.py` | Versioned delegation/result/ack schemas and strict validation. |
| Orchestration | `ik_extensions/persona_orchestration/routing.py`, `execution.py`, `plugin.py` | Work/personal/mixed routing, execution ladder, ownership, persona availability, and Hermes registration. |
| Privacy | `ik_extensions/persona_orchestration/privacy.py`, `reintegrate.py` | Deterministic sanitizer, local opaque mappings, policy check, and result reintegration. |
| Durable handoff | `ik_extensions/persona_orchestration/store.py`, `transport.py`, `availability.py` | Per-cell inbox/outbox, acknowledgement, CAS/idempotency, cross-host transport adapter, and offline-Ernie heartbeat. |
| Nate OS adapter | `ik_extensions/persona_orchestration/nate_os.py` | Thin identity/visibility/proposal checks; no copied policy body. |
| Self-improvement | `ik_extensions/persona_orchestration/learning.py` | Candidate detection, sandbox/eval state machine, authority gates, and retire loop. |
| Model workers | `ik_extensions/model_workers/capabilities.py`, `router.py`, `history.py`, `provenance.py` | Task-boundary model selection, tool-history normalization, runtime capabilities, and artifact provenance. |
| Evals | `evals/ik/*.json`, `scripts/ik-model-eval`, `tests/ik_*` | Synthetic/public acceptance corpus, deterministic harnesses, privacy canaries, migration parity, and lifecycle tests. |
| Cell manifests | `ik_cells/ernie.yaml`, `ik_cells/bert.yaml` | Non-secret cell topology and required interfaces; never credentials or live private values. |

If the target release already provides an equivalent public type or API, extend
that public surface and delete the duplicate planned overlay file. Record that
decision in the implementation receipt and keep the same external contract.

## Dependency order and rollback points

```text
release/remote contract
  -> candidate + supply-chain gates
  -> delegation contracts
  -> routing/privacy/Nate OS adapters
  -> durable transport + availability
  -> self-improvement lifecycle
  -> semantic migration
  -> model worker router/evals
  -> health/promotion/rollback
  -> Ernie synthetic rehearsal and approved canary
  -> separately approved Bert rehearsal/promotion
  -> recurring discovery/health activation
```

- **RP0 — legacy preservation:** old release/profile pair and baseline digests.
- **RP1 — disposable candidate:** delete only the unpromoted candidate directory.
- **RP2 — disposable migrated clone:** delete only the cloned profile generation.
- **RP3 — pre-traffic rollback:** atomically restore both prior release/profile pointers.
- **RP4 — post-write rollback:** freeze traffic, reconcile append-only task/cron/outbox delta, then restore both pointers. Ambiguity requires approval.

---

### Task 1: Freeze the upstream and remote contract

**Owner:** Hermes lifecycle
**Mode:** quiet background, read-only remote access
**Rollback point:** RP0

**Files:**
- Create: `ik_lifecycle/__init__.py`
- Create: `ik_lifecycle/models.py`
- Create: `ik_lifecycle/remote_contract.py`
- Create: `ik_lifecycle/release_discovery.py`
- Create: `ik_lifecycle/receipt.py`
- Create: `ik_lifecycle/cli.py`
- Create: `scripts/ik-hermes-lifecycle`
- Test: `tests/ik_lifecycle/test_remote_contract.py`
- Test: `tests/ik_lifecycle/test_release_discovery.py`
- Test: `tests/ik_lifecycle/test_receipt.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class StableRelease:
    tag: str
    commit_sha: str
    published_at: datetime
    html_url: str

@dataclass(frozen=True)
class ReleaseSelection:
    latest: StableRelease
    target: StableRelease
    discovered_at: datetime

def validate_remote_contract(repo: Path) -> RemoteContractResult: ...
def discover_one_behind(source: ReleaseSource, git: GitRefs) -> ReleaseSelection: ...
def write_receipt(path: Path, receipt: LifecycleReceipt) -> None: ...
```

- Consumes: authoritative GitHub release JSON and `git ls-remote` tag refs.
- Produces: a deterministic `ReleaseSelection` and non-secret receipt used by every later task.

- [ ] **Step 1: Write failing remote-contract tests**

  Cover `bitdepth` writable/default, `upstream` NousResearch fetch URL,
  push-disabled upstream, namespaced upstream tags, expected `upstream/main`
  tracking, and refusal when a URL/refspec is ambiguous.

  ```python
  def test_upstream_must_be_push_disabled(repo_with_valid_remotes):
      repo_with_valid_remotes.set_push_url("upstream", "git@github.com:NousResearch/hermes-agent.git")
      result = validate_remote_contract(repo_with_valid_remotes.path)
      assert result.status == "BLOCKED"
      assert result.code == "upstream_push_enabled"
  ```

- [ ] **Step 2: Run the focused tests and confirm the failure**

  Run: `pytest -q tests/ik_lifecycle/test_remote_contract.py`
  Expected: FAIL because the validator does not exist.

- [ ] **Step 3: Implement the remote validator without mutating Git config**

  The command prints/receipts drift and exits nonzero on mismatch. A separate
  future approved repair command may change metadata; validation never does.

- [ ] **Step 4: Write failing release-selection tests**

  Fixtures must include drafts, prereleases, unordered publication dates,
  duplicate dates, missing tag refs, moving tags, only one stable release, and
  at least three stable releases. Assert the penultimate published stable tag is
  selected and no issue-title or compatibility fallback exists.

  ```python
  def test_selects_exact_penultimate_stable_release(source, refs):
      selection = discover_one_behind(source, refs)
      assert selection.latest.tag == "v2026.8.19"
      assert selection.target.tag == "v2026.8.18"
  ```

- [ ] **Step 5: Implement release discovery and immutable receipt serialization**

  Use strict tag/ref validation, UTC timestamps, canonical JSON (`sort_keys=True`,
  compact separators), and SHA-256 receipt digests. Unknown/ambiguous data is
  BLOCKED, never silently skipped.

- [ ] **Step 6: Run focused tests**

  Run: `pytest -q tests/ik_lifecycle/test_remote_contract.py tests/ik_lifecycle/test_release_discovery.py tests/ik_lifecycle/test_receipt.py`
  Expected: PASS with no network fixture escape.

- [ ] **Step 7: Commit**

  ```bash
  git add ik_lifecycle scripts/ik-hermes-lifecycle tests/ik_lifecycle
  git commit -m "feat(lifecycle): enforce authoritative one-behind release selection"
  ```

**Stop conditions:** remote mutation, ambiguous tag order, fewer than two stable
releases, tag/ref mismatch, or an upstream push path.

---

### Task 2: Build a screened immutable candidate

**Owner:** Hermes lifecycle
**Mode:** quiet static preparation; dependency execution approval required
**Rollback point:** RP1

**Files:**
- Create: `ik_lifecycle/supply_chain.py`
- Create: `ik_lifecycle/candidate.py`
- Create: `ik_lifecycle/filesystem.py`
- Test: `tests/ik_lifecycle/test_supply_chain.py`
- Test: `tests/ik_lifecycle/test_candidate.py`
- Test: `tests/ik_lifecycle/test_filesystem.py`

**Interfaces:**

```python
def inspect_manifests(source: Path, base: Path | None) -> SupplyChainReport: ...
def build_candidate(selection: ReleaseSelection, cell: CellSpec, root: Path) -> Candidate: ...
def seal_candidate(candidate: Candidate, validation: GateSet) -> SealedCandidate: ...
```

- Consumes: Task 1 selection, cell manifest, upstream source tree, committed manifests/locks.
- Produces: `candidates/<receipt-id>` and, only after all build gates, immutable `releases/<tag>-<sha>` plus manifest.

- [ ] **Step 1: Write failing supply-chain fixture tests**

  Assert BLOCKED for forbidden manifest, lock resolution, installed metadata,
  cache/download log, and executable install command. Assert CLEAR for passive
  policy, safeguard, and test-fixture mentions. Parse and list changed lifecycle
  hooks without executing them.

- [ ] **Step 2: Implement static manifest/lock/hook inspection**

  Support `pyproject.toml`, `uv.lock`, `requirements*.txt`, `package.json`,
  npm/pnpm lockfiles, Dockerfiles, CI YAML, and package-manager logs. Emit safe
  paths and package/version evidence only.

- [ ] **Step 3: Write failing filesystem/candidate tests**

  Prove candidate roots reject symlink escapes, existing non-candidate paths,
  broad targets, writable sealed releases, moving HEAD, mismatched receipt SHA,
  and any path under a configured running release/profile.

- [ ] **Step 4: Implement isolated checkout and sealing**

  Checkout the exact tag SHA into the candidate root, never the running tree.
  Record source tree digest, manifest/lock digests, hook inventory, Python/Node
  requirements, customization inventory, and planned install commands. Sealing
  fails until tests and, when separately approved, frozen installs complete.

- [ ] **Step 5: Add a two-phase install gate**

  Phase A is static and allowed. Phase B prints the exact frozen command and
  exits `approval_required` unless a scoped approval receipt matches the
  candidate digest. Prefer dependency audit with scripts disabled; normal
  runtime installation may enable only reviewed required hooks.

- [ ] **Step 6: Run focused tests and static scan**

  Run: `pytest -q tests/ik_lifecycle/test_supply_chain.py tests/ik_lifecycle/test_candidate.py tests/ik_lifecycle/test_filesystem.py`
  Run: `python scripts/ik-hermes-lifecycle supply-chain --candidate <fixture-path>`
  Expected: tests PASS; fixture receipt CLEAR; no package manager executes.

- [ ] **Step 7: Commit**

  ```bash
  git add ik_lifecycle tests/ik_lifecycle
  git commit -m "feat(lifecycle): stage screened immutable Hermes candidates"
  ```

**Stop conditions:** forbidden version implementation evidence, unreviewed hook,
lock mismatch, candidate path overlap, source/tag SHA drift, install without a
matching approval receipt, or any mutation of the running checkout.

---

### Task 3: Define the delegation, result, and acknowledgement contracts

**Owner:** Hermes persona orchestration
**Mode:** quiet local implementation
**Rollback point:** RP1

**Files:**
- Create: `ik_extensions/persona_orchestration/__init__.py`
- Create: `ik_extensions/persona_orchestration/contracts/delegation-envelope-v1.json`
- Create: `ik_extensions/persona_orchestration/contracts/task-result-v1.json`
- Create: `ik_extensions/persona_orchestration/contracts/transport-ack-v1.json`
- Create: `ik_extensions/persona_orchestration/envelope.py`
- Test: `tests/ik_orchestration/test_envelope.py`
- Test: `tests/ik_orchestration/test_contract_fixtures.py`

**Interfaces:**

```python
class Owner(StrEnum):
    BERT = "bert"
    ERNIE = "ernie"
    CODEX = "codex"

class PrivacyClass(StrEnum):
    PUBLIC = "public"
    SANITIZED_CLOUD = "sanitized-cloud"
    LOCAL_PRIVATE = "local-private"
    SECRET_PROHIBITED = "secret-prohibited"

def validate_envelope(value: Mapping[str, object]) -> DelegationEnvelope: ...
def canonical_digest(envelope: DelegationEnvelope) -> str: ...
def transfer_owner(envelope: DelegationEnvelope, event: OwnershipEvent) -> DelegationEnvelope: ...
```

- Consumes: no runtime/profile data.
- Produces: stable types and JSON contracts used by routing, persistence, transport, migration, and evals.

- [ ] **Step 1: Write failing contract tests for every required field**

  Include schema version, task/parent IDs, owner, requester persona, task/privacy
  classes, sanitized payload/local ref, provenance, constraints, approval,
  expected result, completion, idempotency, lineage, retry, and integrity.
  Reject unknown major versions, invalid transitions, hop overflow, task-ID
  changes, and local-private payload addressed to Bert.

- [ ] **Step 2: Run tests and confirm failure**

  Run: `pytest -q tests/ik_orchestration/test_envelope.py tests/ik_orchestration/test_contract_fixtures.py`
  Expected: FAIL because schemas/types do not exist.

- [ ] **Step 3: Implement canonical serialization and transition validation**

  Use deterministic JSON and explicit enum/state transition tables. Preserve
  immutable request/provenance fields during transfer; create an ownership event
  rather than overwriting history.

- [ ] **Step 4: Add forward/backward compatibility fixtures**

  V1 readers accept optional unknown minor fields while preserving them for
  forwarding. Unknown major versions fail closed. Golden fixture digests remain
  stable across repeated serialization.

- [ ] **Step 5: Run tests and commit**

  Run: `pytest -q tests/ik_orchestration/test_envelope.py tests/ik_orchestration/test_contract_fixtures.py`
  Expected: PASS.

  ```bash
  git add ik_extensions/persona_orchestration tests/ik_orchestration
  git commit -m "feat(orchestration): define versioned delegation contracts"
  ```

**Stop conditions:** schema loses provenance/approval/idempotency, private
payload can target Bert, or contract requires raw Nate OS policy duplication.

---

### Task 4: Implement persona routing and the execution ladder

**Owner:** Hermes persona orchestration
**Mode:** quiet local implementation
**Rollback point:** RP1

**Files:**
- Create: `ik_extensions/persona_orchestration/routing.py`
- Create: `ik_extensions/persona_orchestration/execution.py`
- Create: `ik_extensions/persona_orchestration/plugin.py`
- Create: `ik_extensions/persona_orchestration/manifest.yaml`
- Test: `tests/ik_orchestration/test_routing.py`
- Test: `tests/ik_orchestration/test_execution_ladder.py`
- Test: `tests/ik_orchestration/test_persona_continuity.py`
- Test: `tests/ik_orchestration/test_plugin_registration.py`

**Interfaces:**

```python
class TaskClass(StrEnum):
    PERSONAL = "personal"
    WORK = "work"
    MIXED = "mixed"

class ExecutionRung(IntEnum):
    INLINE = 1
    TOOL = 2
    WORKFLOW = 3
    SUBAGENT = 4
    DURABLE = 5

def classify_request(request: IntakeRequest, policy: RoutingPolicy) -> Classification: ...
def decompose_mixed(request: IntakeRequest, classification: Classification) -> tuple[DelegationEnvelope, ...]: ...
def choose_execution_rung(task: DelegationEnvelope, catalog: CapabilityCatalog) -> ExecutionDecision: ...
```

- Consumes: Task 3 envelope and existing Hermes plugin/delegation/Kanban APIs.
- Produces: exactly-once ownership decision plus background handle/status for the originating persona.

- [ ] **Step 1: Write failing routing cases**

  Required cases: Bert receives build request -> Codex; Ernie receives work with
  private details -> Codex safe projection/local ref; personal scheduling stays
  with Bert/Ernie; mixed request produces work and personal children; ambiguous
  mixed request preserves conversation and asks a bounded clarification or
  routes conservatively; accepted Codex work never returns to Bert/Ernie ownership.

- [ ] **Step 2: Write failing execution-ladder cases**

  Prove cheapest safe rung wins; a deterministic workflow outranks a subagent;
  new permission cannot be gained by selecting a higher rung; recurring work is
  durable; and an active background task does not block a new conversational turn.

- [ ] **Step 3: Implement pure classification and rung selection**

  Keep policy inputs explicit and versioned. Classification returns evidence
  codes, not hidden keyword-only behavior. The model may propose classification,
  but deterministic ownership/privacy/approval checks make the final decision.

- [ ] **Step 4: Register through supported Hermes extension APIs**

  Load the overlay only when the profile enables it. Add no permanent core model
  tool. Use existing delegate/Kanban APIs for rungs 4/5 and return a background
  tracking handle to the persona.

- [ ] **Step 5: Run focused and integration tests**

  Run: `pytest -q tests/ik_orchestration/test_routing.py tests/ik_orchestration/test_execution_ladder.py tests/ik_orchestration/test_persona_continuity.py tests/ik_orchestration/test_plugin_registration.py`
  Expected: PASS; system prompt bytes remain stable in the continuity fixture.

- [ ] **Step 6: Commit**

  ```bash
  git add ik_extensions/persona_orchestration tests/ik_orchestration
  git commit -m "feat(orchestration): route personal and work ownership safely"
  ```

**Stop conditions:** core tool/schema growth without a proven need, model-only
ownership decisions, duplicate execution, prompt mutation, or role-alternation break.

---

### Task 5: Enforce sanitization, local reintegration, and Nate OS boundaries

**Owner:** Ernie trust boundary and Nate OS adapter
**Mode:** quiet synthetic implementation; private data access prohibited
**Rollback point:** RP1

**Files:**
- Create: `ik_extensions/persona_orchestration/privacy.py`
- Create: `ik_extensions/persona_orchestration/reintegrate.py`
- Create: `ik_extensions/persona_orchestration/nate_os.py`
- Test: `tests/ik_orchestration/test_privacy.py`
- Test: `tests/ik_orchestration/test_reintegration.py`
- Test: `tests/ik_orchestration/test_nate_os_boundaries.py`

**Interfaces:**

```python
def sanitize_for_recipient(source: LocalTask, recipient: Owner, policy: PrivacyPolicy) -> SanitizedTask: ...
def reintegrate_local(result: TaskResult, mapping_store: LocalMappingStore) -> ReintegratedResult: ...
def authorize_memory_action(agent_id: str, action: MemoryAction, visibility: str) -> BoundaryDecision: ...
```

- Consumes: Task 3 envelope, Nate OS agent identity/visibility response, synthetic canaries.
- Produces: task-minimal payload, local opaque mapping reference, safe receipt, and fail-closed memory decision.

- [ ] **Step 1: Write a deterministic private-canary corpus**

  Use obviously synthetic secrets, names, paths, identifiers, raw-content
  fragments, and re-personalization mappings. Assert zero canaries in Bert/Codex
  payloads, logs, receipts, exception text, shared-memory proposals, and result
  echoes. Assert the local mapping remains addressable only inside Ernie.

- [ ] **Step 2: Implement field-aware sanitizer and safe evidence**

  Prefer allowlisted output fields and typed transforms over free-form regex
  alone. Emit removed-field counts, policy/sanitizer version, payload digest,
  and local mapping ID; never removed values.

- [ ] **Step 3: Implement local result validation/reintegration**

  Require task ID, sanitized payload digest, result schema, recipient, and
  completion state to match. Reject a result that requests the local mapping or
  returns a new instruction outside the task authority.

- [ ] **Step 4: Implement the thin Nate OS adapter**

  Verify configured agent identity and visibility ceiling. Bert: all-agents
  read-only and no proposals. Ernie: allowed retrieval plus sourced proposal
  mode, no direct canonical writes. Unknown identity: all-agents read-only.

- [ ] **Step 5: Run boundary tests**

  Run: `pytest -q tests/ik_orchestration/test_privacy.py tests/ik_orchestration/test_reintegration.py tests/ik_orchestration/test_nate_os_boundaries.py`
  Expected: PASS with zero synthetic-canary leakage and denied Bert writes.

- [ ] **Step 6: Commit**

  ```bash
  git add ik_extensions/persona_orchestration tests/ik_orchestration
  git commit -m "feat(orchestration): enforce directional privacy and memory boundaries"
  ```

**Stop conditions:** raw-value logging, sanitizer uncertainty, exported local
mapping, Bert write capability, or a Nate OS contract conflict.

---

### Task 6: Build durable per-cell handoff and offline-Ernie recovery

**Owner:** Hermes cross-cell transport
**Mode:** quiet loopback/synthetic implementation; external endpoint/credential activation approval required
**Rollback point:** RP1

**Files:**
- Create: `ik_extensions/persona_orchestration/store.py`
- Create: `ik_extensions/persona_orchestration/transport.py`
- Create: `ik_extensions/persona_orchestration/availability.py`
- Create: `ik_extensions/persona_orchestration/migrations/001_initial.sql`
- Test: `tests/ik_orchestration/test_store.py`
- Test: `tests/ik_orchestration/test_transport.py`
- Test: `tests/ik_orchestration/test_offline_ernie.py`
- Test: `tests/ik_orchestration/test_relay_contract.py`

**Interfaces:**

```python
class HandoffStore(Protocol):
    def enqueue_once(self, envelope: DelegationEnvelope) -> StoredHandoff: ...
    def claim(self, task_id: UUID, expected_version: int, claimant: Owner) -> StoredHandoff: ...
    def acknowledge(self, ack: TransportAck) -> StoredHandoff: ...
    def due(self, now: datetime, limit: int) -> tuple[StoredHandoff, ...]: ...

class Transport(Protocol):
    def deliver(self, envelope: DelegationEnvelope) -> TransportAck: ...

def run_availability_tick(now: datetime, config: AvailabilityConfig, store: HandoffStore, transport: Transport) -> TickReceipt: ...
```

- Consumes: Tasks 3-5 contracts/sanitizer; Hermes relay only if its contract test passes.
- Produces: separate cell-local SQLite inbox/outbox, authenticated delivery adapter, monotonic acknowledgement, and quiet 25-minute availability tick.

- [ ] **Step 1: Write failing store/idempotency/CAS tests**

  Assert repeated enqueue returns one row, conflicting payload under the same
  idempotency key is BLOCKED, concurrent claims produce one winner, completion
  cannot regress, and acknowledgement sequence/digest must match.

- [ ] **Step 2: Implement the cell-local SQLite store**

  Use WAL only within one cell, explicit transactions, schema versioning,
  compare-and-swap version columns, unique idempotency/digest constraints, and
  append-only events. Do not expose a network-shared database path.

- [ ] **Step 3: Prove or reject the Hermes relay primitive**

  Against v0.20.4 `gateway/relay/` fixtures, test authenticated sender identity,
  recipient binding, replay rejection, payload caps, acknowledgement, reconnect,
  and safe failure text. If the public contract passes, adapt it. If not, retain
  the `Transport` protocol and implement a standalone authenticated adapter in
  the overlay; do not patch private relay internals.

- [ ] **Step 4: Write the offline-Ernie state-machine tests**

  Cover one pending handoff, 25-minute configurable base interval, deterministic
  jitter within 20-30 minutes, bounded per-tick retries, no duplicate work or
  notifications, no Bert substitution, unchanged ownership/privacy/approval,
  acknowledgement-before-stop, restart recovery, expiry/escalation gate, and
  exactly-once transfer after Ernie returns.

- [ ] **Step 5: Implement availability tick and receipt suppression**

  Heartbeat state stores `next_attempt_at`, attempt count, last safe error code,
  last acknowledged sequence, and escalation state. Hash-identical CLEAR/no-change
  receipts remain quiet. Missing expiry/escalation policy returns BLOCKED before
  activation.

- [ ] **Step 6: Run concurrency/restart tests**

  Run: `pytest -q tests/ik_orchestration/test_store.py tests/ik_orchestration/test_transport.py tests/ik_orchestration/test_offline_ernie.py tests/ik_orchestration/test_relay_contract.py`
  Expected: PASS under repeated and concurrent fixture delivery.

- [ ] **Step 7: Commit**

  ```bash
  git add ik_extensions/persona_orchestration tests/ik_orchestration
  git commit -m "feat(orchestration): add durable acknowledged cross-cell handoff"
  ```

**Stop conditions:** mutable shared DB, unauthenticated transport, replayable
envelope, no acknowledgement, duplicate execution/notification, or invented
expiry/escalation authority.

---

### Task 7: Add evidence-gated self-improvement

**Owner:** Hermes persona orchestration
**Mode:** quiet candidate detection/drafting; promotion bounded by authority
**Rollback point:** RP1

**Files:**
- Create: `ik_extensions/persona_orchestration/learning.py`
- Create: `ik_extensions/persona_orchestration/contracts/improvement-candidate-v1.json`
- Test: `tests/ik_orchestration/test_learning_candidates.py`
- Test: `tests/ik_orchestration/test_learning_authority.py`

**Interfaces:**

```python
def detect_candidate(receipts: Sequence[SafeReceipt], policy: LearningPolicy) -> ImprovementCandidate | None: ...
def evaluate_candidate(candidate: ImprovementCandidate, sandbox: Sandbox) -> CandidateEvaluation: ...
def promotion_decision(candidate: ImprovementCandidate, evaluation: CandidateEvaluation) -> PromotionDecision: ...
```

- Consumes: safe completed receipts only; retries/duplicate parent work excluded.
- Produces: draft -> sandboxed -> evaluated -> approved/auto-enabled -> monitored -> merged/retired state record.

- [ ] **Step 1: Write candidate-detection tests**

  Three substantially similar independently successful requests create a
  candidate; three retries, schedule replays, or children of one parent do not.
  Fewer than three may still be manually proposed but never auto-enabled.

- [ ] **Step 2: Write authority-gate tests**

  Only validated read-only local capability with no dependency, permission,
  schedule, write, external effect, cloud exposure, privacy expansion, or
  authority may auto-enable. Every protected change returns
  `approval_required` with safe evidence.

- [ ] **Step 3: Implement lifecycle and monitor/retire transitions**

  Preserve baseline metrics and candidate provenance. Monitoring compares
  correctness, privacy, latency, failures, and maintenance cost. Regression
  retires or disables the candidate without changing central policy.

- [ ] **Step 4: Run tests and commit**

  Run: `pytest -q tests/ik_orchestration/test_learning_candidates.py tests/ik_orchestration/test_learning_authority.py`
  Expected: PASS.

  ```bash
  git add ik_extensions/persona_orchestration tests/ik_orchestration
  git commit -m "feat(orchestration): gate self-improvement on repeated evidence"
  ```

**Stop conditions:** raw transcript mining, one-example auto-learning, central
policy mutation, or any silent authority expansion.

---

### Task 8: Export and semantically migrate continuity state

**Owner:** Hermes lifecycle/data migration
**Mode:** synthetic clones quiet; private profile access and final snapshot approval required
**Rollback point:** RP2

**Files:**
- Create: `ik_lifecycle/profile_inventory.py`
- Create: `ik_lifecycle/sqlite_backup.py`
- Create: `ik_lifecycle/migration.py`
- Create: `ik_lifecycle/semantic_validation.py`
- Create: `ik_lifecycle/migrations/legacy_015_to_current.py`
- Test: `tests/ik_lifecycle/test_profile_inventory.py`
- Test: `tests/ik_lifecycle/test_sqlite_backup.py`
- Test: `tests/ik_lifecycle/test_migration.py`
- Test: `tests/ik_lifecycle/test_semantic_validation.py`

**Interfaces:**

```python
def inventory_profile(home: Path, policy: InventoryPolicy) -> ProfileInventory: ...
def online_backup(source: Path, destination: Path) -> DatabaseBackupReceipt: ...
def migrate_profile(source: ProfileSnapshot, candidate: Candidate, destination: Path) -> MigratedProfile: ...
def validate_semantics(before: ProfileSnapshot, after: MigratedProfile, cases: ContinuityCases) -> GateSet: ...
```

- Consumes: immutable candidate, synthetic/approved profile snapshot, current and target schema knowledge.
- Produces: cloned migrated profile, non-secret inventory/digests, semantic gate report, and rollback pairing.

- [ ] **Step 1: Build synthetic legacy fixtures**

  Include identity/persona files, safe memory records, sessions with tool-call
  history, tasks/comments/handoffs/approvals, active/completed/failed Kanban
  runs, cron enabled/disabled jobs and execution ledger, timezone/DST cases,
  profile isolation, and synthetic private canaries.

- [ ] **Step 2: Write online-backup integrity tests**

  Simulate WAL activity and prove a raw file copy can miss a committed record
  while `sqlite3.Connection.backup` produces `integrity_check=ok`, empty
  `foreign_key_check`, consistent schema/user version, row counts, and ID digests.

- [ ] **Step 3: Implement non-secret profile inventory**

  Record file role, size, mode, safe digest, schema version, counts, timestamp
  ranges, schedule metadata, plugin/skill identity, and excluded secret paths.
  Do not record content from private files.

- [ ] **Step 4: Implement explicit source-to-target migration functions**

  Run only against destination clones. Use target Hermes migrations where they
  preserve semantics; add explicit legacy transforms for custom ownership,
  daily-goal, and handoff records. Every dropped/merged field requires a
  documented equivalence rule and test.

- [ ] **Step 5: Implement semantic validators**

  Validate stable IDs, ownership/completion/approval/provenance, session role
  alternation and tool pairing, retrieval behavior, persona rubric, cron next
  fire/disabled state/timezone, execution-ledger continuity, profile isolation,
  Nate OS identity/visibility, and zero canary leakage.

- [ ] **Step 6: Rehearse restart and rollback on synthetic clones**

  Start only fixture-local processes/nonconflicting ports when authorized for
  test execution. Prove old fixture runtime/profile remains readable, candidate
  uses only clone, and deleting RP2 artifacts leaves RP0 unchanged.

- [ ] **Step 7: Run tests and commit**

  Run: `pytest -q tests/ik_lifecycle/test_profile_inventory.py tests/ik_lifecycle/test_sqlite_backup.py tests/ik_lifecycle/test_migration.py tests/ik_lifecycle/test_semantic_validation.py`
  Expected: PASS; raw-copy negative control FAILS its semantic assertion.

  ```bash
  git add ik_lifecycle tests/ik_lifecycle
  git commit -m "feat(lifecycle): migrate Hermes continuity state semantically"
  ```

**Stop conditions:** live profile read without approval, raw file copy treated
as proof, integrity/FK failure, lost ID/owner/approval/provenance/schedule state,
or private canary outside Ernie clone.

---

### Task 9: Replace keyword switching with task-boundary model workers

**Owner:** Ernie model worker layer
**Mode:** harness implementation quiet; model download/configuration approval required
**Rollback point:** RP1/RP2

**Files:**
- Create: `ik_extensions/model_workers/__init__.py`
- Create: `ik_extensions/model_workers/capabilities.py`
- Create: `ik_extensions/model_workers/router.py`
- Create: `ik_extensions/model_workers/history.py`
- Create: `ik_extensions/model_workers/provenance.py`
- Create: `evals/ik/chief-of-staff-v1.json`
- Create: `evals/ik/tools-v1.json`
- Create: `evals/ik/coding-reasoning-v1.json`
- Create: `evals/ik/long-context-v1.json`
- Create: `evals/ik/privacy-handoff-v1.json`
- Create: `scripts/ik-model-eval`
- Test: `tests/ik_models/test_capabilities.py`
- Test: `tests/ik_models/test_router.py`
- Test: `tests/ik_models/test_qwen_tool_history.py`
- Test: `tests/ik_models/test_provenance.py`
- Test: `tests/ik_models/test_eval_harness.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ModelCapability:
    model_id: str
    runtime: str
    supports_tools: bool
    supports_parallel_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    max_validated_context: int
    max_validated_concurrency: int
    artifact_digest: str

def select_worker(task: DelegationEnvelope, catalog: CapabilityCatalog) -> WorkerSelection: ...
def normalize_tool_history(messages: Sequence[Message], adapter: HistoryAdapter) -> tuple[Message, ...]: ...
def verify_artifact_provenance(manifest: ArtifactManifest) -> ProvenanceResult: ...
```

- Consumes: bounded task envelope at subagent/durable-worker boundary, never raw persona switching.
- Produces: primary generalist or explicitly justified specialist worker selection and frozen comparative results.

- [ ] **Step 1: Write router invariants before touching the current router**

  Assert model remains stable for one conversation; tools do not automatically
  force Gemma; specialist choice occurs only for a new bounded task; missing
  capability fails/falls back safely; fallback preserves owner/privacy/approval/
  idempotency; and deterministic workflow remains preferred when applicable.

- [ ] **Step 2: Write the exact Qwen history regressions**

  Include assistant `tool_calls[].function.arguments` as both JSON string and
  mapping, parallel calls, content plus call, tool results, multi-step calls,
  preserved thinking, non-thinking mode, and Hermes role alternation. The chosen
  runtime/template adapter must render and round-trip all fixtures.

- [ ] **Step 3: Implement capability registry and history adapters**

  Make reasoning mode per-task and model-capability aware. Remove the global
  Qwen reasoning-disable behavior from the candidate router only after tests.
  Do not use an unreviewed community chat template; pin official/runtime-owned
  behavior and normalize at the boundary when required.

- [ ] **Step 4: Implement artifact provenance validation**

  Require official source ID/revision/license/model-card digest plus quantizer,
  source-revision link, runtime version, file size, and SHA-256. Reject modified
  derivatives from the primary catalog.

- [ ] **Step 5: Implement frozen public/synthetic eval suites**

  Cases cover chief-of-staff planning, execution-rung choice, tool call and
  result synthesis, coding, reasoning, image/OCR, 32K/64K/128K continuity,
  hallucination resistance, open-ended-but-authorized behavior, privacy canary,
  Codex/Bert/Ernie handoff, retries, latency, memory pressure, swap, and
  concurrency 1/2.

- [ ] **Step 6: Encode comparative gates**

  - zero unauthorized actions, private leaks, duplicate ownership, and anti-loop failures;
  - 100% valid approval envelopes and tool-history regression cases;
  - no critical-class pass-rate regression;
  - no unexplained quality reduction greater than 5 percentage points;
  - no unexplained primary-route p95 latency regression greater than 25%;
  - normal memory pressure, no OOM, and bounded measured swap growth;
  - context/concurrency capability limited to the largest level that passes all gates.

- [ ] **Step 7: Run harness unit tests without model artifacts**

  Run: `pytest -q tests/ik_models`
  Expected: PASS using deterministic fake runtimes; no network/model download.

- [ ] **Step 8: After separate approval, run the offline bake-off**

  Compare current Gemma, DeepSeek, Qwen3.6, official Qwen3.8-27B, and only
  provenance/health-qualified installed candidates. Start Qwen3.8 at Q4,
  context 32K, concurrency 1; advance one gate at a time. Record add/retain/
  replace/remove from evidence. Never assume Qwen promotion.

- [ ] **Step 9: Commit code/harness separately from model-result receipt**

  ```bash
  git add ik_extensions/model_workers evals/ik scripts/ik-model-eval tests/ik_models
  git commit -m "feat(models): route validated workers at task boundaries"
  ```

**Stop conditions:** artifact provenance failure, third-party derivative primary
candidate, Qwen history failure, global reasoning suppression, keyword-only
mid-conversation switching, memory-pressure/OOM, or any model action without approval.

---

### Task 10: Add cell manifests, health gates, paired promotion, and rollback

**Owner:** Hermes lifecycle
**Mode:** synthetic implementation quiet; real service/config/pointer changes approval required
**Rollback point:** RP3/RP4

**Files:**
- Create: `ik_cells/ernie.yaml`
- Create: `ik_cells/bert.yaml`
- Create: `ik_lifecycle/cells.py`
- Create: `ik_lifecycle/health.py`
- Create: `ik_lifecycle/promotion.py`
- Create: `ik_lifecycle/rollback.py`
- Test: `tests/ik_lifecycle/test_cells.py`
- Test: `tests/ik_lifecycle/test_health.py`
- Test: `tests/ik_lifecycle/test_promotion.py`
- Test: `tests/ik_lifecycle/test_rollback.py`

**Interfaces:**

```python
def load_cell_spec(path: Path) -> CellSpec: ...
def verify_cell(candidate: Candidate, profile: MigratedProfile, spec: CellSpec) -> GateSet: ...
def promote_pair(candidate: SealedCandidate, profile: MigratedProfile, approval: ApprovalReceipt) -> PromotionReceipt: ...
def rollback_pair(promotion: PromotionReceipt, mode: RollbackMode) -> RollbackReceipt: ...
```

- Consumes: sealed candidate, migrated profile, cell-specific health contract, scoped approval.
- Produces: paired atomic release/profile pointer switch, health receipt, and reversible rollback record.

- [ ] **Step 1: Define non-secret cell manifests**

  Include cell ID/trust zone, path roots, expected profile ID, required health
  probes, service-manager adapter name, messaging/router/Kanban/cron/Nate OS
  surfaces, transport role, and approval requirements. Values that are private,
  secret, or execution-time facts are resolved from approved local config and
  represented by key names, never copied values.

- [ ] **Step 2: Write health-gate tests**

  Verify release/profile receipt digest, runtime/code SHA parity, profile
  generation, endpoint/heartbeat, tool-bearing task, router model disclosure,
  Kanban claim/complete, cron disabled/next-fire/ledger, profile isolation,
  Nate OS allowed retrieval/denied write, messaging compatibility, restart, and
  backup existence. Any WARN/BLOCKED/CRITICAL rejects promotion.

- [ ] **Step 3: Write paired atomic promotion tests**

  Simulate failure between pointer writes and prove recovery never leaves mixed
  release/profile generations. Validate lifecycle lock, fsync/rename discipline,
  approval digest/scope/expiry, service-closed preflight, and no running-checkout mutation.

- [ ] **Step 4: Write rollback tests**

  Pre-traffic failure automatically restores both pointers. Post-write rollback
  returns `approval_required` unless the append-only task/cron/outbox delta is
  frozen, inventoried, and reconciled. Failed candidates/logs remain preserved.

- [ ] **Step 5: Implement service adapters as interfaces first**

  Fixture adapters capture commands but do not execute them. Real launchd/systemd
  adapters require explicit execution approval and begin status-first,
  heartbeat-second. Never interpolate credentials into commands or receipts.

- [ ] **Step 6: Run lifecycle tests**

  Run: `pytest -q tests/ik_lifecycle/test_cells.py tests/ik_lifecycle/test_health.py tests/ik_lifecycle/test_promotion.py tests/ik_lifecycle/test_rollback.py`
  Expected: PASS, including injected crash points.

- [ ] **Step 7: Commit**

  ```bash
  git add ik_cells ik_lifecycle tests/ik_lifecycle
  git commit -m "feat(lifecycle): promote and roll back isolated Hermes cells atomically"
  ```

**Stop conditions:** mixed release/profile pointers, missing backup/rollback
proof, unhealthy gate, approval mismatch, runtime SHA skew, or reverse-state ambiguity.

---

### Task 11: Rehearse and canary the Ernie cell

**Owner:** local Ernie operations
**Mode:** synthetic rehearsal quiet; private clone, service, model, schedule, and promotion approval required
**Rollback point:** RP2 then RP3/RP4

**Files:**
- Create: `evals/ik/ernie-cell-acceptance-v1.json`
- Create: `tests/e2e/test_ik_ernie_cell_fixture.py`
- Modify after evidence only: `ik_cells/ernie.yaml`
- Create per run, ignored/uncommitted: cell-local candidate/profile/receipts under approved root

**Interfaces:**
- Consumes: Tasks 1-10 and an approved Ernie-specific execution receipt.
- Produces: fixture rehearsal receipt, then separately approved local canary receipt and observation evidence.

- [ ] **Step 1: Run a fully synthetic temp-HERMES_HOME rehearsal**

  Use nonconflicting ports and fake credentials/adapters. Validate persona/routing,
  tools, deterministic workflow, subagent, Kanban, cron, model fake, sanitizer,
  Nate OS fake identity, transport loopback, restart, migration, and RP2/RP3 rollback.

- [ ] **Step 2: Review manifests/locks/hooks and request the narrow execution approvals**

  Present exact candidate digest, frozen install command, private snapshot scope,
  model-artifact scope if any, service commands, schedule state, backup paths,
  health commands, and rollback command. Do not bundle Bert authority.

- [ ] **Step 3: After approval, capture local Ernie status and final backup**

  Record wrapper/service/profile/current pointers, ports, router, tool-bearing
  tasks, schedules, private boundary, code SHA, and heartbeat. Use SQLite online
  backup and safe config backup with secrets excluded from receipts.

- [ ] **Step 4: Build/migrate/validate the Ernie candidate while old Ernie remains unchanged**

  Run every semantic, privacy, model, router, tool, Kanban, cron, Nate OS, restart,
  and rollback gate. Stop on any non-CLEAR result.

- [ ] **Step 5: After separate promotion approval, switch the paired pointers**

  Quiesce writers, capture final delta, atomically switch, run closed health
  checks, and reopen traffic only after CLEAR. Automatically RP3 rollback on
  pre-traffic failure.

- [ ] **Step 6: Observe the Ernie canary**

  Verify conversational responsiveness during background work, work-to-Codex
  ownership, personal ownership, mixed decomposition, private canary, local
  reintegration, model worker behavior, schedules, restarts, memory pressure,
  and quiet receipts. Preserve old pair throughout the canary window.

- [ ] **Step 7: Commit only stable code/docs/eval corrections**

  Do not commit private data, live receipts containing identifiers, generated
  profiles, credentials, model artifacts, or runtime logs.

**Stop conditions:** missing approval, private leak, semantic mismatch, health
failure, schedule drift, model/tool-history failure, high memory pressure,
rollback failure, or new material choice.

---

### Task 12: Rehearse and promote Bert as a separate cell

**Owner:** live Bert operations
**Mode:** no live action without explicit Bert approval
**Rollback point:** RP2 then RP3/RP4

**Files:**
- Create: `evals/ik/bert-cell-acceptance-v1.json`
- Create: `tests/e2e/test_ik_bert_cell_fixture.py`
- Modify after evidence only: `ik_cells/bert.yaml`
- Create per run, never committed with secrets/private content: Bert release/profile/receipt artifacts on approved host roots

**Interfaces:**
- Consumes: all prior tasks plus a successful Ernie canary and distinct Bert execution approval.
- Produces: separately promoted Bert cell with sanitized transport, read-only Nate OS, and proven rollback.

- [ ] **Step 1: Run a local cloud-cell fixture rehearsal**

  Prove sanitized-only payload, Bert read-only shared memory, no Ernie mapping,
  work-to-Codex exactly once, pending offline-Ernie behavior, messaging/gateway
  compatibility, restart, and rollback without contacting live Bert.

- [ ] **Step 2: Request explicit live Bert access and promotion authority**

  Name exact SSH/status/heartbeat/backup/build/service/promotion/rollback commands,
  target host/account, candidate digest, observation window, and stop gates.

- [ ] **Step 3: After approval, perform status-first and heartbeat-second capture**

  Record actual live branch/head, service commands, release/profile pointers,
  gateway, Telegram, dashboard, cron, Nate OS snapshot/identity, transport,
  backups, and runtime SHA. Preserve outage evidence and do not trust local Mac
  health as proof of live state.

- [ ] **Step 4: Stage and validate without changing the running checkout**

  Build immutable host-local release/profile candidates. Validate sanitizer,
  work routing, tools, Kanban, cron, messaging, Nate OS denied write/visibility,
  offline-Ernie heartbeat, restart, and rollback.

- [ ] **Step 5: After separate promotion approval, atomically promote Bert**

  Quiesce writers, final backup/delta, paired pointer switch, service restart,
  closed health, then reopen. RP3 rollback automatically on pre-traffic failure.

- [ ] **Step 6: Verify post-promotion parity and observation**

  Prove service/runtime SHA equals immutable release SHA; gateway/Telegram/
  dashboard/cron healthy; Bert sees sanitized task only; canonical shared-memory
  write denied; one pending Ernie handoff; duplicate notification absent; old
  pair remains intact.

- [ ] **Step 7: Commit only stable code/docs/eval corrections and publish to `bitdepth` through normal lifecycle**

  Do not push to NousResearch. Publication requires clean owned diff, tests,
  expected remote, and local/remote SHA parity.

**Stop conditions:** no current live authorization, SSH/status/heartbeat
ambiguity, runtime/checkout skew, missing backup, messaging regression, privacy
boundary failure, Nate OS write, unresolved gate, or rollback failure.

---

### Task 13: Activate recurring discovery, drift, and health evidence

**Owner:** Hermes lifecycle
**Mode:** code/tests quiet; schedule activation approval required
**Rollback point:** disable new schedule and preserve retired updater disabled

**Files:**
- Create: `ik_lifecycle/monitor.py`
- Create: `ik_lifecycle/approval_inbox.py`
- Create: `tests/ik_lifecycle/test_monitor.py`
- Create: `tests/ik_lifecycle/test_approval_inbox.py`
- Modify after approval: cell-local schedule definitions outside source profiles

**Interfaces:**

```python
def collect_lifecycle_state(cells: Sequence[CellSpec], source: ReleaseSource) -> LifecycleState: ...
def classify_exception(previous: LifecycleState | None, current: LifecycleState) -> ExceptionDecision: ...
def prepare_candidate_if_drifted(state: LifecycleState) -> CandidatePreparationResult: ...
```

- Consumes: release discovery, cell health, candidate/promotion/rollback receipts.
- Produces: idempotent drift state, silent candidate preparation, hash-suppressed CLEAR evidence, exception-only approval items, and next-check time.

- [ ] **Step 1: Write monitor idempotency/no-promotion tests**

  Repeated unchanged checks create no duplicate notification. New upstream
  release selects exact one-behind and may prepare a candidate, but never
  promotes/restarts. Concurrent ticks use one lifecycle lock. Retired in-place
  updater remains disabled.

- [ ] **Step 2: Write exception routing tests**

  CLEAR remains background. WARN/BLOCKED/CRITICAL includes safe evidence,
  required decision, candidate/cell digest, and no secrets. Approval items cannot
  grant themselves authority or be satisfied by a generic/stale approval.

- [ ] **Step 3: Implement steady-state evidence**

  Include latest/target tag+SHA, deployed cell releases/profiles, candidate
  stage, approval/promotion state, rollback artifact, runtime/code parity,
  health summaries, receipt digest, and next check.

- [ ] **Step 4: Run tests**

  Run: `pytest -q tests/ik_lifecycle/test_monitor.py tests/ik_lifecycle/test_approval_inbox.py`
  Expected: PASS; no real scheduler touched.

- [ ] **Step 5: After explicit approval, activate cell-local recurring checks**

  Install schedules idempotently, record prior state/rollback, run one manual
  read-only tick, prove unchanged CLEAR is quiet, prove a fixture exception
  reaches the approval inbox once, and keep promotion disabled.

- [ ] **Step 6: Commit**

  ```bash
  git add ik_lifecycle tests/ik_lifecycle
  git commit -m "feat(lifecycle): monitor Hermes release drift without auto-promotion"
  ```

**Stop conditions:** schedule activation without approval, autonomous promotion,
duplicate notifications, retired updater re-enabled, or incomplete next-check evidence.

---

### Task 14: Full verification, documentation, and publication gate

**Owner:** Hermes project integration
**Mode:** local verification quiet; publication approval/workflow dependent
**Rollback point:** revert only owned unpromoted commits; never reset user work

**Files:**
- Modify: `docs/architecture/bert-ernie-hermes-cell-architecture.md`
- Modify: `docs/superpowers/plans/2026-08-21-bert-ernie-hermes-hybrid-migration.md`
- Create: `docs/planning-receipts/<execution-date>-bert-ernie-hermes-implementation.md`
- Modify only if required by current repo workflow: thin repo-local index links

- [ ] **Step 1: Run focused suites by ownership area**

  Run all `tests/ik_lifecycle`, `tests/ik_orchestration`, `tests/ik_models`, and
  cell fixture E2E tests. Keep failures separated by lifecycle, privacy,
  orchestration, migration, model, and live-approval surfaces.

- [ ] **Step 2: Run relevant upstream regression suites**

  At minimum: delegation, Kanban, cron, profiles, relay, gateway approvals,
  session DB/migrations, plugin registration, update safety, and prompt/history
  tests identified against the execution-time target. Use the repository's
  standard test runner and frozen environment.

- [ ] **Step 3: Run supply-chain and hygiene gates**

  Review manifest/lock diffs and hooks again; run the forbidden-version scanner,
  `git diff --check`, Markdown link checks, schema fixture validation, secret/
  private-canary scan, generated-artifact scan, and owned-path inventory.

- [ ] **Step 4: Produce the implementation receipt**

  Distinguish updater/lifecycle implementation, candidate build/test, Ernie
  promotion or blocker, Bert promotion or blocker, recurring schedule or
  blocker, rollback evidence, repository publication, Nate OS pointers, and
  every skipped/unverified surface. CLEAR stays background; exceptions surface.

- [ ] **Step 5: Verify commit and remote hygiene**

  Confirm clean owned diff, expected branch, `bitdepth` writable/default,
  `upstream` push-disabled, no unrelated commits/files, no credentials/private
  data/model artifacts, and no live receipt content in Git.

- [ ] **Step 6: Publish only through the normal verified lifecycle if authorized**

  Push the owned branch to `bitdepth`, verify remote SHA parity, and never push
  to NousResearch. If publication is not authorized, stop with the exact local
  commit SHA and verification evidence.

**Stop conditions:** any failed focused/full gate, unrelated dirty work overlap,
secret/private artifact, missing rollback evidence, upstream push target,
unresolved exception, or publication without authority.

---

## Final acceptance checklist

- [ ] Exact authoritative latest and one-behind target are recorded by tag and SHA.
- [ ] Candidate and both production cells use one code/release pipeline without divergent forks.
- [ ] Ernie/Bert profiles, credentials, state, release pointers, health, promotion, and rollback are independent.
- [ ] Cross-host transport is versioned, authenticated, acknowledged, replay-resistant, and never shares SQLite.
- [ ] Bert/Ernie persona remains responsive while background work runs.
- [ ] Work reaches Codex exactly once; personal work stays with Bert/Ernie; mixed work decomposes coherently.
- [ ] Execution ladder selects the least expensive safe rung without gaining authority.
- [ ] Self-improvement requires repeated independent evidence and cannot silently expand authority.
- [ ] Ernie web, Bert heavy personal research, and Codex work routing pass.
- [ ] Sanitizer, local mapping, reintegration, canary, and Nate OS visibility/write boundaries pass.
- [ ] Offline-Ernie produces one pending handoff, quiet 25-minute heartbeat, bounded retry, and exactly-once acknowledged transfer.
- [ ] Model selection occurs at task boundaries; current reasoning/tool-history defects are corrected and tested.
- [ ] Official Qwen3.8 is evaluated, not presumed; provenance/context/concurrency/resource/safety gates pass before any role.
- [ ] Memory/history/identity/task/schedule/Kanban/cron/provenance/approval migration passes integrity and semantic checks.
- [ ] Ernie candidate/canary succeeds before any Bert promotion request.
- [ ] Running checkout is never mutated; paired atomic promotion and RP3/RP4 rollback are proven.
- [ ] CLEAR evidence is quiet; exceptions are deduplicated and approval-scoped.
- [ ] Retired in-place updater stays disabled and no autonomous promotion exists.

## Decisions intentionally deferred to evidence

The executor should prove these in sandbox and select the passing option without
asking Nate to choose implementation trivia:

- existing Hermes relay versus a standalone overlay transport adapter;
- legacy custom commits superseded versus replayed at supported edges;
- primary/specialist model roles;
- maximum safe Mac context/concurrency;
- need for any generic upstream hook; and
- final tool/workflow split for repeated patterns.

Stop for Nate only when evidence leaves a material trade-off involving privacy,
authority, cost, reliability, live promotion, model adoption, or rollback—not
when a sandbox test can determine the safer implementation.
