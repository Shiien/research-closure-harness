---
name: research-handoff
description: >
  Carry research work across the boundary where it leaves your context and comes
  back. Two directions, equal weight. Inbound — use whenever a Codex, Claude Code,
  or other agent transcript, log, results table, or report is pasted in and any
  interpretation is wanted (a summary, "what does this mean", "did it work", "what
  should I do next"), to turn it into evidence, one decision, and one next action.
  Outbound — use whenever a task, prompt, or spec is being written for an agent that
  does not share this conversation, to turn an experiment card into a prompt that
  stands alone. Also use when reconciling a run against a frozen claim or deciding
  whether a result closes an experiment. Pairs with the research-closure skill,
  which owns the state machine.
---

# Research Handoff

## Scope

Two directions across one boundary — the point where work leaves your context and returns.

- **Inbound**: an agent returns logs, tables, and claims. Convert them into evidence, one decision, and one next action.
- **Outbound**: a task goes to an agent that cannot see this conversation. Convert the current claim and experiment card into a prompt that stands alone.

The `research-closure` skill and `docs/protocol.md` (installed as `docs/research_closure_protocol.md` in a bootstrapped repository) own the state machine: frozen claim, WIP limits, experiment cards, decision taxonomy, claim-lowering ladder. Reference them rather than restating them.

**There is no required output shape.** Structure and length follow the input — a twenty-line log gets a five-line synthesis, not a report. What follows are constraints on what must be true of the output, not a template to fill.

## Before either direction

Read `.research/state.json` and the open experiment card, then run:

```bash
python tools/research_closure.py guard
```

Three things are needed: the frozen claim, the open card if there is one, and that card's pre-registered metric and kill criterion. Outside an initialized repository, ask which claim the work is meant to test rather than inferring one — a synthesis anchored to a claim the researcher never froze is worse than no synthesis, because it looks like closure.

---

## Part I — Reading agent output

### Keep measurement separate from conclusion

Five kinds of statement, kept distinct whether or not they are explicitly labeled:

1. **Observed** — directly measured, with an artifact behind it.
2. **Derived** — computed from observed values.
3. **Interpretation** — the best current explanation.
4. **Hypothesis** — plausible, untested.
5. **Claim boundary** — what the evidence does not support.

Agent output routinely blurs 1–3. The characteristic failure is an interpretation inheriting the certainty of the number that suggested it, and a hypothesis becoming a result because it was restated confidently.

These map onto the harness: 1–2 are `--evidence`, 3 is the evidence-backed conclusion, 4 goes to the idea backlog or "remaining uncertainty," 5 is "what was ruled out."

### Define the terms that carry the argument

Give operational definitions — what a term means *in this experiment* — for internal status labels, method-arm names, cost definitions, and metrics whose direction is not obvious. Define what the reader needs to follow the reasoning and nothing more; a glossary of terms the argument doesn't rest on is padding.

### Correct the logic, not only the wording

Overbroad summaries recur in a few shapes:

- **Method vs. instance** — "X fails" usually means "this configuration or protocol of X fails." The scope decides whether the card is `falsified` or `inconclusive`.
- **Cost-basis conflation** — a win on one basis (per-deployment, amortized) is not a win on another (total development, cold-start). Name the basis.
- **Range-limited generalization** — "it transfers" may hold only within the tested range.
- **Name vs. implementation** — a component named after a theory may implement only a restricted special case of it.

State the most accurate formulation and why the difference changes the decision. A correction that doesn't change a decision is a wording note.

### Build the evidence chain

For each main insight: claim → mechanism → minimum supporting results → alternatives ruled out → remaining uncertainty.

If the minimum supporting results are not in the artifacts, that absence is the finding. Do not reconstruct them from the agent's prose.

### Classify the failure before proposing anything

Work down the harness order: implementation correctness → measurement validity → hypothesis falsification → claim narrowing → termination → project pivot. Do not jump from step 1 to step 6.

A failed run is an implementation-level event until shown otherwise. It does not license a new method family, benchmark, or research question.

### Land on one decision

`supported`, `falsified`, `inconclusive`, or `terminated`.

`inconclusive` must name a specific defect and prescribe one minimal repair with a strict time budget. "Needs more experiments" is not a decision. Two consecutive inconclusive results trigger claim narrowing or termination.

### Hand back a command only when it is warranted

Emit a `close-experiment` call only when **both** hold: a card is open, and the evidence determines one of the four decisions.

```bash
python tools/research_closure.py close-experiment \
  --id EXP-00N \
  --decision falsified \
  --evidence "results/sweep.csv,figures/main.pdf" \
  --conclusion "..."
```

Otherwise, say which condition failed and emit nothing. A `--decision` that cannot be filled is itself informative — usually it means the run covered part of a card, not all of it, and forcing a command there converts partial evidence into recorded closure.

`--evidence` takes real paths. If a reported number has no artifact behind it, report the gap instead of listing a path.

Ideas that surfaced but do not test the frozen claim go to `add-idea` with the reason, not into the synthesis as future work.

### End with one next action

The smallest action that reduces the named uncertainty — not a menu of directions.

---

## Part II — Writing prompts for agents

### Make the prompt self-contained

The agent cannot see this conversation, the repository state, or prior rounds. The prompt carries: verified current state; one falsifiable question; why the previous round succeeded or failed; which files to inspect; the scope of this round; evaluation and stopping criteria; required outputs. "As discussed above" resolves to nothing.

When a card exists, the prompt is largely the card — question, hypothesis, intervention, controls, measurement, expected artifact, kill criterion, time budget. Carry those fields across rather than re-deriving them in fresh words: the card is the pre-registration, and paraphrase silently loosens it.

### Instruct the agent to inspect before acting

Read the committed reports, configs, artifacts, and tests first. If a referenced file is missing or renamed, locate the committed equivalent or report the gap — never infer a result from a filename.

### Freeze the science, not the engineering

Specify what is scientifically load-bearing: data splits and leakage rules, ground-truth restrictions, baselines, matched budgets, cost accounting, independent replicates, the accuracy target, claim boundaries, and any compute ceiling.

Leave class names, directory layout, helper structure, and style to the agent unless one of them is load-bearing. Engineering instructions crowd out the constraints that determine whether the result means anything.

### Arms belong inside one card

Use three to five arms, each answering a distinct question: trivial baseline, strongest practical baseline, current anchor method, proposed innovation, one mechanism control. Skip the combinatorial grid unless the grid is the question.

Multiple arms and seeds inside one card are fine. Multiple open cards are not. Conditional escalation — cheap controlled test, then realistic check, then expensive run — belongs inside a single card, gated on a stated prerequisite, rather than being spun out as parallel workstreams.

### Freeze before the confirmatory card opens

Exploratory work is legitimate: debugging, budget selection, candidate screening, comparing acquisition strategies. It has to finish *before* the confirmatory card is created. Once that card is open, methods, seeds, thresholds, models, and cost rules are frozen. A metric changed after seeing results creates a new card; it does not amend this one.

### Define success and honest failure

State the primary metric, the primary cost basis, the comparison baseline, the minimum condition for success, the diagnostic outcomes that distinguish failure modes, and the conditions under which the agent stops. A negative result delivered against these is a completed deliverable.

### Treat cost as a measurement

When the question is efficiency, cost pre-registers like any other metric. Enumerate its components before running, state which basis is primary and which secondary, and require the agent to report whether an omitted component would flip the comparison. A favorable cost produced by silently dropping setup, acquisition, or failed work is a measurement error, not a result.

### Rule out fabrication

Do not invent missing data, completed runs, literature results, filenames, or metrics. Distinguish planned, implemented, executed, and validated. Use ground truth only where evaluation requires it. Report `NOT_RUN` or `NOT_ESTIMABLE` rather than filling a table.

### Keep the bug policy narrow

No global infrastructure audits. Require a fix only when a bug changes trajectories, statistical weights, data or features, the model, the cost, or the conclusions — and pair each with a minimal reproduction, a fix, and a regression or invariant test. Other bugs get noted, not chased; bug-hunting is the most common way a bounded round becomes unbounded.

### Preserve auditability

Ask for replicate-level raw outputs, a frozen config, seeds and provenance, concise central tables, honest reporting of superseded or failed checks, and a final status with one recommendation.

---

## Style

- One central question in preference to several goals.
- Preserve the researcher's terminology and the project objective.
- Explain before judging; separate evidence from interpretation.
- Preserve negative results and uncertainty rather than resolving them for readability.
- No motivational prose, and no expanding an experiment for the sake of completeness.
