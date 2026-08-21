# Hermes Lifecycle Task 2 Source-Candidate Receipt

Status: `BLOCKED_PLATFORM`

Observed: 2026-08-21 (America/Detroit)

## Revalidated inputs

- Starting implementation commit: `1e2b4ffead43d932bae839717f4e0f1b23314b33`
- Current legacy comparison commit (`bitdepth/bert-live-main`):
  `dca28dd07b69bfb1747836e716e6b5cb6d85a340`
- Latest stable: `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`
- Exact one-stable-release-behind target: `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`
- Fresh release selection, the Task 1 machine receipt, the Task 2 receipt,
  Nate OS authority, the writable `bitdepth`/push-disabled `upstream` remote
  contract, and immutable-root guards remained consistent.
- Final refresh release-selection receipt SHA-256:
  `83093c30128858e59ea73acaad4cc752973c0a4e6606442aa0fd03a202b18c8e`.
- Candidate root: `/Users/react/.codex/state/hermes-platform`; it is outside
  the legacy checkout, isolated Codex worktree, live-service roots, and real
  profile root. No Ernie or Bert target/current pointer exists.

## Construction outcome

- Deterministic Ernie candidate ID: `1e79082041b08781ca40`
- Cell/trust zone: `ernie` / `local_private`
- Attempt 1 stopped with `source_case_collision`; attempt 2 returned the
  retained `candidate_failed` state without rebuilding or overwriting it.
- Retained non-secret failure manifest:
  `/Users/react/.codex/state/hermes-platform/cells/ernie/candidates/1e79082041b08781ca40/build-manifest.json`
- Retained failure-manifest SHA-256:
  `5be42020368005b2252b7afd2b6c35d963d99dac7872062b3ea9474c055abee3`.
- The manifest is bound to the exact target SHA and the ordered 12-entry replay
  manifest digest
  `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`.
- No source snapshot, tree digest, dependency execution, release, profile,
  pointer, Bert candidate, promotion, or rollback artifact was created.

The target commit tracks two distinct paths with distinct Git blobs:

```text
contributors/emails/agent@Agents-Mac-mini.local
contributors/emails/agent@agents-Mac-mini.local
```

They collapse to one path on the current case-insensitive macOS filesystem.
The detached target worktree is therefore dirty immediately after Git
materializes it, so it cannot be treated as an exact immutable source tree.
The lifecycle now detects normalized/case-folded tracked-path collisions before
the generic clean-tree gate and retains the exact conflicting paths as
machine-readable failure evidence. Proceeding requires an approved
case-sensitive APFS or Linux builder; silently dropping or renaming one file
would change the upstream tree and is rejected.

## Static manifest and lock comparison

The comparison used Git objects at the exact legacy and target SHAs. It did not
invoke a package manager, resolver, installer, hook, or build script.

- Changed dependency/install artifacts: 24 total — 3 added `package.json`, 1
  added `package-lock.json`, 11 changed manifests/locks, 4 removed child npm
  locks, changed `pyproject.toml`, `uv.lock`, and `setup.py`, and removed
  `MANIFEST.in`.
- Artifact paths reviewed: root `package.json`/`package-lock.json`;
  `pyproject.toml`; `uv.lock`; `setup.py`; `MANIFEST.in`; package manifests or
  locks under `apps/bootstrap-installer`, `apps/desktop`, `apps/shared`,
  `plugins/platforms/photon/sidecar`, `scripts/whatsapp-bridge`, `tests-js`,
  `ui-tui`, `ui-tui/packages/hermes-ink`, `web`, and `website`.
- Direct dependency delta: npm 50 added, 164 changed, 33 removed; Python 36
  added, 26 changed, 3 removed.
- Lock delta: root npm 364 added, 356 changed, 547 removed package entries;
  website npm 17/291/69; WhatsApp npm 3/52/2; new Photon npm lock 137 added;
  `uv.lock` 41 added, 34 changed, 5 removed package names.
- Important Python core additions include `certifi`, `cryptography`,
  `Markdown`, `nemo-relay`, `packaging`, `pathspec`, `Pillow`,
  `python-multipart`, `urllib3`, `websockets`, and platform-conditional
  `concurrent-log-handler`/`pywin32`; `PyJWT` changes from 2.12.1 to 2.13.0.
  The target also pins build-time `setuptools==83.0.0` and contains material
  optional-extra changes including MCP 2.0.
