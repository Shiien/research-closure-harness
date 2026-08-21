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
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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

RETROSPECTIVE_CLASSES = (
    "defect",
    "risk",
    "capability_gap",
    "loop_health",
    "other",
)
RETROSPECTIVE_STATUSES = ("open", "converted", "superseded", "obsolete")
RETROSPECTIVE_DISPOSITIONS = ("converted", "superseded", "obsolete")

# patch_file may touch every engine file except the immutable state journal and
# VCS/local-secret plumbing. The engine is deliberately allowed to patch itself.
PROTECTED_PATCH_FILES = {
    ".gitignore",
    ".gitmodules",
    ".research/auto_research.json",
}
PROTECTED_PATCH_PREFIXES = (
    ".git/",
    ".ssh_github/",
    ".research/auto_snapshots/",
)
CORE_FILES_PROTECTED_FROM_DELETE = {
    "tools/auto_research.py",
}


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
SNAP_INDEX_NAME = "index.json"
DEFAULT_SNAPSHOT_RETENTION = {
    "keep_last": 25,
    "keep_labels": ["init", "modification_applied"],
}
DEFAULT_HEALTH_LIMITS = {
    "max_open_proposal_age_hours": 24,
    "max_self_test_age_hours": 24,
    "max_last_event_age_hours": 6,
    "max_reject_streak": 5,
    "max_snapshot_bytes": 2_000_000_000,
    "max_state_bytes": 100_000_000,
    "max_retro_age_days": 7,
}


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
        "snapshot_retention": copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION),
        "health_limits": copy.deepcopy(DEFAULT_HEALTH_LIMITS),
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
        "retrospectives": {},
        "file_backups": [],
        "events": [],
        "counters": {
            "node": 1,
            "proposal": 0,
            "verification": 0,
            "retrospective": 0,
        },
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
        ("retrospectives", {}),
        ("file_backups", []),
        ("snapshot_retention", copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION)),
        ("health_limits", copy.deepcopy(DEFAULT_HEALTH_LIMITS)),
    ):
        state.setdefault(key, default)
    return state


def snapshot_name_label(name: str) -> str:
    return "_".join(Path(name).stem.split("_")[3:])


def snapshot_files() -> list[Path]:
    if not SNAP_DIR.exists():
        return []
    return sorted(
        path for path in SNAP_DIR.glob("*.json")
        if path.name != SNAP_INDEX_NAME
    )


def rebuild_snapshot_index() -> dict[str, Any]:
    index: dict[str, Any] = {"version": 1, "files": {}}
    for path in snapshot_files():
        try:
            blob = path.read_text()
            index["files"][path.name] = {
                "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                "label": snapshot_name_label(path.name),
                "at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            }
        except Exception:
            continue
    save_snapshot_index(index)
    return index


def load_snapshot_index() -> dict[str, Any]:
    path = SNAP_DIR / SNAP_INDEX_NAME
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return {"version": 1, "files": {}}
    files = raw.get("files", {})
    return {
        "version": 1,
        "files": files if isinstance(files, dict) else {},
    }


def save_snapshot_index(index: dict[str, Any]) -> None:
    path = SNAP_DIR / SNAP_INDEX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def prune_snapshots(state: dict[str, Any], index: dict[str, Any]) -> None:
    files = snapshot_files()
    if not files:
        return
    retention = state.get("snapshot_retention", copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION))
    keep_last = max(1, int(retention.get("keep_last", DEFAULT_SNAPSHOT_RETENTION["keep_last"])))
    keep_labels = set(retention.get("keep_labels", DEFAULT_SNAPSHOT_RETENTION["keep_labels"]))
    keep = set(files[-keep_last:])
    keep.add(files[0])
    for path in files:
        if snapshot_name_label(path.name) in keep_labels:
            keep.add(path)
    index_files = index.setdefault("files", {})
    for path in files:
        if path in keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        index_files.pop(path.name, None)
    save_snapshot_index(index)


def snapshot_state(state: dict[str, Any], label: str = "") -> Path | None:
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(state, indent=2, sort_keys=False) + "\n"
        sha = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        index = load_snapshot_index()

        files = snapshot_files()
        if files:
            try:
                if json.loads(files[-1].read_text()) == state:
                    return files[-1]
            except Exception:
                pass
        for name, info in index.get("files", {}).items():
            if info.get("sha256") == sha and (SNAP_DIR / name).exists():
                return SNAP_DIR / name

        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        seq = len(files)
        safe = label.replace("/", "_") or "snapshot"
        while (SNAP_DIR / f"{stamp}_{seq:03d}_{safe}.json").exists():
            seq += 1
        path = SNAP_DIR / f"{stamp}_{seq:03d}_{safe}.json"
        path.write_text(blob)
        index.setdefault("files", {})[path.name] = {
            "sha256": sha,
            "label": safe,
            "at": now_iso(),
        }
        save_snapshot_index(index)
        prune_snapshots(state, index)
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

    retention = state.get("snapshot_retention", {})
    if not isinstance(retention, dict):
        blocks.append("snapshot_retention must be an object")
    else:
        keep_last = retention.get("keep_last")
        if not isinstance(keep_last, int) or isinstance(keep_last, bool) or keep_last < 1:
            blocks.append("snapshot_retention.keep_last must be a positive integer")
        keep_labels = retention.get("keep_labels")
        if not isinstance(keep_labels, list) or not all(
            isinstance(item, str) and item for item in keep_labels
        ):
            blocks.append("snapshot_retention.keep_labels must be a list of non-empty strings")

    limits = state.get("health_limits", {})
    if not isinstance(limits, dict):
        blocks.append("health_limits must be an object")
    else:
        for key in DEFAULT_HEALTH_LIMITS:
            value = limits.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                blocks.append(f"health_limits.{key} must be a positive number")

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
        retro_id = prop.get("retrospective_id")
        if retro_id and retro_id not in state.get("retrospectives", {}):
            blocks.append(f"proposal {pid} references unknown retrospective {retro_id!r}")

    for rid, retro in state.get("retrospectives", {}).items():
        if not isinstance(retro, dict):
            blocks.append(f"retrospective {rid} must be an object")
            continue
        if retro.get("class", "other") not in RETROSPECTIVE_CLASSES:
            blocks.append(f"retrospective {rid} has invalid class {retro.get('class')!r}")
        if retro.get("status", "open") not in RETROSPECTIVE_STATUSES:
            blocks.append(f"retrospective {rid} has invalid status {retro.get('status')!r}")
        if not isinstance(retro.get("observation", ""), str) or not retro["observation"].strip():
            blocks.append(f"retrospective {rid} needs a non-empty observation")
        if retro.get("status") == "converted":
            prop = state.get("proposals", {}).get(retro.get("proposal_id"))
            if not prop:
                blocks.append(f"retrospective {rid} is converted without a valid proposal_id")
            elif prop.get("status") != "applied":
                blocks.append(f"retrospective {rid} is converted but {retro.get('proposal_id')} is not applied")

    for i, entry in enumerate(state.get("file_backups", [])):
        pos = f"file_backups[{i}]"
        if not isinstance(entry, dict):
            blocks.append(f"{pos} must be an object")
            continue
        if not isinstance(entry.get("modification_node", ""), str):
            blocks.append(f"{pos} needs modification_node")
        if entry.get("modification_node") not in nodes:
            blocks.append(f"{pos} references unknown node {entry.get('modification_node')!r}")
        if not isinstance(entry.get("files", {}), dict):
            blocks.append(f"{pos} needs a files object")
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
    "patch_file",
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


