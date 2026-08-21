# Hermes Case-Sensitive Source-Candidate Receipt

Status: `CLEAR_SOURCE / APPROVAL_REQUIRED_DEPENDENCIES`

Observed: 2026-08-21 (America/Detroit)

## Authority and target

- Nate OS index, standing rules, registry, Bert/Hermes, background,
  supply-chain, Codex-operations, skill-learning, and Bert-health authority were
  refreshed read-only. Existing unrelated Nate OS work was preserved.
- Branch started clean at `17fd904f4f2f4d0e1d494f9eb33ce5e6fee478a4` on
  `codex/hermes-staged-lifecycle`.
- Fresh authoritative selection remained `v2026.8.18` at
  `e624e9fde561e1add9388384012b295fde669ade`, exactly one published stable
  release behind `v2026.8.19` at
  `fcbd1076a93841fa88855acce810e342a5b78101`.
- Remote audit remained CLEAR: `bitdepth` is writable/default and NousResearch
  `upstream` is fetch-only, namespaced, and push-disabled.

## Isolated builder

- Image: `/Users/react/.codex/state/hermes-platform/builders/hermes-case-sensitive-v1.sparsebundle`
- Mounted volume: `/Volumes/HermesCaseBuilderV1`
- Volume UUID: `2BF764C6-D18B-4ABA-BE20-47E280CE8145`
- Filesystem: case-sensitive APFS; 32 GiB sparse maximum; non-browsing local
  mount; no running checkout, service, profile, credential, or private-data
  path overlaps it.
- Ownership enforcement could not be enabled without root, but distinct-inode,
  distinct-content, and read-only mode probes passed. Candidate immutability is
  independently checked over every source path.
- Exact source worktree:
  `/Volumes/HermesCaseBuilderV1/sources/v2026.8.18-e624e9fde561`
- Git source HEAD: `e624e9fde561e1add9388384012b295fde669ade`;
  Git tree: `99270c7e45ccf1669314dc7dd591ce5c6fb3a412`; status clean.
- Both upstream contributor paths that differ only by case exist with distinct
  inodes and distinct SHA-256 digests. No path was renamed, dropped, or merged.
- The prior failed case-insensitive candidate and its evidence remain retained
  under `/Users/react/.codex/state/hermes-platform`; they were not overwritten.

## Immutable Ernie source candidate

- Candidate ID: `1e79082041b08781ca40`
- Candidate path:
  `/Volumes/HermesCaseBuilderV1/platform/cells/ernie/candidates/1e79082041b08781ca40`
- Status: `STATIC_PREPARED`
- Candidate manifest SHA-256:
  `07f5308b6bcc48d812067c8aa234923a11d4f207072b16611a2d1e2be1d01e75`
- Source tree SHA-256:
  `d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`
- Replay manifest SHA-256:
  `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`
- Static scan: CLEAR; 19 manifest/lock artifacts digested; no forbidden-version
  implementation evidence; changed root and Photon postinstall surfaces remain
  the previously reviewed two-hook inventory.
- Re-running construction left the manifest byte-identical and reverified the
  read-only source tree. A disposable writable dependency-audit mirror was
  created beside the immutable source and its pre-install digest exactly
  matches the source tree digest.
- No Bert target/candidate, release, profile, current pointer, promotion, or
  rollback action was created.

## Pinned audit runtime

The audit runtime is read-only at
`/Volumes/HermesCaseBuilderV1/runtime/node-v24.19.0-npm-v11.17.0`.

- Node 24.19.0; binary SHA-256
  `27db838bb204ef7c21df2931f5656e4c8fb32e6e947f363a402b49714d32b5b1`;
  copied from the current Codex bundled runtime and suitable for dependency
  audit only, not yet a promoted Hermes runtime.
- npm 11.17.0; official registry integrity
  `sha512-PurxiZexEHDTE4SSaLI3ZrnbAGiZfeyUcQcxcP5D+hfytNAze/D1IzDuInTn9XVLIbAQUnQuSPXJx02LHjLvQw==`;
  tarball SHA-256
  `b290bbb35b9e72c3ef84edbe041f28c4479c4d9ee79f555817b8caafe7ce4bba`.
  Registry metadata reported one signature. The published package has no
  install/postinstall/prepare hook; its `prepack` build hook was inspected and
  not executed. No forbidden-version evidence was found.
- uv 0.9.28, matching the exact target's pinned CI version; official release
  checksum verified; binary SHA-256
  `ff9abd2affc410ed2a51f468d19fb281fa02d9ca1bd5c74633c2ceb71e97f6c2`.
- Python 3.11.3 at the existing framework path; binary SHA-256
  `c220ae7b6c2b9da2a4e498c09cdbc64b03b43db96dbbf9f64d26bfbbd698e2bd`.
- Only runtime version commands ran. No Hermes dependency resolution,
  installation, package lifecycle hook, or build hook ran.

## Dependency approval gate

The exact argv arrays, isolated workdirs, runtime hashes, cache/home boundaries,
candidate/tree/manifest/replay/artifact bindings, and execution state are in
[the approval input](2026-08-21-hermes-dependency-approval-input.json).

- Approval-input SHA-256:
  `22e282205ea1a878951d026efc4125357109aa7d710c4baecc1afeeb69f23dca`
- Exact command-list SHA-256:
  `049f37b1303caed35d463d61a0cf386dcc864f84f78070ab6f21a9d208e96732`
- Execution remains `false` / `APPROVAL_REQUIRED`.
- Python uses frozen lock state, disables project installation, disables source
  builds, disables external uv config, and prohibits Python downloads.
- npm uses committed locks, ignores every lifecycle script, disables audit/fund
  side requests, ignores user/global npm configuration, and uses isolated
  caches. All commands target the disposable audit mirror, never immutable
  source.

Before execution, the lifecycle approval validator must bind this safer derived
execution-plan digest rather than the earlier relative static-command digest.
That code bridge is not permission to execute dependencies and remains the
last local implementation prerequisite. No command in the approval input was
run.

## Automation and remaining boundaries

- `daily-bert-health-and-ops-repair` remains ACTIVE and is still the
  `legacy_health_automation_pause` final-promotion blocker. It must be
  approval-paused before the final Ernie promotion snapshot, with no overlap
  between old and replacement automation.
- `computer-history-to-nate-os` remains active and non-overlapping; its path
  adaptation stays separately approval-gated.
- No automation, cadence, notification, target task, prompt, state, or receipt
  was changed.
- No model, profile/history/config/credential, service, schedule, live Bert/SSH,
  restart, promotion, deployment, push, or private data was accessed or changed.

## Verification

- Full lifecycle suite on the case-sensitive builder -> `57 passed`.
- Full lifecycle suite on the normal host filesystem -> `56 passed, 1 skipped`;
  only the intentionally case-sensitive integration case skipped.
- Candidate rebuild -> manifest byte-identical; immutable source tree rechecked
  read-only.
- Static source-vs-legacy scan -> CLEAR, no forbidden findings, two reviewed
  hook changes, dependency execution false.
- Approval-input command digest, approval-input digest, candidate-manifest
  digest, source/audit-tree parity, Ernie-only target state, Python compilation,
  JSON parsing, and `git diff --check` -> passed.
