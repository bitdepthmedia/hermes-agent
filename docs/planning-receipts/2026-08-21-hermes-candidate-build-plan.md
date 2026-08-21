# Hermes candidate build-plan receipt

Date: 2026-08-21
Status: `PLAN_CLEAR / EXECUTION_BLOCKED_PREREQUISITES`

## Bound evidence

- latest stable upstream: `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`;
- exact one-stable-release-behind target: `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`;
- candidate: `1e79082041b08781ca40`, still `STATIC_PREPARED`;
- immutable source tree:
  `d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`;
- replay manifest:
  `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`;
- V2 dependency result file:
  `234683de200ecb3016cf56e0dec938a3a6a506f4b78a5fb700accc9384cec9bd`;
- script/hook inventory:
  `6eb96636c7af719cc1dbac40702cf9a7432fd0274795f83064d352bc6e4afaef`.

The release evidence was refreshed from the official GitHub Releases API and
peeled tag refs. It did not change.

## Outcome

Every project script surface and every install-eligible lifecycle hook in the
four cleared npm trees was classified. Required headless-cell work is limited
to a locked Python test/runtime path, the TUI/Ink bundle, dashboard bundle,
focused JavaScript checks, upstream Python tests, and the missing Bert/Ernie
contract evaluations. Website, desktop/Electron, Tauri/bootstrap, interactive
installer, and wheel/sdist surfaces are excluded. WhatsApp and Photon remain
profile-contingent and separately approval-gated.

The command plan contains 10 reviewable future commands across five batches:

- plan self-digest:
  `2b656af94ad6d2bdfe866143f05b5432876bbab9848bfda8c50cf240ea285ab5`;
- command-list digest:
  `bd9253cf6b332aff96c95d46019e1de7c35de104097980bbf03abb7686bcf224`;
- execution authority: `false`.

No scripts-enabled install is proposed. Required asset and test commands use
explicit `npm run ... --ignore-scripts --offline`; the Python test environment
is a distinct frozen, no-project-install, no-build dependency/network batch.

## Blocking discovery

The immutable candidate contains exact upstream only. It has no declared
extension overlay or Tasks 3–10 contracts. The current sealer copies upstream
source only, binds neither runtime environment nor built assets, and requires
real release/profile rollback pointers before private-profile access is
authorized. Building now would test and package the wrong candidate.

The machine plan therefore fails closed with
`BLOCKED_PREREQUISITES`. It cannot be converted to `APPROVAL_REQUIRED` merely
by editing a status field; its source/overlay/release-bundle/executor bindings
must be filled and all command/plan digests recomputed.

## Verification

- 10 focused standard-library tests pass for plan/command self-digests,
  candidate identity, non-executable authority, protected paths, duplicate
  commands, forbidden/floating command tokens, clean explicit npm config,
  tamper detection, the committed plan, and the next approval input.
- Both new JSON artifacts parse and their canonical self-digests validate.
- Candidate identity, release target, source tree, replay manifest, dependency
  result, runtime pins, workdir confinement and no-live-path rules were checked.
- Static review covered inputs, outputs, network/download behavior, filesystem
  mutations, timeouts, expected artifacts, failure retention and later cleanup.
- No forbidden-version implementation evidence was found.

No package-manager, lifecycle, build, test-suite, profile, model, credential,
service, schedule, automation, live Bert/SSH, promotion, deployment or push
command ran. The focused validator tests are repository-local Python only.

## Next approval decision

Approve or deny the digest-bound scope in
[`2026-08-21-hermes-candidate-next-approval-input.json`](2026-08-21-hermes-candidate-next-approval-input.json),
self-digest
`bfc32502f605f457bafcaf157066385f2caf0f59bde19c8dc87819adb706c471`.

Recommended: approve the local code-only prerequisite phase. It implements the
extension overlay, deterministic composed-source assembly, artifact-bound
release bundle, correct sealing/promotion split, network-denial adapter, and
synthetic Tasks 3–10 validators. It still authorizes no dependency, build,
test, private-data, service, automation, live, promotion, model, deployment,
push or external-state action.

Automation blockers remain unchanged: the active legacy Bert health automation
must be approval-paused before final promotion, and Computer History path
adaptation stays a separate approval.
