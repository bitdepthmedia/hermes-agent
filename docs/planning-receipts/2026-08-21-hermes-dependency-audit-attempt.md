# Hermes isolated dependency-audit attempt

Date: 2026-08-21

Status: `BLOCKED_COMMAND_PLAN`

## Revalidated scope

- GitHub's authoritative release API still listed `v2026.8.19` as latest and
  `v2026.8.18` as the immediately previous stable release. The upstream tag
  SHAs remained `fcbd1076a93841fa88855acce810e342a5b78101` and
  `e624e9fde561e1add9388384012b295fde669ade` respectively.
- Branch `codex/hermes-staged-lifecycle` started clean at
  `ca72d0ea04d84837ff0b9763afe8d113c0320416`; `bitdepth` remained the writable
  default and `upstream` remained push-disabled.
- Candidate `1e79082041b08781ca40` remained `STATIC_PREPARED`. Candidate,
  source-tree, audit-mirror, replay, 19 artifact, runtime, approval-input, and
  command-list bindings all matched.
- Candidate and audit-mirror static screening were `CLEAR`. The only hook
  changes remained the reviewed root `postinstall` text and disabled Photon
  postinstall patch. No manifest or committed lock changed.

## Command outcomes

The commands are defined verbatim in
[the approval input](2026-08-21-hermes-dependency-approval-input.json), bound by
execution-plan SHA-256
`22e282205ea1a878951d026efc4125357109aa7d710c4baecc1afeeb69f23dca`
and command-list SHA-256
`049f37b1303caed35d463d61a0cf386dcc864f84f78070ab6f21a9d208e96732`.

1. Frozen uv sync: `CLEAR`. It created the isolated Python environment and
   materialized 60 locked wheel distributions. It requested no source build,
   project installation, external uv configuration, or Python download.
2. Root npm clean install: `BLOCKED` before npm resolved configuration or any
   dependency. npm 11.17.0 rejected the plan because both
   `NPM_CONFIG_USERCONFIG` and `NPM_CONFIG_GLOBALCONFIG` resolve to `/dev/null`:
   `double-loading config "/dev/null" as "global", previously loaded as "user"`.
3. Website npm clean install: not run.
4. WhatsApp bridge npm clean install: not run.
5. Photon sidecar npm clean install: not run.

No command substitution, retry, lock refresh, lifecycle/build hook, or
scripts-enabled install was attempted.

## Preserved evidence and integrity

- The isolated uv environment and cache remain in the candidate dependency
  audit root. Installed Python metadata contains no forbidden package/version.
- npm failed before creating its configured cache directory and before creating
  root `node_modules`; there is no npm package metadata to inspect or clean up.
- Candidate manifest SHA-256 remains
  `07f5308b6bcc48d812067c8aa234923a11d4f207072b16611a2d1e2be1d01e75`.
- Audit source-tree SHA-256 remains
  `d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`.
- No forbidden-version evidence appeared in command output, installed metadata,
  or the uv cache. No suspicious execution occurred.

## Required next decision

The four npm commands need a newly derived and separately approved execution
plan. The recommended correction is to bind user and global npm configuration
to two distinct, empty files inside the isolated audit root, preserving the
no-external-config boundary. That changes the command list and execution-plan
digests, so the current approval cannot authorize a retry.

Do not resume at command 2 until the revised paths exist, the candidate/source/
runtime/artifact bindings are revalidated, the new digests are recorded, and a
new scoped approval is granted. Commands 3-5 must remain unexecuted until then.

No model, profile/history, credential, service, automation, live Bert/SSH,
promotion, deployment, push, or private-data action occurred.

Skill-learning closeout: no skill change is eligible. The defect is in the
digest-bound dependency execution plan and its future validator/generator
coverage, while dependency and supply-chain authority is excluded from
automatic skill-learning changes.
