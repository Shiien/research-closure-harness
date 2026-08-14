# Claim Graph Protocol

The claim graph is the **engine** of the Research Closure Harness.
`docs/protocol.md` defines the methodology (levels, decision taxonomy,
claim-lowering ladder); this document defines the mechanism: probe sets, the
ready frontier, and the resolution map that composes probe outcomes into a
computed claim decision. A sprint cannot be frozen, an experiment cannot be
opened, and a result cannot be recorded without it.

## 1. Why one experiment card is not enough

The base protocol has four levels — agenda, project question, sprint claim,
implementation choices — and Level 3 is explicitly the noise floor: optimizer,
seed, plotting, bug fixes. Nothing sits between a 7–14 day claim and one causal
comparison.

So an experiment card is squeezed. Inflate it to cover the whole claim and its
metric and kill criterion cannot be pre-registered coherently. Shrink it to one
comparison and closing it `supported` over-claims, while closing it
`inconclusive` triggers the two-strike narrowing rule against a claim that was
never actually tested.

The underlying error is that three different scopes shared one vocabulary. The
decision taxonomy is scoped to a hypothesis, a card is scoped to a comparison,
and a claim is scoped to a *set* of comparisons. The claim graph supplies the
missing object: the set, plus the rule that composes its outcomes.

## 2. Three layers

```
theory layer        M-nodes: the mechanism believed
    | DEDUCTION: entails observable structure
observation layer   variables, directed edges, absence assumptions
    | DEDUCTION: derives testable implications
probe layer         P-nodes, each bound to one experiment card
    | INDUCTION: independent closed results support a generalisation
    `----------------------------------------------------------> theory layer

observation anomaly -- ABDUCTION --> candidate structural repairs
```

Keeping theory and observation in one layer is the mistake that makes deduction
impossible, because deduction *is* the step from the first to the second.

These names are part of the public model, not merely implementation labels:

- **Deduction** moves downward from theory and the observation DAG to
  observable implications and probes.
- **Induction** moves upward from multiple independent closed probes to a
  theory node that must make at least one new prediction.
- **Abduction** moves sideways from an anomaly that conflicts with the DAG to a
  finite, auditable set of candidate structural repairs.

Run `claim_graph.py reasoning` to display the same three-mode map in the CLI.

There is one graph per sprint claim, at `.research/claim_graph.json`. Its design
is hashed at `start-sprint`; outcomes are appended as work proceeds.

## 3. Deduction — theory to observable implications

`claim_graph.py deduce`

The full set of conditional independencies a DAG implies is exponential. Its
local Markov basis is linear and entails the rest, so the CLI enumerates the
pairwise form of that basis and reports two derived sets:

- **uncovered implications** — the theory commits to them and no probe tests them;
- **untested absence assumptions** — the load-bearing ones, since the claim
  usually rests on an edge you assert is *not* there.

Both are set differences, not search. What is not mechanised is which uncovered
implication to spend a probe on. That is emitted as a candidate set for ranking
(§7).

Ranking must be by expected falsification value — which probe, if negative,
does the most damage per unit cost — not by likelihood of a positive result.
Under a graduation constraint these orderings are close to opposite.

## 4. Induction — closed results to theory

`claim_graph.py induce --id M2 --statement ... --support EXP-...,EXP-... --entails ...`

The danger here is `docs/protocol.md` §9's abstraction escalation wearing a lab
coat: one negative result triggering a grand new framework. Two mechanical
admission tests:

1. at least two independent closed probes support it;
2. **it entails at least one graph element no existing probe covers.**

A generalisation that predicts nothing beyond the evidence that produced it is a
summary. The second test is the real one, and it is cheap: add the node, re-run
`deduce`, and check the uncovered set grew.

Theory nodes also need retirement (`retire`), or the layer becomes a junk
drawer. Nodes carry `provenance` — `deduced`, `induced`, or `abduced` — because
a node induced from five independent probes and a node posited to explain one
failure are not the same kind of object.

## 5. Abduction — anomaly to a new or modified parent

`claim_graph.py abduce --between X,Y --given Z`

The trigger is mechanical: an observed dependency the graph d-separates. The
repair set is finite and enumerable:

1. add a direct edge (either orientation, if acyclic);
2. add a latent common cause;
3. retract an absence assumption;
4. reverse an existing edge;
5. (for the reverse anomaly) delete an edge.

The CLI keeps only repairs that actually restore compatibility, then prices each
one by what it predicts **beyond the anomaly it was invented to absorb**. A
repair that exposes nothing new is marked `ACCOMMODATION ONLY`.

Pricing is the one place that does not restrict itself to the local Markov
basis. A probe costs a week, so §3 enumerates only the basis; a missed
statement here costs a claim instead, because it marks a falsifiable repair
accommodation-only and forecloses `supported`. Since the graph fits on one
screen (§8), every conditioning set is checked up to
`MAX_EXHAUSTIVE_PRICING_VARS` observed variables, above which pricing falls
back to parent sets and may under-report.

Abduction is therefore not brainstorming; it is choosing among a handful of
structural moves. This is what stops a failed run from turning into a new method
family.

### The debt rule

If every failed prediction can be absorbed by adding a parent node, the theory
is unfalsifiable. Three constraints:

- repairs are hypotheses: they go to `add-idea`, and reach the graph only at a
  sprint boundary;
- each applied amendment owes a new testable implication, and the line cannot
  close as `supported` until a probe discharges it;
- an accommodation-only repair can never discharge its debt, so applying one
  forecloses `supported` and forces the claim-lowering ladder.

`max_amendments` (default 2) caps repairs per line. This is the theory-layer
analogue of the two-strike inconclusive rule.

## 6. What the map buys

The resolution map states, before any result exists, which combinations of probe
outcomes lead to which claim-level conclusion:

```json
{"when": {"P1": "positive", "P2": "negative"},
 "then": "narrow", "rung": "causal->predictive",
 "depends_on_assumption": "L->R"}
