# Ernie Daily Goal Coordinator Ops

## Safe flow

1. `scripts/ik-ernie-daily-goal status`
2. `scripts/ik-ernie-daily-goal dry-run`
3. `scripts/ik-ernie-daily-goal deploy`
4. Restart only the local Ernie scheduler/gateway owner.
5. `scripts/ik-ernie-daily-goal trigger-checkin`
6. Verify one receipt and one Telegram delivery.

`status` and `dry-run` are nonmutating. `deploy` changes only the local Ernie
profile and creates timestamped backups first. The trigger commands mutate job
state and can send Telegram messages; obtain approval before using either.

## Rollback

Restore the timestamped `jobs.json` and `config.yaml` backups created by `deploy`,
restart the same local service, and verify the original check-in job is scheduled.

The wrapper never changes the DigitalOcean Bert runtime.
