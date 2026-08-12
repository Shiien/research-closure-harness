#!/usr/bin/env python3
"""Research Closure Harness CLI.

No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import claim_graph as cg
except ImportError:  # the graph layer is optional
    _tools_dir = str(Path(__file__).resolve().parent)
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    try:
        import claim_graph as cg
    except ImportError:
        cg = None


def discover_root() -> Path:
    """Find the active research repository.

    Priority:
    1. RESEARCH_CLOSURE_ROOT
    2. Current directory or one of its parents containing .research/state.json
    3. Current directory, which allows `init` to bootstrap a new repository
    """
    explicit = os.environ.get("RESEARCH_CLOSURE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".research" / "state.json").exists():
            return candidate
    return cwd


ROOT = discover_root()
STATE_PATH = ROOT / ".research" / "state.json"
LOG_DIR = ROOT / ".research" / "logs"
GRAPH_PATH = ROOT / ".research" / "claim_graph.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().astimezone().date().isoformat()


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise SystemExit("State not found. Run: python tools/research_closure.py init")
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid state file: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def append_history(state: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    state.setdefault("history", []).append(
        {"at": now_iso(), "event": event, "payload": payload}
    )


def write_log(filename: str, content: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / filename
    path.write_text(content.strip() + "\n")
    return path


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def cmd_init(_: argparse.Namespace) -> int:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        print(f"State already exists: {rel(STATE_PATH)}")
        return 0
    state = {
        "version": 1,
        "mode": "graduation",
        "project": {"question": "", "long_term_agenda": "", "minimum_completion": ""},
        "sprint": None,
        "day": None,
        "active_experiment": None,
        "counters": {"experiment": 0, "idea": 0},
        "history": [],
        "limits": {"active_sprints": 1, "active_experiments": 1},
    }
    save_state(state)
    print(f"Initialized {rel(STATE_PATH)}")
    return 0


def cmd_set_project(args: argparse.Namespace) -> int:
    state = load_state()
    state["project"] = {
        "question": args.question,
        "long_term_agenda": args.agenda,
        "minimum_completion": args.minimum,
    }
    append_history(state, "project_set", state["project"])
    save_state(state)
    print("Project charter recorded.")
    return 0


def cmd_start_sprint(args: argparse.Namespace) -> int:
    state = load_state()
    if state.get("sprint"):
        raise SystemExit(
            "BLOCKED: an active sprint already exists. Close or explicitly revise it first."
        )
    start = datetime.now().astimezone()
    end = start + timedelta(days=args.days)
    sprint = {
        "claim": args.claim,
        "artifact": args.artifact,
        "started_at": start.isoformat(timespec="seconds"),
        "ends_at": end.isoformat(timespec="seconds"),
        "status": "active",
    }
    state["sprint"] = sprint
    append_history(state, "sprint_started", sprint)
    if cg and GRAPH_PATH.exists():
        graph = cg.load_graph(GRAPH_PATH)
        blocks, _ = cg.validate(graph)
        if blocks:
            raise SystemExit(
                "BLOCKED: claim graph fails validation; fix it before freezing a sprint:\n  "
                + "\n  ".join(blocks)
            )
        sprint["claim_graph"] = {
            "path": rel(GRAPH_PATH),
            "design_hash": cg.design_hash(graph),
            "frozen_at": now_iso(),
        }
        state["version"] = 2
    save_state(state)
    path = write_log(
        f"{today_str()}_sprint.md",
        f"""# Sprint

## Frozen claim

{args.claim}

## Required artifact

{args.artifact}

## Start

{start.isoformat(timespec="seconds")}

## End

{end.isoformat(timespec="seconds")}

## Out of scope

