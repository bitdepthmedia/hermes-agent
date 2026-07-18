# Daily Goal Coordinator Final Fix Report

## Outcome

All first-, second-, and third-pass findings are fixed locally on
`ik/daily-goal-coordinator`. No live profile, scheduler, Telegram,
DigitalOcean, or activation action was performed.

## Finding closure

### Fifth-pass closure

1. Explicit cron/task pending evidence now takes precedence over incomplete
   conversation history; incompleteness gates idle activation and candidates.
2. Ernie completed candidates require `cutoff <= updated_at <= now`; future
   completed or unresolved records are excluded.
3. A persisted fair selector alternates across all due original and alert
   outboxes, preventing starvation while retaining atomic per-item claims.

### Fourth-pass closure

1. Ernie completeness now combines cap/window coverage, valid timestamps, and
   uncapped per-session entry history; no later check can overwrite failure.
2. Bert idle activation requires complete bounded SessionDB and cron/task
   pagination, while conversations remain non-pending.
3. Verified `PENDING_WORK` retains suppression precedence even with incomplete
   activation provenance; strict provenance applies to idle claims.
4. Ernie candidates are limited to completed records from seven days plus
   explicitly unresolved records; old completed records are excluded.
5. Current eligible original delivery precedes older alerts, then later runs
   drain prior/new alerts without orphaning either queue.
6. Delivery redaction reuses the shared robust redactor and covers compound
   environment secret names, bot URLs, query secrets, and auth headers.

### Third-pass closure

1. Ernie history consumes the real `{sessions,count}` contract and derives
   seven-day completeness from the 25-record cap and real timestamps. It emits
   a canonical client-side digest and record-bound candidates.
2. Trigger resolution requires correct identity, evidence, source receipts,
   current-date freshness, and complete history; invalid idle claims normalize
   to `UNKNOWN`.
3. SessionDB conversations no longer count as tasks. Bert status derives only
   from complete cron/runtime task sources.
4. Clean complete histories select the deterministic process-health fallback
   with evidence bound to both completeness receipts.
5. UNKNOWN retry has a recoverable leased `in_progress` state and durable
   `completed` state.
6. Daily-goal Telegram sends bypass the adapter's three-attempt loop and use a
   bounded single-attempt path.
7. Operator alerts drain across prior cycles with exactly-once claims.
8. Persisted/rendered delivery errors redact URLs, bot tokens, authorization
   headers, query credentials, and generic secrets.

### Second-pass closure

1. Bert status now scans every bounded SessionDB page, retains terminal
   records from seven days plus every older unresolved record, and derives
   status deterministically from complete tracker/session coverage. Model
   status and evidence must exactly match that derivation.
2. Ernie `result-backed` outcomes are terminal only when verification and
   postcheck both passed.
3. Ernie history requires an explicit bounded-complete attestation. Candidate
   evidence on either side must reference a specific attested record; empty or
   digest-only evidence is ineligible.
4. Counterpart review now returns structured observations recomputable from
   the fixed execution metrics. Generic acknowledgements, deleted review
   fields, and observation tampering fail closed.
5. Receipts persist local date, per-agent evidence, freshness, source receipts,
   review observations, and a decision-integrity hash covering all semantic
   fields.
6. UNKNOWN watchdog retry is a durable one-per-date claim, distinct from the
   renewable check-in lease.
7. Ambiguous or exhausted original delivery creates a deduplicated operator
   alert outbox with bounded error context. The ambiguous original is never
   resent.
8. Delivery failures carry definitive/ambiguous certainty. Only explicit
   definitive failures are retryable; generic transport failures are
   ambiguous.
9. Standalone sends run behind a hard return deadline, so a non-returning
   adapter cannot retain the scheduler lock indefinitely.
10. The mutating coordinator is no longer registered in ordinary model tool
    schemas; cron invokes its private module entry point directly.

### First-pass closure

