# Hermes composed-candidate execution receipt

Status: `BLOCKED`

No retry, substitution, cleanup, dependency operation, service action, promotion, deployment, automation change, live access, or push occurred.

## Authority and preflight

- Plan: `11385283fa7dcd7755a37f77dca56ecd19818aaafd68bc7d6815add0e56fdc43`
- Ordered commands: `8fb06a1ec9ac9975e08507dd729dce2d8929cbf069a26fdf128aff2d96043589`
- Composition: `85500d41b3165a9d2c9a957c`
- Composed tree: `d5fabb55cfb3727e09ba9fe37e61362ec27d99836ca872b3656e5fef65e08f80`
- Approval receipt: `docs/planning-receipts/2026-08-21-hermes-composed-execution-approval.json`
- Approval digest: `1edbfdea932ebb8e39cdeec9e03e48d54cc799b754342b720eb9c434c9f03a35`
- Approval file digest: `d49ec0eed2e21caaaba1b9368e4168493e0e296fe5fb85bdabe6358452cbbe70`
- Upstream refresh: latest stable remained `v2026.8.19`; exact one-release-behind target remained `v2026.8.18` at `e624e9fde561e1add9388384012b295fde669ade`.
- Preflight: immutable composition, copied dependency surfaces, two read-only empty npmrc files, runtimes, reviewed hook receipt, dependency receipt, and forbidden-version implementation scan were `CLEAR`.

Each command received a new 300-second macOS `sandbox-exec` deny-network proof. Proof receipts and logs are retained under:

`/Volumes/HermesCaseBuilderV1/platform/cells/ernie/candidates/1e79082041b08781ca40/composed-candidate/artifacts/85500d41b3165a9d2c9a957c/`

The retained execution result file digest is `6da3e70ad09d4ad8837515ae8beae762785bc658444a96249371039129b68dc1`.

## Exact outcomes

| # | Command | Proof | Result |
|---|---|---|---|
| 1 | `build-ui-tui` | `a312dec44deb3740d8943ebe63e12cb5c317e6fc96d39b26b0c9929130d482f5` | exit 0 |
| 2 | `build-dashboard-web` | `b631c80fe3cf45a9b1fe8bb2791be4ac7c6607706425a0e9e09cd022c379d2d9` | exit 0 |
| 3 | `check-ui-tui` | `4521b1b791955fcd790c4265c858362f9b5cbcf46bd732eb23a1c1eafa52b530` | exit 0; 156 test files, 1,654 passed, 4 skipped; 2 lint warnings, 0 errors |
| 4 | `check-dashboard-web` | `e86e17d50e3a89c7c37720fb86289943575a9dbdeaa47995fc9c6fe30be4d6d8` | exit 0; 36 test files, 275 passed; 26 lint warnings, 0 errors |
| 5 | `check-shared-dashboard-source` | `695409d9c2eaf6f5d2f018fe15dce803f17cafb64a06c65fb7ea605b16130599` | exit 0 |
| 6 | `check-root-javascript-tests` | `1acb3573bfbe2f28fc7796eae59eeb1cd658aeb24f7d7726f94cd50446fd73ba` | exit 0; 6 test files, 24 passed |
| 7 | `run-focused-lifecycle-unittest` | `e34f4bd45d6f79473f0495f4a359c7bba1f781680a73cf104dcadd24735fe0cf` | exit 1; stopped batch |

## Blockers

1. The exact seventh argv could not import any of the nine named modules under pinned Python 3.11. Each failed with `ModuleNotFoundError: No module named 'tests.ik_lifecycle'`; zero tests ran. This exact command cannot be retried with a changed invocation without a new digest-bound plan and approval.
2. Post-run dependency integrity found expected-by-tool but undeclared mutations: Vite created `.vite` and `.vite-temp` beneath both `ui-tui/node_modules` and `tests-js/node_modules`. Root `node_modules` stayed identical. The two changed digests are:
   - `ui-tui/node_modules`: cleared `db55e8f14b787890ffbe01722b49cc3af95e6757082149fb58652b3eaa375187`; observed `4e4dfac59815d94be7dee114fd243d1bc1378a7cdcadad357f6b30a79a953cef`.
   - `tests-js/node_modules`: cleared `f8f591938dfb309077ecf5c1bda8774ba56fc38a379daf4038b7f8ea3ac52c76`; observed `f2e3da01891592a436c3d7783851d64a1d4e168c65c00437ccec36ff05a25a96`.

The changed dependency trees and all failure evidence remain untouched.

## Built artifact evidence

- `ui-tui/dist`: `a23d5c30be62201a45fab67049515526b931620801d43ea6e25cddcd83575e1b`
- `ui-tui/packages/hermes-ink/dist`: `8fda8cb946c683dd453f817910e234eebb0fd880cdd3a24517662be98fa86d4a`
- `hermes_cli/web_dist`: `61bb0775f7a8cdf41fd6cdf5586d9dcf2fb62b5f452ca7ade310b4507761281d`
- UI entry file: `187e5e66d8acbde8129792d3e027cd219f11a761099b372046ca35a7fde85d66`
- Ink entry exports: `db183d98f41bdfa182717fad3dca1105a5904b797f4f1b8903bdc2d3e6b19925`
- Dashboard index: `c99cd33a031e83fafd0ea56ac1985418c159c463e342cd0592d3477d29110c2a`

These artifacts are retained evidence only. They are not a sealed release because the exact batch is not `CLEAR`.

## Next gate

A new non-executing correction plan must prove a Python 3.11-compatible focused-test invocation and explicitly isolate or classify Vite's `.vite`/`.vite-temp` caches without weakening dependency integrity. Any corrected argv, environment, mutation contract, plan digest, or command digest requires separate approval before execution.

Skill-learning closeout: no repo-local skill changed. This is one plan-specific failure occurrence, not repeated evidence sufficient to alter shared or local operating instructions.