To be filled before implementation.
""",
    )
    print(f"Sprint started. Log: {rel(path)}")
    return 0


def cmd_close_sprint(args: argparse.Namespace) -> int:
    state = load_state()
    sprint = state.get("sprint")
    if not sprint:
        raise SystemExit("No active sprint.")
    if state.get("active_experiment"):
        raise SystemExit("BLOCKED: close the active experiment before closing the sprint.")
    if cg and GRAPH_PATH.exists():
        graph = cg.load_graph(GRAPH_PATH)
        proposal = cg.propose_decision(graph)
        if proposal["status"] == "determined":
            expected = {"supported": "advance", "falsified": "terminate",
                        "narrow": "narrow", "terminated": "terminate"}.get(proposal["then"])
            if expected and args.decision != expected:
                raise SystemExit(
                    f"BLOCKED: the resolution map, frozen before results, determines "
                    f"'{proposal['then']}' -> close as '{expected}', not '{args.decision}'. "
                    f"To override, amend the map explicitly; do not reinterpret it silently."
                )
            if proposal.get("blocked_by_debt"):
                raise SystemExit(
                    "BLOCKED: unpaid amendment debt. The graph was repaired to fit an "
                    "anomaly and the repair has not been tested. Close as 'narrow'."
                )
        else:
            print(f"NOTE: resolution map not yet determined; probes still ready: "
                  f"{cg.frontier(graph) or 'none'}")
    record = {
        **sprint,
        "closed_at": now_iso(),
        "decision": args.decision,
        "evidence": args.evidence,
        "conclusion": args.conclusion,
    }
    append_history(state, "sprint_closed", record)
    state["sprint"] = None
    save_state(state)
    path = write_log(
        f"{today_str()}_sprint_decision.md",
        f"""# Sprint Decision

## Claim

{sprint['claim']}

## Decision

{args.decision}

## Evidence

{args.evidence}

## Conclusion

{args.conclusion}
""",
    )
    print(f"Sprint closed. Log: {rel(path)}")
    return 0


def cmd_start_day(args: argparse.Namespace) -> int:
    state = load_state()
    if not state.get("sprint"):
        raise SystemExit("BLOCKED: start a sprint before starting a research day.")
    if state.get("day"):
        raise SystemExit("BLOCKED: an active day already exists. Close it first.")
    day = {
        "date": today_str(),
        "deliverable": args.deliverable,
        "started_at": now_iso(),
    }
    state["day"] = day
    append_history(state, "day_started", day)
    save_state(state)
    path = write_log(
        f"{today_str()}_daily.md",
        f"""# Daily Closure

## Frozen claim

{state['sprint']['claim']}

## Today's single deliverable

{args.deliverable}

## Out of scope

Anything not needed to produce the deliverable.
""",
    )
    print(f"Day started. Log: {rel(path)}")
    return 0


def validate_artifacts(paths_csv: str) -> tuple[list[str], list[str]]:
    raw = [p.strip() for p in paths_csv.split(",") if p.strip()]
    existing, missing = [], []
    for p in raw:
        path = Path(p)
        if not path.is_absolute():
            path = ROOT / path
        (existing if path.exists() else missing).append(p)
    return existing, missing


def cmd_close_day(args: argparse.Namespace) -> int:
    state = load_state()
    day = state.get("day")
    if not day:
        raise SystemExit("No active day.")
    existing, missing = validate_artifacts(args.artifact)
    if missing and not args.allow_missing:
        raise SystemExit(
            "BLOCKED: artifact path(s) do not exist: "
            + ", ".join(missing)
            + "\nUse --allow-missing only for a written negative-result decision."
        )
    record = {
        **day,
        "closed_at": now_iso(),
        "artifact": args.artifact,
        "artifact_existing": existing,
        "artifact_missing": missing,
        "decision": args.decision,
    }
    append_history(state, "day_closed", record)
    state["day"] = None
    save_state(state)
    path = LOG_DIR / f"{day['date']}_daily.md"
    with path.open("a") as f:
        f.write(
            f"""
## Artifact

{args.artifact}

## Evidence-backed decision

{args.decision}

## Closed at