```

`rung` is a rung of `docs/protocol.md` §5. Five of its six steps are graph
operations: universal→restricted adds a conditioning node; causal→predictive
demotes `X->Y` to association; algorithmic→mechanism moves the assertion to a
mediator; positive→boundary finds the moderator that switches the edge off;
framework→component keeps one edge. Narrowing a claim stops being a matter of
writing a more careful sentence and becomes a determinate operation.

Three consequences follow:

- **The sprint decision is computed, not chosen.** `close-sprint` checks your
  decision against the frozen map and refuses a silent reinterpretation.
- **WIP=1 becomes derived rather than imposed.** Only probes on the ready
  frontier — upstream guards satisfied — may run. This is a sharper rule than
  "one open card," and it is what stops a result being recorded when it cannot
  be interpreted.
- **Debugging stops costing claims.** An `inconclusive` with defect class
  `implementation` or `measurement` sets the probe to `unresolved`, which does
  not advance the map. Only defect class `hypothesis` counts toward narrowing.

## 7. Where the LLM sits

Four positions on the loop, with different safety properties:

| Position | Allowed | Why |
|---|---|---|
| Generate candidates | yes | failure mode is omission, not a wrong conclusion |
| Rank / filter candidates | yes, recorded | see below |
| Judge a single probe outcome | **no** | the metric is pre-registered; comparison is mechanical |
| Compose outcomes into a claim decision | **no** | that is exactly what the resolution map is for |

Most candidate sets are not exponential once the basis is computed: the local
Markov basis is linear in variables, abductive repairs are O(n²) filtered by a
compatibility check to single digits, and resolution branches are pruned by
guards. Compute the basis mechanically, rank the basis — do not sample the
closure.

Ranking is recorded, not performed inline. `deduce` and `abduce` write
`.research/candidates.json` with a `candidate_set_hash`; a ranking agent returns
a selection record; `select` verifies the hash matches, every candidate is either
selected or rejected, and every rejection carries a reason. A silent filter is
not a decision — "why did we never test X" has to be answerable six months later.

## 8. Limits

- **Not every claim is graph-shaped.** `graph_type` may be `causal`,
  `comparative`, or `theoretical`; only `causal` triggers back-door checking of
  adjustment sets.
- **`assumed_absent` entries are assumptions, not facts.** An unobserved
  confounder or a faithfulness violation can record as `falsified` something that
  was `inconclusive`. Resolution rules therefore carry
  `depends_on_assumption`, so overturning one assumption voids only the branches
  that rest on it.
- **The graph is a place to hide.** The only effective constraint is hard:
  **theory layer plus observation layer must fit on one screen.** If it does
  not, the claim is too large — a mechanically checkable version of the claim
  quality test in `docs/protocol.md` §2.
- **Two variables cannot be repaired informatively.** In a two-variable graph
  every abductive repair is accommodation-only. This is a property of the world,
  not of the tool.

## 9. What was deliberately not built

**Hierarchical experiment ids (`EXP-XXX-YYY`).** Once a graph exists, encoding a
parent in the identifier is redundant with the edges and actively harmful: an id
implies a tree, and a tree cannot represent a probe with two predecessors.
Dependencies live in `guards_in`. Ids stay flat and bind to nodes via `--node`.

**A fifth decision value.** `supported | falsified | inconclusive | terminated`
stays closed. The whole point of four values is to remove the hedging exit; the
defect class carries the nuance that tempted a fifth.