def file_patch_ops(patch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [op for op in patch if op.get("op") == "patch_file"]


def combined_file_patch(patch: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for op in file_patch_ops(patch):
        text = op.get("patch", "")
        if not isinstance(text, str) or not text.strip():
            continue
        parts.append(text if text.endswith("\n") else text + "\n")
    return "".join(parts)


def file_patch_paths(patch_text: str, root: Path = ROOT) -> list[str]:
    """Return repo-relative paths touched by a unified diff, read-only."""
    if not patch_text.strip():
        raise ValueError("patch_file patch must be a non-empty unified diff")
    proc = subprocess.run(
        ["git", "apply", "--check", "--verbose"],
        cwd=str(root),
        input=patch_text,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip()
        raise ValueError(f"git apply --check failed: {detail or 'unknown error'}")
    paths: list[str] = []
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        line = line.strip()
        if line.startswith("Checking patch "):
            raw = line[len("Checking patch "):]
            if raw.endswith("..."):
                raw = raw[:-3]
            raw = raw.strip().strip('"')
            if "=>" in raw:
                raise ValueError("patch_file rename/copy patches are not supported yet")
            if raw:
                paths.append(raw)
    if not paths:
        numstat = subprocess.run(
            ["git", "apply", "--numstat"],
            cwd=str(root),
            input=patch_text,
            text=True,
            capture_output=True,
        )
        for line in numstat.stdout.splitlines():
            parts = line.rstrip().split("\t")
            if len(parts) >= 3 and parts[2] and "=>" not in parts[2]:
                paths.append(parts[2])
    if any("=>" in p for p in paths):
        raise ValueError("patch_file rename/copy patches are not supported yet")
    return sorted(set(paths))


def _patch_path_norm(raw: str) -> str:
    return raw.strip().strip('"').replace("\\", "/")


def validate_patch_path(raw: str) -> str | None:
    norm = _patch_path_norm(raw)
    if not norm or norm.startswith("/"):
        return f"patch_file path {raw!r} must be a repo-relative path"
    parts = Path(norm).parts
    if ".." in parts:
        return f"patch_file path {raw!r} may not escape the repository"
    if norm in PROTECTED_PATCH_FILES:
        return f"patch_file may not touch protected file {norm!r}"
    for prefix in PROTECTED_PATCH_PREFIXES:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return f"patch_file may not touch protected path {norm!r}"
    return None


def patch_deletes_path(patch_text: str, rel: str) -> bool:
    lines = patch_text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"^--- (?:a/)?(.+?)\s*$", line)
        if not match or _patch_path_norm(match.group(1)) != rel:
            continue
        for following in lines[i + 1:i + 4]:
            if re.match(r"^\+\+\+ /dev/null\s*$", following):
                return True
    return False


def check_file_patch(patch: list[dict[str, Any]], root: Path = ROOT) -> list[str]:
    blocks: list[str] = []
    ops = file_patch_ops(patch)
    if not ops:
        return blocks
    for i, op in enumerate(ops):
        text = op.get("patch")
        if not isinstance(text, str) or not text.strip():
            blocks.append(f"patch_file op {i}: patch must be a non-empty unified diff")
    if blocks:
        return blocks
    combined = combined_file_patch(patch)
    try:
        paths = file_patch_paths(combined, root)
    except ValueError as exc:
        return [str(exc)]
    for path in paths:
        block = validate_patch_path(path)
        if block:
            blocks.append(block)
            continue
        if (root / path).is_symlink():
            blocks.append(f"patch_file may not target symlink {path!r}")
        if (
            path in CORE_FILES_PROTECTED_FROM_DELETE
            and patch_deletes_path(combined, path)
        ):
            blocks.append(f"patch_file may not delete core engine file {path!r}")
    if len(ops) == 1 and len(paths) == 1:
        op = ops[0]
        expected_before = op.get("sha256_before")
        target = root / paths[0]
        if isinstance(expected_before, str) and expected_before and target.exists():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected_before:
                blocks.append(
                    f"patch_file sha256_before mismatch for {paths[0]}: "
                    f"expected {expected_before}, got {actual}"
                )
    return blocks


def apply_file_patch_text(patch_text: str, root: Path) -> None:
    proc = subprocess.run(
        ["git", "apply"],
        cwd=str(root),
        input=patch_text,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout + proc.stderr).strip() or "git apply failed")


def apply_file_patch(
    patch: list[dict[str, Any]],
    root: Path,
    backup_label: str,
    state: dict[str, Any],
) -> None:
    """Apply patch_file ops to the working tree with rollback backups."""
    combined = combined_file_patch(patch)
    if not combined.strip():
        return
    paths = file_patch_paths(combined, root)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    safe = (backup_label or "patch").replace("/", "_")
    backup_root = SNAP_DIR / "file_backups" / f"{stamp}_{safe}"
    seq = 0
    while backup_root.exists():
        seq += 1
        backup_root = SNAP_DIR / "file_backups" / f"{stamp}_{safe}_{seq:03d}"
    backup_root.mkdir(parents=True, exist_ok=False)

    files: dict[str, str | None] = {}
    for rel in paths:
        src = root / rel
        if src.exists():
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, backup_path)
            files[rel] = backup_path.relative_to(ROOT).as_posix()
        else:
            files[rel] = None

    def undo() -> None:
        for rel, backup in files.items():
            target = root / rel
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / backup, target)

    try:
        apply_file_patch_text(combined, root)
        ops = file_patch_ops(patch)
        if len(ops) == 1 and len(paths) == 1:
            expected_after = ops[0].get("sha256_after")
            if isinstance(expected_after, str) and expected_after:
                target = root / paths[0]
                content = target.read_bytes() if target.exists() else b""
                actual = hashlib.sha256(content).hexdigest()
                if actual != expected_after:
                    raise RuntimeError(
                        f"patch_file sha256_after mismatch for {paths[0]}: "
                        f"expected {expected_after}, got {actual}"
                    )
    except Exception as exc:
        try:
            undo()
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        raise SystemExit(f"BLOCKED: file patch failed and was rolled back: {exc}") from exc

    state.setdefault("file_backups", []).append({
        "at": now_iso(),
        "modification_node": backup_label,
        "backup_dir": backup_root.relative_to(ROOT).as_posix(),
        "files": files,
    })