{record['closed_at']}
"""
        )
    print(f"Day closed. Log: {rel(path)}")
    return 0


def cmd_new_experiment(args: argparse.Namespace) -> int:
    state = load_state()
    if not state.get("sprint"):
        raise SystemExit("BLOCKED: no active sprint.")
    if state.get("active_experiment"):
        active = state["active_experiment"]["id"]
        raise SystemExit(
            f"BLOCKED: {active} is still active. Close it before opening a new primary experiment."
        )
    state["counters"]["experiment"] += 1
    exp_id = f"EXP-{state['counters']['experiment']:03d}"
    exp = {
        "id": exp_id,
        "question": args.question,
        "hypothesis": args.hypothesis,
        "intervention": args.intervention,
        "measurement": args.measurement,
        "kill_criterion": args.kill,
        "expected_artifact": args.artifact,
        "time_budget_hours": args.hours,
        "started_at": now_iso(),
        "status": "active",
    }
    if cg and GRAPH_PATH.exists():
        graph = cg.load_graph(GRAPH_PATH)
        if not args.node:
            raise SystemExit(
                "BLOCKED: a claim graph exists, so every experiment must state which "
                f"probe it runs (--node). Ready probes: {cg.frontier(graph) or 'none'}"
            )
        if args.node not in graph.get("probes", {}):
            raise SystemExit(f"BLOCKED: {args.node} is not a probe in the claim graph.")
        ready = cg.frontier(graph)
        if args.node not in ready:
            raise SystemExit(
                f"BLOCKED: {args.node} is not on the ready frontier (ready: {ready or 'none'}). "
                "Its upstream guards are unmet, so its result would not be interpretable."
            )
        probe = graph["probes"][args.node]
        if graph.get("graph_type") == "causal" and probe.get("tests", {}).get("kind") == "edge":
            declared = [c.strip() for c in args.controls.split(",") if c.strip()]
            ok, why = cg.verify_adjustment(
                cg.edge_list(graph), probe["tests"]["from"], probe["tests"]["to"], declared
            )
            if not ok:
                rec, _ = cg.recommend_adjustment(
                    graph, probe["tests"]["from"], probe["tests"]["to"]
                )
                raise SystemExit(
                    f"BLOCKED: --controls {declared} is not a valid adjustment set: {why}. "
                    f"Back-door criterion suggests {rec}."
                )
        exp["claim_graph_node"] = args.node
        exp["controls"] = args.controls
        exp["expected_figure"] = args.figure

    state["active_experiment"] = exp
    append_history(state, "experiment_started", exp)
    save_state(state)
    path = write_log(
        f"{exp_id}.md",
        f"""# Experiment {exp_id}

## Frozen sprint claim

{state['sprint']['claim']}

## Research question

{args.question}

## Hypothesis

{args.hypothesis}

## Intervention

{args.intervention}

## Measurement

{args.measurement}

## Expected artifact

{args.artifact}

## Kill criterion

{args.kill}

## Time budget

{args.hours} hours

## Result

Pending.

## Decision

Pending.
""",
    )
    print(f"Experiment opened: {exp_id}. Card: {rel(path)}")
    return 0


def cmd_close_experiment(args: argparse.Namespace) -> int:
    state = load_state()
    exp = state.get("active_experiment")
    if not exp:
        raise SystemExit("No active experiment.")
    if args.id != exp["id"]:
        raise SystemExit(f"Active experiment is {exp['id']}, not {args.id}.")
    record = {
        **exp,
        "closed_at": now_iso(),
        "decision": args.decision,
        "evidence": args.evidence,
        "conclusion": args.conclusion,
        "status": "closed",
    }
    if args.decision == "inconclusive" and not args.defect:
        raise SystemExit(
            "BLOCKED: an inconclusive result must name its defect class. "
            "implementation/measurement/design defects leave the hypothesis untouched and "
            "must not count toward claim narrowing; only 'hypothesis' does."
        )
    if (
        args.decision == "inconclusive"
        and args.defect != "hypothesis"
        and args.outcome
        and args.outcome != "unresolved"
    ):
        raise SystemExit(
            f"BLOCKED: a {args.defect} defect leaves the hypothesis untested, so the probe "
            f"outcome must be 'unresolved', not '{args.outcome}'. Only defect class "
            "'hypothesis' may advance the resolution map."
        )
    record["defect"] = args.defect

    node = exp.get("claim_graph_node")
    if cg and node and GRAPH_PATH.exists():
        outcome = args.outcome or {
            "supported": "positive", "falsified": "negative",
            "inconclusive": "unresolved", "terminated": "unresolved",
        }[args.decision]
        graph = cg.load_graph(GRAPH_PATH)
        graph["probes"][node]["outcome"] = outcome
        graph["probes"][node]["experiment_id"] = exp["id"]
        graph["probes"][node]["defect"] = args.defect
        cg.save_graph(graph, GRAPH_PATH)
        proposal = cg.propose_decision(graph)
        print(f"Claim-graph node {node} set to {outcome}.")
        if proposal["status"] == "determined":
            print(f"Resolution map determines: {proposal['then']}"
                  + (f" (rung: {proposal['rung']})" if proposal.get("rung") else ""))
        else:
            print(f"Line still open. Ready next: {proposal['frontier'] or 'none'}")

    append_history(state, "experiment_closed", record)
    state["active_experiment"] = None
    save_state(state)
    path = LOG_DIR / f"{args.id}.md"
    with path.open("a") as f:
        f.write(
            f"""
