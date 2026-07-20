# Research Closure Operating Rules

This repository is managed in **graduation-mode research closure**.

At the start of every session:

1. Read `.research/state.json`.
2. Read the active sprint and experiment logs in `.research/logs/`.
3. Run `python tools/research_closure.py guard`.
4. State the frozen claim, today's deliverable, and what is out of scope.

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

Use `python tools/research_closure.py close-day` whenever possible.
