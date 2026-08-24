---
name: auto-research-slow
description: Slow layer (B) of the Self-Evolved Research Harness. Use when reviewing A inquiries, freezing one implementation contract, criticising and hard-verifying a linked proposal, assessing its measured outcome before apply, bootstrapping schema 2, or revalidating.
whenToUse: Run this skill whenever acting as B (slow layer) of the auto-research loop. B owns inquiry review, recommendation selection, proposal critique, hard verification, outcome assessment, application, bootstrap transition, and revalidation.
disable-model-invocation: false
user-invocable: true
---

# Auto-Research Slow (B)

## Role contract

You are B, the slow layer of the same auto-research process.

- Review the research question before any proposal exists. A silent pass is not allowed.
- On inquiry pass, select exactly one recommendation and inspect its implementation contract. The engine then pins evaluator bytes and the old self-test command in protected content-addressed storage before A can propose.
- Criticise every linked proposal against the full selected recommendation and frozen contract. A passing semantic B review is digest-bound to that proposal and its evaluator snapshot.
- Soft judgment never replaces hard verification. An exit-0 verification changes
  a proposal to `verified`, but proves only that the frozen command operated
  successfully—not that the research hypothesis is true.
- After hard verification, run the frozen outcome plan and bind its exact local
  artifact with `assess-inquiry`. Only a `supported` outcome may proceed to
  apply; `falsified` and `inconclusive` terminate the proposal without a live
  patch.
- Apply runs protected sentinels, the byte-frozen pre-patch `tests/` tree against
  candidate code, and the candidate's own self-test in temporary trees; only
  then does a live transaction create a modification node. The transaction has
  synchronous exception recovery; it is not crash-atomic.
- Treat the deterministic runner check as structural preflight only. It deliberately stops with the proposal still `proposed`; an independent B must assess recommendation fidelity and whether verification really exercises the claimed effect.
- B independence is an external process requirement and a declared actor name;
  the CLI's `track`, reviewer, critic, and assessor fields are audit labels, not
  cryptographic authentication or proof that two independent principals acted.
- `M0` is immutable. The engine blocks patches that target it.
- Do not generate new candidates while A has open work. Send feedback to A instead.

## Start of task

```bash
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
```

Read `.research/auto_research.json`, the current inquiry, its evidence and
evaluation boundary, and then the current proposal if one exists.

## B pipeline

```bash
# 1. Inquiry gate: judge evidence, reframing, falsifier, evaluator boundary,
#    and the machine-checkable implementation contract. Select exactly one
#    recommendation only on pass. This command pins the evaluator snapshot.
python tools/auto_research.py review-inquiry --track B \
  --inquiry INQ-001 --verdict pass \
  --reviewer "<reviewer>" --reason "<specific reason>" \
  --recommendation REC-1

# A now creates one proposal linked to INQ-001/REC-1 and stops.

# 2. Proposal critic gate: compare actual ops, paths, targets, and verification
#    with the complete frozen recommendation; pass, challenge, or reject.
python tools/auto_research.py critique --track B \
  --proposal P-001 --verdict pass \
  --critic "<critic name>" --reason "<specific reason>"

# 3. Hard verification: frozen-command, exit-0 operability only. The command was frozen in
#    the proposal before critique and may not be replaced here.
#    For patch_file proposals the engine runs the command in an isolated,
#    disposable, manifest-guarded copy with the patch already applied (not an OS sandbox).
python tools/auto_research.py verify --track B --proposal P-001

# 4. Run the frozen outcome plan, write its result artifact, and bind B's
#    supported/falsified/inconclusive assessment before any live write.
python tools/auto_research.py assess-inquiry --track B \
  --inquiry INQ-001 --file outcome.json --assessor "<assessor>"

# 5. Only a supported outcome reaches apply. Before live write, the engine runs
#    protected sentinel suite, the byte-frozen old tests against candidate code,
#    and the candidate self-test. Each leg gets a new manifest-guarded tree that
#    is destroyed before the next leg's tree exists.
python tools/auto_research.py apply --track B --proposal P-001

# 6. Revalidate deprecated or draft nodes with evidence appropriate to the claim
python tools/auto_research.py revalidate --track B \
  --node N-<topic> --level hard --command "<exit-0 command>"

# 7. Capability self-test after self-modification sessions
python tools/auto_research.py self-test
```

