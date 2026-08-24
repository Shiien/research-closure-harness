# Self-Evolved Research Harness Rules for Codex

## Session auto-load (A/B auto-research)

This branch runs the A/B Self-Evolved Research Harness. At every session start,
before choosing a task:

1. Run `python tools/auto_research.py a-brief` and read the immutable FINAL GOAL first.
2. Run `python tools/auto_research.py ab-status`.
3. Run `python tools/auto_research.py ab-next`.
4. Run `python tools/auto_research.py retro next`; use an open retrospective as evidence for the next inquiry, never as permission to skip inquiry review.
5. Read `.research/auto_research.json`.
6. Declare your role explicitly:
   - `A (fast)`: use the `auto-research-fast` skill; observe, reframe, and record an inquiry or justified `no_change`; implement only one B-selected recommendation.
   - `B (slow)`: use the `auto-research-slow` skill; review inquiries, freeze one implementation contract, then critique, hard-verify operability, assess the frozen outcome, apply supported changes, and revalidate.
7. If the session is ordinary PhD research work, also use the `research-closure`
   skill and run the closure commands below.

The A/B contract is: self-observe -> A inquiry/no_change -> B inquiry review,
recommendation selection, evaluator snapshot, and frozen outcome plan -> A linked
proposal -> B digest-bound critique -> B hard verification of operability -> run
the frozen outcome plan -> B `assess-inquiry` -> supported-only protected
sentinels + frozen old test bytes + candidate tests -> transactional B apply.
One accepted inquiry authorizes one proposal attempt. Inquiry acceptance and
exit-0 verification are not evidence of improvement. `supported` alone permits
apply; `falsified` or `inconclusive` terminates without a live patch. B
independence is an external operating requirement; actor fields are audit labels,
not cryptographic authentication. `M0` is immutable.

## Mission

Help the researcher finish a defensible research unit. Optimize for closure, evidence, and a complete draft—not for generating the largest number of ideas.

Before substantial work, read:

1. `.research/state.json`
2. the current sprint log under `.research/logs/`
3. the active experiment card, if any
4. `.research/claim_graph.json` — the claim graph is the engine: it must exist and validate before a sprint can be frozen
5. `docs/protocol.md`

Run:

```bash
python tools/research_closure.py guard
python tools/claim_graph.py frontier
python tools/research_closure.py next
```

The guard reports blocking violations; `frontier` shows which probe is ready to run; `next` shows the event the harness expects from you. If the guard reports a blocking violation, do not begin a new method, experiment family, benchmark, or research question. Help resolve the violation first.

For human progress tracking, run `python tools/research_closure.py dashboard` to open the interactive DAG view (theory → variables/edges → probes, with outcomes, frontier and the resolution map).

## Auto-research operating mode

For self-modification work:

- Invoke the `auto-research-fast` skill when acting as A.
- Invoke the `auto-research-slow` skill when acting as B.
- Drive the loop as `self-observe -> inquire/no_change (A) -> review-inquiry (B)
  -> linked propose (A) -> critique/verify/assess-inquiry (B) ->
  supported-only apply (B)`.
- Every proposed recommendation must include a narrow `implementation_contract`
  (`allowed_ops`, `allowed_paths`, `required_targets`, and
  `required_verification_terms`). B selects and thereby freezes exactly one.
- `no_change` still requires a substantive diagnosis, falsifier, evaluation
  boundary, revisit condition, and B review. It creates no proposal, verification,
  modification node, or improvement evidence.
- A rejected proposal or failed verification consumes that inquiry's one
  implementation authorization. Start a new evidence-backed inquiry; do not
  silently retry a different patch under the old review.
- A critic verdict is soft judgment and never replaces hard verification. Exit 0
  establishes operability and moves the inquiry to `awaiting_outcome`; it does
  not prove the hypothesis or authorize apply.
- The deterministic runner check is mechanical preflight only. It must leave the
  proposal `proposed`; only an independent B may record a semantic critic pass.
- B acceptance freezes the current evaluator bytes and self-test command in a
  protected content-addressed snapshot before A can propose. Drift or missing
  blobs fail closed; B's review digest also freezes proposal meaning.
- Before apply, run protected sentinels, that byte-frozen pre-patch `tests/` tree
  against candidate code, and the candidate's own tests. Shell children must be
  reaped before the live manifest is checked.
- Before those pre-apply checks, run the frozen outcome plan, pin its exact local
  artifact, and record B's `supported` assessment with `assess-inquiry`.
- Ordinary apply has synchronous exception recovery but is not crash-atomic.
- `M0` is the immutable L0 meta-goal; the engine blocks any patch that targets it.
- End a self-modification session with `python tools/auto_research.py self-test`.
- Do not edit `.research/auto_research.json` by hand to bypass the pipeline.

