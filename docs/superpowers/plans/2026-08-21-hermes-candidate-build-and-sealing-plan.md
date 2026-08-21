# Hermes candidate build, sealing, and rollback gate plan

Date: 2026-08-21
Status: `BLOCKED_PREREQUISITES / NO COMMAND AUTHORIZED`

This plan refines Tasks 3–10 of the committed hybrid migration plan. It is
bound to candidate `1e79082041b08781ca40`, upstream tag `v2026.8.18`, commit
`e624e9fde561e1add9388384012b295fde669ade`, and source-tree SHA-256
`d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`.
The latest stable release remains `v2026.8.19`, so the target still satisfies
the immediately-previous-stable-release policy.

The digest-bound machine plan is
[`2026-08-21-hermes-candidate-execution-plan.json`](../../planning-receipts/2026-08-21-hermes-candidate-execution-plan.json):

- plan SHA-256: `2b656af94ad6d2bdfe866143f05b5432876bbab9848bfda8c50cf240ea285ab5`;
- command-list SHA-256: `bd9253cf6b332aff96c95d46019e1de7c35de104097980bbf03abb7686bcf224`;
- command count: 10;
- execution authority: `false`.

## Critical plan review

The current immutable candidate is an exact upstream snapshot. It does not
contain `ik_lifecycle`, the declared extension overlay, cell contracts,
routing/privacy/offline-Ernie machinery, semantic migration code, or focused
Nate OS evaluations. The current `seal_candidate()` copies only the upstream
source tree. It does not bind a composed overlay, Python runtime environment,
TUI bundle, dashboard bundle, or their receipts. It also requires real
release/profile rollback pointers while sealing, before private profile access
is authorized.

Therefore, running build or test commands now would validate upstream alone,
not the intended Bert/Ernie platform. Those artifacts would be invalidated by
the missing overlay. The next safe phase is local code-only prerequisite work,
not script execution.

Required corrections before any command becomes approval-ready:

1. Implement the supported-edge contracts in Tasks 3–10 with synthetic tests.
2. Assemble a deterministic writable `composed-source` from immutable upstream
   plus the declared overlay; bind both tree digests and replay order.
3. Make a release bundle bind source, overlay, runtime environment, built TUI,
   built dashboard, test receipts, and provenance.
4. Separate candidate sealing from real profile rollback proof. Sealing needs a
   rollback-bundle contract; promotion needs the real release/profile pair.
5. Add a local executor that proves network denial for build/test batches.

## Surface decisions

The full package-script and install-hook inventory is
[`2026-08-21-hermes-script-hook-inventory.json`](../../planning-receipts/2026-08-21-hermes-script-hook-inventory.json),
SHA-256 `6eb96636c7af719cc1dbac40702cf9a7432fd0274795f83064d352bc6e4afaef`.

| Surface | Decision | Reason |
| --- | --- | --- |
| Python/Hermes core | Required | Runtime, gateways, profiles, state, cron, tools and extension contracts live here. Use locked `uv` sync without project install/build, then run from immutable source plus a bound environment. |
| `ui-tui` + Hermes Ink | Required | The dashboard embeds the real TUI; prebuild to prevent runtime auto-install/build. |
| `web` dashboard | Required | `hermes dashboard` needs `hermes_cli/web_dist`. |
| `apps/shared` | Required transitively | Dashboard imports its source; check it separately. |
| `tests-js` | Required validation | Root JavaScript contract tests. |
| Website | Excluded | Documentation only. Its prebuild may fetch the live skills index and generate files. |
| WhatsApp bridge | Profile-contingent | No build script. Defer lifecycle hooks; run only static checks until approved profile evidence shows it is used. |
| Photon sidecar | Profile-contingent | Native `better-sqlite3` install and a source-patching postinstall require their own approval. |
| Desktop/Electron | Excluded | Neither headless Ernie nor Bert cell is a desktop distribution. Hooks may download Electron/native binaries and packaging can sign/notarize. |
| Bootstrap/Tauri installer | Excluded | Distribution installer, not a cell runtime. |
| `setup-hermes.sh` | Rejected | May download uv/Python, mutate an install, and fall back across dependency sets. |
| Wheel/sdist build | Rejected | Upstream `setup.py` intentionally blocks wheel/sdist outside Nix. |

No scripts-enabled `npm ci` is proposed. The cleared V2 `node_modules` trees
remain the only dependency materialization input. Explicit `npm run` commands
retain `--ignore-scripts --offline`; that permits the named script while
suppressing automatic pre/post hooks.

