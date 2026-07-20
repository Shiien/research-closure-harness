# Project Charter — Continual Option Learning

## Long-term agenda

Construct, retain, adapt, and retire reusable options that accelerate learning across changing reinforcement-learning tasks.

## Current project question

When can a learned representation support stable and reusable option discovery?

## Immediate bottleneck

A predictive representation may achieve low loss while remaining poorly identifiable. A representation-driven subtask generator may therefore produce unstable rewards, policies, occupancies, or termination conditions.

## Minimal thesis contribution

Establish and empirically validate a learnability–identifiability separation, and determine whether a conditioning quantity predicts affine representation recovery.

## Explicitly out of scope for the current sprint

- a full continual option library;
- a new large-scale benchmark;
- simultaneous optimization of representation and policy with F3;
- a universal exploration objective;
- comparing every spectral and successor representation method;
- proving long-horizon continual utility.

## Backup scope

If option stability cannot be connected within the time budget, close the work as a representation-recovery conditioning analysis and thesis technical chapter.