## Evidence

{args.evidence}

## Decision

{args.decision}

## Evidence-backed conclusion

{args.conclusion}

## Closed at

{record['closed_at']}
"""
        )
    print(f"Experiment closed: {args.id}. Decision: {args.decision}")
    return 0


def cmd_add_idea(args: argparse.Namespace) -> int:
    state = load_state()
    state["counters"]["idea"] += 1
    idea_id = f"IDEA-{state['counters']['idea']:03d}"
    record = {
        "id": idea_id,
        "idea": args.idea,
        "reason_not_now": args.reason,
        "revisit": args.revisit or "",
        "created_at": now_iso(),
    }
    append_history(state, "idea_backlogged", record)
    save_state(state)
    path = write_log(
        f"{idea_id}.md",
        f"""# {idea_id}

## Idea

{args.idea}

## Why not now

{args.reason}

## Revisit condition

{args.revisit or 'After the current sprint is closed.'}
""",
    )
    print(f"Idea backlogged: {idea_id}. Log: {rel(path)}")
    return 0


def guard_messages(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    warnings: list[str] = []
    project = state.get("project", {})
    if not project.get("question"):
        warnings.append("Project question is empty. Use set-project.")
    sprint = state.get("sprint")
    if not sprint:
        blocks.append("No active sprint claim.")
    else:
        try:
            ends_at = datetime.fromisoformat(sprint["ends_at"])
            if datetime.now().astimezone() > ends_at:
                warnings.append("Sprint deadline has passed; close or explicitly revise it.")
        except Exception:
            warnings.append("Sprint end date could not be parsed.")
    day = state.get("day")
    if day and day.get("date") != today_str():
        warnings.append("An old daily session remains open.")
    exp = state.get("active_experiment")
    if exp and not exp.get("kill_criterion"):
        blocks.append("Active experiment has no kill criterion.")
    if exp and not exp.get("expected_artifact"):
        blocks.append("Active experiment has no expected artifact.")

    if cg and GRAPH_PATH.exists():
        graph = cg.load_graph(GRAPH_PATH)
        gblocks, gwarn = cg.validate(graph)
        blocks.extend(gblocks)
        warnings.extend(gwarn)

        frozen = (sprint or {}).get("claim_graph", {}).get("design_hash")
        if frozen and frozen != cg.design_hash(graph):
            blocks.append(
                "claim graph design has drifted since the sprint was frozen. The "
                "pre-registration no longer matches what will be reported. Record an "
                "amendment or close the sprint."
            )
        if exp and exp.get("claim_graph_node"):
            if exp["claim_graph_node"] not in cg.frontier(graph) + list(cg.outcomes_map(graph)):
                blocks.append(
                    f"active experiment runs {exp['claim_graph_node']}, which is not on "
                    f"the ready frontier"
                )
        debts = cg.unpaid_debts(graph)
        if debts:
            warnings.append(
                f"{len(debts)} unpaid amendment debt(s); this line cannot close as supported"
            )
        proposal = cg.propose_decision(graph)
        if proposal["status"] == "determined":
            warnings.append(
                f"resolution map is already determined ({proposal['then']}); "
                f"further probes on this line are out of scope"
            )
    return blocks, warnings


def cmd_guard(_: argparse.Namespace) -> int:
    state = load_state()
    blocks, warnings = guard_messages(state)
    print("RESEARCH CLOSURE GUARD")
    if state.get("sprint"):
        print(f"Frozen claim: {state['sprint']['claim']}")
    if state.get("day"):
        print(f"Today's deliverable: {state['day']['deliverable']}")
    if state.get("active_experiment"):
        print(f"Active experiment: {state['active_experiment']['id']}")
    for msg in warnings:
        print(f"WARNING: {msg}")
    for msg in blocks:
        print(f"BLOCK: {msg}")
    if blocks:
        return 2
    print("PASS: work may proceed within the frozen claim.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    state = load_state()
    print(json.dumps({
        "mode": state.get("mode"),
        "project": state.get("project"),
        "sprint": state.get("sprint"),
        "day": state.get("day"),
        "active_experiment": state.get("active_experiment"),
        "backlogged_ideas": state.get("counters", {}).get("idea", 0),
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research Closure Harness")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("set-project")
    sp.add_argument("--question", required=True)
    sp.add_argument("--agenda", required=True)
    sp.add_argument("--minimum", required=True)
    sp.set_defaults(func=cmd_set_project)

    sp = sub.add_parser("start-sprint")
    sp.add_argument("--claim", required=True)
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--artifact", required=True)
    sp.set_defaults(func=cmd_start_sprint)

    sp = sub.add_parser("close-sprint")
    sp.add_argument("--decision", choices=["continue", "narrow", "terminate", "advance"], required=True)
    sp.add_argument("--evidence", required=True)
    sp.add_argument("--conclusion", required=True)
    sp.set_defaults(func=cmd_close_sprint)

    sp = sub.add_parser("start-day")
    sp.add_argument("--deliverable", required=True)
    sp.set_defaults(func=cmd_start_day)

    sp = sub.add_parser("close-day")
    sp.add_argument("--artifact", required=True)
    sp.add_argument("--decision", required=True)
    sp.add_argument("--allow-missing", action="store_true")
    sp.set_defaults(func=cmd_close_day)

    sp = sub.add_parser("new-experiment")
    sp.add_argument("--question", required=True)
    sp.add_argument("--hypothesis", required=True)
    sp.add_argument("--intervention", required=True)
    sp.add_argument("--measurement", required=True)
    sp.add_argument("--kill", required=True)
    sp.add_argument("--artifact", required=True)
    sp.add_argument("--hours", type=float, required=True)
    sp.add_argument("--node", help="claim-graph probe this card runs, e.g. P2")
    sp.add_argument("--controls", default="", help="comma-separated adjustment set")
    sp.add_argument("--figure", default="", help="expected figure")
    sp.set_defaults(func=cmd_new_experiment)

    sp = sub.add_parser("close-experiment")
    sp.add_argument("--id", required=True)
    sp.add_argument("--decision", choices=["supported", "falsified", "inconclusive", "terminated"], required=True)
    sp.add_argument("--evidence", required=True)
    sp.add_argument("--conclusion", required=True)
    sp.add_argument("--defect", choices=["implementation", "measurement", "design", "hypothesis"],
                    help="required when --decision inconclusive")
    sp.add_argument("--outcome", choices=["positive", "negative", "unresolved"],
                    help="probe outcome, when the card is bound to a claim-graph node")
    sp.set_defaults(func=cmd_close_experiment)

    sp = sub.add_parser("add-idea")
    sp.add_argument("--idea", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--revisit")
    sp.set_defaults(func=cmd_add_idea)

    sp = sub.add_parser("guard")
    sp.set_defaults(func=cmd_guard)

    sp = sub.add_parser("status")
    sp.set_defaults(func=cmd_status)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