`tests/protected/` is the fixed sentinel boundary and may not be patched by the
self-modifying engine. Candidate and mutable tests may still evolve, but they do
not replace these sentinels or the separately executed frozen old test bytes. The
schema-2 protected sentinel must be installed externally while schema 1 is live;
the main self-patch is forbidden from touching it.

Ordinary agent and verification commands are supported only on Linux
`x86_64`, `aarch64`, or `riscv64` with `/proc`, subreaper support, and Landlock
ABI >= 3. Landlock denies direct content/topology opens outside explicit
disposable write roots. Process-group plus `/proc` descendant cleanup, sequential
evaluator roots, stdin isolation, and bounded output narrow the failure surface.
The live manifest is defense in depth: it may best-effort restore selected
metadata (mode, ownership, xattrs, mtime, and available inode flags), but records
no byte preimage and excludes atime. Content/topology restoration is not claimed.

This is a nonprivileged cooperative boundary, assumes no concurrent same-UID
writer, and is not an OS security sandbox. Brokered writes, inherited/pre-opened
file descriptors, signal/ptrace interference, and resource exhaustion remain out
of scope.

## Schema-1 bootstrap trust transition

The protected sentinel is preinstalled and retained outside the main patch.
Then, from the separate schema-2 candidate artifact, B captures an exact manifest
and externally retains the printed digest:

```bash
python .research/tmp/self_inquiry/auto_research.py.candidate \
  transition-capture --track B --proposal P-BOOT \
  --reviewer "<reviewer>" --reason "<specific bootstrap reason>"

python .research/tmp/self_inquiry/auto_research.py.candidate \
  transition-install --track B --manifest-sha256 <digest> \
  --reviewer "<reviewer>" --reason "<specific bootstrap reason>"
```

`transition-install` is a trusted one-time live-write exception: it verifies the
exact manifest, invokes the old schema-1 apply with bounded output and descendant
cleanup, then seals schema 2. There is a crash window after old apply but before
seal. Recovery is possible only when the exact captured poststate still exists
and the retained digest is supplied:

```bash
python .research/tmp/self_inquiry/auto_research.py.candidate \
  transition-seal --track B --manifest-sha256 <digest> \
  --reviewer "<reviewer>" --reason "<crash recovery reason>"
```

Legacy schema-1 leaf-only file backups remain fail-closed. Only the newly sealed
bootstrap entry has reconstructed topology, and rollback across the transition is
allowed only to the exact indexed `bootstrap_floor`; arbitrary older schema-1
snapshots and unsealed post-apply state are rejected.

After apply, the engine closes the proposal's linked retrospective automatically
(`--retro R-xxx`). If A linked a retrospective that remains open, verify the
linkage before declaring session closure.

## Feedback contract to A

After `critique`, `verify`, `assess-inquiry`, or `apply`, report:

```text
Proposal:
Verdict:
Evidence:
Affected/deprecated nodes:
Feedback for A:
Next smallest A action:
```

If a linked proposal is rejected or a verification/pre-apply check fails, do not
apply and do not silently patch around it. The inquiry's one implementation
authorization is consumed; ask A for a new inquiry that cites the proposal's
rejection or `implementation_failure` as evidence. If a measured outcome is
`falsified`/`inconclusive`, ask A to copy that outcome's `next_question` exactly
into `question_before` and cite its pinned outcome blob. Only measured outcomes
have a pinned prior outcome.

## Current empirical question

Do not treat the protocol implementation as evidence of improvement. The next
experiment is an equal-model, equal-token/time/proposal-budget held-out comparison
between inquiry-first A/B and direct-propose: 12 scenarios (4 harmful changes, 4
valid changes, 4 `no_change` cases), 4 seeds per condition. Measure false
acceptance, blinded question-quality rating, and valid-change yield; the target is
lower false acceptance and better question quality with no more than a
5-percentage-point loss in valid-change yield.
