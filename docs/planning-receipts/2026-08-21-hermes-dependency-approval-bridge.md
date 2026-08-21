# Hermes dependency approval bridge receipt

Date: 2026-08-21

Status: `CLEAR` for the local validator bridge; `APPROVAL_REQUIRED` for dependency execution.

## Result

Candidate sealing now requires a verified derived execution plan and binds both
the approval receipt and dependency-install result to that plan. The earlier
relative static-command digest cannot authorize sealing.

The validator checks:

- the execution plan's self-digest and command-list digest;
- candidate id, manifest digest, source commit/tree, replay manifest, and
  supply-chain artifact-map bindings;
- `execution_performed: false` and `APPROVAL_REQUIRED` state;
- absolute command workdirs confined to the candidate's disposable dependency
  audit mirror;
- approval receipt bindings to the execution-plan and command digests; and
- install-result bindings to the same digests and the exact approval-file hash.

Real prepared-candidate validation was read-only and returned:

- candidate: `1e79082041b08781ca40`
- execution-plan SHA-256:
  `22e282205ea1a878951d026efc4125357109aa7d710c4baecc1afeeb69f23dca`
- command-list SHA-256:
  `049f37b1303caed35d463d61a0cf386dcc864f84f78070ab6f21a9d208e96732`
- command count: `5`

## Verification

- Red phase: the legacy static-plan receipt incorrectly sealed; derived-plan
  receipts and tamper detection failed before the implementation.
- Green phase: legacy receipt rejection, valid derived-plan sealing, and
  tampered-plan rejection all pass.
- Lifecycle suite: `59 passed, 1 skipped` on the host filesystem.
- The exact real approval input passes the new derived-plan validator.
- Candidate source screening remains `CLEAR`. The worktree scanner's three
  forbidden-version hits were only generated `.pytest_cache` node ids; tracked
  manifests and locks contain no forbidden resolution, and this change does not
  modify the existing package lifecycle-hook surface.
- The isolated dependency environment contains no files, no cache files, and no
  `node_modules` directory.
- No dependency/package-manager command, lifecycle hook, or build script ran.

## Remaining approval boundary

The next step would materialize dependency trees only inside the disposable
candidate audit mirror using the five digest-bound commands in
[the approval input](2026-08-21-hermes-dependency-approval-input.json): one
frozen, no-build Python sync and four npm clean installs with lifecycle scripts
disabled. Execution is not authorized by this receipt.

The active `daily-bert-health-and-ops-repair` automation remains a final
promotion blocker until separately approval-paused. Computer History path
adaptation also remains separately approval-gated. No automation was changed.

Skill-learning closeout: no skill change is eligible. The durable correction is
the lifecycle validator plus regression coverage, and dependency/supply-chain
authority is explicitly outside automatic skill-learning scope.

Execution was later attempted under explicit approval and stopped on the
second command before npm configuration resolution. See the
[blocked dependency-audit receipt](2026-08-21-hermes-dependency-audit-attempt.md).
