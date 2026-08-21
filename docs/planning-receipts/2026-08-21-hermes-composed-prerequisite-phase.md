# Hermes composed-candidate prerequisite receipt

Status: `COMPLETE_AWAITING_EXECUTION_APPROVAL`

## Bound identity

- Release policy: immediately previous published stable release.
- Refreshed upstream: latest `v2026.8.19` at `fcbd1076a93841fa88855acce810e342a5b78101`; target `v2026.8.18` at `e624e9fde561e1add9388384012b295fde669ade`.
- Lifecycle implementation commit: `bc4d69f15b10ed98aaedb029a4f2416f422ef5f1` (post-`ec392f4a34b88585d2f874f47d616f32cde20520`).
- Composition: `85500d41b3165a9d2c9a957c`.
- Composed tree: `d5fabb55cfb3727e09ba9fe37e61362ec27d99836ca872b3656e5fef65e08f80`.
- Immutable source: `/Volumes/HermesCaseBuilderV1/platform/cells/ernie/candidates/1e79082041b08781ca40/composed-candidate/compositions/85500d41b3165a9d2c9a957c/source`.
- Writable build root: `/Volumes/HermesCaseBuilderV1/platform/cells/ernie/candidates/1e79082041b08781ca40/composed-candidate/build-roots/85500d41b3165a9d2c9a957c/worktree`.
- Declared overlay source: `5740bef01c4f5e013bdfaf49c385038d64bd9d285ae17108569256a8d1749653`.
- Replay manifest: `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`.

## Dependency materialization

No package manager, resolver, install hook, lifecycle hook, build, or project test ran in this phase. Three surfaces from the previously CLEAR audit mirror were copied and compared byte-for-byte, with relative-symlink confinement and installed-metadata screening:

- `node_modules`: `f853501204dfa2098226e116967152f2584cc6fe0ec5854b64284a007da50657`
- `ui-tui/node_modules`: `db55e8f14b787890ffbe01722b49cc3af95e6757082149fb58652b3eaa375187`
- `tests-js/node_modules`: `f8f591938dfb309077ecf5c1bda8774ba56fc38a379daf4038b7f8ea3ac52c76`

Combined copy digest: `6238ba0bb3d5ad9eafc16e525833f8088203b8e0ad368ac907c83f2ff6415a6b`. Forbidden installed-version screen: `CLEAR`.

## Network isolation

Adapter: nonprivileged `/usr/bin/sandbox-exec` with `(deny network*)`; no host firewall or global network setting changed. The proof used one local loopback listener: the unsandboxed control connected, while the identical sandboxed Python child was denied with `EPERM` (`errno 1`).

- Adapter digest: `6fdf49544e2c57d69f7a7f4a892e062265aa292cc31f2ee43a42a0e1673e5a4f`
- Policy digest: `5c358b8d847211333e7ba22df82d84f796b5f30a41a2682209a949d783adbd08`
- Sandbox binary digest: `8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16`
- Probe runtime digest: `c220ae7b6c2b9da2a4e498c09cdbc64b03b43db96dbbf9f64d26bfbbd698e2bd`
- Evidence proof: `c0b48c678ca5cf220d18389423e20a0b08662685fefc03cc76436a025f83383c`, observed `2026-08-21T23:01:35.279260+00:00`.

Proof receipts expire after 300 seconds. Execution must generate and validate a fresh proof; this evidence receipt cannot authorize a later command.

## Exact next plan

- Plan: `docs/planning-receipts/2026-08-21-hermes-composed-execution-plan-v2.json`
- Plan digest: `11385283fa7dcd7755a37f77dca56ecd19818aaafd68bc7d6815add0e56fdc43`
- Ordered command-set digest: `8fb06a1ec9ac9975e08507dd729dce2d8929cbf069a26fdf128aff2d96043589`
- Command count: 7 (two required builds, four JavaScript checks, one focused lifecycle unittest command).
- Executable: `false` until a separate exact `ik.hermes.execution-approval.v1` receipt is supplied.

The legacy upstream-only plan remains schema-ineligible. Composition drift, adapter/policy/runtime drift, stale or missing proof, approval drift, and command digest drift fail closed. The active legacy Bert health automation remains a final-promotion blocker until approval-paused; the Computer History heartbeat path remains a separate approval-gated adaptation.

## Verification and retained evidence

- Red-green coverage added for network availability/proof/expiry/drift, read-only official source composition, overlap/symlink/tamper/idempotency, copied dependency metadata/symlinks/tamper, legacy-plan rejection, exact approval, and exact sandboxed command execution.
- `39` dependency-free lifecycle tests passed on the final implementation tree.
- Six legacy pytest-based lifecycle modules remain deferred because pytest was not installed or resolved in this phase.
- The first real construction attempt failed safely because copied read-only directory modes blocked overlay insertion. Its incomplete root and `FAILED` marker remain retained; the regression is fixed and tested. No source, service, profile, automation, or live runtime was mutated.
- No build/test command from the new plan has run.

## Next gate

User approval is required for exactly the seven command digests in `2026-08-21-hermes-composed-execution-approval-input.json`. A future execution must first refresh upstream/candidate/branch identity, re-screen implementation evidence, validate the pristine code and copied dependency bindings, create a fresh network proof, create the exact approval receipt, and then use `run_approved_plan_command` without argv/env/workdir substitution.
