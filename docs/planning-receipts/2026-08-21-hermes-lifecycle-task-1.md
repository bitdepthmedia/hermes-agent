# Hermes Lifecycle Task 1 Receipt

Status: `CLEAR`

Observed: 2026-08-21 (America/Detroit)

## Scope completed

- Refreshed current Nate OS routing, standing rules, Bert/Hermes, background,
  shared-memory, privacy, supply-chain, and evaluation contracts read-only.
- Revalidated the isolated branch, planning commit, writable fork,
  push-disabled upstream, namespaced upstream tags, and `upstream/main` fetch
  contract without changing Git metadata.
- Re-screened manifests, lockfiles, installed-package surfaces, package hooks,
  Docker/CI, and executable install/update paths. No forbidden-version
  implementation evidence was found; no dependency command ran.
- Classified all twelve legacy custom commits and inventoried the two local
  Codex automations with a Hermes/Bert/Ernie or adjacent Nate OS migration
  relationship in
  [the migration inventory](../architecture/hermes-legacy-customization-and-automation-inventory.md).
- Added a read-only remote-contract validator, authoritative release discovery,
  exact one-stable-release-behind selection, immutable canonical receipts, and
  a source-checkout CLI.

## Current release evidence

- Latest stable: `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`
- Selected target: `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`
- Selection reason: immediately previous published stable release
- Machine receipt:
  [2026-08-21-hermes-release-selection.json](2026-08-21-hermes-release-selection.json)

The selector reads official GitHub release metadata and resolves both selected
tags twice with `git ls-remote`. Drafts and prereleases are ignored; ambiguity,
missing or moving tags, invalid metadata, fewer than two stable releases, and
remote-contract drift fail closed.

## Verification

- Red gate: focused remote tests initially failed with
  `ModuleNotFoundError: ik_lifecycle`.
- Red gate: release/receipt tests initially failed because those modules did
  not exist.
- Red gate: the source-checkout executable test reproduced an import-path
  failure before the launcher fix.
- Green gate:
  `TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 /Library/Frameworks/Python.framework/Versions/3.11/bin/pytest -q -o addopts='' tests/ik_lifecycle/test_remote_contract.py tests/ik_lifecycle/test_release_discovery.py tests/ik_lifecycle/test_receipt.py`
  -> `20 passed`.
- `scripts/ik-hermes-lifecycle --help` -> exit `0`.
- `scripts/ik-hermes-lifecycle remote-audit --repo .` -> `CLEAR`.
- `scripts/ik-hermes-lifecycle release-select --repo .` -> selected the SHAs
  above and emitted a digest-verified canonical receipt.

The canonical repository test wrapper could not be used because the existing
shared Hermes `.venv` lacks `pytest`; installing it is outside this phase. The
focused suite used an already-installed Python 3.11 pytest and disabled only
the unavailable timeout-plugin addopts. No test dependency was installed.

## Automation cutover exception

`daily-bert-health-and-ops-repair` remains `ACTIVE` and still mixes mutable
legacy local and cloud runtime assumptions; its prompt also retains stale
`main` wording. This does not overlap the isolated Task 1 scaffold, but it must
be approval-paused before the final Ernie promotion snapshot, and no
replacement may activate concurrently. `computer-history-to-nate-os` remains
active and non-overlapping, but its disposable Nate OS worktree path needs a
separate approved adaptation before that worktree is retired. No automation was
changed.

## Boundary and next gate

No model, profile, history, credential, service, schedule, automation, live
host, deployment, push, or external state was changed. No package manager or
dependency command ran.

The next dependency-ordered task is screened immutable candidate construction
from the selected target. Candidate checkout/build preparation may proceed only
under the plan's Task 2 gates; dependency execution, profiles, schedules,
services, promotion, and live Bert remain separately approval-required.
