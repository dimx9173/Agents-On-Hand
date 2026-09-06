# Agents-On-Hand — Agent Operations Rules

Project-specific operational rules for agents working in this repo.

## Session cleanup (session-leak prevention)

**Rule**: When deleting aoh/opencode sessions — or clearing offline sessions —
the **prime-agent side must be cleaned in the same pass**, otherwise
orphaned session files leak.

Two stores must stay in sync:

| Side | Location | How to delete |
|---|---|---|
| opencode (路徑選單 / ses_ ids) | `~/.local/share/opencode/opencode.db` | `opencode session delete <ses_id>` |
| prime-agent (agent side) | `~/.prime/agent/sessions/<uuid>.jsonl` + `~/.prime/agent/session-artifacts/<internal-id>/` | remove file + artifact dir **after** checking `~/.prime/agent/session-leases/` for references |

**Safety gates** (never skip):
1. Always **dry-run first** — the tool below defaults to dry-run.
2. Never delete a prime session referenced by an active `session-leases/*/owner.json`.
3. A prime session's filename is NOT its session id — read the `id` from the
   first jsonl record (`type:"session"`) to locate its artifact dir.

**Tool**:

```bash
# inspect prime sessions under a cwd (list msgs/size/live status)
python3 scripts/clean-agent-sessions.py --scan-cwd /home/brian/project/Agents-On-Hand

# delete both sides in one pass (dry-run by default; add --force to apply)
python3 scripts/clean-agent-sessions.py \
    --opencode-ids <ses_id>... \
    --prime <prime-file-prefix>... \
    --force
```

**One-off manual equivalent** (when not using the tool):

```bash
opencode session delete <ses_id>            # opencode side
rm ~/.prime/agent/sessions/<uuid>.jsonl      # agent side
rm -r ~/.prime/agent/session-artifacts/<internal-id>/
```

## Background / rationale

The aoh 路徑選單 lists opencode sessions (ses_...) grouped by directory, but
the agent backend also writes its own session records to `~/.prime/agent/`.
Deleting only the opencode record leaves the prime file + kernel-state
artifacts orphaned — these accumulate as "session leaks" and pollute later
session listings/analyses.