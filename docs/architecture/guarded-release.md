# Guarded Hermes Releases

Operator tooling for the Bert/Ernie lifecycle. This does not authorize a release,
install dependencies, run lifecycle scripts, communicate across hosts, or update
the running checkout. The canonical policy is Nate OS `workflows/bert-hermes.md`.

## Execution Boundary

Use `scripts/ik-hermes-lifecycle` from the reviewed operator checkout. Read-only
`release-select` proves the current one-stable-behind target; `drift-audit
--release PATH` validates sealed integrity and distinguishes currency from drift.
Neither command promotes. Legacy artifacts without source provenance are
`UNVERIFIED`, not retroactively certified.

The guarded commands are `export-source`, `seal`, `verify-artifact`, and `promote`. Each requires
`--plan PATH --approve-sha256 DIGEST`. `plan-digest --plan PATH` calculates the
digest; calculation alone is not approval. The operator must obtain approval for
those exact bytes through the existing approval channel. JSON plans contain:

- `schema_id`: `ik.hermes.guarded-release.v1`.
- `operation`: the exact command; `authority`: the approval reference.
- `expires_at`: timezone-aware expiry no more than 24 hours away.
- `reviews`: separate `supply_chain` and `privacy` objects, each with `path` and
  `sha256` binding a reviewed JSON receipt with `status: CLEAR`, `authority` and
  `subject_sha256`. The subject is SHA256 of the UTF-8 JSON plan without `reviews`,
  serialized with sorted keys and separators `(',', ':')` (Python's default ASCII
  escaping), also available as `guarded_release.review_subject(plan)`.
  Reviewers must cover the exact inputs in this plan; these local receipts are
  operator attestations, not signatures or grants of authority.
- `provenance`: `repository`, full `implementation_commit`, and optional relative
  `overlay_manifest` (the declared Bert/Ernie extension manifest).

Supply-chain/privacy review remains a separate gate even though probes use an
empty temporary home and do not inherit credentials. This is environment
isolation, not a network or operating-system sandbox. Unknown dependencies and
lifecycle scripts require their own approval and are never installed here.

## Seal And Verify

First use `export-source` with `source` (a new isolated destination), nonempty
`protected_roots`, `target_tag`, `target_commit_sha`, and the common plan fields.
It exports raw Git blobs and verifies provenance. Ordinary `git archive` and
checkout can apply line-ending filters; those are not exact-object exports.

`seal` takes `release_root`, nonempty `protected_roots`, and `inputs` containing
`candidate_id`, `target_tag`, `target_commit_sha`, `source`, `surfaces` (name to
directory), `lockfiles` (name to path), `router_config`, `model_manifest`, and
`expected_python` (major/minor). Prepare these in isolation with separately
reviewed tools. Source must be the exact committed implementation export, without
`.git`, caches, or untracked files. Every upstream tracked file must remain
byte/mode identical; additions must exactly match the declared overlay. Copying
an older core file into a newer release is rejected before interpreter execution.

Sealing rechecks imports and real terminal/file dispatch at the final immutable
path, then checks artifact integrity again. Failure quarantines a newly sealed
candidate. Existing artifacts also repeat probes; their mere existence cannot
skip validation. No profile credentials enter these checks.

`verify-artifact` takes `release` and `manifest_sha256`. It repeats Git provenance,
sealed integrity, imports, and real tools. Its CLEAR receipt includes host, time,
manifest digest, target commit, and source digest. Save receipts privately.
Each host verifies independently; matching source does not mean platform-specific
Python/runtime surfaces have identical digests.

## Approved Host-Local Promotion

`promote` additionally takes `cell_root`, `release`, optional `deployment` for a
cell wrapper, a new `profile` destination, `expected_current` as
`[release, profile, generation]`, `release_roots`, `profile_roots`, and
`peer_receipt`. The independently acquired peer receipt must be CLEAR, from a
different host, less than 15 minutes old, and match source identity. It is not
authenticated cross-host transport proof; the missing production adapter remains
BLOCKED. The command performs no SSH or automatic receipt transfer.

`services` contains `kind` (`launchd` or `systemd-system`) and ordered `units`.
Each unit binds `name`, `definitions` (installed absolute file paths to SHA256),
`program`, `program_sha256`, `workdir`, `profile`, and systemd `account`.
A unit's optional `candidate` object overrides next-generation program/workdir/
profile bindings. `prepared_program` locates the candidate launcher before a
`current-release` pointer switches. Unit identity and installed definition paths
cannot change. Optional `definition_changes` contain `installed`, `candidate`,
and `sha256`; only already-declared definition files may be replaced, while
services are closed. Backups retain bytes, mode, owner and group.

`health` contains nonempty `profiles` (relative `path`, nonempty `platforms`) and
`ports` (loopback TCP ports). Verification binds fresh gateway state/heartbeat to
the service PID, approved Python executable, source cwd/PYTHONPATH, and expected
HERMES_HOME. Connected Telegram is observed without sending a message. Native
loaded service bindings must agree with the approved configuration. This generic
gate does not replace model/reasoning/latency/credential-binding evaluations in
the canonical audit.

Promotion takes an exclusive cell lock, validates the rollback artifact, stops
services, snapshots the current independent profile, switches the journaled
release/profile pair, checks startup, performs a second restart, checks new PIDs
and fresh evidence, and repeats final artifact validation. Private evidence and
definition backups are retained under `promotion-evidence`.

On failure it closes partial starts. If durable candidate state is unchanged,
it restores definitions and the prior pair, then verifies rollback health. If
durable state changed, it leaves services closed and the candidate preserved with
`rollback_reconciliation_required`. Do not discard state or blindly retry;
reconciliation requires operator approval. No unattended promotion is enabled.

## Limits

These gates protect the supported operator path, not an administrator deliberately
bypassing it. Existing live services are not restarted by installing this tooling.
OS service integration must be accepted on each native host in the next approved
rollout. Full model turns, restart durability, external delivery and credential
binding are separate evidence tiers; local fixture tests prove none of those live
claims. Keep retired updater schedules disabled and retain one read-only audit.
