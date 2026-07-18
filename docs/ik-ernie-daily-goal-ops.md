# Ernie Daily Goal Coordinator Ops

## Safe flow

1. `scripts/ik-ernie-daily-goal status`
2. `scripts/ik-ernie-daily-goal dry-run`
3. `scripts/ik-ernie-daily-goal deploy`
4. Restart only the local Ernie scheduler/gateway owner.
5. `scripts/ik-ernie-daily-goal trigger-checkin`
6. Verify one receipt and one Telegram delivery.

`status` and `dry-run` are nonmutating. `deploy` changes only the local Ernie
profile and creates a distinct collision-proof backup first. It refuses to
invent a delivery target, requires the canonical check-in job to retain a
nonempty `telegram:` target, and uses an interpreter with `croniter`.

The normal target resolves from the Git common checkout, so invoking the wrapper
from a linked worktree still selects the local stack's canonical
`config/ik-agents/hermes-ernie` profile. `HERMES_HOME` must be an absolute,
existing safe path; temporary homes are allowed only for tests with the explicit
test-only guard. The trigger commands mutate only their selected job's state and
can send Telegram messages; obtain approval before using either.

## Rollback

Restore the timestamped `jobs.json` and `config.yaml` backups created by `deploy`,
restart the same local service, and verify the original check-in job is scheduled.

The wrapper never changes the DigitalOcean Bert runtime.
