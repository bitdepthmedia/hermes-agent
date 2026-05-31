# IK Bert Live Ops

Live Bert runs from `/Users/react/opt/hermes-agent`, which must point at this checkout:

```bash
/Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
```

## Rules

- There is one canonical Bert: the live orchestrator runtime that Nate and Ernie interact with.
- Ernie is an independent peer orchestrator and collaborator, not a delegated Bert worker.
- Local bridges, subprocesses, shells, spawned agents, helper scripts, and other Bert-side inference paths are delegated workers under Bert's control, not separate Bert identities.
- Delegated Bert-side workers return evidence and execution results; Bert owns synthesis, policy, next action, and final response for Bert's domain.
- Keep the live checkout on a named branch, not detached HEAD.
- Commit every live behavior change before considering it done.
- Use small commits so rollback is `git revert <commit>`.
- Restart Bert after code changes.
- Run the heartbeat after restart.
- Do not commit `.DS_Store`, logs, caches, secrets, or dependency churn.

## Commands

```bash
cd /Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
scripts/ik-bert-live status
scripts/ik-bert-live restart
scripts/ik-bert-live heartbeat
scripts/ik-bert-live rollback <commit>
```

## Normal Change Flow

```bash
cd /Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
git status --short --branch
git add <owned files>
git commit -m "<type>: <short live Bert change>"
scripts/ik-bert-live restart
scripts/ik-bert-live heartbeat
git status --short --branch
```

The final state should be a clean working tree and a heartbeat with `last_status ok`.
