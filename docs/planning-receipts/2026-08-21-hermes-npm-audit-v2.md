# Hermes npm dependency audit v2

Date: 2026-08-21

Status: `CLEAR_DEPENDENCY_AUDIT / NEXT_GATE_REQUIRED`

## Bound execution

The user approved and all four commands in the immutable [v2 approval
input](2026-08-21-hermes-dependency-approval-input-v2.json) completed with
exit code 0 using Node 24.19.0 and npm 11.17.0:

1. root workspace: 1,293 packages added;
2. website: 1,389 packages added;
3. WhatsApp bridge: 141 packages added; and
4. Photon sidecar: 137 packages added.

Every command used its exact approved workdir, cache, user/global npmrc pair,
and `npm ci --ignore-scripts --no-audit --no-fund` argv. The v2 execution-plan
SHA-256 remained
`69e1ec0acc4ca55ea51e0055176bf4618eb99b63e2423c83ae54011e47ec9c01`;
the command-list SHA-256 remained
`b2b3c012ff7be1be50acd166668d3109de58e2c35e5093337c723f0074e05823`.
The prior frozen uv synchronization was not rerun.

## Verification

After each command, the installed package metadata, generated hidden lock,
committed lock, cache, and npm debug log were checked before the next command.
All generated lock entries matched their committed-lock entries for every
materialized version, resolution, integrity, link, and dependency-state field.

| Surface | Installed metadata files | Generated lock entries | Committed lock entries |
| --- | ---: | ---: | ---: |
| root | 1,406 | 1,300 | 1,370 |
| website | 1,458 | 1,389 | 1,390 |
| WhatsApp bridge | 147 | 141 | 167 |
| Photon sidecar | 175 | 137 | 138 |

- No implementation evidence for a forbidden version appeared in manifests,
  committed or generated locks, installed metadata, caches, or logs.
- Every npm log proves npm 11.17.0, Node 24.19.0, the four approved flags,
  exit 0, and no lifecycle-script execution record.
- Candidate `1e79082041b08781ca40` remains `STATIC_PREPARED`; its immutable
  source tree SHA-256 remains
  `d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83`.
- Every non-`node_modules` audit-mirror file still matches the immutable source,
  all 19 dependency artifact digests remain bound, and the committed repository
  locks were unchanged.
- Package deprecation warnings were observed in root and Photon output; they
  are maintenance evidence, not resolution or integrity failures, and no
  incidental upgrade was performed.

Machine-verifiable approval and result evidence is recorded in the
[approval receipt](2026-08-21-hermes-dependency-execution-approval-v2.json)
and [install result](2026-08-21-hermes-dependency-install-result-v2.json).
The isolated npm caches and logs remain retained under the candidate audit root.

## Boundary and next gate

This clears only the scripts-disabled dependency audit. It does not authorize
or prove the reviewed Hermes/Photon lifecycle hooks, build scripts, project
installation, candidate tests, semantic profile migration, rollback pair,
release sealing, promotion, service or automation changes, live Bert access,
deployment, or push. The next phase must derive and separately approve any
scripts-enabled build/install/test command set before candidate sealing can be
considered.

No model, profile/history, credential, service, schedule, automation, live
Bert/SSH, promotion, deployment, push, or private-data action occurred.

Skill-learning closeout: no change is eligible. This execution exposed no new
repeated repo-local workflow friction, and supply-chain authority remains a
shared approval boundary.