## Lifecycle and transitive behavior

### Required build scripts

- `ui-tui build` executes `ui-tui/scripts/build.mjs`, which calls pinned
  `esbuild@0.28.1`, reads TUI/Ink sources, writes
  `ui-tui/dist/entry.js`, and strips the output shebang. It has no intended
  network behavior. The already materialized `@esbuild/darwin-arm64` binary
  must work; the build fails rather than running `esbuild` postinstall.
- `web build` executes `tsc -b && vite build`, reads `web`, `apps/shared`, and
  locked packages, empties/writes `hermes_cli/web_dist`, and has no intended
  network behavior in production-build mode.
- `ui-tui check` builds Hermes Ink, type-checks, runs Vitest, and lints. It may
  rewrite only the Ink `dist` directory.
- `web`, `apps/shared`, and `tests-js` checks type-check, test where defined,
  and lint. They must not write source.
- `scripts/run_tests.sh` uses a Python with `pytest`, applies deterministic
  locale/time/hash settings, performs best-effort compileall, and runs the
  upstream parallel test runner. It must receive a synthetic `HERMES_HOME` and
  an executor-enforced network deny.

### Reviewed automatic install hooks

- Root `postinstall` only prints a message and is unnecessary.
- `esbuild` postinstall can spawn npm or fetch a registry tarball and rewrite
  the binary wrapper. It will not run.
- Electron, `get-windows`, `electron-winstaller`, and `node-pty` hooks select,
  download, compile, copy, or remove platform binaries. They are excluded with
  the desktop surface unless later headless-runtime evidence specifically
  proves `node-pty` is required.
- `unicode-animations` postinstall is explicitly denied by upstream
  `allowScripts` and stays denied.
- Website `core-js` writes only funding-banner state, while the nested
  `ljharb-monorepo-symlink-test` hook runs `lerna bootstrap`; neither will run.
- WhatsApp's Baileys preinstall checks Node >=20 and protobufjs postinstall
  checks compatibility. Both remain deferred with the bridge.
- Photon's `better-sqlite3` install downloads a prebuild or invokes node-gyp.
  The sidecar postinstall then idempotently patches installed Spectrum output.
  Both require a separate optional-feature decision.

Any manifest, lock, installed metadata, cache, log, CI/Docker command, or
planned executable command that implements a forbidden package version stops
the phase. Passive policy text remains non-evidence.

## Dependency-ordered command batches

Every argv, workdir, environment, timeout, mutation, artifact, network policy,
failure-retention rule, and command digest is in the machine plan. No batch is
currently authorized.

### Batch A — Python test environment

One frozen `uv sync` creates `env/python-test` from the committed lock with the
`dev` extra, no project install, no build, no config, no Python download, and an
explicit PyPI default index. This is a dependency/network approval separate
from build scripts. It writes only the isolated environment/cache. On any
failure, retain both plus logs.

Gate: composed-source manifest/lock digests must match the reviewed upstream
artifacts; an overlay may not change dependencies without restarting supply-
chain review.

### Batch B — required runtime assets

Run the two offline explicit builds for `ui-tui` and `web`. The execution
adapter must deny network. Hash all outputs, prove no non-output source change,
remove only outputs in a fresh build fixture, run again, and require identical
artifact digests. Preserve failed outputs/logs instead of cleaning them.

Expected release artifacts:

- `ui-tui/dist/entry.js`;
- `ui-tui/packages/hermes-ink/dist/entry-exports.js`;
- `hermes_cli/web_dist/index.html` plus its asset tree.

### Batch C — JavaScript validation

Run the four offline explicit checks for `ui-tui`, `web`, `@hermes/shared`, and
`@hermes/root-tests`. Do not use root `npm run --ws check`, because that would
pull excluded desktop/bootstrap surfaces into the batch.

### Batch D — Python validation

Run the upstream test script against `env/python-test` and a disposable,
synthetic Hermes home. Follow it with the focused Task 3–10 evals for:

- delegation-envelope schema, exactly-once work-to-Codex routing and mixed
  decomposition;
- cloud sanitization, local reintegration and privacy-canary non-leakage;
- owner/provenance/constraints/approval/idempotency preservation;
- deterministic workflow versus ephemeral/durable worker selection;
- one durable offline-Ernie handoff, CAS retry, 25-minute default, bounded
  backoff/expiry/escalation and acknowledgement;
- separate Ernie/Bert state, health, release/profile pointers and rollback;
- memory, session, Kanban, cron and approval semantic migration fixtures;
- tool history/template compatibility and model-worker routing contracts only,
  without downloading or configuring a model.

