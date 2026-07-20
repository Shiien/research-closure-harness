# Research Closure Protocol

## 1. Four levels that must not drift together

### Level 0 — Long-term agenda

Stable for approximately six months.

Example:

> Continual option learning: construct, retain, adapt, and retire reusable temporal abstractions for future tasks.

### Level 1 — Current project question

Stable for approximately six weeks.

Example:

> When can a learned representation support stable and reusable option discovery?

### Level 2 — Sprint claim

Stable for 7–14 days.

Example:

> A conditioning quantity predicts affine representation recovery under controlled excitation.

### Level 3 — Implementation choices

May change daily:

- optimizer;
- seed;
- estimator;
- plotting;
- environment size;
- bug fixes.

A Level-3 failure must not automatically change Levels 0–2.

---

## 2. Claim quality test

A valid sprint claim must specify:

1. object being studied;
2. relation or separation being asserted;
3. controlled setting;
4. measurable outcome;
5. possible falsification.

Bad:

> Study identifiability for option discovery.

Better:

> Across controlled excitation levels, log condition number predicts affine recovery error more reliably than predictive loss.

---

## 3. Experiment design rule

One experiment card corresponds to one causal comparison or diagnostic chain.

It may include multiple seeds and problem instances. Those are not separate experiments unless they test different hypotheses.

Required fields:

- question;
- hypothesis;
- intervention;
- controls;
- metric;
- expected figure;
- artifact;
- kill criterion;
- time budget.

---

## 4. Decision taxonomy

### Supported

Evidence meets the predefined threshold and implementation checks pass.

### Falsified

The predicted relation is absent or reversed under a valid test.

### Inconclusive

The test cannot distinguish the hypothesis because of a named defect.

An inconclusive decision must prescribe one minimal repair and a strict new time budget.

### Terminated

The hypothesis may remain unresolved, but further work has insufficient expected value under the graduation constraint.

---

## 5. Claim-lowering ladder

When evidence is weaker than hoped, lower the claim in this order:

1. universal → restricted setting;
2. causal → predictive association;
3. algorithmic improvement → mechanism characterization;
4. positive result → boundary or failure mode;
5. full framework → diagnostic component;
6. paper contribution → thesis technical note.

Do not discard valid evidence merely because it does not support the original strongest story.

---

## 6. Daily closure

At the beginning of a day:

> What exact uncertainty will be smaller tonight?

At the end:

- name the artifact;
- state what the evidence says;
- make a decision;
- name only one next action.

A day can be successful with a negative result.

---

## 7. Weekly closure

Every week, answer:

1. What claim was frozen?
2. What evidence was produced?
3. What was ruled out?
4. What entered the written draft?
5. What ideas were rejected as out of scope?
6. Continue, narrow, terminate, or advance?

---

## 8. WIP limits

Default graduation-mode limits:

- active project questions: 1
- active sprint claims: 1
- primary active experiments: 1
- optional debugging subtask: 1
- new method families per sprint: 0 unless explicitly planned

---

## 9. Anti-patterns

### Abstraction escalation

A failed experiment triggers a more fundamental research question.

Countermeasure: classify the failure first as implementation, measurement, hypothesis, or project-level.

### Method accumulation

Each negative result creates another objective or module.

Countermeasure: no new method before closing the previous experiment.

### Moving success criterion

The metric changes after seeing results.

Countermeasure: preserve the original metric; any new metric belongs to a new card.

### Draft avoidance

Writing starts only after the “main result” is complete.

Countermeasure: create the draft in week one and update it every week.

### Infinite inconclusiveness

Every test is declared insufficient.

Countermeasure: two consecutive inconclusive runs trigger claim narrowing or termination.

---

## 10. Meeting preparation

Do not present a landscape of possibilities. Present:

1. frozen problem;
2. current claim;
3. evidence;
4. decision;
5. one blocker requiring advisor input.

The advisor should be asked to decide whether the scoped contribution is enough—not to select among ten new directions.
