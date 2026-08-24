---
name: auto-research-fast
description: Fast layer (A) of the Self-Evolved Research Harness. Use when observing the harness, reframing its research question, recording an inquiry/no_change, or implementing one B-selected recommendation for later outcome assessment.
whenToUse: Run this skill whenever acting as A (fast layer) of the auto-research loop. A investigates and proposes; A never reviews its own inquiry, verifies, or applies.
disable-model-invocation: false
user-invocable: true
---

# Auto-Research Fast (A)

## Role contract

You are A, the fast layer of the same auto-research process.

- Observe and reframe before proposing. Do not apply modifications and do not run slow hard verification.
- An inquiry is a research judgment, not evidence that capability improved.
- One B-accepted inquiry authorizes exactly one proposal attempt.
- Hard verification establishes operability of the frozen command. It does not
  establish the inquiry hypothesis; only a pinned outcome artifact and B's
  `supported` assessment can authorize apply.
- `M0` is immutable. Never edit `.research/auto_research.json` by hand to change M0.
- Soft pre-checks are useful for ranking your own candidates, but they never count as verification.
- Keep candidates local and cheap to falsify. Prefer one-node changes over broad rewrites.

## Start of task

```bash
python tools/auto_research.py a-brief
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
python tools/auto_research.py self-observe --json
python tools/auto_research.py retro next
```

Read `.research/auto_research.json` before acting. Check the active inquiry and
proposal first; never bypass an unresolved state.

## Evidence-first rule

If `retro next` reports an open item, use it as evidence in the inquiry. It does
not authorize a proposal and must not bypass B's inquiry review.

## First produce an inquiry

Write an inquiry JSON that contains concrete evidence, `question_before`, a
materially better and falsifiable `question_after`, a hypothesis/falsifier, and
an evaluation boundary separating mutable machinery from protected evaluation.
Pre-register the baseline, measure, success rule, guardrails, and fixed budget.
In the evaluator or success-rule prose, state the expected artifact type/location
convention and the result-to-next-question policy. The actual `artifact_locator`
and `next_question` are outcome fields submitted only after hard verification and
measurement. Each recommendation must include a narrow machine-checkable contract:

```json
{
  "implementation_contract": {
    "allowed_ops": ["patch_file"],
    "allowed_paths": ["tools/target.py", "tests/test_target.py"],
    "required_targets": ["N-target"],
    "required_verification_terms": ["unittest", "test_target"]
  }
}
```

Then record it and stop for B:

```bash
python tools/auto_research.py inquire --track A --file inquiry.json
```

If evidence supports no modification, use `disposition=no_change`, an empty
recommendation list, and substantive `no_change_reason` and `revisit_when`.
`no_change` still requires B review and creates no proposal or improvement evidence.

## Implement the selected recommendation

Only after B passes the inquiry and selects one recommendation, create the patch
and bind the proposal to both exact IDs. That B review also pins evaluator bytes
and the old self-test command in protected content-addressed storage; do not edit
the live evaluator after acceptance:

```bash
python tools/auto_research.py add-node \
  --id N-<topic> --type assumption \
  --statement "<candidate statement>" --layer L1 --status draft

python tools/auto_research.py propose --track A \
  --inquiry INQ-001 --recommendation REC-1 \
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
`.research/auto_snapshots/`. It may never touch `tests/protected/`; the protected
bootstrap sentinel is installed externally while the old schema-1 engine is
still live. The engine may patch itself, including
`tools/auto_research.py`. A file patch still needs one or two graph target nodes.

Build a patch without editing the live file, and stay within the selected
recommendation's contract:

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
in a disposable staged copy, then runs the frozen outcome plan and records its
artifact before any apply. The ordinary agent/verification runner fails closed
unless it has Linux `/proc`, Landlock ABI >= 3, subreaper support, and a supported
architecture (`x86_64`, `aarch64`, or `riscv64`). Landlock denies ordinary direct
content/topology opens outside explicitly disposable roots; process-group and
`/proc` descendant cleanup plus bounded output are additional controls.

This is a nonprivileged, cooperative containment boundary, not an OS security
sandbox. It assumes no concurrent same-UID writer. The manifest can best-effort
reset selected metadata (mode, ownership, xattrs, mtime, and available inode
flags), but it stores neither byte preimages nor atime and does not claim to
repair content/topology changes. Brokered writes, inherited/pre-opened file
descriptors, signal/ptrace interference, and resource-exhaustion attacks are out
of scope.

Schema-1 to schema-2 installation is a separate one-time trust transition owned
by B. A prepares only the exact main patch; it must not include the preinstalled
protected sentinel. `transition-install` is the sole exact-manifest live-write
exception, not a normal proposal path. B must first use `transition-capture` and
retain its digest externally; a crash after old apply can be recovered with
`transition-seal` only from the exact captured poststate. Legacy leaf-only
backups remain fail-closed, and rollback is limited to the exact indexed
`bootstrap_floor`.

## Hand-off to B

After `inquire`, stop for inquiry review. After a linked `propose`, stop for
proposal critique. Do not run `review-inquiry`, `critique`, `verify`,
`assess-inquiry`, `transition-*`, or `apply`.
The runner's deterministic check is only mechanical preflight and intentionally
cannot accept your proposal on B's behalf.
State:

```text
Inquiry and selected recommendation:
Reframed research question:
Protected evaluation boundary:
Proposal (if B already selected a recommendation):
Retrospective converted (or blocking reason):
Target nodes:
Expected local gain:
Cheap self-check run by A:
Verification command proposed for B:
Frozen outcome artifact/measure proposed for B:
```

If B challenges the inquiry, revise the inquiry fields and preserve its review
history. If a linked proposal is rejected or fails verification, its one
authorization is consumed: open a new evidence-backed inquiry instead of
silently retrying another patch under the old review.

## Current empirical question

The implemented protocol is structural machinery, not evidence that question
quality improved. The next experiment is a held-out, equal-budget comparison of
inquiry-first A/B against direct-propose: 12 scenarios (4 harmful changes, 4
valid changes, 4 `no_change` cases), 4 seeds per condition, with the same model,
token, time, and proposal budgets. Test whether inquiry-first lowers false
acceptance and improves blinded question-quality ratings while reducing
valid-change yield by no more than 5 percentage points. Do not report this as an
achieved result until the frozen artifacts are assessed.
