# Hermes Lifecycle Task 2 Safe-Phase Receipt

Status: `CLEAR_LOCAL_SCAFFOLD`

Observed: 2026-08-21 (America/Detroit)

## Inputs revalidated

- Starting commit: `56013e40b841ffb377aab34b56119e9ba8c16ef5`
- Latest stable: `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`
- Exact one-stable-release-behind target: `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`
- The Task 1 release-selection receipt digest, writable `bitdepth` fork,
  push-disabled `upstream`, namespaced tag refspec, Nate OS authority, legacy
  customization inventory, and automation inventory remained consistent.
- Static read-only screening found no forbidden-version implementation
  evidence. No package manager, dependency resolver, or lifecycle hook ran.

## Local implementation completed

- Added independent `ernie` and `bert` cell roots for candidates, releases,
  profiles, backups, receipts, and state; all path traversal, symlink escape,
  broad-root, protected-running-path, and source/platform overlap cases fail
  closed.
- Added deterministic candidate identity, exact source-HEAD enforcement,
  clean-source checks, immutable read-only source snapshots, tree digests,
  idempotent rebuild behavior, tamper detection, retained failed candidates,
  and separate per-cell target records.
- Added canonical candidate-build and customization-replay schemas plus an
  ordered manifest for all 12 Task 1 legacy commit dispositions. No legacy
  commit was replayed into Hermes core.
- Added static manifest, lockfile, installed-package, package-manager-log,
  Docker/CI/install-command, lifecycle-hook, and planned frozen-command
  inspection. The static CLI emits a digest-covered receipt and never executes
  a planned command or hook.
- Sealing requires complete build gates, a valid paired rollback
  release/profile pointer, and digest-verified candidate-bound dependency
  approval/install receipts. A boolean cannot substitute for approval
  evidence. Sealing creates no live pointer and performs no promotion.
- `daily-bert-health-and-ops-repair=ACTIVE` is encoded as the
  `legacy_health_automation_pause` final-promotion blocker. The Computer
  History path adaptation remains a separate `approval_required` gate.

## Verification

- Red gates reproduced missing modules, then missing Python/CI supply-chain
  surfaces, source/platform overlap, writable candidate payload, candidate
  tamper acceptance, and the absent static CLI before implementation.
- Focused and full lifecycle suite:
  `TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 /Library/Frameworks/Python.framework/Versions/3.11/bin/pytest -q -o addopts='' tests/ik_lifecycle`
  -> `55 passed`.
- Python compilation of all changed lifecycle modules -> exit `0`.
- All new JSON schemas/manifests parsed successfully; `git diff --check` ->
  exit `0`.
- The canonical wrapper remains unavailable because the existing shared
  Hermes virtual environment lacks `pytest`; no dependency was installed.

## Boundary and next gate

No durable candidate from the real upstream checkout was constructed: the
current authorization hard-stops before lifecycle-script execution. Only
temporary fixture candidates were created under pytest temporary roots.

No model, profile, history, config, credential, service, schedule, automation,
live host, deployment, release pointer, promotion, push, or external state was
changed. The next gate is separately authorized construction of the real
target candidate followed by manifest/lock comparison and explicit approval
before any frozen dependency command. The legacy Bert health automation must
be approval-paused before final promotion; Computer History adaptation remains
independent and approval-gated.