1. Ernie work-queue collection now uses
   `/ik/ernie-dashboard/work-queue/status`, validates `item_count`,
   `status_counts`, and the bounded 20-row page, and treats unseen ready work
   as `UNKNOWN`.
2. Bert status and review use the authenticated loopback-only
   `/v1/orchestrator/read-only` adapter. The server constructs
   `AIAgent(enabled_toolsets=[])` with memory, context, and persistence
   disabled; client and shared-core code validate no-tools attestations and
   digests.
3. Verified `PENDING_WORK` has precedence over `UNKNOWN`, including watchdog
   replay.
4. Queue states and session outcomes now match Ernie's production
   vocabularies and have parameterized coverage.
5. Bert status receipts include bounded, paginated SessionDB and cron
   metadata. Incomplete history forces `UNKNOWN`, and candidate ranking rejects
   incomplete history.
6. The invalid failed-session-to-system-health candidate generator was
   removed.
7. Counterpart review is bound to candidate ID, executor ID, execution summary
   hash, and an echoed caller receipt. Statement, source, metrics hash, and a
   composite integrity hash persist in SQLite; missing, mismatched, or tampered
   review receipts fail closed.
8. Telegram delivery has a durable SQLite outbox. The scheduler atomically
   records `attempting` before send, retries only a clearly failed attempt,
   suppresses ambiguous attempts, and emits explicit operator alerts for
   blocked or unknown outcomes. Live and standalone timeout ambiguity never
   falls back to a second send.
9. Profile deployment holds `cron/.tick.lock` across validation, backup, both
   atomic writes, post-write validation, and cross-file rollback. Contention
   exits 75 without mutation.
10. Manual trigger acquires the scheduler lock before `trigger_job` and returns
    distinct `busy`, `executed`, and `failed` outcomes.
11. The wrapper changes into the validated Hermes checkout before Python
    imports; absolute invocation outside the repository is covered.
12. Direct daily-goal execution runs in a new process group with the manifest
    deadline. Timeout sends TERM/KILL to the group and reaps the process.
13. Loopback HTTP uses a 50-second budget for `/v1/ernie/status` and a
    10-second default for fast endpoints.

## Verification

```text
python3 -m pytest -o addopts='' -q \
  tests/shared_core \
  tests/tools/test_call_orchestrator_tool.py \
  tests/tools/test_daily_goal_coordinator_tool.py \
  tests/cron \
  tests/ik_profiles

375 passed, 4 skipped in 15.92s
```

```text
python3 -m pytest -o addopts='' -q \
  tests/gateway/test_api_server_toolset.py \
  tests/tools/test_call_orchestrator_tool.py

27 passed in 0.25s
```

Additional checks passed:

- `python3 -m compileall -q` on all changed Python runtime modules;
- `bash -n scripts/ik-orchestrator-access-node scripts/ik-ernie-daily-goal
  scripts/ik-orchestrator-last30days`;
- `git diff --check`;
- no dependency manifest or lockfile changed;
- no forbidden-version implementation evidence was introduced.

The broader async API test files are not runnable under the global interpreter
because the existing pytest async plugin is absent. No dependency installation
or refresh was attempted. The synchronous no-tools API/toolset coverage above
is green. The four required-suite skips are the existing `croniter`-unavailable
skips in `tests/cron/test_jobs.py`.

## Commits

- No-tools adapter: `4219aa6c2 feat: enforce no-tools orchestrator adapter`
- First-pass safety fixes: `95a240d6b fix: close daily goal coordinator safety gaps`
- Second-pass fixes and this report: committed together after verification.
- Third-pass real-contract and delivery fixes: committed after verification.
- Fourth-pass completeness, precedence, queue, and redaction fixes: committed
  after verification.
- Fifth-pass pending precedence, candidate-window, and fair-queue fixes:
  committed after verification.

## Activation state

Not authorized and not attempted. There were zero live profile writes,
scheduler activations, Telegram sends, DigitalOcean operations, dependency
installs, or external network calls in this fix pass.