### Batch E — optional platform static checks

The digest-bound Node syntax checks are deferred. Actual WhatsApp or Photon
dependency hooks, runtime starts, and integration tests are not in this plan.
They become separately reviewable only if approved non-secret profile evidence
shows the feature is enabled for a target cell.

## Candidate sealing gates

A candidate may be sealed only when all are CLEAR:

1. latest/target release discovery and tag commit identity are unchanged;
2. immutable upstream and composed overlay tree digests match receipts;
3. all 12 replay entries have a final disposition and deterministic order;
4. manifest/lock/install-surface screen and V2 dependency result remain CLEAR;
5. runtime and test environments are digest-bound and relocatability/runtime
   invocation is proven without wheel/sdist or mutable editable checkout use;
6. required TUI/dashboard assets reproduce from clean output directories;
7. upstream tests and focused Tasks 3–10 evaluations pass;
8. forbidden-version, source-cleanliness, cache/log and network-denial checks
   pass after every batch;
9. the release manifest binds source, overlay, runtime, assets, tests, target,
   replay manifest, supply-chain receipts and promotion state;
10. an immutable prior release/profile rollback bundle exists for the cell,
    but real profile pointer verification waits for the private migration gate;
11. Ernie and Bert retain independent candidate/release/profile/promotion and
    rollback records; sealing Ernie must not create a Bert promotion record;
12. no unresolved WARN, BLOCKED or CRITICAL result remains.

## Semantic clone, migration, and rollback gates

These are later approvals and are intentionally absent from executable argv
because the implementation and approved snapshot paths do not exist yet.

1. Take secret-excluding config and state inventory receipts, then create
   immutable backups and cloned profile homes. Never share/copy one mutable
   SQLite database across cells.
2. Run config and session migrations only against clones, on nonconflicting
   ports, with no external messaging or model calls.
3. Validate schema/integrity plus semantic retrieval for memory, conversations,
   persona/identity, tasks, ownership, schedules, Kanban/cron ledgers,
   provenance and approvals. A file copy is not success.
4. Run privacy canaries: Bert cannot receive Ernie-private mappings/content or
   write canonical shared memory.
5. Create a matched rollback release/profile pair and rehearse atomic pointer
   switch, failed health gate, automatic revert, service/code SHA parity and
   profile continuity in fixtures first.
6. Any data loss, canary leak, duplicate ownership/run, schema ambiguity,
   version mismatch, failed health, or rollback mismatch is a hard stop.

## Automation gates

- `daily-bert-health-and-ops-repair` remains active legacy evidence and is a
  final-promotion blocker. It must be approval-paused before the final Ernie
  snapshot/promotion; its replacement must never overlap it.
- `computer-history-to-nate-os` stays unchanged. Any working-directory or path
  adaptation is a separate approval.
- Candidate construction and tests may only read the inventory. No automation
  create/update/delete/pause/resume, cadence, thread, prompt, or activation
  change is background-authorized.

## Approval boundaries and rollback points

Quiet background work may rediscover releases, compare digests, statically
screen artifacts, assemble already-authorized synthetic fixtures, validate
receipts, and prepare a candidate without touching protected paths.

Separate approval is required for each of the following:

1. local code-only prerequisite implementation (next decision);
2. Batch A dependency/network execution;
3. Batches B–D build/test execution;
4. any optional WhatsApp/Photon feature hook;
5. real profile/config/history/credential snapshot or migration;
6. automation changes;
7. Ernie canary, restart or promotion;
8. live Bert access, snapshot, deployment or promotion;
9. model download/configuration; and
10. push or other external publication.

Before any approved batch, revalidate the release target, plan self-digest,
command-list digest, candidate/build/replay/overlay/dependency/runtime bindings,
empty config files, workdir confinement and protected paths. Stop on drift.

## Next exact decision

Approve or deny the scope in
[`2026-08-21-hermes-candidate-next-approval-input.json`](../../planning-receipts/2026-08-21-hermes-candidate-next-approval-input.json),
self-digest
`bfc32502f605f457bafcaf157066385f2caf0f59bde19c8dc87819adb706c471`:
local test-first implementation of the missing extension overlay, composed-
source assembly, artifact-bound release bundle, sealing/promotion gate split,
network-denial adapter, and synthetic Task 3–10 validators. It authorizes no
dependency, lifecycle, build, test, private-data, service, automation, live,
promotion, model, push or deployment action.
