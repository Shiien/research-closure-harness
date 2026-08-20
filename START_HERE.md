# Global Setup

## Option A — install from the extracted directory

```bash
chmod +x install_research_closure_global.sh
./install_research_closure_global.sh
```

## Option B — install from the zip

Keep the installer and zip in the same directory:

```bash
chmod +x install_research_closure_global.sh
./install_research_closure_global.sh \
  --source ./research-closure-harness.zip
```

Use `--with-claude-hook` only when you also want the optional mechanical gate.

## Initialize the current research repository

```bash
cd /path/to/research-repo
research-closure-init
```

`research-closure-init` now also copies `tools/auto_research.py`, the A/B
skills for Codex, Claude Code and DeepSeek harness, `DEEPSEEK.md`, and the
bootstrap `.research/auto_research.json`.

## Auto-load A/B auto-research

At the start of any session in this repository:

```bash
python tools/auto_research.py ab-status
python tools/auto_research.py ab-next
```

- Codex auto-loads `AGENTS.md`.
- Claude Code auto-loads `CLAUDE.md`.
- DeepSeek harness auto-loads `DEEPSEEK.md` (and falls back to `AGENTS.md`
  when it follows that convention).

A (fast) proposes. B (slow) criticises, verifies, applies, and revalidates.

# 30-Minute Setup

## 1. Copy this harness into the research repository

Keep `AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `HANDOFF_SKILL.md`, `tools/`, `templates/`, and `.research/` at repository root.

## 2. Record the project

```bash
python tools/research_closure.py set-project \
  --agenda "Continual option learning for changing reinforcement-learning tasks." \
  --question "When can a learned representation support stable and reusable option discovery?" \
  --minimum "A self-contained technical chapter with one formal separation and one validated diagnostic."
```

## 3. Author the claim graph (the engine)

```bash
python tools/claim_graph.py init --claim "Under controlled excitation, a conditioning quantity predicts affine representation recovery more reliably than predictive loss."
python tools/claim_graph.py add-variable --id E --name "behaviour-policy excitation" --role intervention
python tools/claim_graph.py add-variable --id K --name "log condition number" --role candidate_predictor
python tools/claim_graph.py add-variable --id L --name "predictive loss" --role rival_predictor
python tools/claim_graph.py add-variable --id R --name "affine recovery error" --role outcome
python tools/claim_graph.py add-edge --from E --to K
python tools/claim_graph.py add-edge --from E --to L
python tools/claim_graph.py add-edge --from K --to R
python tools/claim_graph.py add-absent --from L --to R --justification "Predictive loss is a downstream symptom of excitation, not an independent cause of recovery error."
python tools/claim_graph.py add-probe --id P1 --tests '{"kind":"edge","from":"K","to":"R"}' --metric "spearman_rho(log_cond, recovery_error)" --prereg "rho > 0.5 over 30 instances x 5 seeds" --controls E
python tools/claim_graph.py add-probe --id P2 --tests '{"kind":"independence","x":"L","y":"R","given":["E"]}' --metric "partial_spearman(L, R | E)" --prereg "|partial rho| < 0.2" --controls E --guards "P1==positive"
python tools/claim_graph.py add-probe --id P3 --tests '{"kind":"comparison","stronger":"K","weaker":"L","on":"R"}' --metric "held-out predictive R2, K vs L" --prereg "K beats L on >= 4 of 5 seeds" --controls E --guards "P1==positive,P2==positive"
python tools/claim_graph.py add-resolution --when '{"P1":"negative"}' --then falsified --skip P2,P3 --note "If conditioning does not track recovery at all, the comparison is moot."
python tools/claim_graph.py add-resolution --when '{"P1":"positive","P2":"positive","P3":"positive"}' --then supported
python tools/claim_graph.py validate
```

Each `add-*` command validates before saving, so an invalid design never sticks.

## 4. Freeze the sprint

```bash
python tools/research_closure.py start-sprint \
  --claim "Under controlled excitation, a conditioning quantity predicts affine representation recovery more reliably than predictive loss." \
  --days 14 \
  --artifact "A four-page note, results.csv, and one three-panel main figure."
```

## 5. Ask Codex, Claude Code, or DeepSeek harness

```text
Read the auto-loaded rules (AGENTS.md / CLAUDE.md / DEEPSEEK.md).
Run auto-research ab-status and ab-next first, then decide A or B role.
For PhD research work, also run the closure guard, the probe frontier, and
the next-event command.
Help me complete the next event the harness expects.
Do not introduce a new research direction or method family.
At the end, identify the artifact, evidence, decision, and next smallest action.
```

## 6. Close the experiment even when the result is negative

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision supported \
  --evidence "results/pilot.csv,figures/pilot.pdf" \
  --conclusion "The estimator tracks recovery within the tested excitation range."
```

```bash
python tools/research_closure.py close-experiment \
  --id EXP-001 \
  --decision inconclusive \
  --defect measurement \
  --evidence "results/pilot.csv" \
  --conclusion "The estimator is too noisy at current sample size; keep the claim and repair only the estimator."
```

## 7. Track progress visually

```bash
python tools/research_closure.py dashboard
```

Opens the interactive DAG view in the browser: theory → variables/edges →
probes, colour-coded outcomes, the ready frontier, the resolution map and the
event log. Re-run it any time to refresh.

The first goal is not to feel organized. The first goal is to complete one full
claim–test–decision cycle. The harness is event-driven: `next` always tells you
what it expects next, and `events` shows the log.
