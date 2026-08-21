# Hermes overlay Tasks 3-10 code-only receipt

Date: 2026-08-21

Status: **CLEAR — local code-only phase complete**

## Authority and immutable inputs

- Branch: `codex/hermes-staged-lifecycle`.
- Starting commit: `e18b5e461b260f1cf8c0b67365b83fcb02eef571`.
- Latest verified upstream stable release remained `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`.
- Exact one-stable-release-behind target remained `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`.
- Immutable source candidate remained `1e79082041b08781ca40`, with its retained
  source tree and dependency-audit evidence unchanged.
- Reviewed 12-commit replay manifest SHA-256:
  `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`.
- Declared overlay source SHA-256:
  `3ae5bbe101fb39aa531add3197d13d2d4cd0993add82ba55c00f08914ba46274`.

## Implemented contracts

- Versioned delegation, result, acknowledgement, and improvement contracts;
  canonical digests; immutable ownership history; hop and anti-loop gates.
- Deterministic work/personal/mixed routing, exactly-once Codex work ownership,
  cheapest-safe execution ladder, and a disabled-by-default supported plugin edge.
- Allowlist-first cloud sanitization, local-only mapping/reintegration, private
  canary rejection, and thin Nate OS identity/visibility decisions.
- Cell-local SQLite handoff state with idempotency, CAS, monotonic acknowledgement,
  authenticated loopback transport, and quiet offline-Ernie retry with a
  configurable 25-minute base plus deterministic 20-30 minute jitter.
- Evidence-gated self-improvement candidate and authority decisions.
- Non-secret profile inventory, SQLite online backup, destination-clone migration,
  explicit legacy transforms, and semantic integrity gates.
- Model capabilities, task-boundary primary/specialist routing, Qwen-compatible
  tool-history normalization, official-artifact provenance gates, and frozen
  synthetic/public eval suites. No model artifact was accessed or changed.
- Independent Ernie/Bert cell manifests, all-CLEAR health gates, the approval-pause
  blocker for the legacy Bert automation, paired pointer journaling/recovery, and
  RP3/RP4 rollback decisions.
- Deterministic official-source plus declared-overlay composition, a fail-closed
  network-denial adapter boundary, and immutable release bundles bound to composed
  source plus runtime/build artifacts.
- The legacy upstream-only sealer now fails closed. Code sealing accepts only a
  read-only composed/artifact-bound bundle and records that profile pairing,
  rollback pairing, and promotion have not occurred.

## TDD evidence

Initial RED evidence included missing modules for Tasks 3-10, the former
`rollback_prerequisite_missing` upstream-only sealing path, absent CAS/crash
recovery/eval harness behavior, and a sanitizer that did not yet reject a canary
inside an allowed field. Each case was implemented and rerun GREEN. Two fixture
errors were corrected without weakening production gates: an intentionally stable
digest literal and a malformed non-hex provenance digest.

Final focused verification ran the same code-only suites with both system Python
3.14 and the already materialized pinned audit Python 3.11. No package manager,
installer, lifecycle hook, build command, model runtime, or network operation ran.

- Per runtime: 20 orchestration tests, 4 model-worker/eval tests, 2 continuity
  tests, 4 cell/health/rollback tests, 4 composed-release tests, 2 sealing-split
  tests, and 10 existing execution-plan tests.
- Aggregate: 46 tests per runtime; 92 test executions; all PASS.
- AST parse: CLEAR for owned Python and script sources.
- `git diff --check`: CLEAR.
- Forbidden-version implementation diff: CLEAR.
- Repository-root scanner cache hits were only passive ignored
  `.pytest_cache/v/cache/nodeids` references, not manifest, lock, installed
  metadata, package-manager cache/log, CI, Docker, or executable install evidence.
  The unchanged root `package.json` postinstall hook was not introduced by this phase.

## Boundaries preserved

No dependency command, lifecycle/build hook, model download/configuration, real
profile/history/config/credential access, service or port action, schedule or Codex
automation mutation, live Bert/SSH access, restart, promotion, deployment, push,
or external-state action occurred. The immutable candidate and dependency-audit
artifacts were read only.

The active legacy Bert health automation remains a final-promotion blocker until
approval-paused. Computer History heartbeat path adaptation remains a separate
approval-gated action. Ernie and Bert retain independent profile, state, release,
health, promotion, and rollback records.

## Remaining gate

The next phase is the previously separated scripts-enabled build/test gate for the
real composed candidate: construct the real composed tree from the immutable
`v2026.8.18` source and reviewed overlay, execute only the separately approved
digest-bound build/test commands under proven OS network denial, bind real runtime
and built artifacts, and seal the code bundle. Private profile cloning/migration,
rollback pairing, automations, services, Ernie promotion, and later Bert work each
remain distinct approvals.

The repository's pytest-based legacy lifecycle suite was not executed because no
currently authorized/pre-existing Python runtime contains pytest. Its sealing
fixtures were updated for the composed-bundle contract and received static/AST
review; execution belongs to the next already-planned scripts-enabled test-runtime
gate rather than an incidental dependency install in this phase.