- The removed desktop, TUI, Hermes Ink, and web child locks are consolidated
  under the target root npm workspace lock. Website, WhatsApp, and Photon keep
  dedicated locks. No remote npm or Git dependency was added; new
  `@hermes/shared` references are local `file:` workspace dependencies.
- No implementation evidence for `axios@1.14.1`, `axios@0.30.4`, or
  `plain-crypto-js@4.2.1` was found in the target manifests, committed locks,
  or install surfaces.

## Runtime, reproducibility, and lifecycle review

- Target Python requirement: `>=3.11,<3.14`; local Python 3.11.3 satisfies it.
- Target root/desktop Node requirement: `>=22.22.0`; local Node 23.9.0
  satisfies the declared range, but its odd major is not the preferred LTS
  baseline for a reproducible release builder.
- Target root npm requirement: `<11.10.0 || >=11.17.0`; local npm 10.9.2
  satisfies it. Website requires npm `>=11.17.0`, so a full-repository install
  cannot use the current npm runtime. No `packageManager` field pins npm, which
  is a reproducibility warning that must be resolved in the builder contract.
- Python uses a committed hashed `uv.lock`, `uv sync --frozen`, and a target
  `exclude-newer` policy. Root npm workspaces and each retained independent npm
  surface have committed locks.
- Root `postinstall` changed only its echo text and is low risk.
- Photon adds `postinstall: node patch-spectrum-mixed-attachments.mjs`. The
  reviewed script deliberately and idempotently rewrites installed
  `node_modules/@spectrum-ts/imessage/dist/*.js`, requires exact source-anchor
  counts, preserves line endings, and fails loudly on drift. It must stay
  disabled during audit install and needs its own exact-lock fixture test and
  approval before any scripts-enabled runtime install.
- `setup.py` now guards wheel/sdist creation outside Nix while allowing editable
  installs; the PEP 517 backend is `setuptools.build_meta`. It was inspected but
  not imported or executed.
- New/changed desktop build hooks were inspected statically. They clean and
  write build outputs, stamp Git identity, bundle with esbuild, copy/patch
  native `node-pty`/`get-windows` payloads, may run `electron-rebuild` or
  `npm rebuild get-windows`, patch installed `electron-builder`, validate
  output, and may let electron-builder fetch an Electron distribution. They
  are build/dependency/external-download surfaces and remain outside the
  approved audit-install phase.
- New root `install:*` scripts call non-frozen `npm install`; Docker and install
  helpers also contain non-frozen/fallback installer paths. They are rejected
  for the controlled candidate build in favor of committed-lock commands.

## Planned commands and blocked approval bundle

The static scanner proposed these exact future audit commands, all with scripts
disabled, and executed none:

```text
.                                      uv sync --frozen --no-install-project
.                                      npm ci --ignore-scripts
website                                npm ci --ignore-scripts
scripts/whatsapp-bridge                npm ci --ignore-scripts
plugins/platforms/photon/sidecar       npm ci --ignore-scripts
```

No dependency command is currently approvable. A digest-bound approval must
include the successfully built candidate ID, exact source SHA, source tree
SHA-256, every manifest/lock SHA-256, replay-manifest SHA-256, planned-command
digest, reviewed-hook disposition, builder Python/Node/npm identities, and the
specific command/workdir. The case collision prevents a source tree digest,
and the website npm mismatch prevents one shared current-runtime command set.

The next approval decision is therefore builder topology, not dependency
execution: authorize a case-sensitive, isolated builder and select a pinned
supported npm runtime (or explicitly exclude website from the runtime candidate
while retaining a separate docs gate). After that candidate is built and
digest-bound, the first dependency command may be presented for separate
approval; it must not be inferred from this receipt.

## Verification and boundary

- Test-first coverage reproduces and then detects a tracked case collision,
  returns stable `source_case_collision`, and retains the sorted conflicting
  paths in the failed manifest.
- Full lifecycle suite:
  `TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 python3 -m pytest -q -o addopts='' tests/ik_lifecycle`
  -> `56 passed`.
- Python compilation of `ik_lifecycle/*.py`, parsing every lifecycle JSON
  schema/manifest, and `git diff --check` -> exit `0`.
- The active legacy Bert health automation remains a final-promotion blocker;
  Computer History adaptation remains separately approval-gated. Neither was
  changed.
- No package manager, dependency resolver, installer, lifecycle hook, build
  script, model, private profile/history/config/credential, service, schedule,
  automation, SSH/live Bert surface, restart, promotion, deployment, push, or
  other external system was executed or changed.
