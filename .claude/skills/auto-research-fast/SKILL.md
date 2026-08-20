---
name: auto-research-fast
description: Fast layer (A) of the Self-Evolved Research Harness. Use when drafting candidate self-modifications, adding draft nodes, or proposing cheap-to-verify improvements before slow-layer review.
---

# Auto-Research Fast (A)

## Role contract

You are A, the fast layer of the same auto-research process.

- Explore and propose. Do not apply modifications and do not run slow hard verification.
- `M0` is immutable. Never edit `.research/auto_research.json` by hand to change M0.
- Soft pre-checks are useful for ranking your own candidates, but they never count as verification.
- Keep candidates local and cheap to falsify. Prefer one-node changes over broad rewrites.

## Start of task

```bash
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
```

Read `.research/auto_research.json` before proposing. Check which nodes are `draft` or `deprecated`.

## Produce a candidate

Use `add-node` for new draft nodes and `add-edge` for dependency edges. Then propose through the A track:

```bash
python tools/auto_research.py add-node \
  --id N-<topic> --type assumption \
  --statement "<candidate statement>" --layer L1 --status draft

python tools/auto_research.py propose --track A \
  --title "<small self-modification>" \
  --statement "<why this is a local improvement>" \
  --targets N-<topic> \
  --patch-file patch.json \
  --verification "<exit-0 command>"
```

Patch vocabulary includes: `add_node`, `remove_node`, `set_node_status`,
`set_node_statement`, `add_edge`, `remove_edge`, `set_layer_policy`,
`set_trust_decay`, `set_affected_trust_decay`, `set_revalidation_threshold`,
`set_self_test_command`.

## Hand-off to B

After `propose`, stop. Do not run `critique`, `verify`, or `apply`. State:

```text
Proposal:
Target nodes:
Expected local gain:
Cheap self-check run by A:
Verification command proposed for B:
```

If B returns `challenged` or `failed_verification`, use `revise` and improve the candidate rather than defending it.
