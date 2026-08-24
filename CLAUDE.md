# Self-Evolved Research Harness Operating Rules

This repository is managed in **graduation-mode research closure** and now also
runs the **A/B Self-Evolved Research Harness**.

## Session auto-load (always)

At the start of every session, before task selection:

1. Run `python tools/auto_research.py a-brief` and read the immutable FINAL GOAL first.
2. Run `python tools/auto_research.py ab-status`.
3. Run `python tools/auto_research.py ab-next`.
4. Run `python tools/auto_research.py retro next`; use an open retrospective as inquiry evidence, never as permission to skip inquiry review.
5. Read `.research/auto_research.json`.
6. Declare your role:
   - `A (fast)`: use `/auto-research-fast`; self-observe, reframe, and record an inquiry or justified `no_change`; implement only one B-selected recommendation.
   - `B (slow)`: use `/auto-research-slow`; review inquiries, freeze one implementation contract, then critique, hard-verify operability, assess the frozen outcome, apply supported changes, and revalidate.
7. If the session is ordinary PhD research work, also run the closure commands below.

The loop is `self-observe -> inquire/no_change (A) -> review/select/freeze an
outcome plan (B) -> one linked proposal (A) -> semantic critique (B) -> hard
verification of operability (B) -> frozen outcome run -> assess-inquiry (B) ->
supported-only protected/frozen/candidate checks -> apply (B)`. Inquiry
acceptance and exit-0 verification are judgments about protocol and operability,
not evidence of improvement; one acceptance grants one proposal attempt.
`supported` alone permits apply, while `falsified` and `inconclusive`
terminate without a live patch. `M0` and `tests/protected/` are immutable.
B independence is an external operating requirement; CLI actor fields are audit
labels, not cryptographic authentication.

B acceptance pins evaluator bytes, the old self-test command, and the outcome
plan before A can propose; semantic review is digest-bound to the exact proposal.
Before apply, B must pin a local outcome artifact and assess it `supported`.
Candidate code then passes protected sentinels, the pinned old `tests/` tree,
and its own tests in fresh sequential disposable trees. Ordinary apply has
synchronous exception recovery but is not crash-atomic.

### Containment boundary

Ordinary agent and verification commands fail closed outside Linux
`x86_64`/`aarch64`/`riscv64` with `/proc`, subreaper support, and Landlock
ABI >= 3. Landlock denies ordinary direct content/topology opens outside
disposable write roots. Stdin isolation, bounded output, process-group and
`/proc` descendant cleanup, and sequential evaluator roots are additional
controls. The live manifest may best-effort reset selected metadata (mode,
ownership, xattrs, mtime, and available inode flags), but has no byte preimages,
excludes atime, and does not claim content/topology repair. This is
nonprivileged cooperative containment, not an OS sandbox; assume no concurrent
same-UID writer. Brokered writes, inherited/pre-opened file descriptors,
signal/ptrace interference, and resource exhaustion are out of scope.

### Bootstrap boundary

Preinstall the schema-2 protected sentinel externally while schema 1 is live;
the main patch cannot touch `tests/protected/`. From the separate candidate
artifact, B runs `transition-capture`, retains its digest externally, and uses
`transition-install` as the exact-manifest trusted one-time live-write
exception. Install invokes old schema-1 apply with bounded output and descendant
cleanup, then seals schema 2. A crash after old apply but before seal is
recoverable only from the exact captured poststate with the retained digest via
`transition-seal`. Legacy leaf-only backups remain fail-closed; only the sealed
bootstrap entry has topology, and cross-transition rollback is limited to the
exact indexed `bootstrap_floor`; older schema-1 snapshots and an unsealed
post-apply state are rejected.

### Current evidence boundary

The inquiry-first schema is implemented, not empirically validated. The next
experiment compares inquiry-first A/B with direct-propose under equal model,
token, time, and proposal budgets on 12 held-out scenarios (4 harmful, 4 valid,
4 `no_change`), with 4 seeds per condition. Measure false acceptance, blinded
question quality, and valid-change yield, allowing no more than a
5-percentage-point yield loss.

At the start of every research-closure session:

1. Read `.research/state.json`.
2. Read the active sprint and experiment logs in `.research/logs/`.
3. Run `python tools/research_closure.py guard`.
4. Run `python tools/claim_graph.py frontier` — the claim graph is the engine, and the
   ready frontier is what decides which probe may run next.
5. Run `python tools/research_closure.py next` to see the event the harness expects.
6. State the frozen claim, this session's deliverable, and what is out of scope, plus
   the ready frontier and whether the resolution map is already determined.

For human progress tracking, `python tools/research_closure.py dashboard` opens the
interactive DAG view (theory → variables/edges → probes, outcomes, frontier,
resolution map, event log).

## Hard workflow constraints

- One active project question.
- One frozen sprint claim.
- One primary active experiment.
- No experiment without a written hypothesis and kill criterion.
- No new variant before the previous experiment receives a written decision.
- No scope change without explicitly revising or closing the sprint.
- New ideas go to backlog by default.

Before implementing a new experiment, either locate its existing experiment card or create one:

```bash
python tools/research_closure.py new-experiment ...
```

After results exist, close it:

```bash
python tools/research_closure.py close-experiment ...
```

## Research reasoning discipline

Always distinguish:

- project question;
- current claim;
- experimental hypothesis;
- implementation/debugging issue.

Do not upgrade an implementation failure into a new research direction without evidence.

When results are weak, prefer:

1. verify correctness;
2. test measurement sensitivity;
3. narrow the claim;
4. document the negative result;
5. terminate if the kill criterion is met.

Do not respond to failure by automatically proposing a new representation objective, exploration bonus, benchmark, or theoretical framework.

## Completion hierarchy

### Level C — closed research unit

- precise question;
- one credible test or counterexample;
- explicit conclusion;
- self-contained technical note.

### Level B — submission-ready draft

- mechanism/theory;
- multiple instances/seeds;
- baseline;
- full paper structure.

### Level A — ideal result

- broad theorem or strong algorithm;
- extensive benchmarks;
- top-conference positioning.

Always complete Level C before optimizing Level B or A.

## Session closing rule

Before ending a productive session, require:

- an artifact path;
- a concise evidence summary;
- a decision;
- the next smallest action.

Close the active experiment with the CLI and let the harness compute the verdict:
`python tools/research_closure.py close-experiment ...`, then check
`python tools/research_closure.py next` for what the harness expects next.

## Auto-research operating mode

When improving the harness itself:

- A uses `/auto-research-fast` to self-observe and record an inquiry or
  `no_change`; only after review does A implement one selected recommendation.
- B uses `/auto-research-slow` to review the inquiry and freeze its contract,
  then uses `critique --track B`, `verify --track B`,
  `assess-inquiry --track B`, supported-only `apply --track B`, or
  `revalidate --track B`.
- Run `python tools/auto_research.py ab-status` before acting and
  `python tools/auto_research.py ab-next` after each step.
- Exit-0 hard verification establishes operability and moves a linked inquiry
  to `awaiting_outcome`; it does not prove the hypothesis or authorize apply.
  A pinned `supported` outcome is required before apply. Applying then decays
  trust and deprecates the dependency closure of the changed nodes.
- End self-modification sessions with `python tools/auto_research.py self-test`.
- Never edit `.research/auto_research.json` by hand to bypass the pipeline.
