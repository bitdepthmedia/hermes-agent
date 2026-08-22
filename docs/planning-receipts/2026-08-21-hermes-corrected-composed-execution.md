# Hermes corrected composed-execution plan receipt

Status: `COMPLETE_AWAITING_EXACT_V3_APPROVAL`

No project build/test/lifecycle command, dependency command, install, hook, cleanup, profile/model/service/automation/live action, promotion, deployment, push, or external-state action ran.

- Target remains `v2026.8.18` (`e624e9fde561e1add9388384012b295fde669ade`), one behind `v2026.8.19`.
- Composition: `192844cf7a7b498fd3abeeee`; tree: `43ecf198cea220737e6da0f8e1b8d72b6b7637ae1b050410d931e621579bec0c`; implementation: `c3696086bad9a4ed4db607fac7c5ec3d5e381f35`.
- Plan: `5d5529fb36600ea67b91376be242076fa7374d61d6be1294cdd1ad4eec58a3d3`; ordered commands: `f9c8ec9f4999e45bfcb0326d44d4db1f01e74f94d010d687e89dae4ca9c6d02e`; count: 12.
- Python 3.11.3 compile/import discovery selected 11 exact paths and found 48 tests without running test bodies; selection: `96b8f95ed5bb3e9a338c6b3053aa1455a3933da2c89b3890867c3e8ea72ff476`.
- UI-TUI/tests-js Vite caches are confined to two empty disposable roots outside the build/dependency trees, use `--configLoader runner`, retain on failure, and are disposable only after CLEAR. Copied dependency digests remain immutable.
- Old commands 1-6 are rerun: their outputs are single-run evidence from an old composition with undeclared cache mutation and no reproducibility proof.
- Retained copy failures `4b005057b9c0bddb9d9fbb6d` and `8284080909e23be86d9395f3` were not cleaned; final staging is outside the worktree and validates relocated workspace links.
- Old v2 approval is schema/scope/digest-ineligible. Next approval must bind this exact v3 plan, all command digests, and a fresh <=300-second network proof.
- Promotion automation blockers remain unchanged.
