#!/usr/bin/env python3
"""Self-Evolved Research Harness: deterministic substrate for auto research.

The README blueprint says real auto research studies and modifies itself instead
of scoring itself on external benchmarks. This module mechanises the part that
must not depend on an LLM:

* self-graph nodes: assumption, inference, verify, modification
* edges: dependency (epistemic) and modification (causal operation)
* stability labels: draft -> validated -> deprecated
* trust decay after every self-modification
* invalidation closure along dependency edges
* proposal pipeline: proposed -> challenged -> passed_critic -> verified -> applied
* strict patch vocabulary for self-modification
* snapshots, rollback, and a capability self-test

An LLM (Codex / Claude Code / researcher) is still the proposer and critic.
Soft judgment never replaces hard verification: a node returns to `validated`
only after a structural syntax pass or an exit-0 verification command.

No third-party dependencies.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
NODE_TYPES = ("assumption", "inference", "verify", "modification")
NODE_STATUSES = ("draft", "validated", "deprecated")
LAYERS = ("L0", "L1", "L2", "L3", "L4")
EDGE_KINDS = ("dependency", "modification")
PROPOSAL_STATUSES = (
    "proposed",
    "challenged",
    "passed_critic",
    "rejected",
    "verified",
    "failed_verification",
    "applied",
)
CRITIC_VERDICTS = ("pass", "challenge", "reject")
REVALIDATION_LEVELS = ("syntax", "light", "hard")
TRACKS = ("A", "B")
TRACK_ROLES = {
    "A": "fast auto-research: explore, draft, propose candidates, cheap pre-checks",
    "B": "slow auto-research: critic review, hard verification, consolidation, apply",
}

META_GOAL_ID = "M0"
META_GOAL_STATEMENT = "Improve this system's own auto-research capability."


def discover_root() -> Path:
    explicit = os.environ.get("RESEARCH_CLOSURE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".research" / "state.json").exists() or (
            candidate / ".research" / "auto_research.json"
        ).exists():
            return candidate
    return cwd


ROOT = discover_root()
STATE_PATH = ROOT / ".research" / "auto_research.json"
SNAP_DIR = ROOT / ".research" / "auto_snapshots"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def skeleton(goal: str = META_GOAL_STATEMENT) -> dict[str, Any]:
    now = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "meta_goal": {"id": META_GOAL_ID, "statement": goal, "immutable": True},
        "layers": {
            "L1": {
                "scope": "operational metrics",
                "policy": "Operational metrics are research products. They may "
                          "change, but only when checked indirectly against L0.",
            },
            "L2": {
                "scope": "question generation",
                "policy": "How problems are defined, decomposed, and prioritised "
                          "is itself research content.",
            },
            "L3": {
                "scope": "reasoning and verification strategy",
                "policy": "Reasoning modes and verification procedures are "
                          "modifiable research content.",
            },
            "L4": {
                "scope": "concrete research content",
                "policy": "Concrete nodes and experiments change frequently.",
            },
        },
        "trust_decay": 0.9,
        "affected_trust_decay": 0.5,
        "revalidation_threshold": 0.25,
        "self_test_command": "python3 -m unittest discover -s tests",
        "tracks": TRACK_ROLES,
        "nodes": {
            META_GOAL_ID: {
                "type": "assumption",
                "statement": goal,
                "layer": "L0",
                "status": "validated",
                "trust": 1.0,
                "immutable": True,
                "created_at": now,
                "updated_at": now,
            }
        },
        "edges": [],
        "proposals": {},
        "verifications": [],
        "last_self_test": None,
        "events": [],
        "counters": {"node": 1, "proposal": 0, "verification": 0},
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or STATE_PATH
    if not p.exists():
        raise SystemExit(f"No auto-research state at {p}. Run: auto_research.py init")
    try:
        state = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid auto-research state: {exc}") from exc
    for key, default in (
        ("events", []),
        ("verifications", []),
        ("proposals", {}),
        ("nodes", {}),
        ("edges", []),
        ("last_self_test", None),
        ("tracks", dict(TRACK_ROLES)),
    ):
        state.setdefault(key, default)
    return state


def snapshot_state(state: dict[str, Any], label: str = "") -> Path | None:
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(SNAP_DIR.glob("*.json"))
        if files:
            try:
                if json.loads(files[-1].read_text()) == state:
                    return files[-1]
            except Exception:
                pass
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        seq = len(files)
        safe = label.replace("/", "_") or "snapshot"
        while (SNAP_DIR / f"{stamp}_{seq:03d}_{safe}.json").exists():
            seq += 1
        path = SNAP_DIR / f"{stamp}_{seq:03d}_{safe}.json"
        path.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n")
        return path
    except Exception:
        return None


def save_state(state: dict[str, Any], snapshot_label: str = "") -> Path | None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n")
    return snapshot_state(state, snapshot_label)


def append_event(state: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    state.setdefault("events", []).append(
        {"at": now_iso(), "event": event, "payload": payload}
    )


def next_id(state: dict[str, Any], prefix: str, counter: str) -> str:
    n = int(state.get("counters", {}).get(counter, 0)) + 1
    state.setdefault("counters", {})[counter] = n
    return f"{prefix}-{n:03d}"


# ---------------------------------------------------------------------------
# graph helpers
# ---------------------------------------------------------------------------

def dependency_edges(state: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (e["from"], e["to"])
        for e in state.get("edges", [])
        if e.get("kind") == "dependency"
    ]


def find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    pairs = list(edges)
    colour: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = 1
        path.append(node)
        for src, dst in pairs:
            if src != node:
                continue
            if colour.get(dst, 0) == 1:
                start = path.index(dst)
                return path[start:] + [dst]
            if colour.get(dst, 0) == 0:
                found = visit(dst)
                if found:
                    return found
        path.pop()
        colour[node] = 2
        return None

    for node in sorted({n for e in pairs for n in e}):
        if colour.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return None


def descendants_dependency(state: dict[str, Any], origins: Iterable[str]) -> set[str]:
    children: dict[str, set[str]] = {}
    for src, dst in dependency_edges(state):
        children.setdefault(src, set()).add(dst)
    seen: set[str] = set()
    stack = [n for n in origins if n in state.get("nodes", {})]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(sorted(children.get(node, set())))
    return seen


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    warnings: list[str] = []
    if state.get("schema_version") != SCHEMA_VERSION:
        blocks.append(f"schema_version must be {SCHEMA_VERSION}")

    meta = state.get("meta_goal", {})
    if meta.get("id") != META_GOAL_ID:
        blocks.append(f"meta_goal.id must be {META_GOAL_ID}")
    if not meta.get("immutable", False):
        blocks.append("meta_goal must be immutable")

    nodes = state.get("nodes", {})
    if META_GOAL_ID not in nodes:
        blocks.append(f"{META_GOAL_ID} node (immutable L0 meta-goal) is missing")
    for nid, node in nodes.items():
        if node.get("type") not in NODE_TYPES:
            blocks.append(f"node {nid} has invalid type {node.get('type')!r}")
        if node.get("layer") not in LAYERS:
            blocks.append(f"node {nid} has invalid layer {node.get('layer')!r}")
        if node.get("status") not in NODE_STATUSES:
            blocks.append(f"node {nid} has invalid status {node.get('status')!r}")
        if node.get("layer") == "L0" and nid != META_GOAL_ID:
            blocks.append(f"node {nid} is L0 but only {META_GOAL_ID} may be L0")
        if nid == META_GOAL_ID and node.get("layer") != "L0":
            blocks.append(f"{META_GOAL_ID} must stay at layer L0")
        trust = node.get("trust")
        if (
            not isinstance(trust, (int, float))
            or isinstance(trust, bool)
            or not (0 <= trust <= 1)
        ):
            blocks.append(f"node {nid} trust must be a number in [0, 1]")
        if not isinstance(node.get("statement", ""), str) or not node["statement"].strip():
            blocks.append(f"node {nid} needs a non-empty statement")

    if META_GOAL_ID in nodes:
        m0 = nodes[META_GOAL_ID]
        if m0.get("status") != "validated" or m0.get("trust") != 1.0:
            blocks.append(f"{META_GOAL_ID} must remain validated with trust 1.0")

    for e in state.get("edges", []):
        if e.get("kind") not in EDGE_KINDS:
            blocks.append(f"edge {e.get('from')}->{e.get('to')} has invalid kind")
            continue
        for endpoint in ("from", "to"):
            if e.get(endpoint) not in nodes:
                blocks.append(
                    f"{e.get('kind')} edge {e.get('from')}->{e.get('to')} "
                    f"references undeclared node {e.get(endpoint)!r}"
                )
        if e.get("kind") == "modification":
            src = nodes.get(e.get("from"), {})
            if src.get("type") != "modification":
                blocks.append(
                    f"modification edge {e.get('from')}->{e.get('to')} "
                    "must start at a modification node"
                )
        if e.get("to") == META_GOAL_ID:
            blocks.append(f"edge {e.get('from')}->{META_GOAL_ID} targets the immutable L0 core")

    cycle = find_cycle(dependency_edges(state))
    if cycle:
        blocks.append(f"dependency graph contains a cycle: {' -> '.join(cycle)}")

    for name, value in (
        ("trust_decay", state.get("trust_decay", 0)),
        ("affected_trust_decay", state.get("affected_trust_decay", 0)),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 < value <= 1):
            blocks.append(f"{name} must be in (0, 1]")
    threshold = state.get("revalidation_threshold", -1)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not (0 <= threshold <= 1):
        blocks.append("revalidation_threshold must be in [0, 1]")
    if not isinstance(state.get("self_test_command", ""), str) or not state["self_test_command"].strip():
        warnings.append("self_test_command is empty; self-test will be skipped")

    for pid, prop in state.get("proposals", {}).items():
        if prop.get("status") not in PROPOSAL_STATUSES:
            blocks.append(f"proposal {pid} has invalid status {prop.get('status')!r}")
        if prop.get("track", "A") not in TRACKS:
            blocks.append("proposal " + pid + " has invalid track " + repr(prop.get("track")))
        critic = prop.get("critic") or {}
        if critic.get("track", "B") not in TRACKS:
            blocks.append("proposal " + pid + " has invalid critic track " + repr(critic.get("track")))
        verdict = critic.get("verdict")
        if verdict is not None and verdict not in CRITIC_VERDICTS:
            blocks.append(f"proposal {pid} has invalid critic verdict {verdict!r}")
        if prop.get("status") in ("verified", "applied") and not prop.get("verification"):
            blocks.append(f"proposal {pid} is {prop['status']} without a verification record")
        if prop.get("status") == "applied" and not prop.get("applied_at"):
            blocks.append(f"proposal {pid} is applied without applied_at")
    return blocks, warnings


# ---------------------------------------------------------------------------
# patch vocabulary
# ---------------------------------------------------------------------------

PATCH_OPS = {
    "add_node",
    "remove_node",
    "set_node_status",
    "set_node_statement",
    "add_edge",
    "remove_edge",
    "set_layer_policy",
    "set_trust_decay",
    "set_affected_trust_decay",
    "set_revalidation_threshold",
    "set_self_test_command",
}
NON_L0_LAYERS = tuple(layer for layer in LAYERS if layer != "L0")


def normalize_node(raw: dict[str, Any]) -> dict[str, Any]:
    node = dict(raw)
    if node.get("layer") == "L0":
        raise ValueError("patches may not create or modify L0 nodes")
    status = node.get("status", "draft")
    if status not in NODE_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    node["status"] = status
    node.setdefault("trust", 1.0 if status == "validated" else 0.5)
    node.setdefault("immutable", False)
    node.setdefault("created_at", now_iso())
    node["updated_at"] = now_iso()
    return node


def patch_origins(state: dict[str, Any], patch: list[dict[str, Any]]) -> set[str]:
    origins: set[str] = set()
    nodes = state.get("nodes", {})
    for op in patch:
        kind = op.get("op")
        if kind in ("set_node_status", "set_node_statement", "remove_node"):
            origins.add(op.get("node", ""))
        elif kind in ("add_edge", "remove_edge"):
            origins.update({op.get("from", ""), op.get("to", "")})
    return {n for n in origins if n in nodes}


def apply_raw_patch(state: dict[str, Any], patch: list[dict[str, Any]]) -> set[str]:
    changed: set[str] = set()
    nodes = state.setdefault("nodes", {})
    for op in patch:
        kind = op["op"]
        if kind == "add_node":
            node = normalize_node(op["node"])
            if node["id"] in nodes:
                raise SystemExit(f"BLOCKED: node {node['id']} already exists")
            nodes[node["id"]] = node
            changed.add(node["id"])
        elif kind == "remove_node":
            nid = op["node"]
            if nid == META_GOAL_ID:
                raise SystemExit(f"BLOCKED: {META_GOAL_ID} is immutable")
            nodes.pop(nid, None)
            state["edges"] = [
                e for e in state.get("edges", [])
                if e.get("from") != nid and e.get("to") != nid
            ]
            changed.add(nid)
        elif kind == "set_node_status":
            nid = op["node"]
            nodes[nid]["status"] = op["status"]
            nodes[nid]["updated_at"] = now_iso()
            changed.add(nid)
        elif kind == "set_node_statement":
            nid = op["node"]
            nodes[nid]["statement"] = op["statement"]
            nodes[nid]["updated_at"] = now_iso()
            changed.add(nid)
        elif kind == "add_edge":
            state.setdefault("edges", []).append(
                {"from": op["from"], "to": op["to"], "kind": op["kind"]}
            )
            changed.update({op["from"], op["to"]})
        elif kind == "remove_edge":
            a, b, k = op["from"], op["to"], op["kind"]
            state["edges"] = [
                e for e in state.get("edges", [])
                if not (e.get("from") == a and e.get("to") == b and e.get("kind") == k)
            ]
            changed.update({a, b})
        elif kind == "set_layer_policy":
            state.setdefault("layers", {})[op["layer"]] = {
                "scope": state["layers"].get(op["layer"], {}).get("scope", ""),
                "policy": op["policy"],
            }
        elif kind == "set_trust_decay":
            state["trust_decay"] = op["value"]
        elif kind == "set_affected_trust_decay":
            state["affected_trust_decay"] = op["value"]
        elif kind == "set_revalidation_threshold":
            state["revalidation_threshold"] = op["value"]
        elif kind == "set_self_test_command":
            state["self_test_command"] = op["command"]
    return changed


def validate_patch(state: dict[str, Any], patch: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    if not isinstance(patch, list) or not patch:
        return ["patch must be a non-empty JSON array"]
    nodes = state.get("nodes", {})
    for i, op in enumerate(patch):
        pos = f"patch[{i}]"
        if not isinstance(op, dict) or op.get("op") not in PATCH_OPS:
            blocks.append(f"{pos}: unknown or missing op; allowed: {sorted(PATCH_OPS)}")
            continue
        kind = op["op"]
        if kind == "add_node":
            node = op.get("node")
            if not isinstance(node, dict) or not node.get("id"):
                blocks.append(f"{pos}: add_node requires node.id")
            elif node.get("id") == META_GOAL_ID or node.get("layer") == "L0":
                blocks.append(f"{pos}: cannot add or target the immutable L0 core")
            elif node.get("id") in nodes:
                blocks.append(f"{pos}: node {node['id']} already exists")
            else:
                try:
                    normalize_node(node)
                except ValueError as exc:
                    blocks.append(f"{pos}: {exc}")
        elif kind == "remove_node":
            nid = op.get("node")
            if nid == META_GOAL_ID:
                blocks.append(f"{pos}: cannot remove the immutable L0 core")
            elif nid not in nodes:
                blocks.append(f"{pos}: unknown node {nid!r}")
        elif kind == "set_node_status":
            nid = op.get("node")
            if nid == META_GOAL_ID:
                blocks.append(f"{pos}: cannot modify the immutable L0 core")
            elif nid not in nodes:
                blocks.append(f"{pos}: unknown node {nid!r}")
            elif op.get("status") not in NODE_STATUSES:
                blocks.append(f"{pos}: invalid status {op.get('status')!r}")
        elif kind == "set_node_statement":
            nid = op.get("node")
            if nid == META_GOAL_ID:
                blocks.append(f"{pos}: cannot modify the immutable L0 core")
            elif nid not in nodes:
                blocks.append(f"{pos}: unknown node {nid!r}")
            elif not isinstance(op.get("statement"), str) or not op["statement"].strip():
                blocks.append(f"{pos}: statement must be a non-empty string")
        elif kind in ("add_edge", "remove_edge"):
            if kind == "add_edge":
                if op.get("from") not in nodes:
                    blocks.append(f"{pos}: edge from unknown node {op.get('from')!r}")
                if op.get("to") not in nodes:
                    blocks.append(f"{pos}: edge to unknown node {op.get('to')!r}")
                if op.get("kind") not in EDGE_KINDS:
                    blocks.append(f"{pos}: invalid edge kind {op.get('kind')!r}")
                elif op.get("kind") == "modification" and nodes.get(op.get("from"), {}).get("type") != "modification":
                    blocks.append(f"{pos}: modification edge must start at a modification node")
                if op.get("to") == META_GOAL_ID:
                    blocks.append(f"{pos}: no edge may target the immutable L0 core")
            else:
                a, b, k = op.get("from"), op.get("to"), op.get("kind")
                if not any(
                    e.get("from") == a and e.get("to") == b and e.get("kind") == k
                    for e in state.get("edges", [])
                ):
                    blocks.append(f"{pos}: no such edge {a}->{b} ({k})")
        elif kind == "set_layer_policy":
            if op.get("layer") not in NON_L0_LAYERS:
                blocks.append(f"{pos}: layer must be one of {list(NON_L0_LAYERS)}")
            elif not isinstance(op.get("policy"), str) or not op["policy"].strip():
                blocks.append(f"{pos}: policy must be a non-empty string")
        elif kind in ("set_trust_decay", "set_affected_trust_decay"):
            value = op.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 < value <= 1):
                blocks.append(f"{pos}: value must be in (0, 1]")
        elif kind == "set_revalidation_threshold":
            value = op.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 <= value <= 1):
                blocks.append(f"{pos}: value must be in [0, 1]")
        elif kind == "set_self_test_command":
            if not isinstance(op.get("command"), str) or not op["command"].strip():
                blocks.append(f"{pos}: command must be a non-empty string")
    if blocks:
        return blocks
    trial = copy.deepcopy(state)
    try:
        apply_raw_patch(trial, patch)
    except SystemExit as exc:
        return [str(exc)]
    trial_blocks, _ = validate(trial)
    return [f"after patch: {b}" for b in trial_blocks]


def mutate_validated(
    state: dict[str, Any],
    event: str,
    payload: dict[str, Any],
    mutate,
) -> dict[str, Any]:
    trial = copy.deepcopy(state)
    result = mutate(trial)
    blocks, warnings = validate(trial)
    if blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(blocks))
    state.clear()
    state.update(trial)
    append_event(state, event, payload)
    save_state(state, event)
    for w in warnings:
        print(f"WARNING: {w}")
    return result


# ---------------------------------------------------------------------------
# self-modification mechanics
# ---------------------------------------------------------------------------

def decay_trust(state: dict[str, Any], pre_existing: set[str]) -> None:
    threshold = state.get("revalidation_threshold", 0.25)
    factor = state.get("trust_decay", 0.9)
    for nid, node in state.get("nodes", {}).items():
        if nid == META_GOAL_ID or nid not in pre_existing:
            continue
        trust = max(0.0, round(float(node.get("trust", 1.0)) * factor, 4))
        node["trust"] = trust
        if node.get("status") == "validated" and trust < threshold:
            node["status"] = "draft"


def invalidate_closure(state: dict[str, Any], origins: Iterable[str]) -> set[str]:
    affected = descendants_dependency(state, origins)
    factor = state.get("affected_trust_decay", 0.5)
    for nid in affected:
        node = state.get("nodes", {}).get(nid)
        if not node or nid == META_GOAL_ID or node.get("layer") == "L0":
            continue
        node["status"] = "deprecated"
        node["trust"] = max(0.0, round(float(node.get("trust", 1.0)) * factor, 4))
        node["updated_at"] = now_iso()
    return affected


def apply_modification(
    state: dict[str, Any],
    proposal_id: str,
    patch: list[dict[str, Any]],
    targets: Iterable[str],
    track: str = "B",
) -> dict[str, Any]:
    blocks = validate_patch(state, patch)
    if blocks:
        raise SystemExit("BLOCKED: invalid patch:\n  " + "\n  ".join(blocks))

    proposal = state["proposals"][proposal_id]
    origins = patch_origins(state, patch) | set(targets)
    pre_existing = set(state.get("nodes", {}))
    affected_before = descendants_dependency(state, origins)

    mod_id = next_id(state, "MOD", "node")
    now = now_iso()
    state.setdefault("nodes", {})[mod_id] = {
        "type": "modification",
        "statement": proposal.get("title", proposal_id),
        "layer": "L4",
        "status": "validated",
        "trust": 1.0,
        "immutable": False,
        "proposal_id": proposal_id,
        "created_at": now,
        "updated_at": now,
    }

    changed = apply_raw_patch(state, patch)
    for target in sorted(origins | changed):
        if target in state["nodes"] and target != META_GOAL_ID and target != mod_id:
            state.setdefault("edges", []).append(
                {"from": mod_id, "to": target, "kind": "modification"}
            )

    decay_trust(state, pre_existing)
    affected = affected_before | descendants_dependency(state, origins | changed)
    affected.discard(META_GOAL_ID)
    invalidate_closure(state, affected)
    state["nodes"][mod_id]["status"] = "validated"
    state["nodes"][mod_id]["trust"] = 1.0

    proposal["status"] = "applied"
    proposal["applied_at"] = now_iso()
    proposal["applied_by_track"] = track
    proposal["modification_node"] = mod_id
    proposal["affected_nodes"] = sorted(a for a in affected if a != mod_id)
    append_event(
        state,
        "modification_applied",
        {
            "track": track,
            "proposal": proposal_id,
            "modification_node": mod_id,
            "targets": sorted(origins | changed),
            "affected_nodes": proposal["affected_nodes"],
        },
    )
    save_state(state, "modification_applied")
    return {"modification_node": mod_id, "affected_nodes": proposal["affected_nodes"]}


# ---------------------------------------------------------------------------
# command implementations
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    if STATE_PATH.exists() and not args.force:
        print(f"Auto-research state already exists: {STATE_PATH}")
        return 0
    state = skeleton(args.goal)
    append_event(state, "init", {"goal": args.goal})
    save_state(state, "init")
    print(f"Initialized {STATE_PATH}")
    print(f"Immutable L0 meta-goal: {args.goal}")
    print("Next: add-node, add-edge, then propose/critique/verify/apply")
    return 0


def cmd_add_node(args: argparse.Namespace) -> int:
    if args.layer == "L0":
        raise SystemExit(f"BLOCKED: {META_GOAL_ID} is the only L0 node and is immutable")
    if args.id == META_GOAL_ID:
        raise SystemExit(f"BLOCKED: {META_GOAL_ID} already exists and is immutable")
    state = load_state()

    def mutate(st: dict[str, Any]) -> None:
        if args.id in st["nodes"]:
            raise SystemExit(f"BLOCKED: node {args.id} already exists")
        trust = args.trust
        if trust is None:
            trust = 1.0 if args.status == "validated" else 0.5
        now = now_iso()
        st["nodes"][args.id] = {
            "type": args.type,
            "statement": args.statement,
            "layer": args.layer,
            "status": args.status,
            "trust": trust,
            "immutable": False,
            "created_at": now,
            "updated_at": now,
        }

    mutate_validated(
        state, "node_added",
        {"node": args.id, "type": args.type, "layer": args.layer, "status": args.status},
        mutate,
    )
    print(f"Added node {args.id} ({args.type}, {args.layer}, {args.status})")
    return 0


def cmd_add_edge(args: argparse.Namespace) -> int:
    state = load_state()

    def mutate(st: dict[str, Any]) -> None:
        st.setdefault("edges", []).append(
            {"from": args.from_, "to": args.to, "kind": args.kind}
        )

    mutate_validated(
        state, "edge_added",
        {"from": args.from_, "to": args.to, "kind": args.kind},
        mutate,
    )
    print(f"Added {args.kind} edge {args.from_} -> {args.to}")
    return 0


def load_patch(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.patch_file:
        try:
            raw = json.loads(Path(args.patch_file).read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid patch JSON: {exc}") from exc
    elif args.patch:
        try:
            raw = json.loads(args.patch)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid patch JSON: {exc}") from exc
    else:
        raw = []
    if not isinstance(raw, list):
        raise SystemExit("BLOCKED: patch must be a JSON array")
    return raw


def cmd_propose(args: argparse.Namespace) -> int:
    state = load_state()
    patch = load_patch(args)
    blocks = validate_patch(state, patch) if patch else []
    if blocks:
        raise SystemExit("BLOCKED: invalid patch:\n  " + "\n  ".join(blocks))
    pid = next_id(state, "P", "proposal")
    proposal = {
        "id": pid,
        "track": args.track,
        "title": args.title,
        "statement": args.statement,
        "targets": [t.strip() for t in (args.targets or "").split(",") if t.strip()],
        "patch": patch,
        "verification_command": args.verification,
        "status": "proposed",
        "critic": None,
        "verification": None,
        "revisions": [],
        "proposed_at": now_iso(),
    }
    state["proposals"][pid] = proposal
    append_event(state, "proposal_proposed", {"track": args.track, "proposal": pid, "title": args.title})
    save_state(state, "proposal_proposed")
    print(f"Proposed {pid}: {args.title}")
    print(f"Next: critique --proposal {pid} --verdict pass|challenge|reject "
          "--critic '<critic>' --reason '<reason>'")
    return 0


def cmd_critique(args: argparse.Namespace) -> int:
    state = load_state()
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if prop["status"] not in ("proposed", "challenged"):
        raise SystemExit(f"BLOCKED: proposal {args.proposal} is {prop['status']}, not awaiting critique")
    if not args.reason.strip():
        raise SystemExit("BLOCKED: critic reason is mandatory")
    prop["critic"] = {
        "track": args.track,
        "verdict": args.verdict,
        "critic": args.critic,
        "reason": args.reason,
        "at": now_iso(),
    }
    prop["status"] = {
        "pass": "passed_critic",
        "challenge": "challenged",
        "reject": "rejected",
    }[args.verdict]
    append_event(
        state, "proposal_critiqued",
        {"track": args.track, "proposal": args.proposal, "verdict": args.verdict, "critic": args.critic},
    )
    save_state(state, "proposal_critiqued")
    print(f"{args.proposal}: {args.verdict} ({args.critic})")
    return 0


def cmd_revise(args: argparse.Namespace) -> int:
    state = load_state()
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if prop["status"] not in ("challenged", "failed_verification"):
        raise SystemExit(f"BLOCKED: proposal {args.proposal} is {prop['status']}, not revisable")
    prop.setdefault("revisions", []).append({"note": args.note, "at": now_iso()})
    prop["status"] = "proposed"
    prop["critic"] = None
    prop["verification"] = None
    append_event(state, "proposal_revised", {"proposal": args.proposal, "note": args.note})
    save_state(state, "proposal_revised")
    print(f"{args.proposal} re-opened for critique")
    return 0


def run_verification_command(command: str, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "exit_code": None,
            "timeout": True,
            "stdout_sha256": "",
            "stderr_tail": "",
            "at": now_iso(),
        }
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    return {
        "exit_code": proc.returncode,
        "timeout": False,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8", "replace")).hexdigest(),
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
        "stderr_tail": stderr[-2000:],
        "at": now_iso(),
    }


def cmd_verify(args: argparse.Namespace) -> int:
    state = load_state()
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if prop["status"] != "passed_critic":
        raise SystemExit(
            f"BLOCKED: proposal {args.proposal} is {prop['status']}; "
            "it must pass critique before hard verification"
        )
    command = args.command or prop.get("verification_command")
    if not command or not command.strip():
        raise SystemExit("BLOCKED: no verification command; pass --command")
    record = run_verification_command(command, args.timeout)
    record.update({"track": args.track, "proposal": args.proposal, "level": "hard", "command": command})
    state.setdefault("verifications", []).append(record)
    state["counters"]["verification"] = len(state["verifications"])
    prop["verification"] = {
        "track": args.track,
        "command": command,
        "exit_code": record["exit_code"],
        "timeout": record["timeout"],
        "stdout_sha256": record["stdout_sha256"],
        "at": record["at"],
    }
    passed = not record.get("timeout") and record.get("exit_code") == 0
    prop["status"] = "verified" if passed else "failed_verification"
    outcome = "verified" if passed else "failed_verification"
    append_event(state, "proposal_verified", {"track": args.track, "proposal": args.proposal, "outcome": outcome})
    save_state(state, "proposal_verified")
    print(f"{args.proposal}: {outcome} (exit {record['exit_code']})")
    if record["stderr_tail"]:
        print(record["stderr_tail"].rstrip())
    return 0 if passed else 1


def cmd_apply(args: argparse.Namespace) -> int:
    state = load_state()
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if prop["status"] != "verified":
        raise SystemExit(
            f"BLOCKED: proposal {args.proposal} is {prop['status']}; "
            "it must pass hard verification before application"
        )
    if not prop.get("patch"):
        raise SystemExit(f"BLOCKED: proposal {args.proposal} has no patch")
    result = apply_modification(
        state, args.proposal, prop["patch"], prop.get("targets", []), track=args.track
    )
    role = "B (slow)" if args.track == "B" else "A (fast)"
    print(f"{role} applied {args.proposal} as modification node {result['modification_node']}")
    print(f"Affected/deprecated nodes: {result['affected_nodes'] or 'none'}")
    return 0


def cmd_invalidate(args: argparse.Namespace) -> int:
    state = load_state()
    origins = [o.strip() for o in args.origins.split(",") if o.strip()]
    missing = [o for o in origins if o not in state.get("nodes", {})]
    if missing:
        raise SystemExit(f"Unknown origin nodes: {missing}")
    decay_trust(state, set(state["nodes"]))
    affected = invalidate_closure(state, origins)
    append_event(state, "nodes_invalidated", {"track": args.track, "origins": origins, "affected": sorted(affected)})
    save_state(state, "nodes_invalidated")
    print(f"Invalidated: {sorted(affected) or 'none'}")
    print("Trust decayed for every non-L0 node; revalidate before reuse.")
    return 0


def cmd_revalidate(args: argparse.Namespace) -> int:
    state = load_state()
    node = state.get("nodes", {}).get(args.node)
    if not node:
        raise SystemExit(f"Unknown node {args.node}")
    if node.get("layer") == "L0":
        raise SystemExit(f"{META_GOAL_ID} is always validated and may not be revalidated")
    if args.level == "syntax":
        blocks, warnings = validate(state)
        if blocks:
            for b in blocks:
                print(f"BLOCK: {b}")
            return 2
        for w in warnings:
            print(f"WARNING: {w}")
        record = {
            "track": args.track,
            "node": args.node,
            "level": "syntax",
            "command": None,
            "exit_code": 0,
            "stdout_sha256": hashlib.sha256(b"syntax-ok").hexdigest(),
            "at": now_iso(),
        }
    else:
        if not args.command or not args.command.strip():
            raise SystemExit(f"BLOCKED: {args.level} revalidation requires --command")
        record = run_verification_command(args.command, args.timeout)
        record.update({"track": args.track, "node": args.node, "level": args.level, "command": args.command})
        if record.get("timeout") or record.get("exit_code") != 0:
            append_event(state, "node_revalidation_failed", record)
            save_state(state, "node_revalidation_failed")
            print(f"{args.node}: revalidation failed (exit {record.get('exit_code')})")
            return 1
    state.setdefault("verifications", []).append(record)
    state["counters"]["verification"] = len(state["verifications"])
    node["status"] = "validated"
    node["trust"] = 1.0
    node["updated_at"] = now_iso()
    append_event(state, "node_revalidated", {"track": args.track, "node": args.node, "level": args.level})
    save_state(state, "node_revalidated")
    print(f"{args.node}: validated (trust 1.0, level={args.level})")
    return 0


def cmd_self_test(args: argparse.Namespace) -> int:
    state = load_state()
    command = args.command or state.get("self_test_command")
    if not command or not command.strip():
        raise SystemExit("BLOCKED: no self-test command configured")
    record = run_verification_command(command, args.timeout)
    record.update({"level": "self_test", "command": command, "node": None})
    state.setdefault("verifications", []).append(record)
    state["counters"]["verification"] = len(state["verifications"])
    passed = not record.get("timeout") and record.get("exit_code") == 0
    state["last_self_test"] = {
        "passed": passed,
        "exit_code": record.get("exit_code"),
        "stdout_sha256": record.get("stdout_sha256"),
        "at": record.get("at"),
    }
    append_event(state, "self_test_passed" if passed else "self_test_failed", state["last_self_test"])
    save_state(state, "self_test")
    print(f"Self-test: {'PASS' if passed else 'FAIL'} (exit {record.get('exit_code')})")
    return 0 if passed else 1


def list_snapshots() -> list[Path]:
    return sorted(SNAP_DIR.glob("*.json")) if SNAP_DIR.exists() else []


def cmd_snapshot(_: argparse.Namespace) -> int:
    state = load_state()
    path = snapshot_state(state, "manual")
    if not path:
        raise SystemExit("BLOCKED: could not write snapshot")
    print(f"Snapshot: {path}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    files = list_snapshots()
    if not files:
        raise SystemExit("No snapshots available")
    idx = args.to - 1
    if idx < 0 or idx >= len(files):
        raise SystemExit(f"Snapshot index must be 1..{len(files)}")
    restored = json.loads(files[idx].read_text())
    append_event(restored, "rolled_back", {"from_snapshot": files[idx].name, "to_snapshot_index": args.to})
    save_state(restored, "rolled_back")
    print(f"Rolled back to snapshot {args.to}: {files[idx].name}")
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    state = load_state()
    blocks, warnings = validate(state)
    for w in warnings:
        print(f"WARNING: {w}")
    for b in blocks:
        print(f"BLOCK: {b}")
    if blocks:
        return 2
    print("PASS: auto-research graph is structurally valid.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    state = load_state()
    nodes = state.get("nodes", {})
    print("SELF-EVOLVED RESEARCH HARNESS")
    print(f"L0 meta-goal: {state.get('meta_goal', {}).get('statement')}")
    print(f"Nodes: {len(nodes)}  Edges: {len(state.get('edges', []))}  Snapshots: {len(list_snapshots())}")
    by_status: dict[str, int] = {}
    for node in nodes.values():
        by_status[node.get("status", "?")] = by_status.get(node.get("status", "?"), 0) + 1
    print(f"Status: {by_status}")
    for nid, node in sorted(nodes.items()):
        if nid == META_GOAL_ID:
            continue
        print(f"  {nid:8s} {node.get('type','?'):14s} {node.get('layer','?'):3s} "
              f"{node.get('status','?'):11s} trust={node.get('trust', 0):.2f}")
    print(f"Proposals: {len(state.get('proposals', {}))}")
    for pid, prop in sorted(state.get("proposals", {}).items()):
        print(f"  {pid:8s} [{prop.get('track','?')}] {prop.get('status','?'):18s} {prop.get('title','')[:60]}")
    if state.get("last_self_test"):
        last = state["last_self_test"]
        print(f"Last self-test: {'PASS' if last.get('passed') else 'FAIL'} at {last.get('at')}")
    return 0


def cmd_next(_: argparse.Namespace) -> int:
    state = load_state()
    out: list[str] = []
    for pid, prop in sorted(state.get("proposals", {}).items()):
        status = prop.get("status")
        if status == "proposed":
            out.append(f"critique --proposal {pid} --verdict pass|challenge|reject --critic '<critic>' --reason '<reason>'")
        elif status == "challenged":
            out.append(f"revise --proposal {pid} --note '<revision>'  # then critique again")
        elif status == "passed_critic":
            out.append(f"verify --proposal {pid}")
        elif status == "verified":
            out.append(f"apply --proposal {pid}")
    if not out:
        deprecated = sorted(
            nid for nid, node in state.get("nodes", {}).items()
            if node.get("status") == "deprecated"
        )
        if deprecated:
            out.append(f"revalidate --node {deprecated[0]} --level hard --command '<reproducible verification>'")
        drafts = sorted(
            nid for nid, node in state.get("nodes", {}).items()
            if node.get("status") == "draft"
        )
        if not out and drafts:
            out.append(f"revalidate --node {drafts[0]} --level syntax")
        if not out:
            out.append("propose --title '<candidate self-modification>' --statement '<rationale>' --patch-file patch.json")
            out.append("self-test")
    print("NEXT EVENTS")
    for i, ev in enumerate(out[:8], 1):
        print(f"  {i}. {ev}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    state = load_state()
    events = state.get("events", [])
    if args.json:
        print(json.dumps(events, indent=2))
        return 0
    print(f"EVENT LOG ({len(events)} events)")
    for e in events:
        payload = json.dumps(e.get("payload", {}), ensure_ascii=False, separators=(",", ":"))
        if len(payload) > 100:
            payload = payload[:97] + "..."
        print(f"  {e.get('at')}  {e.get('event')}  {payload}")
    return 0



def cmd_ab_status(_: argparse.Namespace) -> int:
    state = load_state()
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})

    print("A/B AUTO-RESEARCH STATUS")
    print("A (fast): propose, explore, draft candidates, cheap pre-checks")
    drafts = sorted(nid for nid, n in nodes.items() if n.get("status") == "draft")
    deprecated = sorted(nid for nid, n in nodes.items() if n.get("status") == "deprecated")
    print(f"  draft nodes      : {drafts or 'none'}")
    print(f"  deprecated nodes : {deprecated or 'none'}")
    a_props = [pid for pid, prop in proposals.items() if prop.get("track", "A") == "A"]
    print(f"  A proposals      : {a_props or 'none'}")
    for pid in a_props:
        prop = proposals[pid]
        print(f"    {pid:8s} {prop.get('status', '?'):18s} {prop.get('title', '')[:60]}")

    print("B (slow): critic review, hard verification, consolidation, apply")
    b_queue = sorted(
        pid for pid, prop in proposals.items()
        if prop.get("status") in ("proposed", "passed_critic", "verified")
    )
    print(f"  B queue          : {b_queue or 'none'}")
    for pid in b_queue:
        prop = proposals[pid]
        action = {
            "proposed": "critique",
            "passed_critic": "verify",
            "verified": "apply",
        }[prop["status"]]
        print(f"    {pid:8s} {prop.get('status', '?'):18s} -> {action}")

    print("Loop contract")
    print("  A proposes -> B criticises -> B hard-verifies -> B applies")
    print("  -> trust decay + dependency closure -> A revises or opens a new candidate")
    return 0


def cmd_ab_next(_: argparse.Namespace) -> int:
    state = load_state()
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    a_actions: list[str] = []
    b_actions: list[str] = []

    for pid, prop in sorted(proposals.items()):
        status = prop.get("status")
        if status == "proposed":
            b_actions.append(
                f"critique --proposal {pid} --track B --verdict pass|challenge|reject "
                "--critic '<critic>' --reason '<reason>'"
            )
        elif status == "challenged":
            a_actions.append(f"revise --proposal {pid} --note '<A revision>'")
        elif status == "failed_verification":
            a_actions.append(
                f"revise --proposal {pid} --note '<fix verification failure>'"
            )
        elif status == "passed_critic":
            b_actions.append(f"verify --proposal {pid} --track B")
        elif status == "verified":
            b_actions.append(f"apply --proposal {pid} --track B")
        elif status == "rejected":
            a_actions.append(
                f"propose --track A --title '<new candidate>' "
                "--statement '<why this differs from rejected {pid}>'"
            )

    if not b_actions:
        deprecated = sorted(
            nid for nid, n in nodes.items() if n.get("status") == "deprecated"
        )
        if deprecated:
            b_actions.append(
                f"revalidate --node {deprecated[0]} --track B --level hard "
                "--command '<reproducible verification>'"
            )

    if not a_actions and not b_actions:
        drafts = sorted(
            nid for nid, n in nodes.items() if n.get("status") == "draft"
        )
        if drafts:
            b_actions.append(
                f"revalidate --node {drafts[0]} --track B --level syntax"
            )
        else:
            a_actions.append(
                "propose --track A --title '<candidate self-modification>' "
                "--statement '<rationale>' --patch-file patch.json "
                "--verification '<exit-0 command>'"
            )
            b_actions.append("self-test")

    print("A NEXT (fast layer)")
    for action in a_actions[:5]:
        print(f"  - {action}")
    if not a_actions:
        print("  - (wait for B feedback)")

    print("B NEXT (slow layer)")
    for action in b_actions[:5]:
        print(f"  - {action}")
    if not b_actions:
        print("  - (wait for A candidates)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Self-Evolved Research Harness: deterministic self-graph engine"
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create the auto-research state")
    sp.add_argument("--goal", default=META_GOAL_STATEMENT, help="L0 meta-goal")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add-node", help="add a node to the self-graph")
    sp.add_argument("--id", required=True)
    sp.add_argument("--type", required=True, choices=NODE_TYPES)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--layer", required=True, choices=LAYERS)
    sp.add_argument("--status", choices=NODE_STATUSES, default="draft")
    sp.add_argument("--trust", type=float)
    sp.set_defaults(func=cmd_add_node)

    sp = sub.add_parser("add-edge", help="add a dependency or modification edge")
    sp.add_argument("--from", dest="from_", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--kind", required=True, choices=EDGE_KINDS)
    sp.set_defaults(func=cmd_add_edge)

    sp = sub.add_parser("propose", help="A-track: propose a self-modification")
    sp.add_argument("--track", choices=TRACKS, default="A",
                    help="A = fast proposer (default); B = slow proposer")
    sp.add_argument("--title", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--targets", default="", help="comma-separated target node ids")
    sp.add_argument("--patch-file", help="JSON file containing a patch array")
    sp.add_argument("--patch", help="inline JSON patch array")
    sp.add_argument("--verification", help="hard verification command")
    sp.set_defaults(func=cmd_propose)

    sp = sub.add_parser("critique", help="B-track: LLM critic gate (soft judgment only)")
    sp.add_argument("--track", choices=TRACKS, default="B",
                    help="B = slow critic (default)")
    sp.add_argument("--proposal", required=True)
    sp.add_argument("--verdict", required=True, choices=CRITIC_VERDICTS)
    sp.add_argument("--critic", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_critique)

    sp = sub.add_parser("revise", help="re-open a challenged proposal")
    sp.add_argument("--proposal", required=True)
    sp.add_argument("--note", required=True)
    sp.set_defaults(func=cmd_revise)

    sp = sub.add_parser("verify", help="B-track: run hard verification for a proposal")
    sp.add_argument("--track", choices=TRACKS, default="B",
                    help="B = slow verifier (default)")
    sp.add_argument("--proposal", required=True)
    sp.add_argument("--command", help="override the proposal verification command")
    sp.add_argument("--timeout", type=int, default=60)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("apply", help="B-track: apply a verified patch and invalidate dependents")
    sp.add_argument("--track", choices=TRACKS, default="B",
                    help="B = slow applier (default)")
    sp.add_argument("--proposal", required=True)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("invalidate", help="manually invalidate nodes and their closure")
    sp.add_argument("--track", choices=TRACKS, default="B",
                    help="B = slow-layer invalidation (default)")
    sp.add_argument("--origins", required=True, help="comma-separated node ids")
    sp.set_defaults(func=cmd_invalidate)

    sp = sub.add_parser("revalidate", help="B-track: restore a node to validated")
    sp.add_argument("--track", choices=TRACKS, default="B",
                    help="B = slow revalidator (default)")
    sp.add_argument("--node", required=True)
    sp.add_argument("--level", required=True, choices=REVALIDATION_LEVELS)
    sp.add_argument("--command", help="required for light/hard")
    sp.add_argument("--timeout", type=int, default=60)
    sp.set_defaults(func=cmd_revalidate)

    sp = sub.add_parser("ab-status", help="show the A/B fast/slow queue")
    sp.set_defaults(func=cmd_ab_status)

    sp = sub.add_parser("ab-next", help="show the next A and B actions")
    sp.set_defaults(func=cmd_ab_next)

    sp = sub.add_parser("self-test", help="run the configured capability self-test")
    sp.add_argument("--command", help="override the configured self-test command")
    sp.add_argument("--timeout", type=int, default=120)
    sp.set_defaults(func=cmd_self_test)

    sp = sub.add_parser("snapshot", help="write a manual snapshot")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("rollback", help="roll back to a snapshot (1 = oldest)")
    sp.add_argument("--to", type=int, required=True)
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("validate", help="run structural self-checks")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("status", help="show self-graph and proposal pipeline")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("next", help="show the next event in the loop")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("events", help="show the event log")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_events)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.func(args)
        return int(result or 0)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
