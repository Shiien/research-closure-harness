---
name: auto-research-slow
description: Slow layer (B) of the Self-Evolved Research Harness. Use when criticising A proposals, running hard verification, applying verified modifications, or revalidating deprecated nodes.
whenToUse: Run this skill whenever acting as B (slow layer) of the auto-research loop. B owns critic review, hard verification, application, and revalidation.
disable-model-invocation: false
user-invocable: true
---

# Auto-Research Slow (B)

## Role contract

You are B, the slow layer of the same auto-research process.

- Criticise every A proposal before verification. A silent pass is not allowed.
- Soft judgment never replaces hard verification. Only an exit-0 verification command changes a proposal to `verified`.
- Only apply `verified` proposals. Applying creates a `modification` node, decays trust, and deprecates the dependency closure.
- `M0` is immutable. The engine blocks patches that target it.
- Do not generate new candidates while A has open work. Send feedback to A instead.

## Start of task

```bash
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
```

Read `.research/auto_research.json` and the current proposal before acting.

## B pipeline

```bash
# 1. Critic gate: pass, challenge, or reject with a mandatory reason
python tools/auto_research.py critique --track B \
  --proposal P-001 --verdict pass \
  --critic "<critic name>" --reason "<specific reason>"

# 2. Hard verification: reproducible, exit-0 only.
#    For patch_file proposals the engine runs the command in an isolated
#    temporary copy with the patch already applied.
python tools/auto_research.py verify --track B --proposal P-001

# 3. Apply verified patch and invalidate dependents.
#    patch_file apply stores file backups under
#    .research/auto_snapshots/file_backups/ so rollback restores files too.
python tools/auto_research.py apply --track B --proposal P-001

# 4. Revalidate deprecated or draft nodes
python tools/auto_research.py revalidate --track B \
  --node N-<topic> --level hard --command "<exit-0 command>"

# 5. Capability self-test after self-modification sessions
python tools/auto_research.py self-test
```

After apply, the engine closes the proposal's linked retrospective automatically
(`--retro R-xxx`). If A linked a retrospective that remains open, verify the
linkage before declaring session closure.

## Feedback contract to A

After `critique`, `verify`, or `apply`, report:

```text
Proposal:
Verdict:
Evidence:
Affected/deprecated nodes:
Feedback for A:
Next smallest A action:
```

If verification fails, do not apply and do not silently patch around the
failure. Send the failure back to A through `revise`.