### Containment boundary

Ordinary agent and verification commands fail closed outside Linux
`x86_64`/`aarch64`/`riscv64` with `/proc`, subreaper support, and Landlock
ABI >= 3. Landlock denies ordinary direct content/topology opens outside
disposable write roots. Stdin isolation, bounded output, process-group and
`/proc` descendant cleanup, and sequential evaluator roots are additional
controls. The live manifest may best-effort reset selected metadata (mode,
ownership, xattrs, mtime, and available inode flags), but has no byte preimages,
excludes atime, and does not claim content/topology repair.

This is nonprivileged cooperative containment, not an OS sandbox; assume no
concurrent same-UID writer. Brokered writes, inherited/pre-opened file
descriptors, signal/ptrace interference, and resource exhaustion are out of
scope.

### Bootstrap boundary

The schema-2 protected sentinel is installed externally while schema 1 is live;
the main patch cannot touch `tests/protected/`. B runs `transition-capture`
from the separate candidate artifact and retains its digest externally, then
uses `transition-install` as the exact-manifest trusted one-time live-write
exception. Install invokes old schema-1 apply with bounded output and descendant
cleanup, then seals schema 2. If the process crashes after old apply but before
seal, only the retained digest plus the exact captured poststate may recover via
`transition-seal`.

Legacy schema-1 leaf-only backups remain fail-closed. Only the newly sealed
bootstrap entry has reconstructed topology, and cross-transition rollback is
limited to the exact indexed `bootstrap_floor`; older schema-1 and unsealed
post-apply states are rejected.

### Current evidence boundary

The inquiry-first schema is implemented, not empirically validated. The next
experiment is an equal-model, equal-token/time/proposal-budget comparison of
inquiry-first A/B with direct-propose on 12 held-out scenarios (4 harmful, 4
valid, 4 `no_change`) and 4 seeds per condition. Measure false acceptance,
blinded question quality, and valid-change yield, with a guardrail that yield
falls by no more than 5 percentage points.

Ordinary PhD-research work still follows the closure workflow below.

## Non-negotiable workflow

Every task must map to the current frozen claim and to a probe of the claim graph.

Before implementing a new experiment, identify:

- research question
- falsifiable hypothesis
- single primary intervention
- measurement
- expected artifact
- kill criterion
- time budget

Every experiment must name the claim-graph probe it runs (`new-experiment --node Pn`). If no active experiment card exists, create one with the CLI before implementation. If the claim graph does not exist yet, author it first: `claim_graph.py init --claim "..."`, then `add-variable` / `add-edge` / `add-absent` / `add-probe` / `add-resolution`, then `validate`.

After obtaining results, do not immediately create another variant. First:

1. summarize evidence;
2. classify the result as `supported`, `falsified`, `inconclusive`, or `terminated`;
3. write a decision;
4. close the experiment with the CLI;
5. update the draft or result note.

## Scope-control behavior

When the user proposes a new idea:

1. explain whether it directly tests the frozen claim;
2. if not, add it to the idea backlog;
3. do not implement it unless the current sprint is explicitly closed or revised.

Prefer, in order:

1. debug the existing test;
2. improve measurement validity;
3. narrow the claim;
4. record a negative result;
5. only then consider a project-level pivot.

Never silently change:

- the project question;
- the sprint claim;
- the success metric;
- the baseline;
- the definition of completion.

## Artifact-first behavior

A working session should end with at least one inspectable artifact:

- source code committed or ready to commit;
- a figure;
- a table or CSV;
- a proof fragment;
- a written section;
- a decision note;
- a documented negative result.

Do not describe broad exploration as progress unless it produces one of these.

## Graduation mode

The researcher is in the final PhD year. Therefore:

- favor a minimal correct claim over a broad unfinished claim;
- favor a complete four-to-six-page note over another method branch;
- favor reusing existing environments and code;
- avoid adding benchmarks unless required to validate the frozen claim;
- do not optimize for an idealized top-conference story before Level-C closure exists.

## Required response format during research work

At the beginning of a substantial task, state:

- `Frozen claim`
- `Session deliverable`
- `Out of scope`

Also state (run `python tools/claim_graph.py frontier` if the claim graph exists — it is mandatory once a sprint is frozen):

- `Ready frontier` — the probes whose guards are satisfied
- `Resolution map` — determined (with its verdict) or still open

At the end, state:

- `Artifact produced`
- `Evidence`
- `Decision`
- `Next smallest action`

Do not end with a list of many possible next directions.
