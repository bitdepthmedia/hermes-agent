# Hermes dependency execution plan v2

Date: 2026-08-21

Status: `CLEAR_PLAN / APPROVAL_REQUIRED_NPM`

## Correction

The approved v1 plan and its partial-execution receipt remain immutable. The
new [v2 approval input](2026-08-21-hermes-dependency-approval-input-v2.json)
contains only the four npm commands that did not complete; it does not include
or authorize a second uv sync.

Two distinct npm configuration files were created inside the isolated candidate
audit root:

- `dependency-audit/config/npm-user.npmrc`
- `dependency-audit/config/npm-global.npmrc`

Both are zero bytes, read-only, non-symlink files with distinct inodes and
SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Every remaining command binds `NPM_CONFIG_USERCONFIG` and
`NPM_CONFIG_GLOBALCONFIG` to the corresponding exact path. No external npm
configuration can be loaded through those fields, and npm 11.17.0 will no
longer treat one file as both configuration roles.

## Immutable approval bindings

- predecessor execution-plan SHA-256:
  `22e282205ea1a878951d026efc4125357109aa7d710c4baecc1afeeb69f23dca`
- v2 execution-plan SHA-256:
  `69e1ec0acc4ca55ea51e0055176bf4618eb99b63e2423c83ae54011e47ec9c01`
- v2 command-list SHA-256:
  `b2b3c012ff7be1be50acd166668d3109de58e2c35e5093337c723f0074e05823`
- candidate: `1e79082041b08781ca40`
- candidate-manifest SHA-256:
  `07f5308b6bcc48d812067c8aa234923a11d4f207072b16611a2d1e2be1d01e75`
- source/audit-tree SHA-256:
  `d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`
- artifact-map SHA-256:
  `c68181482b2732ec37871ee055f4ba2448434adbcd78e9f0b2dd9f8f8499db96`
- replay-manifest SHA-256:
  `6353761fc90dba784548b5b6ab7c8e92c4487f356b830f3bf597f5a00140d632`
- execution state: `false` / `APPROVAL_REQUIRED`

The existing pinned Node 24.19.0, npm 11.17.0, uv 0.9.28, and Python 3.11.3
hashes remain bound. The validator and direct preflight confirmed all four
workdirs stay beneath the disposable audit source mirror.

## Validator and regression coverage

Candidate sealing now rejects an npm execution plan unless it has exactly one
user and one global npmrc binding, the paths are distinct, absolute, confined
to the candidate audit config root, non-symlink, empty, digest-bound, read-only,
and referenced exactly once by every npm command.

- Red phase: a fully digest-bound plan sharing one npm config path incorrectly
  sealed before the validator correction.
- Green phase: the shared-path regression is rejected; a plan with two valid
  files still passes the complete synthetic plan/approval/install receipt chain.
- Lifecycle suite: `60 passed, 1 skipped`; the known host-filesystem skip is the
  case-sensitive integration case.
- The repository's canonical wrapper could not run because this isolated
  worktree has no discoverable permitted test venv. No dependency was installed;
  verification used the existing Python 3.11 test runtime with a clean env.
- The real v2 plan self-digest, command digest, candidate/source/artifact/replay,
  runtime hashes, npm package version, npmrc files, and workdir confinement all
  validated `CLEAR`.

## Approval request

A new scoped approval is required to run these four commands from committed
locks with the pinned runtime:

1. root workspace `npm ci --ignore-scripts --no-audit --no-fund`;
2. website `npm ci --ignore-scripts --no-audit --no-fund`;
3. WhatsApp bridge `npm ci --ignore-scripts --no-audit --no-fund`;
4. Photon sidecar `npm ci --ignore-scripts --no-audit --no-fund`.

The dependency and lifecycle-hook risk is unchanged: these commands may
download/materialize third-party package contents but cannot run package
scripts. The correction only removes npm configuration ambiguity and prevents
unreviewed user/global npm configuration. Stop conditions for forbidden
versions, lock/resolution/platform failure, unexpected hook/build requests, or
digest drift remain unchanged.

No npm command, resolver, download, install, lifecycle/build hook, uv mutation,
model, profile/history, credential, service, automation, live Bert/SSH,
promotion, deployment, push, or private-data action occurred in this phase.

Skill-learning closeout: no skill change is eligible. The regression and
validator are the narrow durable repository correction; supply-chain authority
remains outside automatic skill-learning scope.
