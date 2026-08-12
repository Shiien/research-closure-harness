# Research Closure Rules for Codex

## Mission

Help the researcher finish a defensible research unit. Optimize for closure, evidence, and a complete draft—not for generating the largest number of ideas.

Before substantial work, read:

1. `.research/state.json`
2. the current sprint log under `.research/logs/`
3. the active experiment card, if any
4. `docs/protocol.md`

Run:

```bash
python tools/research_closure.py guard
```

If the guard reports a blocking violation, do not begin a new method, experiment family, benchmark, or research question. Help resolve the violation first.

## Non-negotiable workflow

Every task must map to the current frozen claim.

Before implementing a new experiment, identify:

- research question
- falsifiable hypothesis
- single primary intervention
- measurement
- expected artifact
- kill criterion
- time budget

If no active experiment card exists, create one with the CLI before implementation.

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
- `Today's deliverable`
- `Out of scope`

If `.research/claim_graph.json` exists, run `python tools/claim_graph.py frontier` and also state:

- `Ready frontier` — the probes whose guards are satisfied
- `Resolution map` — determined (with its verdict) or still open

At the end, state:

- `Artifact produced`
- `Evidence`
- `Decision`
- `Next smallest action`

Do not end with a list of many possible next directions.
