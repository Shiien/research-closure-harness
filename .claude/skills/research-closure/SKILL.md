---
name: research-closure
description: Enforce a claim-to-evidence-to-decision workflow for research projects. Use when planning experiments, implementing research code, analyzing results, changing scope, or preparing weekly updates.
---

# Research Closure Skill

## Objective

Turn open-ended research into small, inspectable, closed units.

The governing loop is:

```text
Frozen claim
→ falsifiable hypothesis
→ bounded test
→ evidence
→ explicit decision
→ written artifact
```

## Start-of-task protocol

Read `.research/state.json`, then run:

```bash
python tools/research_closure.py guard
```

Report:

```text
Frozen claim:
Today's deliverable:
Out of scope:
```

If there is no frozen claim, do not create a large implementation. Help define a 7–14 day claim first.

## New-experiment gate

Before coding, require all fields:

```text
Question:
Hypothesis:
Intervention:
Measurement:
Expected artifact:
Kill criterion:
Time budget:
```

Reject vague tasks such as:

- “explore whether this works”;
- “try several alternatives”;
- “improve the method”;
- “find a better objective”.

Rewrite them as a single testable claim.

## Result gate

Every experiment ends in exactly one decision:

- `supported`
- `falsified`
- `inconclusive`
- `terminated`

“Inconclusive” must name a specific defect, such as estimator variance, implementation uncertainty, insufficient intervention range, or metric saturation.

Never accept “needs more experiments” as a complete decision.

## Scope gate

A new idea is in scope only if success or failure directly changes confidence in the frozen claim.

Otherwise:

```bash
python tools/research_closure.py add-idea \
  --idea "..." \
  --reason "Does not test the current frozen claim."
```

## Failure handling

Use this order:

1. implementation correctness;
2. measurement validity;
3. hypothesis falsification;
4. claim narrowing;
5. termination;
6. project pivot.

Do not jump from step 1 to step 6.

## Graduation-mode optimization

Prefer:

- existing code over a rewrite;
- a minimal environment over a new benchmark;
- a correct limited theorem over a universal informal claim;
- a complete note over an additional method;
- a decision over continued ambiguity.

## End-of-task protocol

Report:

```text
Artifact produced:
Evidence:
Decision:
Next smallest action:
```

Then update the CLI state or write the relevant decision log.
