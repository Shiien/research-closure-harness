# Sprint 01 — Learnability and Identifiability

## Frozen claim

Under controlled excitation, a conditioning quantity predicts affine representation recovery more reliably than predictive loss.

## H1

Instances with similar predictive loss can have substantially different affine recovery error.

## H2

The proposed conditioning quantity has stable rank correlation with affine recovery error across seeds and problem instances.

## Main figure

Three panels:

1. predictive loss vs. affine recovery;
2. conditioning quantity vs. affine recovery;
3. excitation level vs. conditioning quantity.

## Minimum completion

- one controlled finite-state environment;
- at least 30 problem instances;
- at least 5 seeds where computationally feasible;
- `results.csv`;
- one main figure;
- a four-page technical note;
- explicit supported/falsified/inconclusive/terminated decision.

## Kill criterion

If the conditioning quantity has near-zero rank correlation after correctness checks and sufficient intervention range, stop promoting it as the primary predictor and write the negative result.

## Out of scope

Option policies, downstream transfer, F3 variants, new representation objectives, and new benchmarks.
