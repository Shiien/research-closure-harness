---
name: auto-research-fast
description: Fast layer (A) of the Self-Evolved Research Harness. Use when drafting candidate self-modifications, adding draft nodes, or proposing cheap-to-verify improvements before slow-layer review.
whenToUse: Run this skill whenever acting as A (fast layer) of the auto-research loop. A explores and proposes; A never critiques, verifies, or applies.
disable-model-invocation: false
user-invocable: true
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
python tools/auto_research.py retro next
```

Read `.research/auto_research.json` before proposing. Check which nodes are `draft` or `deprecated`.

## Retrospective-first rule

If `retro next` reports an open retrospective item, convert the first open item into
a proposal instead of inventing a new epoch. Use `--retro R-xxx` when calling
`propose`. If the item cannot be expressed in the current patch vocabulary, do not
silently replace it: write the blocking reason in the hand-off and let a new
retrospective record the missing capability.

## Produce a candidate

Use `add-node` for new draft nodes and `add-edge` for dependency edges. Then
propose through the A track:

```bash
python tools/auto_research.py add-node \
  --id N-<topic> --type assumption \
  --statement "<candidate statement>" --layer L1 --status draft

python tools/auto_research.py propose --track A \
  --title "<small self-modification>" \
  --statement "<why this is a local improvement>" \
  --targets N-<topic> \
  --patch-file patch.json \
  --verification "<exit-0 command>" \
  --retro R-001
```

Patch vocabulary includes: `add_node`, `remove_node`, `set_node_status`,
`set_node_statement`, `add_edge`, `remove_edge`, `set_layer_policy`,
`set_trust_decay`, `set_affected_trust_decay`, `set_revalidation_threshold`,
`set_self_test_command`, and `patch_file`.

## Proposing source-code changes

`patch_file` may modify any engine file in the repository except `.git/`,
`.gitignore`, `.gitmodules`, `.ssh_github/`, `.research/auto_research.json`, and
`.research/auto_snapshots/`. The engine may patch itself, including
`tools/auto_research.py`. A file patch still needs one or two graph target nodes.

Build a patch without editing the live file:

```bash
mkdir -p .research/tmp
cp tools/target.py .research/tmp/target.py.candidate
# edit .research/tmp/target.py.candidate
python tools/auto_research.py patch-make \
  --target tools/target.py \
  --candidate .research/tmp/target.py.candidate \
  --out patch.json
```

For multiple files, run `patch-make --append` and put every op in the same
`patch.json`. A `patch_file` proposal requires `--targets`. B verifies the patch
in an isolated temporary copy before apply, so the verification command must
pass against the patched candidate tree.

## Hand-off to B

After `propose`, stop. Do not run `critique`, `verify`, or `apply`. State:

```text
Proposal:
Retrospective converted (or blocking reason):
Target nodes:
Expected local gain:
Cheap self-check run by A:
Verification command proposed for B:
```

If B returns `challenged` or `failed_verification`, use `revise` and improve the
candidate rather than defending it.
