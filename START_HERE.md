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

## 3. Start only the first sprint

```bash
python tools/research_closure.py start-sprint \
  --claim "Under controlled excitation, a conditioning quantity predicts affine representation recovery more reliably than predictive loss." \
  --days 14 \
  --artifact "A four-page note, results.csv, and one three-panel main figure."
```

## 4. Create today's deliverable

```bash
python tools/research_closure.py start-day \
  --deliverable "Make the current recovery experiment reproducible and produce the first scatter plot."
```

## 5. Ask Codex or Claude Code

```text
Read AGENTS.md/CLAUDE.md and the current .research state.
Run the closure guard.
Help me complete today's single deliverable.
Do not introduce a new research direction or method family.
At the end, identify the artifact, evidence, decision, and next smallest action.
```

## 6. End the day even when the result is negative

```bash
python tools/research_closure.py close-day \
  --artifact "results/pilot.csv,figures/pilot.pdf" \
  --decision "The estimator is too noisy at current sample size; keep the claim and repair only the estimator."
```

The first goal is not to feel organized. The first goal is to complete one full claim–test–decision cycle.
