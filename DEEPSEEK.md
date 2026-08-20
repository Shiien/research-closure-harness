# Self-Evolved Research Harness Rules for DeepSeek Harness

This repository runs two cooperating loops:

1. **Research Closure** for ordinary PhD research work.
2. **A/B Self-Evolved Research Harness** for improving the harness itself.

This file is auto-loaded for DeepSeek harness sessions. If your harness reads
`AGENTS.md` instead, the same A/B rules are already present there.

## Session auto-load (always)

At the start of every session, before choosing a task:

1. Run `python tools/auto_research.py ab-status`.
2. Run `python tools/auto_research.py ab-next`.
3. Read `.research/auto_research.json`.
4. Declare your role:
   - `A (fast)`: use the `auto-research-fast` skill, exposed as
     `/auto-research-fast` or `$auto-research-fast` depending on the harness.
   - `B (slow)`: use the `auto-research-slow` skill, exposed as
     `/auto-research-slow` or `$auto-research-slow` depending on the harness.
5. If the session is ordinary PhD research work, also run the closure commands
   in the next section.

The A/B contract: A proposes, B criticises, B hard-verifies, B applies, then B
feeds the result back to A. Soft judgment never replaces hard verification.
`M0` in `.research/auto_research.json` is the immutable L0 meta-goal.

## Auto-research A/B workflow

```bash
# A (fast): explore and propose only
python tools/auto_research.py add-node \
  --id N-<topic> --type assumption \
  --statement "<candidate statement>" --layer L1 --status draft

python tools/auto_research.py propose --track A \
  --title "<small self-modification>" \
  --statement "<why this is a local improvement>" \
  --targets N-<topic> \
  --patch-file patch.json \
  --verification "<exit-0 command>"

# B (slow): critique, verify, apply, revalidate
python tools/auto_research.py critique --track B \
  --proposal P-001 --verdict pass \
  --critic "<critic name>" --reason "<specific reason>"

python tools/auto_research.py verify --track B --proposal P-001
python tools/auto_research.py apply --track B --proposal P-001

python tools/auto_research.py revalidate --track B \
  --node N-<topic> --level hard --command "<exit-0 command>"

# Capability self-test after self-modification sessions
python tools/auto_research.py self-test
python tools/auto_research.py validate
```

Patch vocabulary: `add_node`, `remove_node`, `set_node_status`,
`set_node_statement`, `add_edge`, `remove_edge`, `set_layer_policy`,
`set_trust_decay`, `set_affected_trust_decay`, `set_revalidation_threshold`,
`set_self_test_command`.

Never edit `.research/auto_research.json` by hand to bypass the pipeline.
Applying a modification creates a `modification` node, decays trust on existing
non-L0 nodes, and deprecates the dependency closure of the changed nodes.

## Research closure workflow (ordinary PhD work)

At the start of research-closure work:

1. Read `.research/state.json`.
2. Read the active sprint and experiment logs in `.research/logs/`.
3. Run `python tools/research_closure.py guard`.
4. Run `python tools/claim_graph.py frontier`.
5. Run `python tools/research_closure.py next`.

Hard constraints:

- One active project question.
- One frozen sprint claim.
- One primary active experiment.
- No experiment without hypothesis, measurement, kill criterion, and artifact.
- No new variant before the previous experiment receives a written decision.
- New ideas go to backlog by default.

Use the `research-closure` skill for planning, experiments, result analysis and
scope changes. Use the `research-handoff` skill when interpreting another
agent's output or preparing a task for an agent that does not share this
conversation.

## Session closing

For self-modification work, report:

- proposal id and verdict;
- verification command and exit code;
- modification node and affected/deprecated nodes;
- feedback for A;
- next smallest A or B action.

For research-closure work, close the experiment with
`python tools/research_closure.py close-experiment ...` and report artifact,
evidence, decision, and next smallest action.