def restore_file_backup_entry(entry: dict[str, Any]) -> None:
    root_resolved = ROOT.resolve()
    snap_resolved = SNAP_DIR.resolve()
    for rel, backup in entry.get("files", {}).items():
        target = (ROOT / rel).resolve()
        if not target.is_relative_to(root_resolved):
            raise SystemExit(f"BLOCKED: rollback refuses path outside repository: {rel}")
        if backup is None:
            target.unlink(missing_ok=True)
            continue
        backup_path = (ROOT / backup).resolve()
        if not backup_path.is_relative_to(snap_resolved):
            raise SystemExit(f"BLOCKED: rollback refuses backup outside snapshots: {backup}")
        if not backup_path.exists():
            raise SystemExit(f"BLOCKED: rollback backup is missing: {backup}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, target)


def restore_files_to_target(current: dict[str, Any], target: dict[str, Any]) -> None:
    target_mods = {
        entry.get("modification_node")
        for entry in target.get("file_backups", [])
        if isinstance(entry, dict)
    }
    for entry in reversed(current.get("file_backups", [])):
        if not isinstance(entry, dict):
            continue
        if entry.get("modification_node") in target_mods:
            continue
        restore_file_backup_entry(entry)


def apply_raw_patch(
    state: dict[str, Any],
    patch: list[dict[str, Any]],
    write_files: bool = False,
    backup_label: str = "",
) -> set[str]:
    changed: set[str] = set()
    nodes = state.setdefault("nodes", {})
    for op in patch:
        kind = op["op"]
        if kind == "patch_file":
            # File patches are validated separately and applied below so that
            # state-trial validation never writes to the working tree.
            continue
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
    if write_files and file_patch_ops(patch):
        apply_file_patch(patch, ROOT, backup_label or "patch", state)
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
        elif kind == "patch_file":
            if not isinstance(op.get("patch"), str) or not op["patch"].strip():
                blocks.append(f"{pos}: patch_file requires a non-empty unified diff")
    if blocks:
        return blocks
    file_blocks = check_file_patch(patch)
    if file_blocks:
        return [f"patch_file: {b}" for b in file_blocks]
    trial = copy.deepcopy(state)
    try:
        apply_raw_patch(trial, patch, write_files=False)
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

    changed = apply_raw_patch(
        state,
        patch,
        write_files=True,
        backup_label=mod_id,
    )
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


def _patch_target_values(patch: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for op in patch:
        if not isinstance(op, dict):
            continue
        for key in ("node", "from", "to", "path"):
            value = op.get(key, "")
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, dict):
                values.append(str(value.get("id", "")))
    return values


def novelty_norm(text: str) -> str:
    text = text.lower()
    text = re.sub(
        r"\b(a|b)\s+optimizes\s+(a|b)\b",
        "<track> optimizes <track>",
        text,
    )
    text = re.sub(r"(?<![a-z0-9])l\d(?![a-z0-9])", "<layer>", text)
    text = re.sub(
        r"\b(assumption|inference|verify|modification)\b", "<type>", text
    )
    text = re.sub(r"\bepoch\s+\d+\b", "epoch <n>", text)
    text = re.sub(r"\bP-\d+\b", "p-<n>", text)
    text = re.sub(r"\b[a-z][a-z0-9-]*-\d+\b", "<id>", text)
    text = re.sub(r"\d+", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def novelty_blocks(
    state: dict[str, Any],
    title: str,
    statement: str,
    patch: list[dict[str, Any]],
) -> list[str]:
    blocks: list[str] = []
    candidate_ops = [op.get("op") for op in patch]
    candidate_norm_title = novelty_norm(title)
    candidate_norm_statement = novelty_norm(statement)
    candidate_norm_targets = ",".join(
        sorted(novelty_norm(target) for target in _patch_target_values(patch))
    )
    closest: tuple[float, str, str] | None = None

    for pid, prop in sorted(state.get("proposals", {}).items()):
        prior_ops = [op.get("op") for op in prop.get("patch", [])]
        prior_norm_title = novelty_norm(prop.get("title", ""))
        prior_norm_statement = novelty_norm(prop.get("statement", ""))
        prior_norm_targets = ",".join(
            sorted(novelty_norm(target) for target in _patch_target_values(prop.get("patch", [])))
        )
        title_ratio = difflib.SequenceMatcher(None, candidate_norm_title, prior_norm_title).ratio()
        statement_ratio = difflib.SequenceMatcher(None, candidate_norm_statement, prior_norm_statement).ratio()
        score = max(title_ratio, statement_ratio)
        if closest is None or score > closest[0]:
            closest = (score, pid, prop.get("title", ""))
        same_ops = candidate_ops == prior_ops
        same_targets = candidate_norm_targets == prior_norm_targets
        candidate_file_text = combined_file_patch(patch)
        prior_file_text = combined_file_patch(prop.get("patch", []))
        file_similarity = (
            difflib.SequenceMatcher(None, candidate_file_text, prior_file_text).ratio()
            if "patch_file" in candidate_ops and "patch_file" in prior_ops
            else 1.0
        )
        state_only = "patch_file" not in candidate_ops and "patch_file" not in prior_ops
        if same_ops and same_targets and (
            candidate_norm_title == prior_norm_title
            or candidate_norm_statement == prior_norm_statement
            or (title_ratio >= 0.92 and statement_ratio >= 0.85)
        ) and (state_only or file_similarity >= 0.99):
            blocks.append(
                f"semantically equivalent to prior proposal {pid}: "
                f"{prop.get('title', '')[:80]}"
            )

    recent_applied = [
        prop for _, prop in sorted(state.get("proposals", {}).items())
        if prop.get("status") == "applied"
    ][-20:]
    op_counts: dict[str, int] = {}
    for prop in recent_applied:
        for op in prop.get("patch", []):
            name = op.get("op", "?")
            op_counts[name] = op_counts.get(name, 0) + 1
    top_op = max(op_counts, key=op_counts.get) if op_counts else None
    top_ratio = (op_counts[top_op] / sum(op_counts.values())) if top_op else 0.0
    state_only_ops = {
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
    if (
        top_op in state_only_ops
        and top_ratio > 0.8
        and candidate_ops
        and all(op == top_op for op in candidate_ops)
    ):
        blocks.append(
            f"patch-op concentration guard: {top_op} is {top_ratio:.0%} of the "
            "last 20 applied patches; add a file patch or a second op that changes capability"
        )
    if closest:
        print(f"A novelty check: closest prior is {closest[1]} "
              f"(score {closest[0]:.2f}): {closest[2][:80]}")
    return blocks


def cmd_propose(args: argparse.Namespace) -> int:
    state = load_state()
    patch = load_patch(args)
    if not patch:
        raise SystemExit("BLOCKED: a proposal requires --patch-file or --patch")
    targets = [t.strip() for t in (args.targets or "").split(",") if t.strip()]
    if file_patch_ops(patch) and not targets:
        raise SystemExit(
            "BLOCKED: patch_file proposals must declare one or two graph target "
            "nodes via --targets so file changes have an invalidation-impact claim"
        )
    if args.retro:
        retro = state.get("retrospectives", {}).get(args.retro)
        if not retro:
            raise SystemExit(f"Unknown retrospective {args.retro}")
        if retro.get("status") != "open":
            raise SystemExit(
                f"BLOCKED: retrospective {args.retro} is {retro.get('status')}, not open"
            )
    novelty = novelty_blocks(state, args.title, args.statement, patch)
    if novelty:
        raise SystemExit(
            "BLOCKED: semantic-novelty guard:\n  " + "\n  ".join(novelty)
        )
    blocks = validate_patch(state, patch)
    if blocks:
        raise SystemExit("BLOCKED: invalid patch:\n  " + "\n  ".join(blocks))
    pid = next_id(state, "P", "proposal")
    proposal = {
        "id": pid,
        "track": args.track,
        "title": args.title,
        "statement": args.statement,
        "targets": targets,
        "patch": patch,
        "verification_command": args.verification,
        "retrospective_id": args.retro or None,
        "status": "proposed",
        "critic": None,
        "verification": None,
        "revisions": [],
        "proposed_at": now_iso(),
    }
    state["proposals"][pid] = proposal
    append_event(
        state,
        "proposal_proposed",
        {
            "track": args.track,
            "proposal": pid,
            "title": args.title,
            "retrospective": args.retro or None,
        },
    )
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


def run_verification_command(
    command: str,
    timeout: int,
    root: Path = ROOT,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["RESEARCH_CLOSURE_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            env=env,
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


def _sandbox_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", ".ssh_github", "__pycache__"}
    if Path(directory).name == ".research":
        ignored.add("auto_snapshots")
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def verification_sandbox_for(prop: dict[str, Any]) -> tempfile.TemporaryDirectory | None:
    if not file_patch_ops(prop.get("patch", [])):
        return None
    td = tempfile.TemporaryDirectory(prefix="auto-research-verify-")
    try:
        sandbox = Path(td.name) / "work"
        shutil.copytree(ROOT, sandbox, ignore=_sandbox_ignore)
        apply_file_patch_text(combined_file_patch(prop.get("patch", [])), sandbox)
        return td
    except Exception:
        td.cleanup()
        raise


def _verification_failure_record(message: str, command: str) -> dict[str, Any]:
    return {
        "exit_code": 1,
        "timeout": False,
        "stdout_sha256": "",
        "stdout_bytes": 0,
        "stderr_tail": message[-2000:],
        "at": now_iso(),
        "sandbox": "blocked-before-run",
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

    sandbox: tempfile.TemporaryDirectory | None = None
    sandbox_label = None
    try:
        if file_patch_ops(prop.get("patch", [])):
            blocks = validate_patch(state, prop.get("patch", []))
            if blocks:
                record = _verification_failure_record(
                    "patch validation failed before sandbox run:\n" + "\n".join(blocks),
                    command,
                )
            else:
                try:
                    sandbox = verification_sandbox_for(prop)
                    sandbox_root = Path(sandbox.name) / "work"
                    record = run_verification_command(
                        command,
                        args.timeout,
                        root=sandbox_root,
                    )
                    record["sandbox"] = "temporary-copy"
                    sandbox_label = "temporary-copy"
                except Exception as exc:
                    record = _verification_failure_record(
                        f"could not build patch verification sandbox: {exc}",
                        command,
                    )
        else:
            record = run_verification_command(command, args.timeout)
    finally:
        if sandbox is not None:
            sandbox.cleanup()

    record.update({
        "track": args.track,
        "proposal": args.proposal,
        "level": "hard",
        "command": command,
    })
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
    if sandbox_label:
        prop["verification"]["sandbox"] = sandbox_label
    passed = not record.get("timeout") and record.get("exit_code") == 0
    prop["status"] = "verified" if passed else "failed_verification"
    outcome = "verified" if passed else "failed_verification"
    append_event(
        state,
        "proposal_verified",
        {
            "track": args.track,
            "proposal": args.proposal,
            "outcome": outcome,
            "sandbox": sandbox_label,
        },
    )
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

    retro_id = prop.get("retrospective_id")
    retro = state.get("retrospectives", {}).get(retro_id or "")
    if retro_id and retro and retro.get("status") == "open":
        retro["status"] = "converted"
        retro["proposal_id"] = args.proposal
        retro["resolved_at"] = now_iso()
        append_event(
            state,
            "retrospective_converted",
            {"retrospective": retro_id, "proposal": args.proposal},
        )
        save_state(state, "retrospective_converted")
        print(f"Retrospective {retro_id} converted by {args.proposal}")
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
    return snapshot_files()


def cmd_snapshot(_: argparse.Namespace) -> int:
    state = load_state()
    path = snapshot_state(state, "manual")
    if not path:
        raise SystemExit("BLOCKED: could not write snapshot")
    print(f"Snapshot: {path}")
    return 0


def cmd_snapshot_stats(_: argparse.Namespace) -> int:
    state = load_state()
    files = list_snapshots()
    total = sum(path.stat().st_size for path in files)
    index = load_snapshot_index()
    print("SNAPSHOT JOURNAL")
    print(f"  files        : {len(files)}")
    print(f"  bytes        : {total}")
    print(f"  indexed      : {len(index.get('files', {}))}")
    print(f"  retention    : {json.dumps(state.get('snapshot_retention'), sort_keys=True)}")
    archive = SNAP_DIR / "archive"
    if archive.exists():
        archive_bytes = sum(p.stat().st_size for p in archive.rglob("*") if p.is_file())
        print(f"  archive bytes: {archive_bytes}")
    return 0


def cmd_snapshot_prune(_: argparse.Namespace) -> int:
    state = load_state()
    index = load_snapshot_index()
    before = len(list_snapshots())
    prune_snapshots(state, index)
    after = len(list_snapshots())
    print(f"Snapshot prune: {before} -> {after}")
    return 0


def age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.astimezone()
    return max(0.0, (datetime.now().astimezone() - then).total_seconds())


def build_health_report(state: dict[str, Any]) -> dict[str, Any]:
    blocks, warnings = validate(state)
    limits = state.get("health_limits", copy.deepcopy(DEFAULT_HEALTH_LIMITS))
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    events = state.get("events", [])

    active_statuses = ("proposed", "challenged", "passed_critic", "verified")
    open_props = [
        (pid, prop) for pid, prop in proposals.items()
        if prop.get("status") in active_statuses
    ]
    open_ages = [
        age_seconds(prop.get("proposed_at")) for _, prop in open_props
    ]
    open_ages = [age for age in open_ages if age is not None]
    oldest_open_age = max(open_ages) if open_ages else 0.0

    last_self_test = state.get("last_self_test") or {}
    self_test_passed = bool(last_self_test.get("passed"))
    self_test_age = age_seconds(last_self_test.get("at"))

    last_event_age = age_seconds(events[-1].get("at")) if events else None

    draft_deprecated = sum(
        1 for node in nodes.values()
        if node.get("status") in ("draft", "deprecated")
    )

    ordered = sorted(proposals.items(), key=lambda item: int(item[0][2:]) if item[0].startswith("P-") and item[0][2:].isdigit() else 0)
    reject_streak = 0
    for _, prop in reversed(ordered):
        if prop.get("status") == "rejected":
            reject_streak += 1
        else:
            break

    recent = ordered[-20:]
    recent_tracks = [prop.get("track") for _, prop in recent]
    track_a = recent_tracks.count("A")
    track_b = recent_tracks.count("B")
    track_balance_ok = len(recent_tracks) < 4 or (track_a > 0 and track_b > 0)

    op_counts: dict[str, int] = {}
    recent_applied = [prop for _, prop in reversed(ordered) if prop.get("status") == "applied"][:20]
    for prop in recent_applied:
        for op in prop.get("patch", []):
            name = op.get("op", "?")
            op_counts[name] = op_counts.get(name, 0) + 1
    top_op = max(op_counts, key=op_counts.get) if op_counts else None
    top_ratio = (op_counts[top_op] / sum(op_counts.values())) if top_op else 0.0

    snap_files = list_snapshots()
    snapshot_bytes = sum(path.stat().st_size for path in snap_files)
    state_bytes = STATE_PATH.stat().st_size if STATE_PATH.exists() else 0

    open_retros = [
        (rid, retro) for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    ]
    retro_ages = [age_seconds(retro.get("created_at")) for _, retro in open_retros]
    retro_ages = [age for age in retro_ages if age is not None]
    oldest_retro_age = max(retro_ages) if retro_ages else 0.0

    checks: dict[str, Any] = {
        "structural_valid": {"ok": not blocks, "blocks": blocks, "warnings": warnings},
        "open_proposals": {
            "ok": oldest_open_age <= float(limits.get("max_open_proposal_age_hours", 24)) * 3600,
            "count": len(open_props),
            "oldest_age_seconds": oldest_open_age,
        },
        "last_self_test": {
            "ok": self_test_passed and (self_test_age is None or self_test_age <= float(limits.get("max_self_test_age_hours", 24)) * 3600),
            "passed": self_test_passed,
            "age_seconds": self_test_age,
        },
        "last_event": {
            "ok": last_event_age is not None and last_event_age <= float(limits.get("max_last_event_age_hours", 6)) * 3600,
            "age_seconds": last_event_age,
        },
        "draft_deprecated": {
            "ok": draft_deprecated == 0,
            "count": draft_deprecated,
        },
        "reject_streak": {
            "ok": reject_streak < int(limits.get("max_reject_streak", 5)),
            "streak": reject_streak,
        },
        "recent_track_balance": {
            "ok": track_balance_ok,
            "A": track_a,
            "B": track_b,
        },
        "patch_op_concentration": {
            "ok": top_ratio <= 0.8,
            "top_op": top_op,
            "ratio": round(top_ratio, 3),
        },
        "snapshot_journal": {
            "ok": snapshot_bytes <= float(limits.get("max_snapshot_bytes", 2_000_000_000)),
            "files": len(snap_files),
            "bytes": snapshot_bytes,
        },
        "state_size": {
            "ok": state_bytes <= float(limits.get("max_state_bytes", 100_000_000)),
            "bytes": state_bytes,
        },
        "open_retrospectives": {
            "ok": not open_retros or oldest_retro_age <= float(limits.get("max_retro_age_days", 7)) * 86400,
            "count": len(open_retros),
            "oldest_age_seconds": oldest_retro_age,
        },
    }
    critical_names = {
        "structural_valid",
        "open_proposals",
        "last_self_test",
        "last_event",
        "reject_streak",
        "snapshot_journal",
        "state_size",
    }
    critical = [name for name, check in checks.items() if not check.get("ok") and name in critical_names]
    warning_names = [name for name, check in checks.items() if not check.get("ok") and name not in critical_names]
    report = {
        "generated_at": now_iso(),
        "ok": not critical,
        "checks": checks,
        "critical": critical,
        "warnings": warning_names,
    }
    return report


def print_health_report(report: dict[str, Any]) -> None:
    print("AUTO-RESEARCH HEALTH")
    for name, check in report["checks"].items():
        mark = "OK " if check.get("ok") else "BAD"
        extras = {k: v for k, v in check.items() if k not in ("ok", "blocks", "warnings")}
        detail = json.dumps(extras, sort_keys=True, default=str)
        print(f"  [{mark}] {name} {detail}")
    if report["critical"]:
        print("CRITICAL: " + ", ".join(report["critical"]))
    if report.get("warnings"):
        print("WARNINGS: " + ", ".join(report["warnings"]))
    if not report["critical"] and not report.get("warnings"):
        print("HEALTH OK")


def cmd_health(args: argparse.Namespace) -> int:
    state = load_state()
    report = build_health_report(state)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_health_report(report)
    if report["critical"]:
        return 2
    if args.strict and report.get("warnings"):
        return 1
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    while True:
        state = load_state()
        report = build_health_report(state)
        print_health_report(report)
        if args.once:
            if report["critical"]:
                return 2
            if args.strict and report.get("warnings"):
                return 1
            return 0
        time.sleep(max(1, args.interval))


def cmd_rollback(args: argparse.Namespace) -> int:
    files = list_snapshots()
    if not files:
        raise SystemExit("No snapshots available")
    idx = args.to - 1
    if idx < 0 or idx >= len(files):
        raise SystemExit(f"Snapshot index must be 1..{len(files)}")
    restored = json.loads(files[idx].read_text())
    current = load_state()
    restore_files_to_target(current, restored)
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


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    nodes = state.get("nodes", {})
    by_status: dict[str, int] = {}
    for node in nodes.values():
        by_status[node.get("status", "?")] = by_status.get(node.get("status", "?"), 0) + 1
    proposal_rows = [
        {
            "id": pid,
            "track": prop.get("track", "?"),
            "status": prop.get("status", "?"),
            "title": prop.get("title", ""),
        }
        for pid, prop in sorted(state.get("proposals", {}).items())
    ]
    open_retros = sorted(
        rid for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    )
    if args.json:
        print(json.dumps({
            "mode": "status",
            "meta_goal": state.get("meta_goal", {}).get("statement"),
            "nodes": len(nodes),
            "edges": len(state.get("edges", [])),
            "snapshots": len(list_snapshots()),
            "status": by_status,
            "proposals": len(state.get("proposals", {})),
            "proposal_rows": proposal_rows,
            "retrospectives": len(state.get("retrospectives", {})),
            "open_retrospectives": open_retros,
            "file_backups": len(state.get("file_backups", [])),
            "last_self_test": state.get("last_self_test"),
        }, indent=2, sort_keys=True))
        return 0

    print("SELF-EVOLVED RESEARCH HARNESS")
    print(f"L0 meta-goal: {state.get('meta_goal', {}).get('statement')}")
    print(f"Nodes: {len(nodes)}  Edges: {len(state.get('edges', []))}  Snapshots: {len(list_snapshots())}")
    print(f"Status: {by_status}")
    for nid, node in sorted(nodes.items()):
        if nid == META_GOAL_ID:
            continue
        print(f"  {nid:8s} {node.get('type','?'):14s} {node.get('layer','?'):3s} "
              f"{node.get('status','?'):11s} trust={node.get('trust', 0):.2f}")
    print(f"Proposals: {len(state.get('proposals', {}))}")
    for pid, prop in sorted(state.get("proposals", {}).items()):
        print(f"  {pid:8s} [{prop.get('track','?')}] {prop.get('status','?'):18s} {prop.get('title','')[:60]}")
    print(f"Retrospectives: {len(state.get('retrospectives', {}))} "
          f"(open: {open_retros or 'none'})  File backups: {len(state.get('file_backups', []))}")
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
        active_retro_ids = {
            prop.get("retrospective_id")
            for prop in state.get("proposals", {}).values()
            if prop.get("retrospective_id")
            and prop.get("status") in ("proposed", "challenged", "passed_critic", "verified")
        }
        open_retros = sorted(
            rid for rid, retro in state.get("retrospectives", {}).items()
            if retro.get("status") == "open" and rid not in active_retro_ids
        )
        if open_retros:
            out.append(f"retro next  # convert {open_retros[0]} into a local proposal")
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
    if args.since:
        try:
            since = datetime.fromisoformat(args.since)
            if since.tzinfo is None:
                since = since.astimezone()
        except ValueError:
            raise SystemExit(
                "BLOCKED: invalid --since timestamp " + repr(args.since)
            )
        filtered = []
        for e in events:
            try:
                at = datetime.fromisoformat(e.get("at", ""))
                if at.tzinfo is None:
                    at = at.astimezone()
            except ValueError:
                continue
            if at >= since:
                filtered.append(e)
        events = filtered
    if args.tail is not None and args.tail > 0:
        events = events[-args.tail:]
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


def _retro_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        raw = json.loads(Path(args.file).read_text())
        if not isinstance(raw, dict):
            raise SystemExit("BLOCKED: retrospective file must contain a JSON object")
        return raw
    if not args.observation or not args.observation.strip():
        raise SystemExit("BLOCKED: --observation is required unless --file is used")
    return {
        "observation": args.observation,
        "class": args.retro_class,
        "source": args.source,
        "evidence": list(args.evidence or []),
        "suggested": {
            "title": args.suggested_title,
            "targets": [
                t.strip() for t in (args.suggested_targets or "").split(",") if t.strip()
            ],
            "patch_intent": args.suggested_patch,
            "verification": args.suggested_verification,
        },
    }


def cmd_retro_add(args: argparse.Namespace) -> int:
    state = load_state()
    raw = _retro_from_args(args)
    observation = raw.get("observation")
    if not isinstance(observation, str) or not observation.strip():
        raise SystemExit("BLOCKED: retrospective observation must be a non-empty string")
    rid = f"R-{int(state.get('counters', {}).get('retrospective', 0)) + 1:03d}"

    def mutate(st: dict[str, Any]) -> None:
        next_id(st, "R", "retrospective")
        retro = {
            "id": rid,
            "created_at": now_iso(),
            "class": raw.get("class", "other"),
            "source": raw.get("source", "unknown"),
            "observation": observation,
            "evidence": list(raw.get("evidence") or []),
            "suggested": raw.get("suggested") or {},
            "status": "open",
            "proposal_id": None,
            "resolved_at": None,
            "resolution_note": None,
        }
        st.setdefault("retrospectives", {})[rid] = retro

    mutate_validated(
        state,
        "retrospective_added",
        {"retrospective": rid, "class": raw.get("class", "other")},
        mutate,
    )
    print(f"Added {rid}: {observation[:80]}")
    return 0


def cmd_retro_import(args: argparse.Namespace) -> int:
    state = load_state()
    raw = json.loads(Path(args.file).read_text())
    if not isinstance(raw, list) or not raw:
        raise SystemExit("BLOCKED: retro import file must contain a non-empty JSON array")
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("observation"):
            raise SystemExit(f"BLOCKED: retro import item {i} must have an observation")
    next_number = int(state.get("counters", {}).get("retrospective", 0)) + 1
    ids = [f"R-{next_number + i:03d}" for i in range(len(raw))]

    def mutate(st: dict[str, Any]) -> None:
        for rid, item in zip(ids, raw):
            next_id(st, "R", "retrospective")
            st.setdefault("retrospectives", {})[rid] = {
                "id": rid,
                "created_at": now_iso(),
                "class": item.get("class", "other"),
                "source": item.get("source", "unknown"),
                "observation": item["observation"],
                "evidence": list(item.get("evidence") or []),
                "suggested": item.get("suggested") or {},
                "status": "open",
                "proposal_id": None,
                "resolved_at": None,
                "resolution_note": None,
            }

    mutate_validated(
        state,
        "retrospective_imported",
        {"retrospectives": ids, "count": len(ids)},
        mutate,
    )
    print(f"Imported {len(ids)} retrospectives: {', '.join(ids)}")
    return 0


def cmd_retro_list(args: argparse.Namespace) -> int:
    state = load_state()
    retros = state.get("retrospectives", {})
    selected = retros if args.all else {
        rid: retro for rid, retro in retros.items()
        if retro.get("status") == "open"
    }
    print(f"RETROSPECTIVES ({len(selected)})")
    for rid in sorted(selected):
        retro = selected[rid]
        print(f"  {rid:8s} [{retro.get('status','?'):10s}] "
              f"{retro.get('class','?'):14s} {retro.get('observation','')[:70]}")
        if retro.get("proposal_id"):
            print(f"            proposal={retro['proposal_id']}")
    if not selected:
        print("  (none)")
    return 0


def active_proposals_for_retro(state: dict[str, Any], rid: str) -> list[str]:
    return sorted(
        pid for pid, prop in state.get("proposals", {}).items()
        if prop.get("retrospective_id") == rid
        and prop.get("status") in ("proposed", "challenged", "passed_critic", "verified")
    )


def cmd_retro_next(args: argparse.Namespace) -> int:
    state = load_state()
    open_retros = sorted(
        (rid, retro) for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    )
    if not open_retros:
        print("NEXT RETROSPECTIVE: none open")
        return 0
    rid, retro = open_retros[0]
    suggested = retro.get("suggested") or {}
    print(f"NEXT RETROSPECTIVE: {rid}")
    print(f"  class      : {retro.get('class')}")
    print(f"  source     : {retro.get('source')}")
    print(f"  observation: {retro.get('observation')}")
    evidence = retro.get("evidence") or []
    if evidence:
        print("  evidence   :")
        for item in evidence[:6]:
            print(f"    - {item}")
    print(f"  suggested title       : {suggested.get('title') or '-'}")
    print(f"  suggested targets     : {suggested.get('targets') or '-'}")
    print(f"  suggested patch_intent: {suggested.get('patch_intent') or '-'}")
    print(f"  suggested verification: {suggested.get('verification') or '-'}")
    active = active_proposals_for_retro(state, rid)
    if active:
        print(f"  active proposal       : {active[0]} (wait for B)")
    else:
        print("A must convert this item into a proposal or record why it is blocked.")
    return 0


def cmd_retro_close(args: argparse.Namespace) -> int:
    state = load_state()
    retro = state.get("retrospectives", {}).get(args.id)
    if not retro:
        raise SystemExit(f"Unknown retrospective {args.id}")
    if retro.get("status") != "open":
        raise SystemExit(f"BLOCKED: retrospective {args.id} is {retro.get('status')}, not open")
    if args.disposition == "converted":
        if not args.proposal or args.proposal not in state.get("proposals", {}):
            raise SystemExit("BLOCKED: converted disposition requires --proposal with a valid proposal id")
        if state["proposals"][args.proposal].get("status") != "applied":
            raise SystemExit("BLOCKED: converted disposition requires an applied proposal")

    def mutate(st: dict[str, Any]) -> None:
        item = st["retrospectives"][args.id]
        item["status"] = args.disposition
        item["proposal_id"] = args.proposal or item.get("proposal_id")
        item["resolved_at"] = now_iso()
        item["resolution_note"] = args.note or ""

    mutate_validated(
        state,
        "retrospective_closed",
        {
            "retrospective": args.id,
            "disposition": args.disposition,
            "proposal": args.proposal or None,
        },
        mutate,
    )
    print(f"{args.id}: {args.disposition}")
    return 0


def cmd_patch_make(args: argparse.Namespace) -> int:
    target = (ROOT / args.target).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    root_resolved = ROOT.resolve()
    if not target.is_relative_to(root_resolved):
        raise SystemExit("BLOCKED: --target must be inside the repository")
    if not candidate.exists():
        raise SystemExit(f"BLOCKED: candidate file not found: {candidate}")
    rel = target.relative_to(root_resolved).as_posix()
    block = validate_patch_path(rel)
    if block:
        raise SystemExit(f"BLOCKED: {block}")
    if target == candidate:
        raise SystemExit("BLOCKED: target and candidate must be different files")

    old_text = target.read_text() if target.exists() else ""
    new_text = candidate.read_text()
    fromfile = f"a/{rel}" if target.exists() else "/dev/null"
    tofile = f"b/{rel}" if target.exists() else f"b/{rel}"
    diff_lines = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
    )
    diff = "".join(diff_lines)
    if not diff:
        raise SystemExit("BLOCKED: candidate is identical to target; no patch generated")
    op: dict[str, Any] = {
        "op": "patch_file",
        "path": rel,
        "patch": diff,
        "sha256_after": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
    }
    if target.exists():
        op["sha256_before"] = hashlib.sha256(old_text.encode("utf-8")).hexdigest()

    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = ROOT / out
    if args.append and out.exists():
        existing = json.loads(out.read_text())
        if not isinstance(existing, list):
            raise SystemExit("BLOCKED: --append requires an existing JSON array")
        existing.append(op)
        out.write_text(json.dumps(existing, indent=2) + "\n")
    else:
        out.write_text(json.dumps([op], indent=2) + "\n")
    print(f"Patch written: {out} (target={rel})")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    state = load_state()
    report = build_health_report(state)
    blocks, warnings = validate(state)
    print("AUTO-RESEARCH LAUNCH PREFLIGHT")
    print(f"  structural valid : {'PASS' if not blocks else 'BLOCKED'}")
    for block in blocks[:5]:
        print(f"    - {block}")
    print(f"  health           : {'OK' if report['ok'] else 'CRITICAL: ' + ', '.join(report['critical'])}")
    for warning in report.get("warnings", []):
        print(f"    warning: {warning}")

    role = args.role
    if role == "auto":
        b_queue = [
            pid for pid, prop in state.get("proposals", {}).items()
            if prop.get("status") in ("proposed", "passed_critic", "verified")
        ]
        role = "B" if b_queue else "A"
    print(f"  selected role    : {role}")

    harness = args.harness
    executable: str | None = None
    preset_ready = True
    if harness == "dsh":
        executable = shutil.which("dsh") or shutil.which("npx")
        preset = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / ".agent-presets" / "auto-research-minimal"
        preset_ready = preset.exists()
    elif harness == "claude":
        executable = shutil.which("claude")
    elif harness == "codex":
        executable = shutil.which("codex")
    elif harness == "manual":
        executable = "manual"
    print(f"  harness          : {harness}")
    print(f"  executable       : {executable or 'not found'}")
    if harness == "dsh":
        print(f"  dsh preset ready : {preset_ready}")
        if not preset_ready:
            print("    install: cp -R dsh/agent-presets/auto-research-minimal "
                  "${DSH_HOME:-$HOME/.dsh}/.agent-presets/")

    if harness == "dsh":
        command = f"cd {ROOT} && dsh web"
    elif harness == "claude":
        command = f"cd {ROOT} && claude"
    elif harness == "codex":
        command = f"cd {ROOT} && codex"
    else:
        command = f"cd {ROOT} && start your agent"

    print("  launch command   : " + command)
    print("  session prompt   : run ab-status, ab-next, retro next;")
    print("                     A converts the first open retrospective or proposes one local novel patch;")
    print("                     B criticises, verifies in the patched sandbox, applies, revalidates, self-tests.")

    if not args.dry_run:
        if blocks:
            raise SystemExit("BLOCKED: cannot launch with invalid auto-research state")
        if report["critical"]:
            raise SystemExit("BLOCKED: cannot launch while health is critical")
        if harness != "manual" and not executable:
            raise SystemExit(f"BLOCKED: {harness} executable not found on PATH")
        append_event(
            state,
            "auto_research_launch",
            {"harness": harness, "role": role, "command": command},
        )
        save_state(state, "auto_research_launch")
        print("Launch recorded in the auto-research event log.")
    return 0


def cmd_a_brief(_: argparse.Namespace) -> int:
    state = load_state()
    index = rebuild_snapshot_index()
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    retros = state.get("retrospectives", {})

    open_props = sorted(
        pid for pid, prop in proposals.items()
        if prop.get("status") in ("proposed", "challenged", "passed_critic", "verified")
    )
    open_retros = sorted(
        rid for rid, retro in retros.items() if retro.get("status") == "open"
    )
    drafts = sorted(nid for nid, n in nodes.items() if n.get("status") == "draft")
    deprecated = sorted(nid for nid, n in nodes.items() if n.get("status") == "deprecated")

    print("A BRIEF")
    print(f"  open proposals    : {open_props or 'none'}")
    print(f"  open retrospectives: {open_retros or 'none'}")
    print(f"  draft nodes       : {drafts or 'none'}")
    print(f"  deprecated nodes  : {deprecated or 'none'}")
    snapshots = list_snapshots()
    print(f"  snapshots         : {len(snapshots)} files, {len(index.get('files', {}))} indexed")
    print("  patch vocabulary  : " + ", ".join(sorted(PATCH_OPS)))
    print("  protected paths   : " + ", ".join(sorted(PROTECTED_PATCH_FILES | set(PROTECTED_PATCH_PREFIXES))))
    print("  last proposals    :")
    for pid in sorted(proposals, key=lambda x: int(x[2:]) if x.startswith("P-") and x[2:].isdigit() else 0)[-5:]:
        prop = proposals[pid]
        ops = ",".join(op.get("op", "?") for op in prop.get("patch", []))
        print(f"    {pid} [{prop.get('track','?')}] {prop.get('status','?'):12s} ops={ops or '-'} {prop.get('title','')[:70]}")
    print("  next A action     : run retro next; convert the first open item or use a-check before propose")
    return 0


def cmd_a_check(args: argparse.Namespace) -> int:
    state = load_state()
    patch = load_patch(args)
    if not patch:
        raise SystemExit("BLOCKED: a-check requires --patch-file or --patch")
    targets = [item.strip() for item in (args.targets or "").split(",") if item.strip()]
    print("A PREFLIGHT CHECK")
    print(f"  title      : {args.title or '(not provided)'}")
    print(f"  targets    : {targets or 'none declared'}")
    print(f"  patch ops  : {[op.get('op') for op in patch]}")
    file_ops = file_patch_ops(patch)
    if file_ops:
        try:
            paths = file_patch_paths(combined_file_patch(patch), ROOT)
        except ValueError as exc:
            paths = []
            print(f"  file paths : BLOCKED: {exc}")
        else:
            print(f"  file paths : {paths}")
    if args.retro:
        retro = state.get("retrospectives", {}).get(args.retro)
        if not retro:
            print(f"  retro      : UNKNOWN {args.retro}")
        elif retro.get("status") != "open":
            print(f"  retro      : {args.retro} is {retro.get('status')}, not open")
        else:
            print(f"  retro      : {args.retro} open")
    else:
        print("  retro      : none linked")

    novelty = novelty_blocks(state, args.title or "", args.statement or "", patch)
    print(f"  novelty    : {'PASS' if not novelty else 'BLOCKED'}")
    for block in novelty:
        print(f"    - {block}")

    blocks = validate_patch(state, patch)
    print(f"  patch      : {'PASS' if not blocks else 'BLOCKED'}")
    for block in blocks[:6]:
        print(f"    - {block}")

    origins = patch_origins(state, patch) | set(targets)
    affected = sorted(descendants_dependency(state, origins))
    print(f"  invalidation impact estimate: {affected or 'none'}")

    if file_ops:
        print("  verification note: B will run the command in an isolated temporary copy with the patch applied")
    if not args.verification:
        print("  verification command: MISSING (propose will still require one for verify)")
    else:
        print(f"  verification command: {args.verification}")

    if novelty or blocks:
        return 2
    print("A CHECK PASS: ready for propose")
    return 0


def cmd_verification_stats(args: argparse.Namespace) -> int:
    state = load_state()
    records = state.get("verifications", [])
    total = len(records)
    passed = sum(1 for r in records if not r.get("timeout") and r.get("exit_code") == 0)
    failed = total - passed
    timeouts = sum(1 for r in records if r.get("timeout"))
    by_level: dict[str, int] = {}
    for r in records:
        level = r.get("level", "unknown")
        by_level[level] = by_level.get(level, 0) + 1
    self_test_streak = 0
    for r in reversed(records):
        if r.get("level") != "self_test":
            continue
        if not r.get("timeout") and r.get("exit_code") == 0:
            self_test_streak += 1
        else:
            break
    data = {
        "mode": "verification-stats",
        "total": total,
        "passed": passed,
        "failed": failed,
        "timeouts": timeouts,
        "by_level": by_level,
        "self_test_streak": self_test_streak,
        "last_self_test": state.get("last_self_test"),
    }
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print("VERIFICATION LEDGER")
    print(f"  total={total} passed={passed} failed={failed} timeouts={timeouts}")
    print(f"  by_level={by_level}")
    print(f"  self_test_streak={self_test_streak}")
    return 0


def cmd_proposal_show(args: argparse.Namespace) -> int:
    state = load_state()
    prop = state.get("proposals", {}).get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if args.json:
        print(json.dumps(prop, indent=2, sort_keys=True))
        return 0
    print(f"PROPOSAL {args.proposal}")
    print(f"  track      : {prop.get('track')}")
    print(f"  status     : {prop.get('status')}")
    print(f"  title      : {prop.get('title')}")
    print(f"  statement  : {prop.get('statement')}")
    print(f"  targets    : {prop.get('targets')}")
    print(f"  retro      : {prop.get('retrospective_id')}")
    print(f"  patch ops  : {[op.get('op') for op in prop.get('patch', [])]}")
    print("  patch      :")
    print(json.dumps(prop.get("patch", []), indent=4))
    print(f"  verification command: {prop.get('verification_command')}")
    print(f"  critic     : {json.dumps(prop.get('critic') or {}, sort_keys=True)}")
    print(f"  verification record : {json.dumps(prop.get('verification') or {}, sort_keys=True)}")
    print(f"  modification node   : {prop.get('modification_node')}")
    print(f"  affected nodes      : {prop.get('affected_nodes')}")
    return 0


def cmd_ab_status(args: argparse.Namespace) -> int:
    state = load_state()
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    drafts = sorted(nid for nid, n in nodes.items() if n.get("status") == "draft")
    deprecated = sorted(nid for nid, n in nodes.items() if n.get("status") == "deprecated")
    a_props = [pid for pid, prop in proposals.items() if prop.get("track", "A") == "A"]
    b_queue = sorted(
        pid for pid, prop in proposals.items()
        if prop.get("status") in ("proposed", "passed_critic", "verified")
    )
    open_retros = sorted(
        rid for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    )
    if args.json:
        print(json.dumps({
            "mode": "ab-status",
            "draft_nodes": drafts,
            "deprecated_nodes": deprecated,
            "a_proposals": a_props,
            "b_queue": b_queue,
            "open_retrospectives": open_retros,
            "file_backups": len(state.get("file_backups", [])),
        }, indent=2, sort_keys=True))
        return 0

    print("A/B AUTO-RESEARCH STATUS")
    print("A (fast): propose, explore, draft candidates, cheap pre-checks")
    print(f"  draft nodes      : {drafts or 'none'}")
    print(f"  deprecated nodes : {deprecated or 'none'}")
    print(f"  A proposals      : {a_props or 'none'}")
    for pid in a_props:
        prop = proposals[pid]
        print(f"    {pid:8s} {prop.get('status', '?'):18s} {prop.get('title', '')[:60]}")

    print("B (slow): critic review, hard verification, consolidation, apply")
    print(f"  B queue          : {b_queue or 'none'}")
    for pid in b_queue:
        prop = proposals[pid]
        action = {
            "proposed": "critique",
            "passed_critic": "verify",
            "verified": "apply",
        }[prop["status"]]
        print(f"    {pid:8s} {prop.get('status', '?'):18s} -> {action}")

    print(f"  open retrospectives: {open_retros or 'none'}")
    print(f"  file backups      : {len(state.get('file_backups', []))}")
    print("Loop contract")
    print("  A proposes -> B criticises -> B hard-verifies -> B applies")
    print("  -> trust decay + dependency closure -> A revises or opens a new candidate")
    return 0


def cmd_ab_next(args: argparse.Namespace) -> int:
    state = load_state()
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    a_actions: list[str] = []
    b_actions: list[str] = []
    active_retro_ids = {
        prop.get("retrospective_id")
        for prop in proposals.values()
        if prop.get("retrospective_id")
        and prop.get("status") in ("proposed", "challenged", "passed_critic", "verified")
    }
    open_retros = sorted(
        rid for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open" and rid not in active_retro_ids
    )
    if open_retros:
        a_actions.append(
            f"retro next  # then convert {open_retros[0]} into a local proposal "
            "or document why it is blocked"
        )

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
                f"--statement '<why this differs from rejected {pid}>'"
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

    if args.json:
        print(json.dumps({
            "mode": "ab-next",
            "a_actions": ["a-brief"] + a_actions[:5],
            "b_actions": b_actions[:5],
        }, indent=2, sort_keys=True))
        return 0

    print("A NEXT (fast layer)")
    print("  - a-brief")
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
    sp.add_argument("--retro", help="open retrospective id this proposal converts")
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

    sp = sub.add_parser("patch-make", help="build a patch_file op from a candidate file")
    sp.add_argument("--target", required=True, help="repo-relative target path")
    sp.add_argument("--candidate", required=True, help="candidate file path")
    sp.add_argument("--out", required=True, help="patch JSON output path")
    sp.add_argument("--append", action="store_true", help="append to an existing patch array")
    sp.set_defaults(func=cmd_patch_make)

    sp = sub.add_parser("retro", help="retrospective backlog commands")
    retro_sub = sp.add_subparsers(dest="retro_command", required=True)

    rsp = retro_sub.add_parser("add", help="add a retrospective item")
    rsp.add_argument("--file", help="JSON file with retrospective fields")
    rsp.add_argument("--observation")
    rsp.add_argument("--class", dest="retro_class", choices=RETROSPECTIVE_CLASSES,
                     default="other")
    rsp.add_argument("--source", default="unknown")
    rsp.add_argument("--evidence", action="append", default=[])
    rsp.add_argument("--suggested-title")
    rsp.add_argument("--suggested-targets")
    rsp.add_argument("--suggested-patch")
    rsp.add_argument("--suggested-verification")
    rsp.set_defaults(func=cmd_retro_add)

    rsp = retro_sub.add_parser("import", help="import a JSON array of retrospective items")
    rsp.add_argument("--file", required=True)
    rsp.set_defaults(func=cmd_retro_import)

    rsp = retro_sub.add_parser("list", help="list retrospective items")
    rsp.add_argument("--all", action="store_true", help="include closed items")
    rsp.set_defaults(func=cmd_retro_list)

    rsp = retro_sub.add_parser("next", help="show the next open retrospective item")
    rsp.set_defaults(func=cmd_retro_next)

    rsp = retro_sub.add_parser("close", help="close a retrospective item")
    rsp.add_argument("--id", required=True)
    rsp.add_argument("--disposition", required=True, choices=RETROSPECTIVE_DISPOSITIONS)
    rsp.add_argument("--proposal")
    rsp.add_argument("--note")
    rsp.set_defaults(func=cmd_retro_close)

    sp = sub.add_parser("launch", help="preflight and print an A/B launch command")
    sp.add_argument("--harness", choices=("dsh", "claude", "codex", "manual"),
                    default="dsh")
    sp.add_argument("--role", choices=("A", "B", "auto"), default="auto")
    sp.add_argument("--dry-run", action="store_true", help="do not record or block on missing tools")
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser("a-brief", help="compact A-layer session brief")
    sp.set_defaults(func=cmd_a_brief)

    sp = sub.add_parser("a-check", help="read-only A preflight before propose")
    sp.add_argument("--title")
    sp.add_argument("--statement")
    sp.add_argument("--targets", default="")
    sp.add_argument("--patch-file")
    sp.add_argument("--patch")
    sp.add_argument("--verification")
    sp.add_argument("--retro")
    sp.set_defaults(func=cmd_a_check)

    sp = sub.add_parser("verification-stats", help="summarize the hard-verification ledger")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_verification_stats)

    sp = sub.add_parser("proposal-show", help="inspect one proposal in detail")
    sp.add_argument("--proposal", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_proposal_show)

    sp = sub.add_parser("ab-status", help="show the A/B fast/slow queue")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_ab_status)

    sp = sub.add_parser("ab-next", help="show the next A and B actions")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_ab_next)

    sp = sub.add_parser("self-test", help="run the configured capability self-test")
    sp.add_argument("--command", help="override the configured self-test command")
    sp.add_argument("--timeout", type=int, default=120)
    sp.set_defaults(func=cmd_self_test)

    sp = sub.add_parser("snapshot", help="write a manual snapshot")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("snapshot-stats", help="show snapshot journal size and retention")
    sp.set_defaults(func=cmd_snapshot_stats)

    sp = sub.add_parser("snapshot-prune", help="apply snapshot retention policy now")
    sp.set_defaults(func=cmd_snapshot_prune)

    sp = sub.add_parser("health", help="run auto-research health checks")
    sp.add_argument("--json", action="store_true", help="emit a JSON report")
    sp.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("watch", help="watch auto-research health")
    sp.add_argument("--interval", type=int, default=30, help="seconds between checks")
    sp.add_argument("--once", action="store_true", help="run one check and exit")
    sp.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("rollback", help="roll back to a snapshot (1 = oldest)")
    sp.add_argument("--to", type=int, required=True)
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser("validate", help="run structural self-checks")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("status", help="show self-graph and proposal pipeline")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("next", help="show the next event in the loop")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("events", help="show the event log")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--tail", type=int, help="show only the last N events")
    sp.add_argument("--since", help="ISO timestamp lower bound")
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
