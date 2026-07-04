# IK Orchestrator Access Node Ops

The named primary assistant is the full agentic system and its DigitalOcean orchestrator runtime. This local Hermes profile is an access node inside that environment, not the primary orchestrator identity.

Checkout path:

```bash
/Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
```

## Rules

- Reserve the named primary-orchestrator identity for the DigitalOcean runtime and the broader assistant system it coordinates.
- Local bridges, subprocesses, shells, spawned agents, helper scripts, launch jobs, and local profiles must use neutral role names.
- Ernie is an independent peer orchestrator and collaborator, not a delegated worker.
- The access-node profile source lives in `ik_profiles/orchestrator-access-node/` and is deployed into `config/ik-agents/orchestrator-access-node/`.
- Do not hand-edit live `SOUL.md` or `memories/*.md` without copying the same change back to `ik_profiles/orchestrator-access-node/` and committing it.
- Keep the live checkout on a named branch, not detached HEAD.
- Commit every live behavior change before considering it done.
- Use small commits so rollback is `git revert <commit>`.
- Restart the access node after code changes.
- Run the heartbeat after restart.
- Do not commit `.DS_Store`, logs, caches, secrets, or dependency churn.

## Commands

```bash
cd /Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
scripts/ik-orchestrator-access-node status
scripts/ik-orchestrator-access-node profile-check
scripts/ik-orchestrator-access-node profile-deploy
scripts/ik-orchestrator-access-node restart
scripts/ik-orchestrator-access-node heartbeat
scripts/ik-orchestrator-access-node rollback <commit>
```

## Normal Change Flow

```bash
cd /Users/react/Documents/_IK_International/AI/_apps/bert-ernie-local-stack/runtime/hermes-agent
git status --short --branch
git add <owned files>
git commit -m "<type>: <short access-node change>"
scripts/ik-orchestrator-access-node profile-deploy
scripts/ik-orchestrator-access-node restart
scripts/ik-orchestrator-access-node heartbeat
git status --short --branch
```

The final state should be a clean working tree and a heartbeat with `last_status ok`.
