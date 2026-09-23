# Coherent Hermes Runtime Repair

Authority: Nate explicitly requested repair, commit, merge, and deployment of
both Bert and Ernie on 2026-09-23. The selected model route was confirmed by
Nate after intake; exact runtime effort metadata is unavailable.

## Source

Authoritative upstream latest stable is v2026.9.21 at
d337b736aa1e8ebecfab043842d13e4a2d2f48a3. The penultimate stable target is
v2026.9.14 at 345cd2b057a452236de401d3534b8502a7465e8d (Hermes 0.21.3).
The repair uses its complete core plus the deployed Ernie declared overlay.
The paused maintenance implementation is excluded. Both cells receive this
same source; profiles, credentials, model workers, and state remain separate.

Bert's previous sealed display.py matches the legacy checkout exactly and
lacks a redaction function required by its newer tool executor. The deployed
interpreter reproduces the ImportError. Replacing individual upstream core
files across versions is not a valid release composition.

## Dependency Review

Reviewed upstream pyproject/package manifests and structured lock differences
against v2026.8.18. No forbidden versions were found. Python lock changes:
brotlicffi 1.2.0.2, firecrawl-anydoc 0.2.4, nemo-relay 0.8.3,
pillow-heif 1.5.0, slack-sdk 3.44.1, snowballstemmer 3.1.1,
tornado 6.5.8; optional Google Chat adds google-cloud-pubsub 2.39.0,
grpc-google-iam-v1 0.14.5 and grpcio-status 1.81.1. Only core, messaging,
and development extras are installed with uv sync --frozen --no-build
--no-install-project. Wheels are bound to upstream lock hashes. No sdist or
project setup build runs. setup.py was inspected: wheel/sdist guards plus
root module discovery; it is not executed by this installation.

Root npm lock changes include Babel 8/compiler packages, Playwright 1.62.1,
nanostores 1.4.2 and nanoid 3.3.18. The dashboard is built from the target
source. Use npm ci --ignore-scripts under Node 24; no npm lifecycle execution
is authorized by this review. Root postinstall is an informational echo.
Photon sidecar postinstall patches Spectrum mixed attachments; that independent
sidecar is not installed or built. Lock metadata marks hooks in electron
40.10.2, electron-winstaller 5.4.0, esbuild 0.28.1, fsevents 2.3.2/2.3.3,
get-windows 9.3.0, node-pty 1.1.0 and unicode-animations 1.0.3. All are
suppressed by --ignore-scripts. Any required hook must be separately inspected
before execution. The web build script is tsc -b followed by vite build.

## Acceptance

- Regression rejects mixed display/tool executor modules before sealing.
- Real runtime imports pass in a temporary profile without operator credentials.
- Sealed source identity matches on both hosts; manifests and service bindings
  match the selected immutable release.
- Real terminal/file tool dispatch, provider responses, fresh Telegram state,
  dashboard/API health, service restart durability, and rollback pairs pass.
- Retired updater remains disabled. Existing model and memory boundaries hold.
- Production cross-host transport remains an explicit unresolved capability;
  this runtime repair does not activate or claim that adapter.
