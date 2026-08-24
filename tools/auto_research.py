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
* inquiry-first pipeline with acceptance-time evaluator snapshots and one linked proposal
* digest-bound semantic B review: proposed -> passed_critic -> verified -> applied
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
import ctypes
import difflib
import errno
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
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
    "terminated",
    "failed_verification",
    "applied",
)
CRITIC_VERDICTS = ("pass", "challenge", "reject")
REVALIDATION_LEVELS = ("syntax", "light", "hard")
TRACKS = ("A", "B")
TRACK_ROLES = {
    "A": "fast self-inquiry: diagnose, reframe, recommend, then implement one selected recommendation",
    "B": "slow review: judge inquiries, select one recommendation, criticise, verify, assess outcomes, and apply",
}
INQUIRY_STATUSES = (
    "proposed",
    "challenged",
    "accepted",
    "implementing",
    "awaiting_outcome",
    "supported",
    "falsified",
    "inconclusive",
    "rejected",
    "implementation_failed",
    "closed_no_change",
    "converted",
)
INQUIRY_DISPOSITIONS = ("propose", "no_change")
INQUIRY_REVIEW_VERDICTS = ("pass", "challenge", "reject")
OUTCOME_VERDICTS = ("supported", "falsified", "inconclusive")
INQUIRY_TEXT_FIELDS = (
    "scope",
    "diagnosis",
    "question_before",
    "question_after",
    "why_better",
)
EVIDENCE_FIELDS = ("kind", "locator", "observation")
HYPOTHESIS_FIELDS = ("claim", "falsifier")
EVALUATION_BOUNDARY_FIELDS = (
    "mutable",
    "protected",
    "baseline",
    "measure",
    "evaluator",
    "success_rule",
    "guardrails",
    "budget",
)
OUTCOME_INPUT_FIELDS = (
    "verdict",
    "evidence",
    "artifact_locator",
    "baseline_result",
    "candidate_result",
    "budget_used",
    "guardrail_results",
    "reason",
    "next_question",
)
GUARDRAIL_RESULT_FIELDS = ("guardrail", "result", "passed")
RECOMMENDATION_FIELDS = (
    "id",
    "title",
    "target",
    "change",
    "rationale",
    "validation",
    "implementation_contract",
)
IMPLEMENTATION_CONTRACT_FIELDS = (
    "allowed_ops",
    "allowed_paths",
    "required_targets",
    "required_verification_terms",
)
INQUIRY_PAYLOAD_FIELDS = {
    *INQUIRY_TEXT_FIELDS,
    "evidence",
    "hypothesis",
    "evaluation_boundary",
    "recommendations",
    "disposition",
    "no_change_reason",
    "revisit_when",
    "selected_recommendation_id",
}
PLACEHOLDER_TEXT = {"?", "tbd", "todo", "none", "n/a", "na", "unknown"}

META_GOAL_ID = "M0"
META_GOAL_STATEMENT = "Improve this system's own auto-research capability."
FINAL_GOAL = {
    "statement": (
        "Complete a better auto-research system: an engine that can read and modify "
        "its own source files, verify changes with reproducible tests, monitor its "
        "own behavior, and improve its auto-research capability over time."
    ),
    "criteria": [
        "Prefer executable engine changes (patch_file) over declarative policy nodes.",
        "Every applied change must pass the full test suite and self-test.",
        "The system must remain able to launch, verify, apply, and monitor itself.",
        "Do not weaken M0, hard verification, or the final goal itself.",
    ],
    "immutable": True,
}

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
    "tests/protected/",
)
CORE_FILES_PROTECTED_FROM_DELETE = {
    "tools/auto_research.py",
}
def trusted_unittest_command(start_directory: str) -> str:
    """Build an isolated runner whose stdlib cannot be shadowed by candidate code."""
    code = (
        "import os,sys,unittest;"
        "root=os.environ['RESEARCH_CLOSURE_ROOT'];"
        "shadow=sorted(name for name in getattr(sys,'stdlib_module_names',()) "
        "if os.path.isfile(os.path.join(root,name+'.py')) or "
        "os.path.isfile(os.path.join(root,name,'__init__.py')));"
        "assert not shadow,'repository shadows stdlib modules: '+','.join(shadow);"
        "sys.path.append(root);"
        f"start=os.path.join(root,{start_directory!r});"
        "suite=unittest.defaultTestLoader.discover(start);"
        "expected=suite.countTestCases();"
        "result=unittest.TextTestRunner(verbosity=1).run(suite);"
        "complete=(expected>0 and result.testsRun==expected and not result.skipped);"
        "raise SystemExit(0 if result.wasSuccessful() and complete else 1)"
    )
    return f"{shlex.quote(sys.executable)} -I -c {shlex.quote(code)}"


PROTECTED_EVALUATOR_COMMAND = trusted_unittest_command("tests/protected")
TRUSTED_FROZEN_EVALUATOR_COMMAND = trusted_unittest_command("tests")
FROZEN_EVALUATOR_PATHS = ("tests",)


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
BOOTSTRAP_MANIFEST_VERSION = 1
BOOTSTRAP_SENTINEL = "tests/protected/test_auto_research_sentinels.py"
BOOTSTRAP_MANIFEST_DIR = SNAP_DIR / "bootstrap_manifests"
BOOTSTRAP_LOCK_PATH = ROOT / ".research" / "auto_research.bootstrap.lock"
DEFAULT_SNAPSHOT_RETENTION = {
    "keep_last": 25,
    "keep_labels": ["init", "modification_applied", "bootstrap_floor"],
}
DEFAULT_LAUNCH_PROMPTS = {
    "A": [
        "Inspect source, tests, and history before acting; first record a grounded inquiry or justified no_change.",
        "Reframe the question into a falsifiable comparison with a baseline, measure, protected evaluator, success rule, guardrails, fixed budget, and explicit falsifier.",
        "Recommend concrete changes, but do not emit a patch until B accepts the inquiry and selects exactly one recommendation.",
        "After selection, implement only that recommendation and bind the proposal to its inquiry.",
    ],
    "B": [
        "Review the inquiry's evidence, reframing, falsifier, and evaluator boundary before selecting any recommendation.",
        "Treat inquiry acceptance as permission to test one recommendation, never as evidence of improvement.",
        "For a linked proposal, compare the actual patch and verification against the complete selected recommendation.",
        "Hard verification proves operability, not the hypothesis; require a pinned outcome artifact and supported B assessment before application.",
    ],
}
RUNNER_TARGET_WHITELIST = [
    "A-BRIEF", "A-CHECK", "A-PRECHECK", "A-SCOPE-GUARD", "A-REVISION-LOOP",
    "A-EVIDENCE-FRESHNESS", "B-CRITIC-SPECIFICITY", "B-VERIFY-RECORD",
    "B-REVALIDATE-GATE", "B-CLOSURE-AUDIT", "B-SELF-TEST-CLOSURE",
    "NOVELTY-GUARD", "LAUNCH-PROMPTS", "RUN-SCHEDULER", "VERIFY-DRY-RUN",
    "SNAPSHOT-RETENTION", "SNAPSHOT-INDEX-CLI", "HEALTH-WATCH",
    "LAUNCH-PREFLIGHT", "AB-MONITOR-JSON", "STATUS-JSON", "PROPOSAL-INSPECT",
    "VERIFY-METRICS", "RETRO-IMPORT", "ROLLBACK-PREVIEW", "SELF-TEST-JSON",
    "WATCH-JSON", "EVENTS-MONITOR", "MONITOR-DASHBOARD",
]
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
        "self_test_command": TRUSTED_FROZEN_EVALUATOR_COMMAND,
        "snapshot_retention": copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION),
        "health_limits": copy.deepcopy(DEFAULT_HEALTH_LIMITS),
        "launch_prompts": copy.deepcopy(DEFAULT_LAUNCH_PROMPTS),
        "final_goal": copy.deepcopy(FINAL_GOAL),
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
        "legacy_proposal_ids": [],
        "legacy_file_backup_ids": [],
        "bootstrap_transition": None,
        "inquiries": {},
        "evaluator_snapshots": {},
        "active_inquiry_id": None,
        "verifications": [],
        "last_self_test": None,
        "retrospectives": {},
        "file_backups": [],
        "events": [],
        "counters": {
            "node": 1,
            "proposal": 0,
            "inquiry": 0,
            "verification": 0,
            "retrospective": 0,
        },
    }


def normalize_loaded_state(state: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an in-memory historical state without rewriting its source."""
    original_schema = state.get("schema_version", 1)
    if original_schema == 1:
        state["legacy_proposal_ids"] = sorted(state.get("proposals", {}))
        state["legacy_file_backup_ids"] = [
            file_backup_identity(entry)
            for entry in state.get("file_backups", [])
            if isinstance(entry, dict)
        ]
        state["schema_version"] = SCHEMA_VERSION
    for key, default in (
        ("events", []),
        ("verifications", []),
        ("proposals", {}),
        ("legacy_proposal_ids", []),
        ("legacy_file_backup_ids", []),
        ("bootstrap_transition", None),
        ("inquiries", {}),
        ("evaluator_snapshots", {}),
        ("active_inquiry_id", None),
        ("nodes", {}),
        ("edges", []),
        ("last_self_test", None),
        ("tracks", dict(TRACK_ROLES)),
        ("retrospectives", {}),
        ("file_backups", []),
        ("snapshot_retention", copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION)),
        ("health_limits", copy.deepcopy(DEFAULT_HEALTH_LIMITS)),
        ("launch_prompts", copy.deepcopy(DEFAULT_LAUNCH_PROMPTS)),
        ("final_goal", copy.deepcopy(FINAL_GOAL)),
    ):
        state.setdefault(key, default)
    state.setdefault("counters", {}).setdefault("inquiry", 0)
    return state


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or STATE_PATH
    if not p.exists():
        raise SystemExit(f"No auto-research state at {p}. Run: auto_research.py init")
    try:
        state = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid auto-research state: {exc}") from exc
    if not isinstance(state, dict):
        raise SystemExit("Invalid auto-research state: root must be an object")
    return normalize_loaded_state(state)


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


def snapshot_index_integrity_blocks(
    index: dict[str, Any],
) -> list[str]:
    """Report root snapshot/index drift without blessing new bytes."""
    blocks: list[str] = []
    indexed = index.get("files", {})
    if not isinstance(indexed, dict):
        return ["snapshot index files map is invalid"]
    visible: dict[str, Path] = {
        path.name: path for path in snapshot_files()
    }
    for name, path in visible.items():
        entry = indexed.get(name)
        if path.is_symlink() or not path.is_file():
            blocks.append(f"snapshot is not a regular file: {name}")
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            blocks.append(f"snapshot cannot be read: {name}: {exc}")
            continue
        if not isinstance(entry, dict):
            blocks.append(f"snapshot is missing from index: {name}")
        elif entry.get("sha256") != digest:
            blocks.append(f"snapshot digest differs from index: {name}")
    for name in sorted(set(indexed) - set(visible)):
        blocks.append(f"snapshot index references missing root file: {name}")
    return blocks


def _atomic_replace_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int | None = None,
) -> None:
    """Replace one file via an O_EXCL private sibling without name collisions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = (
            stat.S_IMODE(path.stat().st_mode)
            if path.is_file() and not path.is_symlink()
            else 0o644
        )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.auto-research-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def save_snapshot_index(index: dict[str, Any]) -> None:
    path = SNAP_DIR / SNAP_INDEX_NAME
    _atomic_replace_bytes(
        path,
        (json.dumps(index, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


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


def snapshot_state(
    state: dict[str, Any],
    label: str = "",
    *,
    prune: bool = True,
) -> Path | None:
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
        if prune:
            prune_snapshots(state, index)
        return path
    except Exception:
        return None


def save_state(
    state: dict[str, Any],
    snapshot_label: str = "",
    *,
    prune_snapshot: bool = True,
) -> Path | None:
    _atomic_replace_bytes(
        STATE_PATH,
        (json.dumps(state, indent=2, sort_keys=False) + "\n").encode("utf-8"),
    )
    return snapshot_state(state, snapshot_label, prune=prune_snapshot)


def require_snapshot_commit(
    snapshot_path: Path | None,
    state: dict[str, Any],
) -> None:
    """Require a readable, indexed rollback point before apply may commit."""
    if snapshot_path is None:
        raise RuntimeError("state committed without a rollback snapshot")
    try:
        resolved = snapshot_path.resolve(strict=True)
        resolved.relative_to(SNAP_DIR.resolve())
        payload = resolved.read_bytes()
        if json.loads(payload) != state:
            raise RuntimeError("rollback snapshot content differs from applied state")
        index = load_snapshot_index()
        entry = index.get("files", {}).get(resolved.name)
        digest = hashlib.sha256(payload).hexdigest()
        if not isinstance(entry, dict) or entry.get("sha256") != digest:
            raise RuntimeError("rollback snapshot is missing from the verified index")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"rollback snapshot commit is unreadable: {exc}") from exc


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

def _meaningful_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = re.sub(r"\s+", " ", value).strip().lower()
    words_only = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()
    if not words_only or words_only in PLACEHOLDER_TEXT:
        return False
    placeholder_prefixes = (
        "tbd ", "todo ", "unknown ", "placeholder ", "fill later",
        "to be determined", "not available",
    )
    return not words_only.startswith(placeholder_prefixes)


def _substantive_text(value: Any, minimum_chars: int = 12) -> bool:
    if not _meaningful_text(value):
        return False
    content = re.sub(r"[^\w]+", "", value, flags=re.UNICODE)
    return len(content) >= minimum_chars


def _semantic_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.lower(), flags=re.UNICODE)


def implementation_contract_blocks(raw: Any, position: str) -> list[str]:
    """Validate the machine-checkable scope B freezes with a recommendation."""
    if not isinstance(raw, dict):
        return [f"{position} must be an object"]
    blocks: list[str] = []
    extra = sorted(set(raw) - set(IMPLEMENTATION_CONTRACT_FIELDS))
    if extra:
        blocks.append(f"{position} has unknown fields: " + ", ".join(extra))
    allowed_ops = raw.get("allowed_ops")
    if not isinstance(allowed_ops, list) or not allowed_ops or not all(
        isinstance(item, str) and item in PATCH_OPS for item in allowed_ops
    ):
        blocks.append(
            f"{position}.allowed_ops must be a non-empty list from the patch vocabulary"
        )
        allowed_ops = []
    elif len(allowed_ops) != len(set(allowed_ops)):
        blocks.append(f"{position}.allowed_ops may not contain duplicates")
    for field in (
        "allowed_paths", "required_targets", "required_verification_terms",
    ):
        values = raw.get(field)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            blocks.append(f"{position}.{field} must be a list of non-empty strings")
            continue
        if len(values) != len(set(values)):
            blocks.append(f"{position}.{field} may not contain duplicates")
    required_targets = raw.get("required_targets")
    if isinstance(required_targets, list) and not required_targets:
        blocks.append(
            f"{position}.required_targets must freeze at least one graph target"
        )
    allowed_paths = raw.get("allowed_paths")
    if isinstance(allowed_paths, list):
        for path in allowed_paths:
            if not isinstance(path, str):
                continue
            normalized = path.strip().replace("\\", "/")
            if (
                not normalized
                or normalized.startswith("/")
                or ".." in Path(normalized).parts
                or normalized in (".", "./")
            ):
                blocks.append(
                    f"{position}.allowed_paths contains unsafe path {path!r}"
                )
    if "patch_file" in allowed_ops and not allowed_paths:
        blocks.append(f"{position}.allowed_paths is required for patch_file")
    terms = raw.get("required_verification_terms")
    if isinstance(terms, list) and not terms:
        blocks.append(
            f"{position}.required_verification_terms must bind verification to the recommendation"
        )
    return blocks


def recommendation_sha256(recommendation: dict[str, Any]) -> str:
    encoded = json.dumps(
        recommendation, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inquiry_payload_blocks(raw: Any) -> list[str]:
    """Validate inquiry structure without claiming that its reasoning is true."""
    if not isinstance(raw, dict):
        return ["inquiry must be a JSON object"]
    blocks: list[str] = []
    unknown = sorted(set(raw) - INQUIRY_PAYLOAD_FIELDS)
    if unknown:
        blocks.append("inquiry has unknown fields: " + ", ".join(unknown))
    forbidden = sorted({"patch", "verification", "verification_command", "targets"} & set(raw))
    if forbidden:
        blocks.append(
            "inquiry may not carry proposal fields: " + ", ".join(forbidden)
        )
    for field in INQUIRY_TEXT_FIELDS:
        if not _substantive_text(raw.get(field)):
            blocks.append(f"inquiry.{field} must be substantive non-placeholder text")

    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        blocks.append("inquiry.evidence must be a non-empty list")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                blocks.append(f"inquiry.evidence[{index}] must be an object")
                continue
            extra = sorted(set(item) - set(EVIDENCE_FIELDS))
            if extra:
                blocks.append(
                    f"inquiry.evidence[{index}] has unknown fields: "
                    + ", ".join(extra)
                )
            for field in EVIDENCE_FIELDS:
                validator = _substantive_text if field == "observation" else _meaningful_text
                if not validator(item.get(field)):
                    blocks.append(
                        f"inquiry.evidence[{index}].{field} must be meaningful text"
                    )

    hypothesis = raw.get("hypothesis")
    if not isinstance(hypothesis, dict):
        blocks.append("inquiry.hypothesis must be an object")
    else:
        extra = sorted(set(hypothesis) - set(HYPOTHESIS_FIELDS))
        if extra:
            blocks.append(
                "inquiry.hypothesis has unknown fields: " + ", ".join(extra)
            )
        for field in HYPOTHESIS_FIELDS:
            if not _substantive_text(hypothesis.get(field)):
                blocks.append(f"inquiry.hypothesis.{field} must be substantive text")
        if (
            _meaningful_text(hypothesis.get("claim"))
            and _meaningful_text(hypothesis.get("falsifier"))
            and hypothesis["claim"].strip().lower() == hypothesis["falsifier"].strip().lower()
        ):
            blocks.append("inquiry hypothesis claim and falsifier must differ")

    boundary = raw.get("evaluation_boundary")
    if not isinstance(boundary, dict):
        blocks.append("inquiry.evaluation_boundary must be an object")
    else:
        extra = sorted(set(boundary) - set(EVALUATION_BOUNDARY_FIELDS))
        if extra:
            blocks.append(
                "inquiry.evaluation_boundary has unknown fields: "
                + ", ".join(extra)
            )
        for field in ("mutable", "protected"):
            values = boundary.get(field)
            if not isinstance(values, list) or not values or not all(
                _meaningful_text(item) for item in values
            ):
                blocks.append(
                    f"inquiry.evaluation_boundary.{field} must be a non-empty list of meaningful strings"
                )
        for field in (
            "baseline", "measure", "evaluator", "success_rule", "budget",
        ):
            if not _substantive_text(boundary.get(field)):
                blocks.append(
                    f"inquiry.evaluation_boundary.{field} must be meaningful text"
                )
        guardrails = boundary.get("guardrails")
        if not isinstance(guardrails, list) or not guardrails or not all(
            _substantive_text(item) for item in guardrails
        ):
            blocks.append(
                "inquiry.evaluation_boundary.guardrails must be a non-empty "
                "list of substantive strings"
            )
        elif len(guardrails) != len(set(guardrails)):
            blocks.append(
                "inquiry.evaluation_boundary.guardrails may not contain duplicates"
            )
        mutable = boundary.get("mutable") if isinstance(boundary.get("mutable"), list) else []
        protected = boundary.get("protected") if isinstance(boundary.get("protected"), list) else []
        overlap = {
            str(item).strip().lower() for item in mutable
        } & {
            str(item).strip().lower() for item in protected
        }
        if overlap:
            blocks.append(
                "inquiry evaluation mutable/protected sets overlap: "
                + ", ".join(sorted(overlap))
            )

    disposition = raw.get("disposition")
    if disposition not in INQUIRY_DISPOSITIONS:
        blocks.append(
            f"inquiry.disposition must be one of {list(INQUIRY_DISPOSITIONS)}"
        )
    recommendations = raw.get("recommendations")
    if not isinstance(recommendations, list):
        blocks.append("inquiry.recommendations must be a list")
        recommendations = []
    ids: set[str] = set()
    for index, item in enumerate(recommendations):
        if not isinstance(item, dict):
            blocks.append(f"inquiry.recommendations[{index}] must be an object")
            continue
        extra = sorted(set(item) - set(RECOMMENDATION_FIELDS))
        if extra:
            blocks.append(
                f"inquiry.recommendations[{index}] has unknown fields: "
                + ", ".join(extra)
            )
        for field in RECOMMENDATION_FIELDS:
            if field == "implementation_contract":
                continue
            validator = _meaningful_text if field == "id" else _substantive_text
            if not validator(item.get(field)):
                blocks.append(
                    f"inquiry.recommendations[{index}].{field} must be meaningful text"
                )
        blocks.extend(implementation_contract_blocks(
            item.get("implementation_contract"),
            f"inquiry.recommendations[{index}].implementation_contract",
        ))
        rec_id = item.get("id")
        if isinstance(rec_id, str):
            if rec_id in ids:
                blocks.append(f"duplicate recommendation id {rec_id!r}")
            ids.add(rec_id)

    selected = raw.get("selected_recommendation_id")
    if selected not in (None, ""):
        blocks.append("A may not pre-select a recommendation; B selects it during review")
    if disposition == "propose":
        if not recommendations:
            blocks.append("propose inquiry requires at least one recommendation")
        if raw.get("no_change_reason") not in (None, ""):
            blocks.append("propose inquiry may not set no_change_reason")
        if raw.get("revisit_when") not in (None, ""):
            blocks.append("propose inquiry may not set revisit_when")
        before = raw.get("question_before")
        after = raw.get("question_after")
        if (
            _meaningful_text(before)
            and _meaningful_text(after)
            and _semantic_text(before) == _semantic_text(after)
        ):
            blocks.append("propose inquiry must materially reframe question_after")
    elif disposition == "no_change":
        if recommendations:
            blocks.append("no_change inquiry may not include recommendations")
        if not _substantive_text(raw.get("no_change_reason")):
            blocks.append("no_change inquiry requires no_change_reason")
        if not _substantive_text(raw.get("revisit_when")):
            blocks.append("no_change inquiry requires revisit_when")
    return blocks


def canonical_inquiry_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        field: copy.deepcopy(raw.get(field))
        for field in (
            *INQUIRY_TEXT_FIELDS,
            "evidence",
            "hypothesis",
            "evaluation_boundary",
            "recommendations",
            "disposition",
            "no_change_reason",
            "revisit_when",
        )
    }


def inquiry_review_sha256(inquiry: dict[str, Any]) -> str:
    """Bind B's review to all research semantics and frozen boundaries."""
    review = inquiry.get("review") if isinstance(inquiry.get("review"), dict) else {}
    material = {
        "id": inquiry.get("id"),
        "track": inquiry.get("track"),
        "source": inquiry.get("source"),
        "payload": canonical_inquiry_payload(inquiry),
        "selected_recommendation_id": inquiry.get("selected_recommendation_id"),
        "selected_recommendation_sha256": inquiry.get(
            "selected_recommendation_sha256"
        ),
        "evaluator_snapshot_id": inquiry.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": inquiry.get(
            "evaluator_manifest_sha256"
        ),
        "outcome_plan_sha256": (
            inquiry.get("outcome_plan", {}).get("sha256")
            if isinstance(inquiry.get("outcome_plan"), dict)
            else None
        ),
        "review": {
            field: copy.deepcopy(review.get(field))
            for field in (
                "track", "verdict", "reviewer", "reason",
                "selected_recommendation_id", "evaluator_snapshot_id",
                "evaluator_manifest_sha256", "outcome_plan_sha256", "at",
            )
        },
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inquiry_review_binding_blocks(inquiry: dict[str, Any]) -> list[str]:
    review = inquiry.get("review")
    if not isinstance(review, dict):
        return ["review binding requires a B review record"]
    expected = inquiry_review_sha256(inquiry)
    blocks: list[str] = []
    if inquiry.get("inquiry_review_sha256") != expected:
        blocks.append("inquiry review digest no longer matches research semantics")
    if review.get("inquiry_review_sha256") != expected:
        blocks.append("B review is not bound to the current inquiry digest")
    if review.get("evaluator_manifest_sha256") != inquiry.get(
        "evaluator_manifest_sha256"
    ):
        blocks.append("B review evaluator manifest binding has drifted")
    return blocks


def hard_verification_sha256(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    material = {
        field: copy.deepcopy(record.get(field))
        for field in (
            "level", "track", "proposal", "command", "exit_code", "timeout",
            "requested_timeout_seconds", "stdout_sha256", "stdout_bytes",
            "stderr_sha256", "stderr_bytes", "output_limit_exceeded",
            "containment", "at", "sandbox", "proposal_review_sha256",
            "proposal_critique_sha256", "inquiry_review_sha256",
            "evaluator_snapshot_id", "evaluator_manifest_sha256",
            "state_basis_sha256",
        )
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_outcome_plan(
    inquiry: dict[str, Any],
    evaluator_snapshot_id: str,
) -> dict[str, Any]:
    """Freeze the question's pre-registered decision rule before A proposes."""
    boundary = inquiry.get("evaluation_boundary", {})
    plan = {
        "evaluator": boundary.get("evaluator"),
        "evaluator_snapshot_id": evaluator_snapshot_id,
        "evaluator_manifest_sha256": inquiry.get(
            "evaluator_manifest_sha256"
        ),
        "baseline": boundary.get("baseline"),
        "primary_metric": boundary.get("measure"),
        "success_rule": boundary.get("success_rule"),
        "guardrails": copy.deepcopy(boundary.get("guardrails")),
        "budget": boundary.get("budget"),
    }
    encoded = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    plan["sha256"] = hashlib.sha256(encoded).hexdigest()
    return plan


def outcome_plan_blocks(
    inquiry: dict[str, Any],
    plan: Any,
) -> list[str]:
    if not isinstance(plan, dict):
        return ["outcome_plan must be an object"]
    expected = build_outcome_plan(
        inquiry, str(inquiry.get("evaluator_snapshot_id") or ""),
    )
    if plan != expected:
        return ["outcome_plan differs from the B-frozen evaluation boundary"]
    return []


def canonical_outcome_input(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        field: copy.deepcopy(raw.get(field))
        for field in OUTCOME_INPUT_FIELDS
    }


def resolve_outcome_artifact(locator: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in ("", "."):
        raise ValueError("artifact path must be safe and repository-relative")
    source = ROOT / relative
    resolved = source.resolve(strict=True)
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("artifact path resolves outside the repository") from exc
    cursor = ROOT
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ValueError("artifact path may not traverse symlinks")
    if not resolved.is_file():
        raise ValueError("artifact path must resolve to a regular file")
    return resolved


def outcome_input_blocks(
    raw: Any,
    inquiry: dict[str, Any],
    *,
    require_source_artifact: bool,
) -> list[str]:
    """Validate an outcome report without treating its prose as ground truth."""
    if not isinstance(raw, dict):
        return ["outcome must be a JSON object"]
    blocks: list[str] = []
    unknown = sorted(set(raw) - set(OUTCOME_INPUT_FIELDS))
    if unknown:
        blocks.append("outcome has unknown fields: " + ", ".join(unknown))
    verdict = raw.get("verdict")
    if verdict not in OUTCOME_VERDICTS:
        blocks.append(f"outcome.verdict must be one of {list(OUTCOME_VERDICTS)}")
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        blocks.append("outcome.evidence must be a non-empty list")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                blocks.append(f"outcome.evidence[{index}] must be an object")
                continue
            extra = sorted(set(item) - set(EVIDENCE_FIELDS))
            if extra:
                blocks.append(
                    f"outcome.evidence[{index}] has unknown fields: "
                    + ", ".join(extra)
                )
            for field in EVIDENCE_FIELDS:
                validator = (
                    _substantive_text if field == "observation"
                    else _meaningful_text
                )
                if not validator(item.get(field)):
                    blocks.append(
                        f"outcome.evidence[{index}].{field} must be meaningful text"
                    )
    for field in (
        "baseline_result",
        "candidate_result",
        "budget_used",
        "reason",
        "next_question",
    ):
        if not _substantive_text(raw.get(field)):
            blocks.append(f"outcome.{field} must be substantive text")
    if (
        _meaningful_text(raw.get("next_question"))
        and _meaningful_text(inquiry.get("question_after"))
        and _semantic_text(raw["next_question"])
        == _semantic_text(inquiry["question_after"])
    ):
        blocks.append(
            "outcome.next_question must incorporate the result, not repeat question_after"
        )
    locator = raw.get("artifact_locator")
    if not _meaningful_text(locator):
        blocks.append("outcome.artifact_locator must name a local result artifact")
    else:
        path = Path(str(locator))
        if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
            blocks.append("outcome.artifact_locator must be a safe repository-relative path")
        elif require_source_artifact:
            try:
                resolve_outcome_artifact(str(locator))
            except (OSError, ValueError):
                blocks.append(
                    "outcome.artifact_locator must resolve inside the repository "
                    "to a regular file without symlinks"
                )
    results = raw.get("guardrail_results")
    plan = inquiry.get("outcome_plan")
    planned_guardrails = (
        plan.get("guardrails", []) if isinstance(plan, dict) else []
    )
    observed_guardrails: list[str] = []
    all_passed = True
    if not isinstance(results, list) or not results:
        blocks.append("outcome.guardrail_results must be a non-empty list")
    else:
        for index, item in enumerate(results):
            pos = f"outcome.guardrail_results[{index}]"
            if not isinstance(item, dict):
                blocks.append(f"{pos} must be an object")
                continue
            extra = sorted(set(item) - set(GUARDRAIL_RESULT_FIELDS))
            if extra:
                blocks.append(f"{pos} has unknown fields: " + ", ".join(extra))
            if not _substantive_text(item.get("guardrail")):
                blocks.append(f"{pos}.guardrail must be substantive text")
            else:
                observed_guardrails.append(item["guardrail"])
            if not _substantive_text(item.get("result")):
                blocks.append(f"{pos}.result must be substantive text")
            if not isinstance(item.get("passed"), bool):
                blocks.append(f"{pos}.passed must be boolean")
                all_passed = False
            elif not item["passed"]:
                all_passed = False
    if observed_guardrails != planned_guardrails:
        blocks.append(
            "outcome.guardrail_results must cover the frozen guardrails in order"
        )
    if verdict == "supported" and not all_passed:
        blocks.append("supported outcome requires every frozen guardrail to pass")
    return blocks


def pin_outcome_artifact(locator: str) -> dict[str, Any]:
    """Copy B's exact result artifact into protected content-addressed storage."""
    source = resolve_outcome_artifact(locator)
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    blob_dir = SNAP_DIR / "outcome_blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    destination = blob_dir / digest
    if destination.exists():
        if destination.read_bytes() != payload:
            raise RuntimeError(f"outcome blob digest collision at {digest}")
    else:
        temporary = blob_dir / f".{digest}.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        temporary.chmod(0o444)
        os.replace(temporary, destination)
    destination.chmod(0o444)
    return {
        "artifact_sha256": digest,
        "artifact_size": len(payload),
    }


def outcome_assessment_sha256(outcome: dict[str, Any]) -> str:
    """Bind the complete recorded B assessment, excluding its own digest."""
    material = {
        key: copy.deepcopy(value)
        for key, value in outcome.items()
        if key != "outcome_sha256"
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def outcome_record_blocks(
    inquiry: dict[str, Any],
    outcome: Any,
    proposal: dict[str, Any] | None = None,
) -> list[str]:
    if not isinstance(outcome, dict):
        return ["outcome must be an object"]
    stored_fields = {
        *OUTCOME_INPUT_FIELDS,
        "artifact_sha256",
        "artifact_size",
        "plan_sha256",
        "inquiry_review_sha256",
        "proposal_review_sha256",
        "proposal_critique_sha256",
        "hard_verification_sha256",
        "evaluator_manifest_sha256",
        "track",
        "assessor",
        "at",
        "outcome_sha256",
    }
    blocks: list[str] = []
    extra = sorted(set(outcome) - stored_fields)
    if extra:
        blocks.append("outcome record has unknown fields: " + ", ".join(extra))
    raw = {field: copy.deepcopy(outcome.get(field)) for field in OUTCOME_INPUT_FIELDS}
    blocks.extend(outcome_input_blocks(
        raw, inquiry, require_source_artifact=False,
    ))
    if outcome.get("track") != "B":
        blocks.append("outcome assessment must be on track B")
    if not _meaningful_text(outcome.get("assessor")):
        blocks.append("outcome assessment requires an assessor")
    if not _meaningful_text(outcome.get("at")):
        blocks.append("outcome assessment requires a timestamp")
    recorded_outcome_digest = outcome.get("outcome_sha256")
    if (
        not isinstance(recorded_outcome_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", recorded_outcome_digest)
        or recorded_outcome_digest != outcome_assessment_sha256(outcome)
    ):
        blocks.append("outcome assessment digest is missing or inconsistent")
    plan = inquiry.get("outcome_plan")
    expected_plan_digest = plan.get("sha256") if isinstance(plan, dict) else None
    if outcome.get("plan_sha256") != expected_plan_digest:
        blocks.append("outcome assessment is not bound to the frozen outcome plan")
    if outcome.get("inquiry_review_sha256") != inquiry.get(
        "inquiry_review_sha256"
    ):
        blocks.append("outcome assessment is not bound to the reviewed inquiry")
    if outcome.get("evaluator_manifest_sha256") != inquiry.get(
        "evaluator_manifest_sha256"
    ):
        blocks.append("outcome assessment evaluator manifest binding has drifted")
    if not isinstance(proposal, dict):
        blocks.append("outcome assessment has no linked proposal record")
    else:
        if outcome.get("proposal_review_sha256") != proposal_review_sha256(
            proposal
        ):
            blocks.append("outcome assessment proposal review binding has drifted")
        critic = proposal.get("critic")
        if outcome.get(
            "proposal_critique_sha256"
        ) != proposal_critique_sha256(critic):
            blocks.append("outcome assessment proposal critique binding has drifted")
        if outcome.get("hard_verification_sha256") != hard_verification_sha256(
            proposal.get("verification")
        ):
            blocks.append("outcome assessment hard verification binding has drifted")
        if proposal.get("verification", {}).get(
            "hard_verification_sha256"
        ) != hard_verification_sha256(proposal.get("verification")):
            blocks.append("stored hard verification digest is inconsistent")
    digest = outcome.get("artifact_sha256")
    size = outcome.get("artifact_size")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        blocks.append("outcome artifact needs a sha256 digest")
    elif not isinstance(size, int) or isinstance(size, bool) or size < 0:
        blocks.append("outcome artifact needs a non-negative size")
    else:
        blob = SNAP_DIR / "outcome_blobs" / digest
        try:
            payload = blob.read_bytes()
        except OSError:
            blocks.append(f"outcome artifact blob is missing: {digest}")
        else:
            if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
                blocks.append(f"outcome artifact blob is corrupt: {digest}")
    return blocks


def inquiry_context_blocks(
    state: dict[str, Any],
    raw: dict[str, Any],
) -> list[str]:
    blocks: list[str] = []
    question_after = raw.get("question_after")
    protected_questions = {
        str(state.get("meta_goal", {}).get("statement", "")).strip().lower(),
        str(state.get("final_goal", {}).get("statement", "")).strip().lower(),
    }
    if (
        _meaningful_text(question_after)
        and question_after.strip().lower() in protected_questions
    ):
        blocks.append(
            "question_after must operationalize, not repeat, the meta/final goal"
        )

    measured = []
    for inquiry_id, inquiry in state.get("inquiries", {}).items():
        outcome = inquiry.get("outcome")
        match = re.fullmatch(r"INQ-(\d+)", str(inquiry_id))
        if isinstance(outcome, dict) and match:
            measured.append((int(match.group(1)), inquiry_id, outcome))
    if measured:
        _, prior_id, prior_outcome = max(measured)
        expected_question = prior_outcome.get("next_question")
        if (
            not _meaningful_text(raw.get("question_before"))
            or not _meaningful_text(expected_question)
            or _semantic_text(raw["question_before"])
            != _semantic_text(expected_question)
        ):
            blocks.append(
                "question_before must equal the latest measured outcome's "
                f"next_question from {prior_id}"
            )
        digest = prior_outcome.get("artifact_sha256")
        expected_locator = (
            f".research/auto_snapshots/outcome_blobs/{digest}"
        )
        evidence = raw.get("evidence", [])
        if not any(
            isinstance(item, dict)
            and item.get("locator") == expected_locator
            for item in evidence
        ):
            blocks.append(
                "evidence must cite the latest measured outcome artifact: "
                + expected_locator
            )
    return blocks


def _evaluator_snapshot_material(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "roots": copy.deepcopy(snapshot.get("roots")),
        "frozen_self_test_command": snapshot.get("frozen_self_test_command"),
        "trusted_frozen_command": snapshot.get("trusted_frozen_command"),
        "protected_command": snapshot.get("protected_command"),
        "entries": copy.deepcopy(snapshot.get("entries")),
    }


def evaluator_manifest_sha256(snapshot: dict[str, Any]) -> str:
    material = json.dumps(
        _evaluator_snapshot_material(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _collect_evaluator_entries(
    root: Path,
    roots: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    entries: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    for relative_root in roots:
        normalized_root = _patch_path_norm(str(relative_root))
        base = root / normalized_root
        if not base.exists():
            raise RuntimeError(f"evaluator root is missing: {normalized_root}")
        if base.is_symlink():
            raise RuntimeError(f"evaluator root may not be a symlink: {normalized_root}")
        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise RuntimeError(
                    f"evaluator snapshot refuses symlink: {relative.as_posix()}"
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise RuntimeError(
                    f"evaluator snapshot refuses special file: {relative.as_posix()}"
                )
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            entries.append({
                "path": relative.as_posix(),
                "sha256": digest,
                "size": len(payload),
                "mode": path.stat().st_mode & 0o777,
            })
            blobs.setdefault(digest, payload)
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise RuntimeError("evaluator snapshot contains no files")
    return entries, blobs


def evaluator_snapshot_blocks(
    snapshot_id: str,
    snapshot: Any,
) -> list[str]:
    if not isinstance(snapshot, dict):
        return [f"evaluator snapshot {snapshot_id} must be an object"]
    blocks: list[str] = []
    if snapshot.get("id") != snapshot_id:
        blocks.append(f"evaluator snapshot {snapshot_id} id must match its map key")
    if not _meaningful_text(snapshot.get("inquiry_id")):
        blocks.append(f"evaluator snapshot {snapshot_id} needs inquiry_id")
    roots = snapshot.get("roots")
    if not isinstance(roots, list) or not roots or not all(
        isinstance(item, str) and item.strip() for item in roots
    ):
        blocks.append(f"evaluator snapshot {snapshot_id} needs non-empty roots")
        roots = []
    else:
        for relative in roots:
            normalized = relative.strip().replace("\\", "/")
            if (
                normalized.startswith("/")
                or ".." in Path(normalized).parts
                or normalized in ("", ".")
            ):
                blocks.append(
                    f"evaluator snapshot {snapshot_id} has unsafe root {relative!r}"
                )
    for field in (
        "frozen_self_test_command", "trusted_frozen_command",
        "protected_command", "frozen_at",
    ):
        if not _meaningful_text(snapshot.get(field)):
            blocks.append(f"evaluator snapshot {snapshot_id} needs {field}")
    entries = snapshot.get("entries")
    if not isinstance(entries, list) or not entries:
        blocks.append(f"evaluator snapshot {snapshot_id} needs file entries")
        entries = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        pos = f"evaluator snapshot {snapshot_id}.entries[{index}]"
        if not isinstance(entry, dict):
            blocks.append(f"{pos} must be an object")
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path or path in seen:
            blocks.append(f"{pos} needs a unique path")
        else:
            seen.add(path)
            if roots and not any(
                path == root or path.startswith(root.rstrip("/") + "/")
                for root in roots
            ):
                blocks.append(f"{pos} is outside frozen roots")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            blocks.append(f"{pos} needs a sha256 digest")
        if not isinstance(entry.get("size"), int) or entry.get("size", -1) < 0:
            blocks.append(f"{pos} needs a non-negative size")
        mode = entry.get("mode")
        if not isinstance(mode, int) or isinstance(mode, bool) or not (0 <= mode <= 0o777):
            blocks.append(f"{pos} needs a file mode")
    computed_manifest = evaluator_manifest_sha256(snapshot)
    manifest = snapshot.get("manifest_sha256")
    if manifest != computed_manifest:
        blocks.append(f"evaluator snapshot {snapshot_id} manifest digest is inconsistent")
    expected_id = f"EV-{snapshot.get('inquiry_id')}-{computed_manifest}"
    if snapshot_id != expected_id:
        blocks.append(
            f"evaluator snapshot {snapshot_id} id is not bound to its full manifest"
        )
    return blocks


def freeze_evaluator_snapshot(
    state: dict[str, Any],
    inquiry_id: str,
) -> str:
    """Freeze evaluator bytes when B accepts an inquiry, before A can patch."""
    roots = list(FROZEN_EVALUATOR_PATHS)
    entries, blobs = _collect_evaluator_entries(ROOT, roots)
    snapshot: dict[str, Any] = {
        "roots": roots,
        "frozen_self_test_command": state.get("self_test_command"),
        "trusted_frozen_command": TRUSTED_FROZEN_EVALUATOR_COMMAND,
        "protected_command": PROTECTED_EVALUATOR_COMMAND,
        "entries": entries,
    }
    manifest = evaluator_manifest_sha256(snapshot)
    snapshot_id = f"EV-{inquiry_id}-{manifest}"
    snapshot.update({
        "id": snapshot_id,
        "inquiry_id": inquiry_id,
        "manifest_sha256": manifest,
        "frozen_at": now_iso(),
    })
    blob_dir = SNAP_DIR / "evaluator_blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    for digest, payload in blobs.items():
        blob_path = blob_dir / digest
        if blob_path.exists():
            if hashlib.sha256(blob_path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"corrupt evaluator blob already exists: {digest}")
            continue
        temporary = blob_dir / f".{digest}.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        temporary.chmod(0o444)
        temporary.replace(blob_path)
    existing = state.setdefault("evaluator_snapshots", {}).get(snapshot_id)
    if existing is not None and existing != snapshot:
        raise RuntimeError(f"evaluator snapshot id collision: {snapshot_id}")
    state["evaluator_snapshots"][snapshot_id] = snapshot
    return snapshot_id


def evaluator_snapshot_runtime_blocks(
    state: dict[str, Any],
    snapshot_id: Any,
    require_live_match: bool = True,
) -> list[str]:
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return ["evaluator snapshot id is missing"]
    snapshot = state.get("evaluator_snapshots", {}).get(snapshot_id)
    if snapshot is None:
        return [f"evaluator snapshot {snapshot_id!r} is missing"]
    blocks = evaluator_snapshot_blocks(snapshot_id, snapshot)
    if blocks:
        return blocks
    blob_dir = SNAP_DIR / "evaluator_blobs"
    for entry in snapshot["entries"]:
        blob_path = blob_dir / entry["sha256"]
        if not blob_path.is_file():
            blocks.append(
                f"evaluator snapshot {snapshot_id} is missing blob {entry['sha256']}"
            )
            continue
        payload = blob_path.read_bytes()
        if (
            len(payload) != entry["size"]
            or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        ):
            blocks.append(
                f"evaluator snapshot {snapshot_id} has corrupt blob {entry['sha256']}"
            )
    if require_live_match:
        try:
            current_entries, _ = _collect_evaluator_entries(ROOT, snapshot["roots"])
        except RuntimeError as exc:
            blocks.append(str(exc))
        else:
            if current_entries != snapshot["entries"]:
                blocks.append(
                    f"live evaluator drifted after {snapshot_id} was frozen"
                )
        if state.get("self_test_command") != snapshot.get(
            "frozen_self_test_command"
        ):
            blocks.append(
                f"live self_test_command drifted after {snapshot_id} was frozen"
            )
    return blocks


def validate(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    warnings: list[str] = []
    if state.get("schema_version") != SCHEMA_VERSION:
        blocks.append(f"schema_version must be {SCHEMA_VERSION}")

    meta = state.get("meta_goal", {})
    if meta.get("id") != META_GOAL_ID:
        blocks.append(f"meta_goal.id must be {META_GOAL_ID}")
    if meta.get("statement") != META_GOAL_STATEMENT:
        blocks.append("meta_goal.statement must equal the compiled immutable M0")
    if not meta.get("immutable", False):
        blocks.append("meta_goal must be immutable")

    final_goal = state.get("final_goal", {})
    if final_goal != FINAL_GOAL:
        blocks.append("final_goal must equal the compiled immutable final goal")

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
        if (
            m0.get("statement") != META_GOAL_STATEMENT
            or m0.get("immutable") is not True
            or m0.get("type") != "assumption"
        ):
            blocks.append(f"{META_GOAL_ID} must equal the compiled immutable L0 node")

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

    prompts = state.get("launch_prompts", {})
    if not isinstance(prompts, dict):
        blocks.append("launch_prompts must be an object")
    else:
        for role in TRACKS:
            role_prompts = prompts.get(role)
            if not isinstance(role_prompts, list) or not role_prompts or not all(
                isinstance(item, str) and item.strip() for item in role_prompts
            ):
                blocks.append(f"launch_prompts.{role} must be a non-empty list of strings")

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

    inquiries = state.get("inquiries", {})
    if not isinstance(inquiries, dict):
        blocks.append("inquiries must be an object")
        inquiries = {}

    evaluator_snapshots = state.get("evaluator_snapshots", {})
    if not isinstance(evaluator_snapshots, dict):
        blocks.append("evaluator_snapshots must be an object")
        evaluator_snapshots = {}
    for snapshot_id, snapshot in evaluator_snapshots.items():
        blocks.extend(evaluator_snapshot_blocks(snapshot_id, snapshot))
        if (
            isinstance(snapshot, dict)
            and snapshot.get("inquiry_id") not in inquiries
        ):
            blocks.append(
                f"evaluator snapshot {snapshot_id} references unknown inquiry "
                f"{snapshot.get('inquiry_id')!r}"
            )

    legacy_proposal_ids = state.get("legacy_proposal_ids", [])
    if not isinstance(legacy_proposal_ids, list) or not all(
        isinstance(item, str) and item for item in legacy_proposal_ids
    ):
        blocks.append("legacy_proposal_ids must be a list of proposal ids")
        legacy_proposal_ids = []
    elif len(legacy_proposal_ids) != len(set(legacy_proposal_ids)):
        blocks.append("legacy_proposal_ids may not contain duplicates")
    for legacy_id in legacy_proposal_ids:
        legacy = state.get("proposals", {}).get(legacy_id)
        if not legacy:
            blocks.append(f"legacy proposal registry references unknown {legacy_id}")
        elif legacy.get("inquiry_id"):
            blocks.append(f"legacy proposal {legacy_id} may not carry an inquiry link")

    for pid, prop in state.get("proposals", {}).items():
        if not isinstance(prop, dict):
            blocks.append(f"proposal {pid} must be an object")
            continue
        if prop.get("id") != pid:
            blocks.append(
                f"proposal map key {pid} does not match record id {prop.get('id')!r}"
            )
        if prop.get("status") not in PROPOSAL_STATUSES:
            blocks.append(f"proposal {pid} has invalid status {prop.get('status')!r}")
        if prop.get("track", "A") not in TRACKS:
            blocks.append("proposal " + pid + " has invalid track " + repr(prop.get("track")))
        if not isinstance(prop.get("target_preimage_sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", prop.get("target_preimage_sha256", "")
        ):
            if prop.get("inquiry_id"):
                blocks.append(f"linked proposal {pid} needs a target preimage digest")
        critic = prop.get("critic") or {}
        if critic.get("track", "B") not in TRACKS:
            blocks.append("proposal " + pid + " has invalid critic track " + repr(critic.get("track")))
        verdict = critic.get("verdict")
        if verdict is not None and verdict not in CRITIC_VERDICTS:
            blocks.append(f"proposal {pid} has invalid critic verdict {verdict!r}")
        if critic and pid not in legacy_proposal_ids and critic.get(
            "proposal_critique_sha256"
        ) != proposal_critique_sha256(critic):
            blocks.append(f"proposal {pid} critique digest is inconsistent")
        if prop.get("status") in (
            "verified", "terminated", "failed_verification", "applied",
        ) and not prop.get("verification"):
            blocks.append(f"proposal {pid} is {prop['status']} without a verification record")
        if prop.get("status") == "applied" and not prop.get("applied_at"):
            blocks.append(f"proposal {pid} is applied without applied_at")
        retro_id = prop.get("retrospective_id")
        if retro_id and retro_id not in state.get("retrospectives", {}):
            blocks.append(f"proposal {pid} references unknown retrospective {retro_id!r}")
        inquiry_id = prop.get("inquiry_id")
        recommendation_id = prop.get("recommendation_id")
        if bool(inquiry_id) != bool(recommendation_id):
            blocks.append(
                f"proposal {pid} must set inquiry_id and recommendation_id together"
            )
        if not inquiry_id and pid not in legacy_proposal_ids:
            blocks.append(
                f"unlinked proposal {pid} is not migration-grandfathered"
            )
        if inquiry_id and pid in legacy_proposal_ids:
            blocks.append(f"linked proposal {pid} may not be grandfathered as legacy")
        if inquiry_id:
            inquiry = inquiries.get(inquiry_id)
            if not inquiry:
                blocks.append(f"proposal {pid} references unknown inquiry {inquiry_id!r}")
            else:
                if prop.get("track") != "A":
                    blocks.append(f"linked proposal {pid} must be authored on track A")
                recommendation_ids = {
                    item.get("id") for item in inquiry.get("recommendations", [])
                    if isinstance(item, dict)
                }
                if recommendation_id not in recommendation_ids:
                    blocks.append(
                        f"proposal {pid} references unknown recommendation {recommendation_id!r}"
                    )
                if recommendation_id != inquiry.get("selected_recommendation_id"):
                    blocks.append(
                        f"proposal {pid} does not match inquiry {inquiry_id}'s "
                        "B-selected recommendation"
                    )
                selected_recommendation = next(
                    (
                        item for item in inquiry.get("recommendations", [])
                        if isinstance(item, dict)
                        and item.get("id") == recommendation_id
                    ),
                    None,
                )
                if prop.get("recommendation_snapshot") != selected_recommendation:
                    blocks.append(
                        f"proposal {pid} recommendation snapshot does not match "
                        f"inquiry {inquiry_id}"
                    )
                if prop.get("recommendation_sha256") != inquiry.get(
                    "selected_recommendation_sha256"
                ):
                    blocks.append(
                        f"proposal {pid} recommendation digest does not match "
                        f"inquiry {inquiry_id}"
                    )
                if prop.get("inquiry_review_sha256") != inquiry.get(
                    "inquiry_review_sha256"
                ):
                    blocks.append(
                        f"proposal {pid} inquiry review digest does not match "
                        f"inquiry {inquiry_id}"
                    )
                if prop.get("evaluator_snapshot_id") != inquiry.get(
                    "evaluator_snapshot_id"
                ):
                    blocks.append(
                        f"proposal {pid} evaluator snapshot does not match "
                        f"inquiry {inquiry_id}"
                    )
                if prop.get("evaluator_manifest_sha256") != inquiry.get(
                    "evaluator_manifest_sha256"
                ):
                    blocks.append(
                        f"proposal {pid} evaluator manifest does not match "
                        f"inquiry {inquiry_id}"
                    )
                inquiry_plan = inquiry.get("outcome_plan")
                inquiry_plan_digest = (
                    inquiry_plan.get("sha256")
                    if isinstance(inquiry_plan, dict)
                    else None
                )
                if prop.get("outcome_plan_sha256") != inquiry_plan_digest:
                    blocks.append(
                        f"proposal {pid} outcome plan digest does not match "
                        f"inquiry {inquiry_id}"
                    )
                for contract_block in proposal_contract_blocks(prop):
                    blocks.append(f"proposal {pid}: {contract_block}")
                inquiry_status = inquiry.get("status")
                if inquiry_status not in (
                    "implementing", "awaiting_outcome", "supported",
                    "falsified", "inconclusive", "implementation_failed",
                    "converted",
                ):
                    blocks.append(
                        f"proposal {pid} links inquiry {inquiry_id} in invalid "
                        f"status {inquiry_status!r}"
                    )
                if prop.get("status") == "applied":
                    if (
                        inquiry_status != "converted"
                        or inquiry.get("converted_by") != pid
                    ):
                        blocks.append(
                            f"applied proposal {pid} must be the conversion of "
                            f"inquiry {inquiry_id}"
                        )
                elif prop.get("status") in (
                    "proposed", "challenged", "passed_critic",
                ) and inquiry_status != "implementing":
                    blocks.append(
                        f"open proposal {pid} requires implementing inquiry {inquiry_id}"
                    )
                elif prop.get("status") == "verified" and inquiry_status not in (
                    "awaiting_outcome", "supported",
                ):
                    blocks.append(
                        f"verified proposal {pid} requires awaiting_outcome or "
                        f"supported inquiry {inquiry_id}"
                    )
                elif prop.get("status") == "terminated" and inquiry_status not in (
                    "falsified", "inconclusive",
                ):
                    blocks.append(
                        f"terminated proposal {pid} requires falsified or "
                        f"inconclusive inquiry {inquiry_id}"
                    )
                elif prop.get("status") in ("rejected", "failed_verification") and (
                    inquiry_status != "implementation_failed"
                ):
                    blocks.append(
                        f"terminal proposal {pid} requires implementation_failed "
                        f"inquiry {inquiry_id}"
                    )
                if pid not in inquiry.get("proposal_ids", []):
                    blocks.append(
                        f"proposal {pid} is missing from inquiry {inquiry_id} backlinks"
                    )
                linked_critic = prop.get("critic")
                if linked_critic is not None and linked_critic.get("track") != "B":
                    blocks.append(f"linked proposal {pid} critic must be track B")
                if isinstance(linked_critic, dict) and (
                    not _meaningful_text(linked_critic.get("critic"))
                    or not _substantive_text(linked_critic.get("reason"))
                ):
                    blocks.append(
                        f"linked proposal {pid} requires substantive B critic "
                        "identity and reason"
                    )
                linked_verification = prop.get("verification")
                planned_command = prop.get("verification_command")
                if not isinstance(planned_command, str) or not planned_command.strip():
                    blocks.append(
                        f"linked proposal {pid} requires a frozen verification command"
                    )
                if (
                    linked_verification is not None
                    and linked_verification.get("track") != "B"
                ):
                    blocks.append(f"linked proposal {pid} verification must be track B")
                if (
                    isinstance(linked_verification, dict)
                    and linked_verification.get("command") != planned_command
                ):
                    blocks.append(
                        f"linked proposal {pid} verification command must match "
                        "the reviewed plan"
                    )
                if isinstance(linked_verification, dict) and (
                    linked_verification.get("proposal") != pid
                    or linked_verification.get("proposal_review_sha256")
                    != proposal_review_sha256(prop)
                    or linked_verification.get("proposal_critique_sha256")
                    != proposal_critique_sha256(linked_critic)
                    or linked_verification.get("inquiry_review_sha256")
                    != prop.get("inquiry_review_sha256")
                    or linked_verification.get("evaluator_snapshot_id")
                    != prop.get("evaluator_snapshot_id")
                    or linked_verification.get("evaluator_manifest_sha256")
                    != prop.get("evaluator_manifest_sha256")
                    or linked_verification.get("state_basis_sha256")
                    != prop.get("preflight", {}).get("state_basis_sha256")
                    or linked_verification.get("hard_verification_sha256")
                    != hard_verification_sha256(linked_verification)
                ):
                    blocks.append(
                        f"linked proposal {pid} hard verification binding has drifted"
                    )
                if prop.get("status") in (
                    "passed_critic", "verified", "terminated",
                    "failed_verification", "applied"
                ) and (
                    not isinstance(linked_critic, dict)
                    or linked_critic.get("verdict") != "pass"
                ):
                    blocks.append(
                        f"linked proposal {pid} status {prop.get('status')} "
                        "requires a passing B critique"
                    )
                if prop.get("status") in (
                    "passed_critic", "verified", "terminated",
                    "failed_verification", "applied"
                ):
                    expected_review_digest = proposal_review_sha256(prop)
                    preflight = prop.get("preflight")
                    if (
                        not isinstance(preflight, dict)
                        or preflight.get("status") != "ready_for_B"
                        or preflight.get("proposal_review_sha256")
                        != expected_review_digest
                        or preflight.get("evaluator_snapshot_id")
                        != prop.get("evaluator_snapshot_id")
                        or preflight.get("inquiry_review_sha256")
                        != prop.get("inquiry_review_sha256")
                        or preflight.get("evaluator_manifest_sha256")
                        != prop.get("evaluator_manifest_sha256")
                        or not _meaningful_text(
                            preflight.get("state_basis_sha256")
                        )
                    ):
                        blocks.append(
                            f"linked proposal {pid} lacks a current passing "
                            "mechanical preflight"
                        )
                    if (
                        not isinstance(linked_critic, dict)
                        or linked_critic.get("mode") != "semantic_B"
                        or linked_critic.get("proposal_review_sha256")
                        != expected_review_digest
                        or linked_critic.get("evaluator_snapshot_id")
                        != prop.get("evaluator_snapshot_id")
                        or linked_critic.get("inquiry_review_sha256")
                        != prop.get("inquiry_review_sha256")
                        or linked_critic.get("evaluator_manifest_sha256")
                        != prop.get("evaluator_manifest_sha256")
                        or linked_critic.get("state_basis_sha256")
                        != preflight.get("state_basis_sha256")
                        or linked_critic.get("proposal_critique_sha256")
                        != proposal_critique_sha256(linked_critic)
                    ):
                        blocks.append(
                            f"linked proposal {pid} B critique is not bound to "
                            "the current proposal/evaluator digest"
                        )
                if prop.get("status") in ("verified", "terminated", "applied") and (
                    not isinstance(linked_verification, dict)
                    or linked_verification.get("timeout")
                    or linked_verification.get("exit_code") != 0
                ):
                    blocks.append(
                        f"linked proposal {pid} status {prop.get('status')} "
                        "requires successful hard verification"
                    )
                if prop.get("status") == "applied" and prop.get(
                    "applied_by_track"
                ) != "B":
                    blocks.append(f"linked proposal {pid} must be applied on track B")

    verifications = state.get("verifications", [])
    if not isinstance(verifications, list):
        blocks.append("verifications must be a list")
    else:
        for index, record in enumerate(verifications):
            if not isinstance(record, dict):
                blocks.append(f"verification record {index} must be an object")
                continue
            proposal_id = record.get("proposal")
            if (
                isinstance(proposal_id, str)
                and proposal_id not in legacy_proposal_ids
                and (
                    record.get("level") == "hard"
                    or record.get("hard_verification_sha256") is not None
                )
                and record.get("hard_verification_sha256")
                != hard_verification_sha256(record)
            ):
                blocks.append(
                    f"verification record {index} hard-verification digest has drifted"
                )
        for proposal_id, proposal in state.get("proposals", {}).items():
            if proposal_id in legacy_proposal_ids or not isinstance(proposal, dict):
                continue
            proposal_verification = proposal.get("verification")
            if not isinstance(proposal_verification, dict):
                continue
            expected_digest = proposal_verification.get(
                "hard_verification_sha256"
            )
            proposal_records = [
                record for record in verifications
                if isinstance(record, dict)
                and record.get("proposal") == proposal_id
                and (
                    record.get("level") == "hard"
                    or record.get("hard_verification_sha256") is not None
                )
            ]
            matching_records = [
                record for record in proposal_records
                if record.get("level") == "hard"
                and record.get("hard_verification_sha256") == expected_digest
                and hard_verification_sha256(record) == expected_digest
            ]
            if len(proposal_records) != 1 or len(matching_records) != 1:
                blocks.append(
                    f"proposal {proposal_id} must bind exactly one global hard-verification record"
                )

    active_lifecycle: list[str] = []
    for inquiry_id, inquiry in inquiries.items():
        pos = f"inquiry {inquiry_id}"
        if not isinstance(inquiry, dict):
            blocks.append(f"{pos} must be an object")
            continue
        if inquiry.get("id") != inquiry_id:
            blocks.append(f"{pos} id must match its map key")
        if inquiry.get("track", "A") != "A":
            blocks.append(f"{pos} must be authored on track A")
        status = inquiry.get("status")
        if status not in INQUIRY_STATUSES:
            blocks.append(f"{pos} has invalid status {status!r}")
        if status in (
            "accepted", "implementing", "awaiting_outcome", "supported",
        ):
            active_lifecycle.append(inquiry_id)
        persisted_forbidden = sorted(
            {"patch", "verification", "verification_command", "targets"}
            & set(inquiry)
        )
        if persisted_forbidden:
            blocks.append(
                f"{pos} carries forbidden proposal fields: "
                + ", ".join(persisted_forbidden)
            )
        for block in inquiry_payload_blocks(canonical_inquiry_payload(inquiry)):
            blocks.append(f"{pos}: {block}")
        review = inquiry.get("review")
        expected_review = {
            "challenged": "challenge",
            "accepted": "pass",
            "implementing": "pass",
            "awaiting_outcome": "pass",
            "supported": "pass",
            "falsified": "pass",
            "inconclusive": "pass",
            "rejected": "reject",
            "implementation_failed": "pass",
            "closed_no_change": "pass",
            "converted": "pass",
        }.get(status)
        if status == "proposed" and review is not None:
            blocks.append(f"{pos} proposed status may not carry a current review")
        if status == "proposed" and inquiry.get("inquiry_review_sha256") is not None:
            blocks.append(f"{pos} proposed status may not carry a review digest")
        if expected_review:
            if not isinstance(review, dict):
                blocks.append(f"{pos} status {status} requires a review")
            else:
                if review.get("track") != "B":
                    blocks.append(f"{pos} review must be on track B")
                if review.get("verdict") != expected_review:
                    blocks.append(
                        f"{pos} status {status} requires review verdict "
                        f"{expected_review!r}"
                    )
                if not _meaningful_text(review.get("reviewer")):
                    blocks.append(f"{pos} review requires a reviewer")
                if not _substantive_text(review.get("reason")):
                    blocks.append(f"{pos} review requires a substantive reason")
                if not _meaningful_text(review.get("at")):
                    blocks.append(f"{pos} review requires a timestamp")
                for review_block in inquiry_review_binding_blocks(inquiry):
                    blocks.append(f"{pos}: {review_block}")
        selected = inquiry.get("selected_recommendation_id")
        recommendation_ids = {
            item.get("id") for item in inquiry.get("recommendations", [])
            if isinstance(item, dict)
        }
        if status in (
            "accepted", "implementing", "awaiting_outcome", "supported",
            "falsified", "inconclusive", "implementation_failed", "converted",
        ):
            if inquiry.get("disposition") != "propose":
                blocks.append(f"{pos} {status} must have propose disposition")
            if selected not in recommendation_ids:
                blocks.append(f"{pos} {status} needs a selected recommendation")
            if not isinstance(review, dict) or review.get("verdict") != "pass":
                blocks.append(f"{pos} {status} requires a passing review")
            elif review.get("selected_recommendation_id") != selected:
                blocks.append(
                    f"{pos} selected recommendation must match its B review"
                )
            elif review.get("evaluator_snapshot_id") != inquiry.get(
                "evaluator_snapshot_id"
            ):
                blocks.append(
                    f"{pos} evaluator snapshot must match its B review"
                )
            elif review.get("evaluator_manifest_sha256") != inquiry.get(
                "evaluator_manifest_sha256"
            ):
                blocks.append(
                    f"{pos} evaluator manifest must match its B review"
                )
            plan = inquiry.get("outcome_plan")
            plan_digest = plan.get("sha256") if isinstance(plan, dict) else None
            if review.get("outcome_plan_sha256") != plan_digest:
                blocks.append(
                    f"{pos} outcome plan digest must match its B review"
                )
            selected_recommendation = next(
                (
                    item for item in inquiry.get("recommendations", [])
                    if isinstance(item, dict) and item.get("id") == selected
                ),
                None,
            )
            expected_digest = (
                recommendation_sha256(selected_recommendation)
                if isinstance(selected_recommendation, dict)
                else None
            )
            if inquiry.get("selected_recommendation_sha256") != expected_digest:
                blocks.append(f"{pos} selected recommendation digest is inconsistent")
            snapshot_id = inquiry.get("evaluator_snapshot_id")
            snapshot = evaluator_snapshots.get(snapshot_id)
            if not isinstance(snapshot_id, str) or not isinstance(snapshot, dict):
                blocks.append(f"{pos} {status} needs a frozen evaluator snapshot")
            elif snapshot.get("inquiry_id") != inquiry_id:
                blocks.append(
                    f"{pos} evaluator snapshot {snapshot_id} belongs to another inquiry"
                )
            elif snapshot.get("manifest_sha256") != inquiry.get(
                "evaluator_manifest_sha256"
            ):
                blocks.append(
                    f"{pos} evaluator manifest differs from its frozen snapshot"
                )
            for plan_block in outcome_plan_blocks(
                inquiry, inquiry.get("outcome_plan"),
            ):
                blocks.append(f"{pos}: {plan_block}")
        elif selected is not None:
            blocks.append(f"{pos} status {status} may not select a recommendation")
        elif inquiry.get("selected_recommendation_sha256") is not None:
            blocks.append(
                f"{pos} status {status} may not carry a selected recommendation digest"
            )
        elif inquiry.get("evaluator_snapshot_id") is not None:
            blocks.append(
                f"{pos} status {status} may not carry an evaluator snapshot"
            )
        elif inquiry.get("evaluator_manifest_sha256") is not None:
            blocks.append(
                f"{pos} status {status} may not carry an evaluator manifest"
            )
        elif inquiry.get("outcome_plan") is not None:
            blocks.append(f"{pos} status {status} may not carry an outcome plan")
        if status == "closed_no_change":
            if inquiry.get("disposition") != "no_change":
                blocks.append(f"{pos} closed_no_change needs no_change disposition")
            if selected is not None:
                blocks.append(f"{pos} closed_no_change may not select a recommendation")
        if status in (
            "rejected", "falsified", "inconclusive", "implementation_failed",
            "closed_no_change", "converted",
        ):
            if not inquiry.get("resolved_at"):
                blocks.append(f"{pos} status {status} requires resolved_at")
        elif inquiry.get("resolved_at") is not None:
            blocks.append(f"{pos} status {status} may not set resolved_at")
        if status != "converted" and inquiry.get("converted_by") is not None:
            blocks.append(f"{pos} converted_by is only valid for converted inquiries")
        outcome_proposal_id = inquiry.get("outcome_proposal_id")
        if status not in (
            "awaiting_outcome", "supported", "falsified", "inconclusive",
            "implementation_failed", "converted",
        ) and outcome_proposal_id is not None:
            blocks.append(
                f"{pos} outcome_proposal_id is invalid before hard verification"
            )
        outcome = inquiry.get("outcome")
        if outcome is not None:
            linked_outcome_proposal = state.get("proposals", {}).get(
                outcome_proposal_id or ""
            )
            for outcome_block in outcome_record_blocks(
                inquiry, outcome, linked_outcome_proposal,
            ):
                blocks.append(f"{pos}: {outcome_block}")
        if status in ("supported", "falsified", "inconclusive", "converted"):
            if not isinstance(outcome, dict):
                blocks.append(f"{pos} status {status} requires an outcome assessment")
            elif outcome.get("verdict") != (
                "supported" if status == "converted" else status
            ):
                blocks.append(
                    f"{pos} status {status} conflicts with outcome verdict "
                    f"{outcome.get('verdict')!r}"
                )
        elif status != "implementation_failed" and outcome is not None:
            blocks.append(f"{pos} status {status} may not carry an outcome assessment")
        failure = inquiry.get("implementation_failure")
        if status == "implementation_failed":
            if not isinstance(failure, dict):
                blocks.append(f"{pos} implementation_failed requires failure detail")
            else:
                if failure.get("proposal") not in inquiry.get("proposal_ids", []):
                    blocks.append(f"{pos} failure must reference its proposal")
                if failure.get("stage") not in ("critique", "verification", "pre_apply"):
                    blocks.append(f"{pos} failure has invalid stage")
                if not _substantive_text(failure.get("reason")):
                    blocks.append(f"{pos} failure requires a substantive reason")
                if not _meaningful_text(failure.get("at")):
                    blocks.append(f"{pos} failure requires a timestamp")
        elif failure is not None:
            blocks.append(f"{pos} implementation_failure is only valid after failure")
        revisions = inquiry.get("revisions")
        if not isinstance(revisions, list):
            blocks.append(f"{pos}.revisions must be a list")
            revisions = []
        for revision_index, revision in enumerate(revisions):
            revision_pos = f"{pos}.revisions[{revision_index}]"
            if not isinstance(revision, dict):
                blocks.append(f"{revision_pos} must be an object")
                continue
            if not _meaningful_text(revision.get("at")):
                blocks.append(f"{revision_pos} requires a timestamp")
            if not _substantive_text(revision.get("note")):
                blocks.append(f"{revision_pos} requires a substantive note")
            previous = revision.get("previous")
            for block in inquiry_payload_blocks(previous):
                blocks.append(f"{revision_pos}.previous: {block}")
            previous_review = revision.get("review")
            if not isinstance(previous_review, dict):
                blocks.append(f"{revision_pos} requires the B challenge review")
            else:
                if previous_review.get("track") != "B":
                    blocks.append(f"{revision_pos} review must be on track B")
                if previous_review.get("verdict") != "challenge":
                    blocks.append(f"{revision_pos} must preserve a challenge verdict")
                if not _meaningful_text(previous_review.get("reviewer")):
                    blocks.append(f"{revision_pos} review requires a reviewer")
                if not _substantive_text(previous_review.get("reason")):
                    blocks.append(f"{revision_pos} review requires a reason")
        proposal_ids = inquiry.get("proposal_ids", [])
        if not isinstance(proposal_ids, list) or not all(
            isinstance(pid, str) and pid for pid in proposal_ids
        ):
            blocks.append(f"{pos}.proposal_ids must be a list of ids")
            proposal_ids = []
        elif len(proposal_ids) != len(set(proposal_ids)):
            blocks.append(f"{pos}.proposal_ids may not contain duplicates")
        elif len(proposal_ids) > 1:
            blocks.append(f"{pos} may authorize only one proposal attempt")
        for pid in proposal_ids:
            proposal = state.get("proposals", {}).get(pid)
            if not proposal:
                blocks.append(f"{pos} references unknown proposal {pid!r}")
            elif proposal.get("inquiry_id") != inquiry_id:
                blocks.append(f"{pos} backlink to proposal {pid} is inconsistent")
        if status in (
            "proposed", "challenged", "accepted", "rejected", "closed_no_change",
        ):
            if proposal_ids:
                blocks.append(f"{pos} status {status} may not link proposals")
        if status == "implementing":
            if len(proposal_ids) != 1:
                blocks.append(f"{pos} implementing status requires one proposal")
            elif state.get("proposals", {}).get(proposal_ids[0], {}).get("status") not in (
                "proposed", "challenged", "passed_critic", "verified",
            ):
                blocks.append(f"{pos} implementing proposal must remain nonterminal")
        if status == "awaiting_outcome":
            if len(proposal_ids) != 1:
                blocks.append(f"{pos} awaiting_outcome requires one proposal")
            elif state.get("proposals", {}).get(
                proposal_ids[0], {}
            ).get("status") != "verified":
                blocks.append(f"{pos} awaiting_outcome proposal must be verified")
            if outcome_proposal_id != (
                proposal_ids[0] if len(proposal_ids) == 1 else None
            ):
                blocks.append(
                    f"{pos} awaiting_outcome must bind its verified proposal"
                )
            if outcome is not None:
                blocks.append(f"{pos} awaiting_outcome may not already carry outcome")
        if status == "supported":
            if len(proposal_ids) != 1:
                blocks.append(f"{pos} supported status requires one proposal")
            elif state.get("proposals", {}).get(
                proposal_ids[0], {}
            ).get("status") != "verified":
                blocks.append(f"{pos} supported proposal must remain verified")
            if outcome_proposal_id != (
                proposal_ids[0] if len(proposal_ids) == 1 else None
            ):
                blocks.append(f"{pos} supported outcome must bind its proposal")
        if status in ("falsified", "inconclusive"):
            if len(proposal_ids) != 1:
                blocks.append(f"{pos} {status} status requires one proposal")
            elif state.get("proposals", {}).get(
                proposal_ids[0], {}
            ).get("status") != "terminated":
                blocks.append(f"{pos} {status} proposal must be terminated")
            if outcome_proposal_id != (
                proposal_ids[0] if len(proposal_ids) == 1 else None
            ):
                blocks.append(f"{pos} {status} outcome must bind its proposal")
        if status == "implementation_failed":
            if len(proposal_ids) != 1:
                blocks.append(f"{pos} implementation_failed requires one proposal")
            elif state.get("proposals", {}).get(proposal_ids[0], {}).get("status") not in (
                "rejected", "failed_verification",
            ):
                blocks.append(f"{pos} failed proposal must be terminal")
        if status == "converted":
            converted_by = inquiry.get("converted_by")
            proposal = state.get("proposals", {}).get(converted_by)
            if not proposal or proposal.get("status") != "applied":
                blocks.append(f"{pos} converted_by must name an applied proposal")
            elif converted_by not in proposal_ids:
                blocks.append(f"{pos} converted_by must be one of its proposal_ids")
            elif proposal.get("inquiry_id") != inquiry_id:
                blocks.append(f"{pos} converted_by proposal must link back to the inquiry")
            elif proposal.get("recommendation_id") != selected:
                blocks.append(
                    f"{pos} converted proposal must implement the selected recommendation"
                )
            if outcome_proposal_id != converted_by:
                blocks.append(
                    f"{pos} converted outcome must bind the applied proposal"
                )

    active_inquiry_id = state.get("active_inquiry_id")
    if active_inquiry_id is not None:
        active = inquiries.get(active_inquiry_id)
        if not active or active.get("status") not in (
            "accepted", "implementing", "awaiting_outcome", "supported",
        ):
            blocks.append(
                "active_inquiry_id must reference an active inquiry lifecycle state"
            )
    if len(active_lifecycle) > 1:
        blocks.append(
            f"only one inquiry may be active at a time: {active_lifecycle}"
        )
    if active_lifecycle and active_inquiry_id != active_lifecycle[0]:
        blocks.append("the accepted/implementing inquiry must be active_inquiry_id")

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
        topology = entry.get("topology")
        if topology is not None:
            if not isinstance(topology, dict):
                blocks.append(f"{pos}.topology must be an object")
            else:
                backup_dir = entry.get("backup_dir")
                backup_dir_path = Path(str(backup_dir))
                if (
                    not isinstance(backup_dir, str)
                    or backup_dir_path.is_absolute()
                    or ".." in backup_dir_path.parts
                    or not backup_dir.startswith(
                        ".research/auto_snapshots/file_backups/"
                    )
                ):
                    blocks.append(f"{pos}.backup_dir is unsafe")
                if entry.get("backup_id") != file_backup_identity(entry):
                    blocks.append(f"{pos} backup identity is inconsistent")
                for relative, item in topology.items():
                    path_block = validate_patch_path(str(relative))
                    if path_block:
                        blocks.append(f"{pos}.topology: {path_block}")
                    if item is None:
                        continue
                    if not isinstance(item, dict) or item.get("kind") not in (
                        "file", "dir",
                    ):
                        blocks.append(
                            f"{pos}.topology[{relative!r}] has invalid kind"
                        )
                        continue
                    mode = item.get("mode")
                    if not isinstance(mode, int) or isinstance(mode, bool) or not (
                        0 <= mode <= 0o777
                    ):
                        blocks.append(
                            f"{pos}.topology[{relative!r}] has invalid mode"
                        )
                    backup = item.get("backup")
                    if item.get("kind") == "file" and not isinstance(backup, str):
                        blocks.append(
                            f"{pos}.topology[{relative!r}] needs file backup"
                        )
                    elif item.get("kind") == "file" and (
                        not isinstance(backup_dir, str)
                        or not Path(backup).is_relative_to(Path(backup_dir))
                    ):
                        blocks.append(
                            f"{pos}.topology[{relative!r}] backup is outside backup_dir"
                        )
                    if item.get("kind") == "file":
                        digest = item.get("sha256")
                        size = item.get("size")
                        if not isinstance(digest, str) or not re.fullmatch(
                            r"[0-9a-f]{64}", digest,
                        ):
                            blocks.append(
                                f"{pos}.topology[{relative!r}] needs backup sha256"
                            )
                        if (
                            not isinstance(size, int)
                            or isinstance(size, bool)
                            or size < 0
                        ):
                            blocks.append(
                                f"{pos}.topology[{relative!r}] needs backup size"
                            )
                    if item.get("kind") == "dir" and (
                        backup is not None
                        or item.get("sha256") is not None
                        or item.get("size") is not None
                    ):
                        blocks.append(
                            f"{pos}.topology[{relative!r}] directory has bytes"
                        )

    legacy_backup_ids = state.get("legacy_file_backup_ids", [])
    if not isinstance(legacy_backup_ids, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
        for item in legacy_backup_ids
    ):
        blocks.append("legacy_file_backup_ids must be a list of sha256 ids")
        legacy_backup_ids = []
    elif len(legacy_backup_ids) != len(set(legacy_backup_ids)):
        blocks.append("legacy_file_backup_ids may not contain duplicates")
    backup_by_id = {
        file_backup_identity(entry): entry
        for entry in state.get("file_backups", [])
        if isinstance(entry, dict)
    }
    for backup_id in legacy_backup_ids:
        entry = backup_by_id.get(backup_id)
        if entry is None:
            blocks.append(
                f"legacy file-backup registry references unknown {backup_id}"
            )
        elif entry.get("topology") is not None:
            blocks.append(
                f"legacy file-backup {backup_id} may not carry sealed topology"
            )
    for entry in state.get("file_backups", []):
        if isinstance(entry, dict) and entry.get("topology") is None:
            identity = file_backup_identity(entry)
            if identity not in legacy_backup_ids:
                blocks.append(
                    "topology-less file backup is not frozen in the legacy prefix: "
                    + identity
                )

    transition = state.get("bootstrap_transition")
    if transition is not None:
        if not isinstance(transition, dict):
            blocks.append("bootstrap_transition must be null or an object")
        else:
            for field in (
                "manifest_sha256", "proposal", "rollback_floor",
                "rollback_floor_sha256", "engine_before_sha256",
                "engine_after_sha256", "sealed_at", "sealed_by",
            ):
                if not _meaningful_text(transition.get(field)):
                    blocks.append(f"bootstrap_transition needs {field}")
            if transition.get("legacy_file_backup_ids") != legacy_backup_ids:
                blocks.append(
                    "bootstrap_transition legacy prefix differs from state registry"
                )
            for field in (
                "manifest_sha256", "rollback_floor_sha256",
                "engine_before_sha256", "engine_after_sha256",
            ):
                value = transition.get(field)
                if not isinstance(value, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", value,
                ):
                    blocks.append(f"bootstrap_transition {field} must be sha256")
            floor = transition.get("rollback_floor")
            if (
                not isinstance(floor, str)
                or Path(floor).name != floor
                or floor == SNAP_INDEX_NAME
            ):
                blocks.append(
                    "bootstrap_transition rollback_floor must be a snapshot filename"
                )
            if not _substantive_text(transition.get("reason")):
                blocks.append("bootstrap_transition needs a substantive reason")
            proposal_id = transition.get("proposal")
            transition_prop = state.get("proposals", {}).get(proposal_id)
            if (
                not isinstance(transition_prop, dict)
                or transition_prop.get("status") != "applied"
                or transition_prop.get("applied_by_track") != "B"
            ):
                blocks.append(
                    "bootstrap_transition must reference its B-applied proposal"
                )
            sealed_entries = [
                entry for entry in state.get("file_backups", [])
                if isinstance(entry, dict)
                and entry.get("bootstrap_manifest_sha256")
                == transition.get("manifest_sha256")
            ]
            if len(sealed_entries) != 1:
                blocks.append(
                    "bootstrap_transition must bind exactly one topology backup"
                )
            elif isinstance(transition_prop, dict) and sealed_entries[0].get(
                "modification_node"
            ) != transition_prop.get("modification_node"):
                blocks.append(
                    "bootstrap_transition backup and proposal modification differ"
                )
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
    "set_launch_prompt",
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


def declared_file_patch_paths(patch_text: str) -> list[str]:
    """Parse destination paths without requiring an already-applied diff to reapply."""
    paths: list[str] = []
    for line in patch_text.splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        raw = line[4:].split("\t", 1)[0].strip().strip('"')
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        if raw:
            paths.append(_patch_path_norm(raw))
    return sorted(set(paths))


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


def patch_uses_symlink_mode(patch_text: str) -> bool:
    """Reject git's symlink file mode rather than trying to serialize links."""
    metadata = re.compile(
        r"^(?:(?:old|new|new file|deleted file) mode 120000|"
        r"index [0-9a-f]+\.\.[0-9a-f]+ 120000)$"
    )
    return any(metadata.fullmatch(line.strip()) for line in patch_text.splitlines())


def patch_path_traverses_symlink(
    root: Path,
    relative: str,
) -> bool:
    """Return true when an existing component, including the leaf, is a link."""
    cursor = root
    for component in Path(relative).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            return True
        if not cursor.exists():
            break
    return False


def patch_target_preimage_manifest(
    patch: list[dict[str, Any]],
    root: Path = ROOT,
) -> list[dict[str, Any]]:
    """Describe exact target bytes and ancestor topology."""
    targets = declared_file_patch_paths(combined_file_patch(patch))
    relatives: set[str] = set()
    for target in targets:
        prefix = Path()
        for component in Path(target).parts:
            prefix = prefix / component
            relatives.add(prefix.as_posix())
    manifest: list[dict[str, Any]] = []
    for relative in sorted(
        relatives, key=lambda item: (len(Path(item).parts), item),
    ):
        path = root / relative
        if path.is_symlink():
            item = {
                "path": relative, "kind": "symlink",
                "target": os.readlink(path), "mode": path.lstat().st_mode & 0o777,
            }
        elif path.is_dir():
            item = {
                "path": relative, "kind": "dir",
                "mode": path.stat().st_mode & 0o777,
            }
        elif path.is_file():
            payload = path.read_bytes()
            item = {
                "path": relative, "kind": "file",
                "mode": path.stat().st_mode & 0o777,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        elif path.exists():
            item = {"path": relative, "kind": "special"}
        else:
            item = {"path": relative, "kind": "absent"}
        manifest.append(item)
    return manifest


def patch_target_preimage_sha256(
    patch: list[dict[str, Any]],
    root: Path = ROOT,
) -> str:
    """Bind proposal review to exact target bytes and ancestor topology."""
    encoded = json.dumps(
        patch_target_preimage_manifest(patch, root),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    if patch_uses_symlink_mode(combined):
        return ["patch_file may not create, delete, or modify symbolic links"]
    try:
        paths = file_patch_paths(combined, root)
    except ValueError as exc:
        return [str(exc)]
    for path in paths:
        block = validate_patch_path(path)
        if block:
            blocks.append(block)
            continue
        if patch_path_traverses_symlink(root, path):
            blocks.append(f"patch_file path may not traverse symlinks: {path!r}")
        if (
            path in CORE_FILES_PROTECTED_FROM_DELETE
            and patch_deletes_path(combined, path)
        ):
            blocks.append(f"patch_file may not delete core engine file {path!r}")
    seen_op_paths: set[str] = set()
    for index, op in enumerate(ops):
        op_paths = declared_file_patch_paths(str(op.get("patch", "")))
        if len(op_paths) != 1:
            if op.get("sha256_before") or op.get("sha256_after"):
                blocks.append(
                    f"patch_file op {index}: SHA binding requires exactly one path"
                )
            continue
        op_path = op_paths[0]
        if op_path in seen_op_paths:
            blocks.append(
                f"patch_file op {index}: duplicate path {op_path!r} is unsupported"
            )
        seen_op_paths.add(op_path)
        declared_path = op.get("path")
        if declared_path is not None and _patch_path_norm(str(declared_path)) != op_path:
            blocks.append(
                f"patch_file op {index}: declared path does not match diff path {op_path!r}"
            )
        expected_before = op.get("sha256_before")
        target = root / op_path
        if isinstance(expected_before, str) and expected_before:
            if not target.is_file():
                blocks.append(
                    f"patch_file sha256_before target is not a file: {op_path}"
                )
            else:
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected_before:
                    blocks.append(
                        f"patch_file sha256_before mismatch for {op_path}: "
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

    preimages = _capture_file_preimages(patch)
    topology: dict[str, dict[str, Any] | None] = {}
    for rel, content in preimages.items():
        if content is None:
            topology[rel] = None
            continue
        kind, payload, mode = content
        backup: str | None = None
        if kind == "file":
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(payload)
            backup_path.chmod(0o444)
            backup = backup_path.relative_to(ROOT).as_posix()
        topology[rel] = {
            "kind": kind,
            "mode": mode,
            "backup": backup,
            "sha256": (
                hashlib.sha256(payload).hexdigest() if kind == "file" else None
            ),
            "size": len(payload) if kind == "file" else None,
        }
    # Retain the leaf-only map for old tooling; topology is authoritative.
    files: dict[str, str | None] = {}
    for rel in paths:
        item = topology.get(rel)
        files[rel] = (
            item.get("backup") if isinstance(item, dict) else None
        )

    def undo() -> None:
        restore_file_backup_entry({
            "backup_dir": backup_root.relative_to(ROOT).as_posix(),
            "topology": topology,
        })

    try:
        apply_file_patch_text(combined, root)
        unsafe_paths = [
            rel for rel in paths if patch_path_traverses_symlink(root, rel)
        ]
        if unsafe_paths:
            raise RuntimeError(
                "file patch produced a symlink path: " + ", ".join(unsafe_paths)
            )
        ops = file_patch_ops(patch)
        for index, op in enumerate(ops):
            op_paths = declared_file_patch_paths(str(op.get("patch", "")))
            if len(op_paths) != 1:
                continue
            op_path = op_paths[0]
            expected_after = op.get("sha256_after")
            if isinstance(expected_after, str) and expected_after:
                target = root / op_path
                content = target.read_bytes() if target.exists() else b""
                actual = hashlib.sha256(content).hexdigest()
                if actual != expected_after:
                    raise RuntimeError(
                        f"patch_file op {index} sha256_after mismatch for {op_path}: "
                        f"expected {expected_after}, got {actual}"
                    )
    except Exception as exc:
        try:
            undo()
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        raise SystemExit(f"BLOCKED: file patch failed and was rolled back: {exc}") from exc

    backup_entry = {
        "at": now_iso(),
        "modification_node": backup_label,
        "backup_dir": backup_root.relative_to(ROOT).as_posix(),
        "files": files,
        "topology": topology,
    }
    backup_entry["backup_id"] = file_backup_identity(backup_entry)
    state.setdefault("file_backups", []).append(backup_entry)


def file_backup_identity(entry: dict[str, Any]) -> str:
    material = {
        key: copy.deepcopy(value)
        for key, value in entry.items()
        if key != "backup_id"
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_snapshot_backup(backup: str, backup_dir: str) -> Path:
    relative = Path(backup)
    directory = Path(backup_dir)
    if (
        relative.is_absolute()
        or directory.is_absolute()
        or ".." in relative.parts
        or ".." in directory.parts
        or str(relative) in ("", ".")
        or str(directory) in ("", ".")
        or not relative.is_relative_to(directory)
    ):
        raise SystemExit(f"BLOCKED: rollback backup path is unsafe: {backup}")
    cursor = ROOT
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise SystemExit(
                f"BLOCKED: rollback backup path traverses a symlink: {backup}"
            )
    try:
        resolved = (ROOT / relative).resolve(strict=True)
        resolved.relative_to(SNAP_DIR.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"BLOCKED: rollback refuses backup outside snapshots: {backup}"
        ) from exc
    if not resolved.is_file():
        raise SystemExit(f"BLOCKED: rollback backup is missing: {backup}")
    return resolved


def file_backup_preimages(
    entry: dict[str, Any],
) -> dict[str, tuple[str, bytes, int] | None]:
    """Read and verify one backup entry without changing live files."""
    topology = entry.get("topology")
    captured: dict[str, tuple[str, bytes, int] | None] = {}
    if isinstance(topology, dict):
        backup_dir = entry.get("backup_dir")
        if not isinstance(backup_dir, str):
            raise SystemExit("BLOCKED: topology backup is missing backup_dir")
        for rel, item in topology.items():
            block = validate_patch_path(rel)
            if block:
                raise SystemExit(f"BLOCKED: rollback refuses unsafe path: {block}")
            if item is None:
                captured[rel] = None
                continue
            if not isinstance(item, dict) or item.get("kind") not in ("file", "dir"):
                raise SystemExit(f"BLOCKED: rollback topology is invalid: {rel}")
            kind = item["kind"]
            mode = item.get("mode")
            if not isinstance(mode, int) or isinstance(mode, bool):
                raise SystemExit(f"BLOCKED: rollback topology mode is invalid: {rel}")
            payload = b""
            backup = item.get("backup")
            if kind == "file":
                if not isinstance(backup, str):
                    raise SystemExit(f"BLOCKED: rollback file backup is missing: {rel}")
                backup_path = resolve_snapshot_backup(backup, backup_dir)
                payload = backup_path.read_bytes()
                expected_digest = item.get("sha256")
                expected_size = item.get("size")
                actual_digest = hashlib.sha256(payload).hexdigest()
                if (
                    not isinstance(expected_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                    or not isinstance(expected_size, int)
                    or isinstance(expected_size, bool)
                    or expected_size < 0
                    or len(payload) != expected_size
                    or actual_digest != expected_digest
                ):
                    raise SystemExit(
                        f"BLOCKED: rollback backup bytes are corrupt: {backup}"
                    )
            elif (
                backup is not None
                or item.get("sha256") is not None
                or item.get("size") is not None
            ):
                raise SystemExit(
                    f"BLOCKED: directory backup may not carry bytes: {rel}"
                )
            captured[rel] = (kind, payload, mode)
    else:
        raise SystemExit(
            "BLOCKED: legacy leaf-only backup has no pinned byte integrity or "
            "ancestor topology; use an audited manual recovery"
        )
    return captured


def restore_file_backup_entry(entry: dict[str, Any]) -> None:
    captured = file_backup_preimages(entry)
    try:
        _restore_file_preimages(captured)
    except RuntimeError as exc:
        raise SystemExit(f"BLOCKED: persistent file rollback failed: {exc}") from exc


def rollback_file_backup_suffix(
    current: dict[str, Any], target: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the exact current-branch suffix that a backward rollback undoes."""
    current_events = current.get("events", [])
    target_events = target.get("events", [])
    if (
        not isinstance(current_events, list)
        or not isinstance(target_events, list)
        or current_events[:len(target_events)] != target_events
    ):
        raise SystemExit(
            "BLOCKED: rollback is backward-only; target event history is not "
            "an exact ancestor prefix of the current branch"
        )
    current_entries = current.get("file_backups", [])
    target_entries = target.get("file_backups", [])
    if not isinstance(current_entries, list) or not all(
        isinstance(entry, dict) for entry in current_entries
    ):
        raise SystemExit("BLOCKED: current file-backup ledger is invalid")
    if not isinstance(target_entries, list) or not all(
        isinstance(entry, dict) for entry in target_entries
    ):
        raise SystemExit("BLOCKED: target file-backup ledger is invalid")
    current_ids = [file_backup_identity(entry) for entry in current_entries]
    target_ids = [file_backup_identity(entry) for entry in target_entries]
    if current_ids[:len(target_ids)] != target_ids:
        raise SystemExit(
            "BLOCKED: rollback is backward-only; target file backups are not "
            "an ancestor prefix of the current branch"
        )
    return current_entries[len(target_entries):]


def restore_files_to_target(current: dict[str, Any], target: dict[str, Any]) -> None:
    entries = list(reversed(rollback_file_backup_suffix(current, target)))
    # Verify every referenced backup before the first live-file mutation.
    prepared = [file_backup_preimages(entry) for entry in entries]
    for captured in prepared:
        try:
            _restore_file_preimages(captured)
        except RuntimeError as exc:
            raise SystemExit(
                f"BLOCKED: persistent file rollback failed: {exc}"
            ) from exc


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bootstrap_proposal_projection(proposal: dict[str, Any]) -> dict[str, Any]:
    """Freeze the proposal fields that the schema-1 apply must not change."""
    post_apply_fields = {
        "status", "applied_at", "applied_by_track", "modification_node",
        "affected_nodes", "pre_apply_self_test",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in proposal.items()
        if key not in post_apply_fields
    }


def _read_raw_auto_state() -> tuple[bytes, dict[str, Any]]:
    try:
        if STATE_PATH.is_symlink() or not STATE_PATH.is_file():
            raise RuntimeError("state journal must be a regular non-symlink file")
        payload = STATE_PATH.read_bytes()
        raw = json.loads(payload)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        raise SystemExit(f"BLOCKED: cannot read exact auto-research state: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("BLOCKED: auto-research state root must be an object")
    return payload, raw


def _sha256_file(path: Path, label: str) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("must be a regular non-symlink file")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"BLOCKED: {label} is unavailable: {exc}") from exc


def _simulate_bootstrap_postimage(
    patch: list[dict[str, Any]],
    preimage: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the frozen diff to a minimal exact tree and describe its result."""
    with tempfile.TemporaryDirectory(prefix="auto-research-bootstrap-") as td:
        root = Path(td)
        for item in preimage:
            relative = item.get("path")
            kind = item.get("kind")
            if not isinstance(relative, str):
                raise SystemExit("BLOCKED: bootstrap preimage has an invalid path")
            destination = root / relative
            if kind == "absent":
                continue
            if kind == "dir":
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(int(item["mode"]))
                continue
            if kind == "file":
                source = ROOT / relative
                digest = _sha256_file(source, f"bootstrap preimage {relative}")
                if digest != item.get("sha256"):
                    raise SystemExit(
                        f"BLOCKED: bootstrap preimage drifted while simulating: {relative}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
                destination.chmod(int(item["mode"]))
                continue
            raise SystemExit(
                f"BLOCKED: bootstrap patch traverses unsupported {kind!r}: {relative}"
            )
        try:
            apply_file_patch_text(combined_file_patch(patch), root)
        except RuntimeError as exc:
            raise SystemExit(
                f"BLOCKED: bootstrap candidate cannot be reproduced: {exc}"
            ) from exc
        return patch_target_preimage_manifest(patch, root)


def _ensure_bootstrap_floor(
    raw_state_bytes: bytes,
) -> tuple[str, str]:
    """Create one content-exact, indexed, non-prunable schema-1 rollback floor."""
    floor = SNAP_DIR / "00000000_000000_000_bootstrap_floor.json"
    digest = hashlib.sha256(raw_state_bytes).hexdigest()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    if floor.exists() or floor.is_symlink():
        if floor.is_symlink() or not floor.is_file():
            raise SystemExit("BLOCKED: bootstrap floor path is not a regular file")
        if hashlib.sha256(floor.read_bytes()).hexdigest() != digest:
            raise SystemExit(
                "BLOCKED: a different bootstrap rollback floor already exists"
            )
    else:
        _atomic_replace_bytes(floor, raw_state_bytes, mode=0o444)
    floor.chmod(0o444)
    index = load_snapshot_index()
    existing = index.setdefault("files", {}).get(floor.name)
    if existing is not None and (
        not isinstance(existing, dict) or existing.get("sha256") != digest
    ):
        raise SystemExit("BLOCKED: bootstrap floor conflicts with snapshot index")
    index["files"][floor.name] = {
        "sha256": digest,
        "label": "bootstrap_floor",
        "at": now_iso(),
    }
    save_snapshot_index(index)
    return floor.name, digest


def _bootstrap_manifest_material_sha256(manifest: dict[str, Any]) -> str:
    return _canonical_sha256({
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key != "material_sha256"
    })


def _bootstrap_manifest_path(raw_digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", raw_digest):
        raise SystemExit("BLOCKED: bootstrap manifest digest must be lowercase sha256")
    return BOOTSTRAP_MANIFEST_DIR / f"{raw_digest}.json"


def _load_bootstrap_manifest(raw_digest: str) -> dict[str, Any]:
    path = _bootstrap_manifest_path(raw_digest)
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("manifest must be a regular non-symlink file")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != raw_digest:
            raise RuntimeError("manifest bytes do not match the supplied digest")
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        raise SystemExit(f"BLOCKED: invalid bootstrap manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SystemExit("BLOCKED: bootstrap manifest root must be an object")
    if manifest.get("version") != BOOTSTRAP_MANIFEST_VERSION:
        raise SystemExit("BLOCKED: unsupported bootstrap manifest version")
    if manifest.get("material_sha256") != _bootstrap_manifest_material_sha256(
        manifest
    ):
        raise SystemExit("BLOCKED: bootstrap manifest material digest is invalid")
    return manifest


def _indexed_snapshot_matches(name: str, digest: str) -> bool:
    path = SNAP_DIR / name
    try:
        path.resolve(strict=True).relative_to(SNAP_DIR.resolve(strict=True))
        payload = path.read_bytes()
    except (OSError, ValueError):
        return False
    entry = load_snapshot_index().get("files", {}).get(name)
    return (
        not path.is_symlink()
        and path.is_file()
        and hashlib.sha256(payload).hexdigest() == digest
        and isinstance(entry, dict)
        and entry.get("sha256") == digest
        and entry.get("label") == "bootstrap_floor"
    )


def _verify_bootstrap_prestate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Require the exact schema-1 state and artifacts captured before install."""
    state_bytes, raw = _read_raw_auto_state()
    if raw.get("schema_version", 1) != 1:
        raise SystemExit("BLOCKED: bootstrap install requires an exact schema-1 state")
    if hashlib.sha256(state_bytes).hexdigest() != manifest.get("raw_state_sha256"):
        raise SystemExit("BLOCKED: bootstrap state journal changed after capture")
    if len(raw.get("events", [])) != manifest.get("raw_event_count") or (
        _canonical_sha256(raw.get("events", []))
        != manifest.get("raw_events_sha256")
    ):
        raise SystemExit("BLOCKED: bootstrap event prefix changed after capture")
    live_engine = ROOT / "tools" / "auto_research.py"
    if _sha256_file(live_engine, "live schema-1 engine") != manifest.get(
        "engine_before_sha256"
    ):
        raise SystemExit("BLOCKED: live engine changed after bootstrap capture")
    if _sha256_file(Path(__file__).resolve(), "candidate engine artifact") != manifest.get(
        "engine_after_sha256"
    ):
        raise SystemExit("BLOCKED: candidate engine artifact changed after capture")
    sentinel = ROOT / BOOTSTRAP_SENTINEL
    if _sha256_file(sentinel, "external bootstrap sentinel") != manifest.get(
        "protected_sentinel_sha256"
    ):
        raise SystemExit("BLOCKED: protected bootstrap sentinel changed after capture")
    prop = raw.get("proposals", {}).get(manifest.get("proposal"))
    if not isinstance(prop, dict):
        raise SystemExit("BLOCKED: bootstrap proposal is missing")
    if prop.get("status") != "verified":
        raise SystemExit("BLOCKED: bootstrap proposal is no longer verified")
    critic = prop.get("critic")
    verification = prop.get("verification")
    if (
        not isinstance(critic, dict)
        or critic.get("track") != "B"
        or critic.get("verdict") != "pass"
        or not _substantive_text(critic.get("reason"))
        or not isinstance(verification, dict)
        or verification.get("track") != "B"
        or verification.get("exit_code") != 0
        or verification.get("timeout")
    ):
        raise SystemExit(
            "BLOCKED: bootstrap requires a passing semantic B critique and hard verification"
        )
    if prop.get("retrospective_id"):
        raise SystemExit("BLOCKED: bootstrap proposal may not convert a retrospective")
    if _canonical_sha256(_bootstrap_proposal_projection(prop)) != manifest.get(
        "proposal_projection_sha256"
    ):
        raise SystemExit("BLOCKED: bootstrap proposal changed after capture")
    patch = prop.get("patch")
    if not isinstance(patch, list) or _canonical_sha256(patch) != manifest.get(
        "patch_sha256"
    ):
        raise SystemExit("BLOCKED: bootstrap patch changed after capture")
    preimage = patch_target_preimage_manifest(patch)
    if preimage != manifest.get("patch_target_preimage"):
        raise SystemExit("BLOCKED: bootstrap patch target preimage changed")
    backup_ids = [
        file_backup_identity(entry)
        for entry in raw.get("file_backups", [])
        if isinstance(entry, dict)
    ]
    if backup_ids != manifest.get("legacy_file_backup_ids"):
        raise SystemExit("BLOCKED: bootstrap file-backup prefix changed")
    if not _indexed_snapshot_matches(
        str(manifest.get("rollback_floor")),
        str(manifest.get("rollback_floor_sha256")),
    ):
        raise SystemExit("BLOCKED: bootstrap rollback floor is missing or unindexed")
    return raw


def _cmd_transition_capture_unlocked(args: argparse.Namespace) -> int:
    if args.track != "B":
        raise SystemExit("BLOCKED: bootstrap capture is a B-track responsibility")
    if not _meaningful_text(args.reviewer) or not _substantive_text(args.reason):
        raise SystemExit("BLOCKED: bootstrap capture needs a reviewer and substantive reason")
    live_engine = ROOT / "tools" / "auto_research.py"
    if Path(__file__).resolve() == live_engine.resolve():
        raise SystemExit(
            "BLOCKED: transition-capture must run from the separate candidate artifact"
        )
    state_bytes, raw = _read_raw_auto_state()
    if raw.get("schema_version", 1) != 1:
        raise SystemExit("BLOCKED: transition-capture is only valid for schema 1")
    normalized = normalize_loaded_state(copy.deepcopy(raw))
    state_blocks, _ = validate(normalized)
    if state_blocks:
        raise SystemExit(
            "BLOCKED: schema-1 state cannot migrate cleanly:\n  "
            + "\n  ".join(state_blocks)
        )
    prop = raw.get("proposals", {}).get(args.proposal)
    if not isinstance(prop, dict):
        raise SystemExit(f"Unknown proposal {args.proposal}")
    if prop.get("status") != "verified":
        raise SystemExit("BLOCKED: bootstrap proposal must be verified")
    critic = prop.get("critic")
    verification = prop.get("verification")
    if (
        not isinstance(critic, dict)
        or critic.get("track") != "B"
        or critic.get("verdict") != "pass"
        or not _substantive_text(critic.get("reason"))
        or not isinstance(verification, dict)
        or verification.get("track") != "B"
        or verification.get("exit_code") != 0
        or verification.get("timeout")
    ):
        raise SystemExit(
            "BLOCKED: bootstrap proposal needs a passing B critique and hard verification"
        )
    if prop.get("retrospective_id"):
        raise SystemExit("BLOCKED: bootstrap proposal may not convert a retrospective")
    patch = prop.get("patch")
    if not isinstance(patch, list) or not file_patch_ops(patch):
        raise SystemExit("BLOCKED: bootstrap proposal must contain file patches")
    blocks = validate_patch(normalized, patch)
    if blocks:
        raise SystemExit("BLOCKED: invalid bootstrap patch:\n  " + "\n  ".join(blocks))
    targets = declared_file_patch_paths(combined_file_patch(patch))
    candidate_path = Path(__file__).resolve()
    try:
        candidate_relative = candidate_path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        candidate_relative = None
    if candidate_relative is not None and candidate_relative in targets:
        raise SystemExit(
            "BLOCKED: bootstrap patch may not overwrite its separate candidate artifact"
        )
    if any(
        target == "tests/protected" or target.startswith("tests/protected/")
        for target in targets
    ):
        raise SystemExit("BLOCKED: bootstrap patch may not create its own sentinel")
    engine_mentions = [
        op for op in file_patch_ops(patch)
        if "tools/auto_research.py" in declared_file_patch_paths(
            str(op.get("patch", ""))
        )
    ]
    if (
        len(engine_mentions) != 1
        or declared_file_patch_paths(str(engine_mentions[0].get("patch", "")))
        != ["tools/auto_research.py"]
    ):
        raise SystemExit(
            "BLOCKED: bootstrap patch must contain exactly one engine patch operation"
        )
    engine_before = _sha256_file(live_engine, "live schema-1 engine")
    engine_after = _sha256_file(candidate_path, "candidate engine artifact")
    engine_op = engine_mentions[0]
    if (
        engine_op.get("sha256_before") != engine_before
        or engine_op.get("sha256_after") != engine_after
    ):
        raise SystemExit(
            "BLOCKED: engine patch is not bound to the exact old and candidate artifacts"
        )
    sentinel_digest = _sha256_file(
        ROOT / BOOTSTRAP_SENTINEL, "external bootstrap sentinel",
    )
    preimage = patch_target_preimage_manifest(patch)
    postimage = _simulate_bootstrap_postimage(patch, preimage)
    expected_mod = f"MOD-{int(raw.get('counters', {}).get('node', 0)) + 1:03d}"
    legacy_backup_ids = [
        file_backup_identity(entry)
        for entry in raw.get("file_backups", [])
        if isinstance(entry, dict)
    ]
    # Re-read after all candidate checks, before creating trust artifacts.
    stable_bytes, _ = _read_raw_auto_state()
    if stable_bytes != state_bytes or patch_target_preimage_manifest(patch) != preimage:
        raise SystemExit("BLOCKED: bootstrap inputs changed during capture")
    floor_name, floor_digest = _ensure_bootstrap_floor(state_bytes)
    manifest: dict[str, Any] = {
        "version": BOOTSTRAP_MANIFEST_VERSION,
        "captured_at": now_iso(),
        "track": "B",
        "reviewer": args.reviewer,
        "reason": args.reason,
        "proposal": args.proposal,
        "proposal_projection_sha256": _canonical_sha256(
            _bootstrap_proposal_projection(prop)
        ),
        "patch_sha256": _canonical_sha256(patch),
        "patch_targets": targets,
        "patch_target_preimage": preimage,
        "patch_target_postimage": postimage,
        "raw_state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "raw_event_count": len(raw.get("events", [])),
        "raw_events_sha256": _canonical_sha256(raw.get("events", [])),
        "legacy_file_backup_ids": legacy_backup_ids,
        "expected_modification_node": expected_mod,
        "engine_before_sha256": engine_before,
        "engine_after_sha256": engine_after,
        "protected_sentinel_sha256": sentinel_digest,
        "rollback_floor": floor_name,
        "rollback_floor_sha256": floor_digest,
    }
    manifest["material_sha256"] = _bootstrap_manifest_material_sha256(manifest)
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path = _bootstrap_manifest_path(digest)
    BOOTSTRAP_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise SystemExit("BLOCKED: bootstrap manifest path collision")
    else:
        _atomic_replace_bytes(path, payload, mode=0o444)
    path.chmod(0o444)
    print(f"Bootstrap manifest: {digest}")
    print(f"Artifact: {path.relative_to(ROOT)}")
    print(f"Rollback floor: {floor_name} ({floor_digest})")
    print("Retain the manifest digest outside the candidate process before install.")
    return 0


def _bootstrap_floor_state(manifest: dict[str, Any]) -> dict[str, Any]:
    name = str(manifest.get("rollback_floor"))
    digest = str(manifest.get("rollback_floor_sha256"))
    if not _indexed_snapshot_matches(name, digest):
        raise SystemExit("BLOCKED: bootstrap rollback floor is missing or unindexed")
    try:
        raw = json.loads((SNAP_DIR / name).read_bytes())
    except json.JSONDecodeError as exc:
        raise SystemExit("BLOCKED: bootstrap rollback floor is invalid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema_version", 1) != 1:
        raise SystemExit("BLOCKED: bootstrap rollback floor is not schema 1")
    if digest != manifest.get("raw_state_sha256"):
        raise SystemExit("BLOCKED: bootstrap floor differs from captured raw state")
    return raw


def _bootstrap_time_projection(value: Any) -> Any:
    """Ignore only nondeterministic clock values in the old apply projection."""
    if isinstance(value, dict):
        return {
            key: (
                "<time>"
                if key in {
                    "at", "created_at", "updated_at", "applied_at", "resolved_at",
                }
                and isinstance(item, str)
                else _bootstrap_time_projection(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_bootstrap_time_projection(item) for item in value]
    return value


def _expected_schema1_apply_state(
    pre: dict[str, Any],
    post_backup: dict[str, Any],
    proposal_id: str,
) -> dict[str, Any]:
    """Reproduce the schema-1 state transition without writing patch targets."""
    expected = copy.deepcopy(pre)
    proposal = expected["proposals"][proposal_id]
    patch = proposal["patch"]
    targets = proposal.get("targets", [])
    origins = patch_origins(expected, patch) | set(targets)
    pre_existing = set(expected.get("nodes", {}))
    affected_before = descendants_dependency(expected, origins)
    mod_id = next_id(expected, "MOD", "node")
    marker = now_iso()
    expected.setdefault("nodes", {})[mod_id] = {
        "type": "modification",
        "statement": proposal.get("title", proposal_id),
        "layer": "L4",
        "status": "validated",
        "trust": 1.0,
        "immutable": False,
        "proposal_id": proposal_id,
        "created_at": marker,
        "updated_at": marker,
    }
    changed = apply_raw_patch(expected, patch, write_files=False)
    for target in sorted(origins | changed):
        if target in expected["nodes"] and target not in (META_GOAL_ID, mod_id):
            expected.setdefault("edges", []).append(
                {"from": mod_id, "to": target, "kind": "modification"}
            )
    decay_trust(expected, pre_existing)
    affected = affected_before | descendants_dependency(
        expected, origins | changed,
    )
    affected.discard(META_GOAL_ID)
    invalidate_closure(expected, affected)
    expected["nodes"][mod_id]["status"] = "validated"
    expected["nodes"][mod_id]["trust"] = 1.0
    proposal["status"] = "applied"
    proposal["applied_at"] = marker
    proposal["applied_by_track"] = "B"
    proposal["modification_node"] = mod_id
    proposal["affected_nodes"] = sorted(item for item in affected if item != mod_id)
    expected.setdefault("file_backups", []).append(copy.deepcopy(post_backup))
    expected.setdefault("events", []).append({
        "at": marker,
        "event": "modification_applied",
        "payload": {
            "track": "B",
            "proposal": proposal_id,
            "modification_node": mod_id,
            "targets": sorted(origins | changed),
            "affected_nodes": proposal["affected_nodes"],
        },
    })
    return expected


def _bootstrap_backup_topology(
    manifest: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    backup_dir = entry.get("backup_dir")
    files = entry.get("files")
    if not isinstance(backup_dir, str) or not isinstance(files, dict):
        raise SystemExit("BLOCKED: schema-1 apply produced an invalid backup entry")
    expected_targets = set(manifest.get("patch_targets", []))
    if set(files) != expected_targets:
        raise SystemExit("BLOCKED: bootstrap backup targets differ from the patch")
    topology: dict[str, dict[str, Any] | None] = {}
    for item in manifest.get("patch_target_preimage", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise SystemExit("BLOCKED: bootstrap preimage topology is invalid")
        relative = item["path"]
        kind = item.get("kind")
        if kind == "absent":
            if relative in files and files[relative] is not None:
                raise SystemExit(
                    f"BLOCKED: absent bootstrap target has unexpected backup: {relative}"
                )
            topology[relative] = None
            continue
        if kind == "dir":
            topology[relative] = {
                "kind": "dir", "mode": item.get("mode"),
                "backup": None, "sha256": None, "size": None,
            }
            continue
        if kind != "file":
            raise SystemExit(
                f"BLOCKED: unsupported bootstrap preimage kind {kind!r}: {relative}"
            )
        backup = files.get(relative)
        if not isinstance(backup, str):
            raise SystemExit(f"BLOCKED: bootstrap file backup is missing: {relative}")
        backup_path = resolve_snapshot_backup(backup, backup_dir)
        payload = backup_path.read_bytes()
        if (
            len(payload) != item.get("size")
            or hashlib.sha256(payload).hexdigest() != item.get("sha256")
        ):
            raise SystemExit(f"BLOCKED: bootstrap file backup is corrupt: {relative}")
        topology[relative] = {
            "kind": "file",
            "mode": item.get("mode"),
            "backup": backup,
            "sha256": item.get("sha256"),
            "size": item.get("size"),
        }
    return topology


def _verify_bootstrap_poststate(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_bytes, raw = _read_raw_auto_state()
    if raw.get("schema_version", 1) != 1:
        raise SystemExit("BLOCKED: bootstrap seal expects the unsealed schema-1 poststate")
    if hashlib.sha256(state_bytes).hexdigest() == manifest.get("raw_state_sha256"):
        raise SystemExit("BLOCKED: schema-1 engine has not applied the bootstrap proposal")
    proposal_id = str(manifest.get("proposal"))
    prop = raw.get("proposals", {}).get(proposal_id)
    if not isinstance(prop, dict):
        raise SystemExit("BLOCKED: applied bootstrap proposal is missing")
    if (
        prop.get("status") != "applied"
        or prop.get("applied_by_track") != "B"
        or prop.get("modification_node") != manifest.get("expected_modification_node")
        or not _meaningful_text(prop.get("applied_at"))
    ):
        raise SystemExit("BLOCKED: bootstrap proposal has an unexpected apply result")
    if _canonical_sha256(_bootstrap_proposal_projection(prop)) != manifest.get(
        "proposal_projection_sha256"
    ):
        raise SystemExit("BLOCKED: bootstrap proposal material changed during apply")
    if _canonical_sha256(prop.get("patch")) != manifest.get("patch_sha256"):
        raise SystemExit("BLOCKED: bootstrap patch changed during apply")
    if _sha256_file(
        ROOT / "tools" / "auto_research.py", "installed bootstrap engine",
    ) != manifest.get("engine_after_sha256"):
        raise SystemExit("BLOCKED: installed engine differs from the captured candidate")
    if _sha256_file(
        Path(__file__).resolve(), "candidate bootstrap engine",
    ) != manifest.get("engine_after_sha256"):
        raise SystemExit("BLOCKED: sealing artifact differs from the installed engine")
    if _sha256_file(
        ROOT / BOOTSTRAP_SENTINEL, "external bootstrap sentinel",
    ) != manifest.get("protected_sentinel_sha256"):
        raise SystemExit("BLOCKED: protected bootstrap sentinel changed during install")
    patch = prop.get("patch", [])
    if patch_target_preimage_manifest(patch) != manifest.get(
        "patch_target_postimage"
    ):
        raise SystemExit("BLOCKED: installed patch targets differ from captured postimage")
    events = raw.get("events", [])
    prefix_count = manifest.get("raw_event_count")
    if not isinstance(events, list) or not isinstance(prefix_count, int):
        raise SystemExit("BLOCKED: bootstrap event journal is invalid")
    if len(events) != prefix_count + 1 or _canonical_sha256(
        events[:prefix_count]
    ) != manifest.get("raw_events_sha256"):
        raise SystemExit("BLOCKED: bootstrap event journal is not the captured prefix plus one")
    suffix = events[-1]
    if (
        not isinstance(suffix, dict)
        or suffix.get("event") != "modification_applied"
        or suffix.get("payload", {}).get("track") != "B"
        or suffix.get("payload", {}).get("proposal") != proposal_id
        or suffix.get("payload", {}).get("modification_node")
        != manifest.get("expected_modification_node")
    ):
        raise SystemExit("BLOCKED: bootstrap apply event is not the expected unique suffix")
    entries = raw.get("file_backups", [])
    prefix_ids = manifest.get("legacy_file_backup_ids")
    if not isinstance(entries, list) or not isinstance(prefix_ids, list):
        raise SystemExit("BLOCKED: bootstrap file-backup journal is invalid")
    if len(entries) != len(prefix_ids) + 1 or [
        file_backup_identity(entry) for entry in entries[:-1]
        if isinstance(entry, dict)
    ] != prefix_ids:
        raise SystemExit("BLOCKED: bootstrap backup journal is not the captured prefix plus one")
    last = entries[-1]
    if (
        not isinstance(last, dict)
        or "topology" in last
        or "backup_id" in last
        or last.get("modification_node") != manifest.get("expected_modification_node")
    ):
        raise SystemExit("BLOCKED: schema-1 bootstrap backup has an unexpected shape")
    topology = _bootstrap_backup_topology(manifest, last)
    pre = _bootstrap_floor_state(manifest)
    expected = _expected_schema1_apply_state(pre, last, proposal_id)
    if _bootstrap_time_projection(expected) != _bootstrap_time_projection(raw):
        raise SystemExit(
            "BLOCKED: schema-1 bootstrap poststate differs from the unique apply projection"
        )
    return raw, topology


def _seal_bootstrap_transition(
    manifest_digest: str,
    reviewer: str,
    reason: str,
) -> None:
    if not _meaningful_text(reviewer) or not _substantive_text(reason):
        raise SystemExit("BLOCKED: bootstrap seal needs a reviewer and substantive reason")
    manifest = _load_bootstrap_manifest(manifest_digest)
    state_bytes, raw = _read_raw_auto_state()
    if raw.get("schema_version") == SCHEMA_VERSION:
        transition = raw.get("bootstrap_transition")
        normalized = normalize_loaded_state(copy.deepcopy(raw))
        blocks, _ = validate(normalized)
        if (
            isinstance(transition, dict)
            and transition.get("manifest_sha256") == manifest_digest
            and not blocks
        ):
            print(f"Bootstrap transition already sealed: {manifest_digest}")
            return
        raise SystemExit("BLOCKED: schema-2 state belongs to another or invalid transition")
    if raw.get("schema_version", 1) != 1:
        raise SystemExit("BLOCKED: bootstrap seal only accepts schema 1 or its sealed schema 2")
    raw, topology = _verify_bootstrap_poststate(manifest)
    before_state_bytes = state_bytes
    index_path = SNAP_DIR / SNAP_INDEX_NAME
    before_index = index_path.read_bytes() if index_path.exists() else None
    before_snapshots = set(snapshot_files())
    try:
        state = normalize_loaded_state(copy.deepcopy(raw))
        legacy_ids = copy.deepcopy(manifest["legacy_file_backup_ids"])
        state["legacy_file_backup_ids"] = legacy_ids
        last = state["file_backups"][-1]
        last["topology"] = topology
        last["bootstrap_manifest_sha256"] = manifest_digest
        last["backup_id"] = file_backup_identity(last)
        retention = state.setdefault(
            "snapshot_retention", copy.deepcopy(DEFAULT_SNAPSHOT_RETENTION),
        )
        labels = retention.setdefault("keep_labels", [])
        if "bootstrap_floor" not in labels:
            labels.append("bootstrap_floor")
        state["bootstrap_transition"] = {
            "manifest_sha256": manifest_digest,
            "proposal": manifest["proposal"],
            "rollback_floor": manifest["rollback_floor"],
            "rollback_floor_sha256": manifest["rollback_floor_sha256"],
            "engine_before_sha256": manifest["engine_before_sha256"],
            "engine_after_sha256": manifest["engine_after_sha256"],
            "legacy_file_backup_ids": legacy_ids,
            "sealed_at": now_iso(),
            "sealed_by": reviewer,
            "reason": reason,
        }
        append_event(state, "bootstrap_transition_sealed", {
            "track": "B",
            "proposal": manifest["proposal"],
            "manifest_sha256": manifest_digest,
            "reviewer": reviewer,
        })
        blocks, _ = validate(state)
        if blocks:
            raise RuntimeError(
                "sealed schema-2 state is invalid: " + "; ".join(blocks)
            )
        committed = save_state(
            state, "bootstrap_transition_sealed", prune_snapshot=False,
        )
        require_snapshot_commit(committed, state)
    except BaseException as original_error:
        recovery_errors: list[str] = []
        try:
            _atomic_replace_bytes(STATE_PATH, before_state_bytes)
        except BaseException as exc:
            recovery_errors.append(f"state journal: {exc}")
        try:
            for path in set(snapshot_files()) - before_snapshots:
                path.unlink(missing_ok=True)
        except BaseException as exc:
            recovery_errors.append(f"snapshot files: {exc}")
        try:
            if before_index is None:
                index_path.unlink(missing_ok=True)
            else:
                _atomic_replace_bytes(index_path, before_index)
        except BaseException as exc:
            recovery_errors.append(f"snapshot index: {exc}")
        if recovery_errors:
            raise RuntimeError(
                "bootstrap seal recovery was incomplete: "
                + "; ".join(recovery_errors)
            ) from original_error
        raise
    print(f"Bootstrap transition sealed: {manifest_digest}")


def cmd_transition_seal(args: argparse.Namespace) -> int:
    if args.track != "B":
        raise SystemExit("BLOCKED: bootstrap seal is a B-track responsibility")
    fd = _open_bootstrap_lock()
    try:
        _seal_bootstrap_transition(
            args.manifest_sha256, args.reviewer, args.reason,
        )
        return 0
    finally:
        _release_bootstrap_lock(fd)


def _open_bootstrap_lock() -> int:
    BOOTSTRAP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(BOOTSTRAP_LOCK_PATH, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("lock path is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, RuntimeError) as exc:
        try:
            os.close(fd)
        except (UnboundLocalError, OSError):
            pass
        raise SystemExit(f"BLOCKED: cannot acquire bootstrap lock: {exc}") from exc


def _release_bootstrap_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def cmd_transition_capture(args: argparse.Namespace) -> int:
    fd = _open_bootstrap_lock()
    try:
        return _cmd_transition_capture_unlocked(args)
    finally:
        _release_bootstrap_lock(fd)


def _run_bootstrap_apply_process(
    command: list[str], timeout: int, env: dict[str, str],
) -> dict[str, Any]:
    """Supervise the trusted live-writing migration process with bounded I/O."""
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise SystemExit("BLOCKED: bootstrap process supervision requires POSIX")
    _enable_linux_subreaper()
    baseline = _linux_descendant_pids(os.getpid())
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    stdout = bytearray()
    stderr_tail = bytearray()
    total_bytes = 0
    max_bytes = 8 * 1024 * 1024
    stdout_retain = 1024 * 1024
    stderr_retain = 64 * 1024
    deadline = time.monotonic() + max(1, timeout)
    drain_deadline: float | None = None
    timed_out = False
    output_limit_exceeded = False
    terminated = False

    def terminate() -> None:
        nonlocal terminated, drain_deadline
        if not terminated:
            _terminate_process_group(process.pid)
            terminated = True
        if drain_deadline is None:
            drain_deadline = time.monotonic() + 1.0

    try:
        while selector.get_map():
            now = time.monotonic()
            if not timed_out and not output_limit_exceeded and now >= deadline:
                timed_out = True
                terminate()
            if process.poll() is not None and not terminated:
                terminate()
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait_for = 0.05
            if not timed_out and not output_limit_exceeded:
                wait_for = min(wait_for, max(0.0, deadline - now))
            if drain_deadline is not None:
                wait_for = min(wait_for, max(0.0, drain_deadline - now))
            for key, _ in selector.select(wait_for):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                total_bytes += len(chunk)
                if key.data == "stdout":
                    remaining = stdout_retain - len(stdout)
                    if remaining > 0:
                        stdout.extend(chunk[:remaining])
                else:
                    stderr_tail.extend(chunk)
                    if len(stderr_tail) > stderr_retain:
                        del stderr_tail[:-stderr_retain]
                if total_bytes > max_bytes and not output_limit_exceeded:
                    output_limit_exceeded = True
                    terminate()
        for key in list(selector.get_map().values()):
            try:
                selector.unregister(key.fileobj)
                key.fileobj.close()
            except (OSError, KeyError):
                pass
        if process.poll() is None:
            terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
    except BaseException:
        terminate()
        try:
            process.wait(timeout=1)
        except Exception:
            pass
        _reap_new_descendants(baseline)
        raise
    finally:
        selector.close()
    escaped = _reap_new_descendants(baseline)
    if escaped:
        stderr_tail.extend(
            ("\nBLOCKED: bootstrap apply spawned detached descendants: "
             + ", ".join(str(pid) for pid in escaped)).encode()
        )
    if output_limit_exceeded:
        stderr_tail.extend(b"\nBLOCKED: bootstrap apply output exceeded 8 MiB")
    return {
        "returncode": (
            None if timed_out else (
                1 if escaped or output_limit_exceeded else process.returncode
            )
        ),
        "timeout": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
        "stdout": bytes(stdout).decode("utf-8", "replace"),
        "stderr": bytes(stderr_tail[-stderr_retain:]).decode("utf-8", "replace"),
    }


def cmd_transition_install(args: argparse.Namespace) -> int:
    if args.track != "B":
        raise SystemExit("BLOCKED: bootstrap install is a B-track responsibility")
    if not _meaningful_text(args.reviewer) or not _substantive_text(args.reason):
        raise SystemExit("BLOCKED: bootstrap install needs a reviewer and substantive reason")
    manifest = _load_bootstrap_manifest(args.manifest_sha256)
    fd = _open_bootstrap_lock()
    try:
        _verify_bootstrap_prestate(manifest)
        command = [
            sys.executable,
            str(ROOT / "tools" / "auto_research.py"),
            "apply", "--track", "B", "--proposal", str(manifest["proposal"]),
        ]
        env = os.environ.copy()
        env["RESEARCH_CLOSURE_ROOT"] = str(ROOT)
        result = _run_bootstrap_apply_process(command, args.timeout, env)
        if result["timeout"]:
            raise SystemExit(
                "BLOCKED: schema-1 bootstrap apply timed out; inspect the manifest "
                "and use transition-seal only if the exact poststate exists"
            )
        if result["returncode"] != 0:
            detail = (result["stdout"] + "\n" + result["stderr"])[-4000:].strip()
            raise SystemExit(
                "BLOCKED: schema-1 bootstrap apply failed; no seal attempted"
                + (f":\n{detail}" if detail else "")
            )
        _seal_bootstrap_transition(
            args.manifest_sha256, args.reviewer, args.reason,
        )
        output = result["stdout"].strip()
        if output:
            print(output)
        return 0
    finally:
        _release_bootstrap_lock(fd)


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
        elif kind == "set_launch_prompt":
            state.setdefault("launch_prompts", copy.deepcopy(DEFAULT_LAUNCH_PROMPTS))[op["role"]] = op["prompts"]
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
        elif kind == "set_launch_prompt":
            role = op.get("role")
            prompts = op.get("prompts")
            if role not in TRACKS:
                blocks.append(f"{pos}: role must be one of {list(TRACKS)}")
            elif not isinstance(prompts, list) or not prompts or not all(
                isinstance(item, str) and item.strip() for item in prompts
            ):
                blocks.append(f"{pos}: prompts must be a non-empty list of non-empty strings")
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


def proposal_contract_blocks(prop: dict[str, Any]) -> list[str]:
    """Check a proposal against the exact recommendation contract B accepted."""
    snapshot = prop.get("recommendation_snapshot")
    if not isinstance(snapshot, dict):
        return ["recommendation snapshot is missing"]
    expected_digest = recommendation_sha256(snapshot)
    if prop.get("recommendation_sha256") != expected_digest:
        return ["recommendation digest does not match its frozen snapshot"]
    contract = snapshot.get("implementation_contract")
    blocks = implementation_contract_blocks(
        contract, "selected recommendation implementation_contract",
    )
    if blocks or not isinstance(contract, dict):
        return blocks

    allowed_ops = set(contract.get("allowed_ops", []))
    actual_ops = {
        item.get("op") for item in prop.get("patch", []) if isinstance(item, dict)
    }
    outside_ops = sorted(actual_ops - allowed_ops)
    if outside_ops:
        blocks.append(
            "patch operations outside selected recommendation contract: "
            + ", ".join(outside_ops)
        )

    if "patch_file" in actual_ops:
        try:
            actual_paths = declared_file_patch_paths(
                combined_file_patch(prop.get("patch", []))
            )
        except (TypeError, ValueError) as exc:
            blocks.append(f"cannot inspect contracted patch paths: {exc}")
            actual_paths = []
        allowed_paths = [
            str(path).strip().replace("\\", "/")
            for path in contract.get("allowed_paths", [])
        ]
        for path in actual_paths:
            if not any(
                path == allowed
                or (allowed.endswith("/") and path.startswith(allowed))
                for allowed in allowed_paths
            ):
                blocks.append(
                    f"patch path {path!r} is outside selected recommendation contract"
                )

    targets = set(prop.get("targets") or [])
    missing_targets = sorted(set(contract.get("required_targets", [])) - targets)
    if missing_targets:
        blocks.append(
            "proposal omits contract-required targets: "
            + ", ".join(missing_targets)
        )
    required_targets = set(contract.get("required_targets", []))
    outside_targets = sorted(targets - required_targets)
    if outside_targets:
        blocks.append(
            "proposal adds targets outside selected recommendation contract: "
            + ", ".join(outside_targets)
        )
    command = str(prop.get("verification_command") or "")
    missing_terms = [
        term for term in contract.get("required_verification_terms", [])
        if term not in command
    ]
    if missing_terms:
        blocks.append(
            "verification omits contract-required terms: "
            + ", ".join(missing_terms)
        )
    return blocks


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


def _apply_modification_unchecked(
    state: dict[str, Any],
    proposal_id: str,
    patch: list[dict[str, Any]],
    targets: Iterable[str],
    track: str = "B",
) -> dict[str, Any]:
    proposal = state["proposals"][proposal_id]
    require_fresh_apply_link(state, proposal)
    require_current_review_binding(state, proposal)
    inquiry_id = proposal.get("inquiry_id")
    inquiry = None
    if inquiry_id:
        inquiry = state.get("inquiries", {}).get(inquiry_id)
        if not inquiry or inquiry.get("status") != "supported":
            raise SystemExit(
                f"BLOCKED: linked inquiry {inquiry_id!r} has no supported "
                "outcome at apply time"
            )
        if state.get("active_inquiry_id") != inquiry_id:
            raise SystemExit(
                f"BLOCKED: linked inquiry {inquiry_id!r} is not the active inquiry"
            )
        if proposal.get("recommendation_id") != inquiry.get(
            "selected_recommendation_id"
        ):
            raise SystemExit(
                "BLOCKED: proposal no longer matches the B-selected recommendation"
            )
        selected_recommendation = next(
            (
                item for item in inquiry.get("recommendations", [])
                if item.get("id") == proposal.get("recommendation_id")
            ),
            None,
        )
        if proposal.get("recommendation_snapshot") != selected_recommendation:
            raise SystemExit(
                "BLOCKED: proposal recommendation snapshot no longer matches "
                "the accepted inquiry"
            )
        if proposal.get("recommendation_sha256") != inquiry.get(
            "selected_recommendation_sha256"
        ) or proposal_contract_blocks(proposal):
            raise SystemExit(
                "BLOCKED: proposal no longer satisfies the frozen recommendation contract"
            )
        if proposal_id not in inquiry.get("proposal_ids", []):
            raise SystemExit(
                f"BLOCKED: inquiry {inquiry_id!r} has no backlink to {proposal_id}"
            )
    blocks = validate_patch(state, patch)
    if blocks:
        raise SystemExit("BLOCKED: invalid patch:\n  " + "\n  ".join(blocks))
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
    if inquiry is not None:
        inquiry["status"] = "converted"
        inquiry["converted_by"] = proposal_id
        inquiry["implementation_failure"] = None
        inquiry["resolved_at"] = now_iso()
        inquiry["updated_at"] = now_iso()
        if state.get("active_inquiry_id") == inquiry_id:
            state["active_inquiry_id"] = None
    append_event(
        state,
        "modification_applied",
        {
            "track": track,
            "proposal": proposal_id,
            "modification_node": mod_id,
            "targets": sorted(origins | changed),
            "affected_nodes": proposal["affected_nodes"],
            "inquiry": inquiry_id,
        },
    )
    committed_snapshot = save_state(
        state, "modification_applied", prune_snapshot=False,
    )
    require_snapshot_commit(committed_snapshot, state)
    return {"modification_node": mod_id, "affected_nodes": proposal["affected_nodes"]}


def _capture_file_preimages(
    patch: list[dict[str, Any]],
) -> dict[str, tuple[str, bytes, int] | None]:
    """Capture target leaves and ancestors so rollback restores topology too."""
    return _capture_path_preimages(
        declared_file_patch_paths(combined_file_patch(patch))
    )


def _capture_path_preimages(
    targets: Iterable[str],
) -> dict[str, tuple[str, bytes, int] | None]:
    """Capture exact paths and ancestors for exception-time restoration."""
    paths: set[str] = set()
    for target in targets:
        block = validate_patch_path(target)
        if block:
            raise RuntimeError(f"refusing unsafe transaction target: {block}")
        prefix = Path()
        for component in Path(target).parts:
            prefix = prefix / component
            paths.add(prefix.as_posix())
    captured: dict[str, tuple[str, bytes, int] | None] = {}
    for relative in sorted(paths, key=lambda item: (len(Path(item).parts), item)):
        path = ROOT / relative
        if patch_path_traverses_symlink(ROOT, relative):
            raise RuntimeError(
                f"refusing transaction preimage through symlink: {relative}"
            )
        if not path.exists():
            captured[relative] = None
        elif path.is_dir():
            captured[relative] = (
                "dir", b"", path.stat().st_mode & 0o777,
            )
        elif path.is_file():
            captured[relative] = (
                "file", path.read_bytes(), path.stat().st_mode & 0o777,
            )
        else:
            raise RuntimeError(
                f"unsupported transaction preimage type: {relative}"
            )
    return captured


def _restore_file_preimages(
    captured: dict[str, tuple[str, bytes, int] | None],
) -> None:
    def current_kind(path: Path) -> str | None:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "dir"
        if path.is_file():
            return "file"
        return None

    # Remove new or type-changed entries deepest first. This handles both a
    # newly-created parent directory and file->directory topology changes.
    deepest_first = sorted(
        captured,
        key=lambda item: (len(Path(item).parts), item),
        reverse=True,
    )
    for relative in deepest_first:
        path = ROOT / relative
        content = captured[relative]
        kind = current_kind(path)
        expected_kind = content[0] if content is not None else None
        if kind is None:
            continue
        if content is None or kind != expected_kind:
            if kind == "dir":
                shutil.rmtree(path)
            else:
                path.unlink()

    # Restore original entries shallowest first so parents have the right type.
    shallow_first = sorted(
        captured,
        key=lambda item: (len(Path(item).parts), item),
    )
    for relative in shallow_first:
        content = captured[relative]
        if content is None:
            continue
        path = ROOT / relative
        kind, payload, mode = content
        if kind == "dir":
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace_bytes(path, payload, mode=mode)

    # Fail loudly if a partial restore survived; the caller will still restore
    # state and snapshot journals before reporting the aggregate failure.
    mismatches: list[str] = []
    for relative, content in captured.items():
        path = ROOT / relative
        kind = current_kind(path)
        if content is None:
            if kind is not None:
                mismatches.append(relative)
            continue
        expected_kind, payload, mode = content
        if kind != expected_kind:
            mismatches.append(relative)
        elif expected_kind == "file" and (
            path.read_bytes() != payload
            or (path.stat().st_mode & 0o777) != mode
        ):
            mismatches.append(relative)
        elif expected_kind == "dir" and (path.stat().st_mode & 0o777) != mode:
            mismatches.append(relative)
    if mismatches:
        raise RuntimeError(
            "file topology rollback remains different at: "
            + ", ".join(sorted(mismatches))
        )


def apply_modification(
    state: dict[str, Any],
    proposal_id: str,
    patch: list[dict[str, Any]],
    targets: Iterable[str],
    track: str = "B",
) -> dict[str, Any]:
    """Apply file and semantic state as one recoverable transaction."""
    before_state = copy.deepcopy(state)
    before_state_bytes = STATE_PATH.read_bytes() if STATE_PATH.exists() else None
    before_snapshots = set(snapshot_files())
    file_backup_root = SNAP_DIR / "file_backups"
    file_backup_root_existed = file_backup_root.exists()
    before_file_backups = (
        set(file_backup_root.iterdir()) if file_backup_root_existed else set()
    )
    snapshot_index_path = SNAP_DIR / SNAP_INDEX_NAME
    before_snapshot_index = (
        snapshot_index_path.read_bytes() if snapshot_index_path.exists() else None
    )
    preimages = _capture_file_preimages(patch)
    try:
        result = _apply_modification_unchecked(
            state, proposal_id, patch, targets, track=track,
        )
        blocks, _ = validate(state)
        if blocks:
            raise RuntimeError(
                "post-apply state validation failed: " + "; ".join(blocks)
            )
        return result
    except BaseException as original_error:
        rollback_errors: list[str] = []
        try:
            _restore_file_preimages(preimages)
        except BaseException as exc:
            rollback_errors.append(f"file preimages: {exc}")
        state.clear()
        state.update(before_state)
        try:
            if before_state_bytes is None:
                if STATE_PATH.exists() or STATE_PATH.is_symlink():
                    STATE_PATH.unlink()
            else:
                _atomic_replace_bytes(STATE_PATH, before_state_bytes)
        except BaseException as exc:
            rollback_errors.append(f"state journal: {exc}")
        try:
            for path in set(snapshot_files()) - before_snapshots:
                path.unlink(missing_ok=True)
        except BaseException as exc:
            rollback_errors.append(f"snapshot journal: {exc}")
        try:
            if file_backup_root.exists():
                for path in set(file_backup_root.iterdir()) - before_file_backups:
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                if not file_backup_root_existed:
                    try:
                        file_backup_root.rmdir()
                    except OSError:
                        pass
        except BaseException as exc:
            rollback_errors.append(f"file backup journal: {exc}")
        try:
            if before_snapshot_index is None:
                snapshot_index_path.unlink(missing_ok=True)
            else:
                _atomic_replace_bytes(
                    snapshot_index_path, before_snapshot_index,
                )
        except BaseException as exc:
            rollback_errors.append(f"snapshot index: {exc}")
        if rollback_errors:
            raise RuntimeError(
                "transaction rollback incomplete after "
                f"{type(original_error).__name__}: "
                + "; ".join(rollback_errors)
            ) from original_error
        raise


# ---------------------------------------------------------------------------
# command implementations
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    if STATE_PATH.exists() and not args.force:
        print(f"Auto-research state already exists: {STATE_PATH}")
        return 0
    if args.goal != META_GOAL_STATEMENT:
        raise SystemExit(
            "BLOCKED: M0 is compiled into this harness and cannot be replaced "
            "through init --goal"
        )
    state = skeleton(args.goal)
    append_event(state, "init", {"goal": args.goal})
    save_state(state, "init")
    print(f"Initialized {STATE_PATH}")
    print(f"Immutable L0 meta-goal: {args.goal}")
    print(
        "Next: self-observe, inquire, B review/select, then linked "
        "propose/critique/verify/assess/supported-only apply"
    )
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


def load_inquiry_file(path: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid inquiry JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("BLOCKED: inquiry file must contain a JSON object")
    return raw


def load_outcome_file(path: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid outcome JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("BLOCKED: outcome file must contain a JSON object")
    return raw


def next_inquiry_id(state: dict[str, Any]) -> str:
    existing = []
    for inquiry_id in state.get("inquiries", {}):
        match = re.fullmatch(r"INQ-(\d+)", inquiry_id)
        if match:
            existing.append(int(match.group(1)))
    counter = int(state.setdefault("counters", {}).get("inquiry", 0))
    number = max([counter, *existing], default=0) + 1
    state["counters"]["inquiry"] = number
    return f"INQ-{number:03d}"


def record_inquiry(
    state: dict[str, Any],
    raw: dict[str, Any],
    track: str,
    source: str,
) -> str:
    if track != "A":
        raise SystemExit("BLOCKED: self-inquiries must be authored on track A")
    open_proposals = sorted(
        proposal_id
        for proposal_id, proposal in state.get("proposals", {}).items()
        if proposal.get("status") in (
            "proposed", "challenged", "passed_critic", "verified",
        )
    )
    if open_proposals:
        raise SystemExit(
            "BLOCKED: resolve open proposals before a new inquiry: "
            + ", ".join(open_proposals)
        )
    unresolved = sorted(
        inquiry_id
        for inquiry_id, inquiry in state.get("inquiries", {}).items()
        if inquiry.get("status") in (
            "proposed", "challenged", "accepted", "implementing",
            "awaiting_outcome", "supported",
        )
    )
    if unresolved:
        raise SystemExit(
            "BLOCKED: resolve the current inquiry before opening another: "
            + ", ".join(unresolved)
        )
    blocks = inquiry_payload_blocks(raw)
    blocks.extend(inquiry_context_blocks(state, raw))
    if blocks:
        raise SystemExit("BLOCKED: invalid inquiry:\n  " + "\n  ".join(blocks))

    trial = copy.deepcopy(state)
    inquiry_id = next_inquiry_id(trial)
    record = canonical_inquiry_payload(raw)
    record.update({
        "id": inquiry_id,
        "track": track,
        "source": source,
        "status": "proposed",
        "selected_recommendation_id": None,
        "selected_recommendation_sha256": None,
        "evaluator_snapshot_id": None,
        "evaluator_manifest_sha256": None,
        "inquiry_review_sha256": None,
        "review": None,
        "outcome_plan": None,
        "outcome": None,
        "implementation_failure": None,
        "revisions": [],
        "proposal_ids": [],
        "outcome_proposal_id": None,
        "converted_by": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "resolved_at": None,
    })
    trial.setdefault("inquiries", {})[inquiry_id] = record
    blocks, warnings = validate(trial)
    if blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(blocks))
    append_event(trial, "inquiry_proposed", {
        "track": track,
        "inquiry": inquiry_id,
        "disposition": record["disposition"],
        "source": source,
    })
    state.clear()
    state.update(trial)
    save_state(state, "inquiry_proposed")
    for warning in warnings:
        print(f"WARNING: {warning}")
    return inquiry_id


def cmd_inquire(args: argparse.Namespace) -> int:
    state = load_state()
    inquiry_id = record_inquiry(
        state,
        load_inquiry_file(args.file),
        track=args.track,
        source=args.source,
    )
    print(f"Recorded {inquiry_id}; no proposal or modification was created.")
    print(
        f"Next: review-inquiry --inquiry {inquiry_id} --track B "
        "--verdict pass|challenge|reject --reviewer '<reviewer>' --reason '<reason>'"
    )
    return 0


def cmd_review_inquiry(args: argparse.Namespace) -> int:
    state = load_state()
    if args.track != "B":
        raise SystemExit("BLOCKED: inquiry review is a B-track responsibility")
    inquiry = state.get("inquiries", {}).get(args.inquiry)
    if not inquiry:
        raise SystemExit(f"Unknown inquiry {args.inquiry}")
    if inquiry.get("status") != "proposed":
        raise SystemExit(
            f"BLOCKED: inquiry {args.inquiry} is {inquiry.get('status')}, not awaiting review"
        )
    if not _meaningful_text(args.reviewer) or not _substantive_text(args.reason):
        raise SystemExit("BLOCKED: reviewer and substantive reason are mandatory")

    trial = copy.deepcopy(state)
    item = trial["inquiries"][args.inquiry]
    selected: str | None = None
    if args.verdict == "pass" and item.get("disposition") == "propose":
        if trial.get("active_inquiry_id") not in (None, args.inquiry):
            raise SystemExit(
                f"BLOCKED: active inquiry {trial['active_inquiry_id']} must resolve first"
            )
        selected = args.recommendation
        recommendation_ids = {
            recommendation.get("id")
            for recommendation in item.get("recommendations", [])
            if isinstance(recommendation, dict)
        }
        if selected not in recommendation_ids:
            raise SystemExit(
                "BLOCKED: passing a propose inquiry requires --recommendation "
                "naming one candidate recommendation"
            )
        item["status"] = "accepted"
        item["selected_recommendation_id"] = selected
        selected_item = next(
            recommendation
            for recommendation in item.get("recommendations", [])
            if recommendation.get("id") == selected
        )
        item["selected_recommendation_sha256"] = recommendation_sha256(
            selected_item
        )
        try:
            item["evaluator_snapshot_id"] = freeze_evaluator_snapshot(
                trial, args.inquiry,
            )
        except RuntimeError as exc:
            raise SystemExit(
                f"BLOCKED: could not freeze evaluator at inquiry review: {exc}"
            ) from exc
        item["evaluator_manifest_sha256"] = trial[
            "evaluator_snapshots"
        ][item["evaluator_snapshot_id"]]["manifest_sha256"]
        item["outcome_plan"] = build_outcome_plan(
            item, item["evaluator_snapshot_id"],
        )
        trial["active_inquiry_id"] = args.inquiry
    elif args.verdict == "pass":
        open_proposals = sorted(
            proposal_id
            for proposal_id, proposal in trial.get("proposals", {}).items()
            if proposal.get("status") in (
                "proposed", "challenged", "passed_critic", "verified",
            )
        )
        if open_proposals:
            raise SystemExit(
                "BLOCKED: no_change cannot close while proposals remain open: "
                + ", ".join(open_proposals)
            )
        if args.recommendation:
            raise SystemExit("BLOCKED: no_change review may not select a recommendation")
        item["status"] = "closed_no_change"
        item["selected_recommendation_id"] = None
        item["selected_recommendation_sha256"] = None
        item["evaluator_snapshot_id"] = None
        item["evaluator_manifest_sha256"] = None
        item["outcome_plan"] = None
        item["resolved_at"] = now_iso()
    elif args.recommendation:
        raise SystemExit(
            "BLOCKED: only a passing propose inquiry may select a recommendation"
        )
    elif args.verdict == "challenge":
        item["status"] = "challenged"
        item["evaluator_manifest_sha256"] = None
    else:
        item["status"] = "rejected"
        item["selected_recommendation_sha256"] = None
        item["evaluator_snapshot_id"] = None
        item["evaluator_manifest_sha256"] = None
        item["outcome_plan"] = None
        item["resolved_at"] = now_iso()
    item["review"] = {
        "track": args.track,
        "verdict": args.verdict,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "selected_recommendation_id": selected,
        "evaluator_snapshot_id": item.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": item.get(
            "evaluator_manifest_sha256"
        ),
        "outcome_plan_sha256": (
            item.get("outcome_plan", {}).get("sha256")
            if isinstance(item.get("outcome_plan"), dict)
            else None
        ),
        "at": now_iso(),
    }
    item["inquiry_review_sha256"] = inquiry_review_sha256(item)
    item["review"]["inquiry_review_sha256"] = item[
        "inquiry_review_sha256"
    ]
    item["updated_at"] = now_iso()
    blocks, warnings = validate(trial)
    if blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(blocks))
    event = (
        "inquiry_closed_no_change"
        if item["status"] == "closed_no_change"
        else "inquiry_reviewed"
    )
    append_event(trial, event, {
        "track": args.track,
        "inquiry": args.inquiry,
        "verdict": args.verdict,
        "recommendation": selected,
        "evaluator_snapshot_id": item.get("evaluator_snapshot_id"),
    })
    state.clear()
    state.update(trial)
    save_state(state, event)
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"{args.inquiry}: {item['status']} ({args.verdict})")
    return 0


def cmd_assess_inquiry(args: argparse.Namespace) -> int:
    """Record independent result evidence before any live self-modification."""
    state = load_state()
    if args.track != "B":
        raise SystemExit("BLOCKED: outcome assessment is a B-track responsibility")
    if not _substantive_text(args.assessor):
        raise SystemExit("BLOCKED: outcome assessment requires a substantive assessor")
    inquiry = state.get("inquiries", {}).get(args.inquiry)
    if not inquiry:
        raise SystemExit(f"Unknown inquiry {args.inquiry}")
    if inquiry.get("status") != "awaiting_outcome":
        raise SystemExit(
            f"BLOCKED: inquiry {args.inquiry} is {inquiry.get('status')}, "
            "not awaiting outcome assessment"
        )
    if state.get("active_inquiry_id") != args.inquiry:
        raise SystemExit(
            f"BLOCKED: inquiry {args.inquiry} is not the active inquiry"
        )
    proposal_id = inquiry.get("outcome_proposal_id")
    proposal = state.get("proposals", {}).get(proposal_id or "")
    if not proposal or proposal.get("status") != "verified":
        raise SystemExit(
            "BLOCKED: outcome assessment requires the inquiry's hard-verified proposal"
        )
    plan = inquiry.get("outcome_plan")
    if (
        not isinstance(plan, dict)
        or proposal.get("outcome_plan_sha256") != plan.get("sha256")
    ):
        raise SystemExit(
            "BLOCKED: proposal is not bound to the current frozen outcome plan"
        )
    snapshot_blocks = evaluator_snapshot_runtime_blocks(
        state, inquiry.get("evaluator_snapshot_id"),
    )
    if snapshot_blocks:
        raise SystemExit(
            "BLOCKED: B-frozen evaluator snapshot is unavailable or drifted:\n  "
            + "\n  ".join(snapshot_blocks)
        )
    require_current_review_binding(state, proposal)
    verification_digest = hard_verification_sha256(
        proposal.get("verification")
    )
    if (
        verification_digest is None
        or proposal.get("verification", {}).get(
            "hard_verification_sha256"
        ) != verification_digest
    ):
        raise SystemExit(
            "BLOCKED: hard verification record is unavailable for outcome binding"
        )
    raw = load_outcome_file(args.file)
    blocks = outcome_input_blocks(
        raw, inquiry, require_source_artifact=True,
    )
    if blocks:
        raise SystemExit("BLOCKED: invalid outcome:\n  " + "\n  ".join(blocks))
    try:
        artifact = pin_outcome_artifact(str(raw["artifact_locator"]))
    except OSError as exc:
        raise SystemExit(f"BLOCKED: could not pin outcome artifact: {exc}") from exc

    trial = copy.deepcopy(state)
    item = trial["inquiries"][args.inquiry]
    outcome = canonical_outcome_input(raw)
    outcome.update({
        **artifact,
        "plan_sha256": item["outcome_plan"]["sha256"],
        "inquiry_review_sha256": item["inquiry_review_sha256"],
        "proposal_review_sha256": proposal_review_sha256(proposal),
        "proposal_critique_sha256": proposal_critique_sha256(
            proposal.get("critic")
        ),
        "hard_verification_sha256": verification_digest,
        "evaluator_manifest_sha256": item[
            "evaluator_manifest_sha256"
        ],
        "track": args.track,
        "assessor": args.assessor,
        "at": now_iso(),
    })
    outcome["outcome_sha256"] = outcome_assessment_sha256(outcome)
    item["outcome"] = outcome
    item["updated_at"] = now_iso()
    verdict = outcome["verdict"]
    if verdict == "supported":
        item["status"] = "supported"
        item["resolved_at"] = None
    else:
        item["status"] = verdict
        item["resolved_at"] = now_iso()
        proposal = trial["proposals"][proposal_id]
        proposal["status"] = "terminated"
        proposal["terminated_at"] = now_iso()
        proposal["termination_reason"] = (
            f"outcome {verdict}: {outcome['reason']}"
        )
        trial["active_inquiry_id"] = None
    validation_blocks, warnings = validate(trial)
    if validation_blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(validation_blocks))
    append_event(trial, "inquiry_outcome_assessed", {
        "track": args.track,
        "inquiry": args.inquiry,
        "proposal": proposal_id,
        "verdict": verdict,
        "artifact_sha256": outcome["artifact_sha256"],
        "plan_sha256": outcome["plan_sha256"],
        "outcome_sha256": outcome["outcome_sha256"],
    })
    state.clear()
    state.update(trial)
    save_state(state, "inquiry_outcome_assessed")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"{args.inquiry}: {verdict} ({args.assessor})")
    if verdict == "supported":
        print(f"Next: apply --track B --proposal {proposal_id}")
    else:
        print("Live patch not applied; next inquiry must use this outcome as evidence.")
    return 0


def cmd_revise_inquiry(args: argparse.Namespace) -> int:
    state = load_state()
    inquiry = state.get("inquiries", {}).get(args.inquiry)
    if not inquiry:
        raise SystemExit(f"Unknown inquiry {args.inquiry}")
    if inquiry.get("status") != "challenged":
        raise SystemExit(
            f"BLOCKED: inquiry {args.inquiry} is {inquiry.get('status')}, not revisable"
        )
    if not _substantive_text(args.note):
        raise SystemExit("BLOCKED: revision note is mandatory")
    raw = load_inquiry_file(args.file)
    blocks = inquiry_payload_blocks(raw)
    blocks.extend(inquiry_context_blocks(state, raw))
    if canonical_inquiry_payload(raw) == canonical_inquiry_payload(inquiry):
        blocks.append("revision must change the canonical inquiry payload")
    if blocks:
        raise SystemExit("BLOCKED: invalid inquiry revision:\n  " + "\n  ".join(blocks))
    trial = copy.deepcopy(state)
    item = trial["inquiries"][args.inquiry]
    previous = canonical_inquiry_payload(item)
    item.setdefault("revisions", []).append({
        "at": now_iso(),
        "note": args.note,
        "previous": previous,
        "review": copy.deepcopy(item.get("review")),
    })
    item.update(canonical_inquiry_payload(raw))
    item["status"] = "proposed"
    item["selected_recommendation_id"] = None
    item["selected_recommendation_sha256"] = None
    item["evaluator_snapshot_id"] = None
    item["evaluator_manifest_sha256"] = None
    item["inquiry_review_sha256"] = None
    item["outcome_plan"] = None
    item["outcome"] = None
    item["review"] = None
    item["implementation_failure"] = None
    item["outcome_proposal_id"] = None
    item["converted_by"] = None
    item["resolved_at"] = None
    item["updated_at"] = now_iso()
    blocks, warnings = validate(trial)
    if blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(blocks))
    append_event(trial, "inquiry_revised", {
        "inquiry": args.inquiry,
        "note": args.note,
    })
    state.clear()
    state.update(trial)
    save_state(state, "inquiry_revised")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"{args.inquiry}: revised and awaiting B review")
    return 0


def cmd_inquiry_list(args: argparse.Namespace) -> int:
    state = load_state()
    inquiries = state.get("inquiries", {})
    selected = inquiries if args.all else {
        inquiry_id: inquiry for inquiry_id, inquiry in inquiries.items()
        if inquiry.get("status") not in (
            "rejected", "falsified", "inconclusive", "implementation_failed",
            "closed_no_change", "converted",
        )
    }
    print(f"SELF-INQUIRIES ({len(selected)})")
    for inquiry_id in sorted(selected):
        inquiry = selected[inquiry_id]
        print(
            f"  {inquiry_id:10s} [{inquiry.get('status','?'):16s}] "
            f"{inquiry.get('question_after','')[:90]}"
        )
    if not selected:
        print("  (none)")
    return 0


def cmd_inquiry_show(args: argparse.Namespace) -> int:
    state = load_state()
    inquiry = state.get("inquiries", {}).get(args.inquiry)
    if not inquiry:
        raise SystemExit(f"Unknown inquiry {args.inquiry}")
    if args.json:
        print(json.dumps(inquiry, indent=2, sort_keys=True))
        return 0
    print(f"SELF-INQUIRY {args.inquiry}")
    print(f"  status         : {inquiry.get('status')}")
    print(f"  disposition    : {inquiry.get('disposition')}")
    print(f"  diagnosis      : {inquiry.get('diagnosis')}")
    print(f"  question before: {inquiry.get('question_before')}")
    print(f"  question after : {inquiry.get('question_after')}")
    print(f"  why better     : {inquiry.get('why_better')}")
    print(f"  selected rec   : {inquiry.get('selected_recommendation_id')}")
    print(f"  proposals      : {inquiry.get('proposal_ids') or 'none'}")
    print(f"  outcome plan   : {json.dumps(inquiry.get('outcome_plan') or {}, sort_keys=True)}")
    print(f"  outcome        : {json.dumps(inquiry.get('outcome') or {}, sort_keys=True)}")
    return 0


def historical_patch_paths(proposal: dict[str, Any]) -> set[str]:
    """Extract path hints from stored diffs without requiring old patches to reapply."""
    paths: set[str] = set()
    for op in file_patch_ops(proposal.get("patch", [])):
        declared = op.get("path")
        if isinstance(declared, str) and declared.strip():
            paths.add(_patch_path_norm(declared))
        text = op.get("patch", "")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            if not (line.startswith("--- ") or line.startswith("+++ ")):
                continue
            raw = line[4:].split("\t", 1)[0].strip().strip('"')
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            normalized = _patch_path_norm(raw)
            if normalized:
                paths.add(normalized)
    return paths


def build_self_observation(state: dict[str, Any]) -> dict[str, Any]:
    proposals = list(state.get("proposals", {}).values())
    applied = [proposal for proposal in proposals if proposal.get("status") == "applied"]
    applied_file = [
        proposal for proposal in applied
        if any(op.get("op") == "patch_file" for op in proposal.get("patch", []))
    ]
    applied_with_inquiry = [proposal for proposal in applied if proposal.get("inquiry_id")]
    applied_legacy_unlinked = [
        proposal for proposal in applied
        if not proposal.get("inquiry_id")
    ]
    file_path_sets = [historical_patch_paths(proposal) for proposal in applied_file]
    engine_and_tests_copatched = sum(
        1
        for paths in file_path_sets
        if "tools/auto_research.py" in paths
        and any(path.startswith("tests/") for path in paths)
    )
    test_touching = sum(
        1 for paths in file_path_sets
        if any(path.startswith("tests/") for path in paths)
    )
    levels: dict[str, int] = {}
    verification_records = state.get("verifications", [])
    hard_records: list[dict[str, Any]] = []
    for record in verification_records:
        level = str(record.get("level", "unknown"))
        levels[level] = levels.get(level, 0) + 1
        if level == "hard":
            hard_records.append(record)
    node_statuses: dict[str, int] = {}
    for node in state.get("nodes", {}).values():
        status = str(node.get("status", "unknown"))
        node_statuses[status] = node_statuses.get(status, 0) + 1
    inquiry_statuses: dict[str, int] = {}
    outcome_verdicts: dict[str, int] = {}
    recent_outcomes: list[dict[str, Any]] = []
    for inquiry_id, inquiry in sorted(state.get("inquiries", {}).items()):
        status = str(inquiry.get("status", "unknown"))
        inquiry_statuses[status] = inquiry_statuses.get(status, 0) + 1
        outcome = inquiry.get("outcome")
        if isinstance(outcome, dict):
            verdict = str(outcome.get("verdict", "unknown"))
            outcome_verdicts[verdict] = outcome_verdicts.get(verdict, 0) + 1
            recent_outcomes.append({
                "inquiry": inquiry_id,
                "verdict": verdict,
                "baseline_result": outcome.get("baseline_result"),
                "candidate_result": outcome.get("candidate_result"),
                "next_question": outcome.get("next_question"),
                "artifact_sha256": outcome.get("artifact_sha256"),
                "outcome_sha256": outcome.get("outcome_sha256"),
            })
    total_nodes = len(state.get("nodes", {}))
    validated_nodes = node_statuses.get("validated", 0)
    return {
        "final_goal": state.get("final_goal", {}).get("statement"),
        "proposals": {
            "total": len(proposals),
            "applied": len(applied),
            "applied_with_file_patch": len(applied_file),
            "applied_state_only": len(applied) - len(applied_file),
            "applied_state_only_ratio": round(
                (len(applied) - len(applied_file)) / len(applied), 4
            ) if applied else 0.0,
            "applied_with_reviewed_inquiry": len(applied_with_inquiry),
            "applied_legacy_unlinked": len(applied_legacy_unlinked),
        },
        "evaluation_coupling": {
            "file_patch_proposals": len(applied_file),
            "test_touching_proposals": test_touching,
            "engine_and_tests_same_patch": engine_and_tests_copatched,
            "warning": (
                "Candidate and evaluator changed together; passing tests are not independent evidence."
                if engine_and_tests_copatched
                else None
            ),
        },
        "nodes": {
            "total": total_nodes,
            "by_status": node_statuses,
            "validated_ratio": round(validated_nodes / total_nodes, 4)
            if total_nodes else 0.0,
        },
        "verifications": {
            "total": len(verification_records),
            "by_level": levels,
            "hard_exit_zero": sum(
                1 for record in hard_records if record.get("exit_code") == 0
            ),
            "hard_with_unittest": sum(
                1
                for record in hard_records
                if "unittest" in str(record.get("command", ""))
            ),
            "distinct_hard_commands": len({
                str(record.get("command", "")) for record in hard_records
            }),
        },
        "inquiries": {
            "total": len(state.get("inquiries", {})),
            "by_status": inquiry_statuses,
            "outcomes_by_verdict": outcome_verdicts,
            "recent_outcomes": recent_outcomes[-5:],
            "active": state.get("active_inquiry_id"),
        },
        "open_retrospectives": sorted(
            retrospective_id
            for retrospective_id, retrospective in state.get("retrospectives", {}).items()
            if retrospective.get("status") == "open"
        ),
    }


def cmd_self_observe(args: argparse.Namespace) -> int:
    state = load_state()
    blocks, warnings = validate(state)
    if blocks:
        raise SystemExit(
            "BLOCKED: self-observation refuses invalid research memory:\n  "
            + "\n  ".join(blocks)
        )
    observation = build_self_observation(state)
    observation["integrity"] = {
        "valid": True,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(observation, indent=2, sort_keys=True))
        return 0
    print("SELF-OBSERVATION")
    for key in ("proposals", "nodes", "verifications", "inquiries"):
        print(f"  {key:14s}: {json.dumps(observation[key], sort_keys=True)}")
    print(f"  active inquiry: {observation['inquiries']['active'] or 'none'}")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    state = load_state()
    if args.track != "A":
        raise SystemExit("BLOCKED: proposals must be authored on track A")
    patch = load_patch(args)
    if not patch:
        raise SystemExit("BLOCKED: a proposal requires --patch-file or --patch")
    targets = [t.strip() for t in (args.targets or "").split(",") if t.strip()]
    inquiry = None
    selected_recommendation = None
    if bool(args.inquiry) != bool(args.recommendation):
        raise SystemExit(
            "BLOCKED: --inquiry and --recommendation must be provided together"
        )
    if not args.inquiry:
        raise SystemExit(
            "BLOCKED: every new proposal requires an accepted --inquiry and its "
            "B-selected --recommendation"
        )
    if args.inquiry:
        inquiry = state.get("inquiries", {}).get(args.inquiry)
        if not inquiry:
            raise SystemExit(f"Unknown inquiry {args.inquiry}")
        if state.get("active_inquiry_id") != args.inquiry:
            raise SystemExit(
                f"BLOCKED: {args.inquiry} is not the active accepted inquiry"
            )
        if inquiry.get("status") != "accepted":
            raise SystemExit(
                f"BLOCKED: inquiry {args.inquiry} is {inquiry.get('status')}, not accepted"
            )
        review_blocks = inquiry_review_binding_blocks(inquiry)
        if review_blocks:
            raise SystemExit(
                "BLOCKED: B-reviewed inquiry has drifted:\n  "
                + "\n  ".join(review_blocks)
            )
        if inquiry.get("selected_recommendation_id") != args.recommendation:
            raise SystemExit(
                "BLOCKED: proposal recommendation must match the recommendation selected by B"
            )
        selected_recommendation = next(
            (
                item for item in inquiry.get("recommendations", [])
                if item.get("id") == args.recommendation
            ),
            None,
        )
        if selected_recommendation is None:
            raise SystemExit(
                "BLOCKED: selected recommendation content is missing from the inquiry"
            )
        snapshot_blocks = evaluator_snapshot_runtime_blocks(
            state, inquiry.get("evaluator_snapshot_id"),
        )
        if snapshot_blocks:
            raise SystemExit(
                "BLOCKED: B-frozen evaluator snapshot is unavailable or drifted:\n  "
                + "\n  ".join(snapshot_blocks)
            )
        plan_blocks = outcome_plan_blocks(
            inquiry, inquiry.get("outcome_plan"),
        )
        if plan_blocks:
            raise SystemExit(
                "BLOCKED: B-frozen outcome plan is invalid:\n  "
                + "\n  ".join(plan_blocks)
            )
        active_links = [
            proposal_id for proposal_id in inquiry.get("proposal_ids", [])
            if state.get("proposals", {}).get(proposal_id, {}).get("status")
            in ("proposed", "challenged", "passed_critic", "verified")
        ]
        if active_links:
            raise SystemExit(
                f"BLOCKED: inquiry {args.inquiry} already has active proposal {active_links[0]}"
            )
        if inquiry.get("proposal_ids"):
            raise SystemExit(
                f"BLOCKED: inquiry {args.inquiry} already spent its one proposal authorization"
            )
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
    if not isinstance(args.verification, str) or not args.verification.strip():
        raise SystemExit(
            "BLOCKED: inquiry-linked proposals require --verification so B reviews "
            "the exact hard-verification plan"
        )
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
        "inquiry_id": args.inquiry or None,
        "recommendation_id": args.recommendation or None,
        "recommendation_snapshot": copy.deepcopy(selected_recommendation),
        "recommendation_sha256": recommendation_sha256(selected_recommendation),
        "target_preimage_sha256": patch_target_preimage_sha256(patch),
        "inquiry_review_sha256": inquiry.get("inquiry_review_sha256"),
        "evaluator_snapshot_id": inquiry.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": inquiry.get(
            "evaluator_manifest_sha256"
        ),
        "outcome_plan_sha256": inquiry["outcome_plan"]["sha256"],
        "status": "proposed",
        "preflight": None,
        "critic": None,
        "verification": None,
        "revisions": [],
        "proposed_at": now_iso(),
    }
    state["proposals"][pid] = proposal
    if inquiry is not None:
        inquiry.setdefault("proposal_ids", []).append(pid)
        inquiry["status"] = "implementing"
        inquiry["updated_at"] = now_iso()
    contract_blocks = proposal_contract_blocks(proposal)
    if contract_blocks:
        raise SystemExit(
            "BLOCKED: proposal violates the B-selected recommendation contract:\n  "
            + "\n  ".join(contract_blocks)
        )
    append_event(
        state,
        "proposal_proposed",
        {
            "track": args.track,
            "proposal": pid,
            "title": args.title,
            "retrospective": args.retro or None,
            "inquiry": args.inquiry or None,
            "recommendation": args.recommendation or None,
        },
    )
    blocks, warnings = validate(state)
    if blocks:
        raise SystemExit("BLOCKED:\n  " + "\n  ".join(blocks))
    save_state(state, "proposal_proposed")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"Proposed {pid}: {args.title}")
    print(f"Next: critique --proposal {pid} --verdict pass|challenge|reject "
          "--critic '<critic>' --reason '<reason>'")
    return 0


def settle_linked_proposal_failure(
    state: dict[str, Any],
    prop: dict[str, Any],
    stage: str,
    reason: str,
) -> None:
    """Consume a reviewed inquiry after its single implementation fails."""
    inquiry_id = prop.get("inquiry_id")
    if not inquiry_id:
        return
    inquiry = state.get("inquiries", {}).get(inquiry_id)
    if not inquiry:
        return
    inquiry["status"] = "implementation_failed"
    inquiry["implementation_failure"] = {
        "proposal": prop.get("id"),
        "stage": stage,
        "reason": reason,
        "at": now_iso(),
    }
    inquiry["resolved_at"] = now_iso()
    inquiry["updated_at"] = now_iso()
    if state.get("active_inquiry_id") == inquiry_id:
        state["active_inquiry_id"] = None


def require_proposal_origin(state: dict[str, Any], prop: dict[str, Any]) -> None:
    """Reject fabricated unlinked records created after the schema-2 migration."""
    if prop.get("inquiry_id"):
        return
    proposal_id = prop.get("id")
    if proposal_id not in state.get("legacy_proposal_ids", []):
        raise SystemExit(
            f"BLOCKED: unlinked proposal {proposal_id} is not migration-grandfathered"
        )


def cmd_critique(args: argparse.Namespace) -> int:
    state = load_state()
    if args.track != "B":
        raise SystemExit("BLOCKED: proposal critique is a B-track responsibility")
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    require_proposal_origin(state, prop)
    legacy_retirement = (
        not prop.get("inquiry_id")
        and prop.get("id") in state.get("legacy_proposal_ids", [])
        and prop.get("status") in (
            "proposed", "challenged", "passed_critic", "verified",
        )
        and args.verdict == "reject"
    )
    if prop["status"] not in ("proposed", "challenged") and not legacy_retirement:
        raise SystemExit(f"BLOCKED: proposal {args.proposal} is {prop['status']}, not awaiting critique")
    if not _meaningful_text(args.critic) or not _substantive_text(args.reason):
        raise SystemExit(
            "BLOCKED: semantic B review requires a substantive critic identity "
            "and proposal-specific reason"
        )
    preflight = proposal_mechanical_preflight(state, args.proposal)
    if args.verdict == "pass" and preflight["status"] != "ready_for_B":
        raise SystemExit(
            "BLOCKED: B cannot pass a proposal that failed mechanical preflight:\n  "
            + preflight["reason"]
        )
    prop["preflight"] = preflight
    prop["critic"] = {
        "track": args.track,
        "mode": "semantic_B",
        "verdict": args.verdict,
        "critic": args.critic,
        "reason": args.reason,
        "proposal_review_sha256": preflight["proposal_review_sha256"],
        "inquiry_review_sha256": prop.get("inquiry_review_sha256"),
        "evaluator_snapshot_id": prop.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": prop.get(
            "evaluator_manifest_sha256"
        ),
        "state_basis_sha256": preflight.get("state_basis_sha256"),
        "at": now_iso(),
    }
    prop["critic"]["proposal_critique_sha256"] = proposal_critique_sha256(
        prop["critic"]
    )
    prop["status"] = {
        "pass": "passed_critic",
        "challenge": "challenged",
        "reject": "rejected",
    }[args.verdict]
    if args.verdict == "reject":
        settle_linked_proposal_failure(
            state, prop, "critique", args.reason,
        )
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
    require_proposal_origin(state, prop)
    if not prop.get("inquiry_id"):
        raise SystemExit(
            "BLOCKED: a migration-grandfathered proposal is historical-only "
            "and cannot be revised; B must reject any open intermediate, then "
            "A may open a new evidence-backed inquiry"
        )
    if prop["status"] not in ("challenged", "failed_verification"):
        raise SystemExit(f"BLOCKED: proposal {args.proposal} is {prop['status']}, not revisable")
    if prop.get("inquiry_id") and prop["status"] == "failed_verification":
        raise SystemExit(
            "BLOCKED: a linked verification failure consumed the inquiry's one "
            "implementation authorization; open a new evidence-backed inquiry"
        )
    prop.setdefault("revisions", []).append({"note": args.note, "at": now_iso()})
    prop["status"] = "proposed"
    prop["critic"] = None
    prop["verification"] = None
    prop["preflight"] = None
    append_event(state, "proposal_revised", {"proposal": args.proposal, "note": args.note})
    save_state(state, "proposal_revised")
    print(f"{args.proposal} re-opened for critique")
    return 0


def _terminate_process_group(process_group: int, grace_seconds: float = 0.25) -> None:
    """Terminate every process left in a shell's private process group."""
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            break
        time.sleep(0.01)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _enable_linux_subreaper() -> None:
    """Make double-forked command descendants reparent to this harness."""
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        raise RuntimeError(
            "shell containment requires Linux /proc subreaper support"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise RuntimeError(
            f"could not enable child subreaper: {os.strerror(error)}"
        )


def _linux_descendant_pids(ancestor: int) -> set[int]:
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            parents[int(entry.name)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    descendants: set[int] = set()
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if pid in descendants or pid == ancestor:
                continue
            if parent == ancestor or parent in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _reap_new_descendants(
    baseline: set[int],
    cleanup_seconds: float = 2.0,
) -> list[int]:
    """Kill setsid/double-fork escapees and require a stable empty tree."""
    detected: set[int] = set()
    deadline = time.monotonic() + cleanup_seconds
    term_until = time.monotonic() + min(0.15, cleanup_seconds / 4)
    consecutive_empty = 0
    while time.monotonic() < deadline:
        current = _linux_descendant_pids(os.getpid()) - baseline
        if not current:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                return sorted(detected)
            time.sleep(0.02)
            continue
        consecutive_empty = 0
        detected.update(current)
        chosen_signal = (
            signal.SIGTERM if time.monotonic() < term_until else signal.SIGKILL
        )
        for pid in current:
            try:
                os.kill(pid, chosen_signal)
            except ProcessLookupError:
                pass
        # Once descendants reparent to the subreaper, collect zombies so /proc
        # convergence means no live or unreaped command process remains.
        for pid in current:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass
        time.sleep(0.02)

    emergency_deadline = time.monotonic() + 0.5
    final_survivors = _linux_descendant_pids(os.getpid()) - baseline
    while final_survivors and time.monotonic() < emergency_deadline:
        detected.update(final_survivors)
        for pid in final_survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.02)
        for pid in final_survivors:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass
        final_survivors = _linux_descendant_pids(os.getpid()) - baseline
    if final_survivors:
        # Kill the exact final scan once more before reporting that the live
        # safety boundary could not be proven.
        for pid in final_survivors:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise RuntimeError(
            "live safety unresolved; could not contain detached descendants: "
            + ", ".join(str(pid) for pid in sorted(final_survivors))
        )
    return sorted(detected)


LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
LANDLOCK_WRITE_RIGHTS = (
    LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_CHAR
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SOCK
    | LANDLOCK_ACCESS_FS_MAKE_FIFO
    | LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_REFER
    | LANDLOCK_ACCESS_FS_TRUNCATE
)


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _landlock_syscall_numbers() -> tuple[int, int, int]:
    machine = os.uname().machine.lower() if hasattr(os, "uname") else ""
    if not sys.platform.startswith("linux") or machine not in (
        "x86_64", "aarch64", "riscv64",
    ):
        raise RuntimeError(
            "strict command containment requires Linux Landlock on a supported architecture"
        )
    return 444, 445, 446


def _landlock_syscall(number: int, *args: Any) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    result = libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return int(result)


def _landlock_abi_version() -> int:
    create_ruleset, _, _ = _landlock_syscall_numbers()
    return _landlock_syscall(
        create_ruleset,
        ctypes.c_void_p(),
        ctypes.c_size_t(0),
        ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION),
    )


def _landlock_restrict_writes(write_roots: Iterable[Path]) -> None:
    """Deny filesystem writes except beneath explicitly disposable roots."""
    create_ruleset, add_rule, restrict_self = _landlock_syscall_numbers()
    abi = _landlock_abi_version()
    if abi < 3:
        raise RuntimeError(
            f"strict command containment requires Landlock ABI >= 3; found {abi}"
        )
    ruleset_attr = _LandlockRulesetAttr(LANDLOCK_WRITE_RIGHTS)
    ruleset_fd = _landlock_syscall(
        create_ruleset,
        ctypes.byref(ruleset_attr),
        ctypes.c_size_t(ctypes.sizeof(ruleset_attr)),
        ctypes.c_uint(0),
    )

    def add_path_rule(path: Path, access: int) -> None:
        resolved = path.resolve(strict=True)
        path_fd = os.open(resolved, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _LandlockPathBeneathAttr(access, path_fd)
            _landlock_syscall(
                add_rule,
                ctypes.c_int(ruleset_fd),
                ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
                ctypes.byref(rule),
                ctypes.c_uint(0),
            )
        finally:
            os.close(path_fd)

    try:
        for raw_root in write_roots:
            resolved_root = Path(raw_root).resolve(strict=True)
            if not resolved_root.is_dir():
                raise RuntimeError(
                    f"Landlock write root is not a directory: {resolved_root}"
                )
            add_path_rule(resolved_root, LANDLOCK_WRITE_RIGHTS)
        # Non-interactive shells commonly open /dev/null for redirection. Allow
        # only write/truncate on that exact device, never the /dev subtree.
        dev_null = Path("/dev/null")
        if dev_null.exists():
            add_path_rule(
                dev_null,
                LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_TRUNCATE,
            )
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_ulong,
        ]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        _landlock_syscall(
            restrict_self, ctypes.c_int(ruleset_fd), ctypes.c_uint(0),
        )
    finally:
        os.close(ruleset_fd)


def _landlock_exec_main() -> int:
    """Hidden child entrypoint: confine, then intentionally invoke /bin/sh."""
    if len(sys.argv) != 4:
        raise SystemExit("invalid Landlock helper invocation")
    try:
        payload = json.loads(sys.argv[2])
        roots = payload["write_roots"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"invalid Landlock helper payload: {exc}") from exc
    if not isinstance(roots, list) or not all(
        isinstance(item, str) and item for item in roots
    ):
        raise SystemExit("invalid Landlock write roots")
    _landlock_restrict_writes(Path(item) for item in roots)
    os.execv("/bin/sh", ["/bin/sh", "-c", sys.argv[3]])
    return 127


def _run_shell_process(
    command: str,
    timeout: int,
    cwd: Path,
    env: dict[str, str],
    writable_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    """Run a shell in a private session and reap its process group.

    A shell timeout otherwise kills only the shell while a spawned child can keep
    running and mutate the live repository after the manifest check. Background
    children are also terminated after an apparently successful shell command;
    verification and agent commands must wait for work they intend to count.
    """
    if os.name != "posix" or not hasattr(os, "killpg"):
        raise RuntimeError(
            "shell containment requires POSIX process-group support"
        )
    abi = _landlock_abi_version()
    if abi < 3:
        raise RuntimeError(
            f"strict command containment requires Landlock ABI >= 3; found {abi}"
        )
    live_root = ROOT.resolve()
    allowed: list[str] = []
    for raw_root in writable_roots:
        resolved = Path(raw_root).resolve(strict=True)
        if not resolved.is_dir():
            raise RuntimeError(f"writable command root is not a directory: {resolved}")
        if (
            resolved == live_root
            or resolved.is_relative_to(live_root)
            or live_root.is_relative_to(resolved)
        ):
            raise RuntimeError(
                f"strict containment refuses live repository write root: {resolved}"
            )
        allowed.append(str(resolved))
    _enable_linux_subreaper()
    baseline_descendants = _linux_descendant_pids(os.getpid())
    helper_argv = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "__landlock_exec__",
        json.dumps({"write_roots": allowed}, separators=(",", ":")),
        command,
    ]
    proc = subprocess.Popen(
        helper_argv,
        shell=False,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
    )
    timed_out = False
    output_limit_exceeded = False
    max_output_bytes = 8 * 1024 * 1024
    stderr_tail_limit = 64 * 1024
    stdout_buffer = bytearray()
    stderr_tail = bytearray()
    stdout_hash = hashlib.sha256()
    stderr_hash = hashlib.sha256()
    stdout_bytes = 0
    stderr_bytes = 0
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None and proc.stderr is not None
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    drain_deadline: float | None = None
    group_terminated = False

    def terminate_group() -> None:
        nonlocal group_terminated, drain_deadline
        if not group_terminated:
            _terminate_process_group(proc.pid)
            group_terminated = True
        if drain_deadline is None:
            drain_deadline = time.monotonic() + 1.0

    try:
        while selector.get_map():
            now = time.monotonic()
            if not timed_out and not output_limit_exceeded and now >= deadline:
                timed_out = True
                terminate_group()
            if proc.poll() is not None and not group_terminated:
                # A shell may return while background children still own the
                # pipes. Such work is outside the command result.
                terminate_group()
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait_for = 0.05
            if not timed_out and not output_limit_exceeded:
                wait_for = min(wait_for, max(0.0, deadline - now))
            if drain_deadline is not None:
                wait_for = min(wait_for, max(0.0, drain_deadline - now))
            for key, _ in selector.select(wait_for):
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if key.data == "stdout":
                    stdout_hash.update(chunk)
                    stdout_bytes += len(chunk)
                    remaining = max_output_bytes - len(stdout_buffer)
                    if remaining > 0:
                        stdout_buffer.extend(chunk[:remaining])
                else:
                    stderr_hash.update(chunk)
                    stderr_bytes += len(chunk)
                    stderr_tail.extend(chunk)
                    if len(stderr_tail) > stderr_tail_limit:
                        del stderr_tail[:-stderr_tail_limit]
                if (
                    not output_limit_exceeded
                    and stdout_bytes + stderr_bytes > max_output_bytes
                ):
                    output_limit_exceeded = True
                    terminate_group()
        for key in list(selector.get_map().values()):
            try:
                selector.unregister(key.fileobj)
                key.fileobj.close()
            except (OSError, KeyError):
                pass
        if proc.poll() is None:
            terminate_group()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait(timeout=1)
    except BaseException:
        terminate_group()
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
        _reap_new_descendants(baseline_descendants)
        raise
    finally:
        selector.close()
    escaped_descendants = _reap_new_descendants(baseline_descendants)
    if escaped_descendants and not timed_out and not output_limit_exceeded:
        detached_message = (
            "\nBLOCKED: command spawned detached descendants: "
            + ", ".join(str(pid) for pid in escaped_descendants)
        ).encode("utf-8")
        stderr_hash.update(detached_message)
        stderr_bytes += len(detached_message)
        stderr_tail.extend(detached_message)
        if len(stderr_tail) > stderr_tail_limit:
            del stderr_tail[:-stderr_tail_limit]
    if output_limit_exceeded:
        limit_message = (
            f"\nBLOCKED: command output exceeded {max_output_bytes} bytes"
        ).encode("utf-8")
        stderr_tail.extend(limit_message)
        if len(stderr_tail) > stderr_tail_limit:
            del stderr_tail[:-stderr_tail_limit]
    stdout_raw = bytes(stdout_buffer)
    stderr_raw = bytes(stderr_tail)
    return {
        "returncode": (
            None if timed_out else (
                1 if escaped_descendants or output_limit_exceeded
                else proc.returncode
            )
        ),
        "timeout": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
        "stdout": stdout_raw.decode("utf-8", "replace"),
        "stderr": stderr_raw.decode("utf-8", "replace"),
        "stdout_raw_sha256": stdout_hash.hexdigest(),
        "stdout_raw_bytes": stdout_bytes,
        "stdout_retained_bytes": len(stdout_raw),
        "stderr_raw_sha256": stderr_hash.hexdigest(),
        "stderr_raw_bytes": stderr_bytes,
    }


def run_verification_command(
    command: str,
    timeout: int,
    root: Path = ROOT,
    extra_env: dict[str, str] | None = None,
    guard_live: bool = False,
    guard_root: bool = False,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["RESEARCH_CLOSURE_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    # Arbitrary verification commands are never allowed to opt out of guarding
    # the live repository. ``guard_live`` is retained only for CLI/API
    # compatibility with older callers.
    before = _live_managed_manifest(ROOT)
    before_root = (
        _stable_managed_manifest(root) if guard_root else None
    )
    result = _verification_failure_record(
        "verification process did not produce a result", command,
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="auto-research-command-scratch-",
        ) as scratch_name:
            scratch = Path(scratch_name).resolve()
            env.update({
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
                "XDG_CACHE_HOME": str(scratch / "xdg-cache"),
                "PYTHONPYCACHEPREFIX": str(scratch / "pycache"),
            })
            resolved_root = root.resolve()
            live_root = ROOT.resolve()
            writable_roots: list[Path] = [scratch]
            if resolved_root != live_root and live_root.is_relative_to(resolved_root):
                raise RuntimeError(
                    "verification root may not contain the live repository"
                )
            if (
                resolved_root != live_root
                and not resolved_root.is_relative_to(live_root)
            ):
                writable_roots.append(resolved_root)
            execution = _run_shell_process(
                command, timeout, root, env,
                writable_roots=writable_roots,
            )
        stdout = execution["stdout"]
        stderr = execution["stderr"]
        if execution["timeout"]:
            result = {
                "exit_code": None,
                "timeout": True,
                "stdout_sha256": execution["stdout_raw_sha256"],
                "stdout_bytes": execution["stdout_raw_bytes"],
                "stderr_tail": stderr[-2000:],
                "at": now_iso(),
            }
        else:
            result = {
                "exit_code": execution["returncode"],
                "timeout": False,
                "stdout_sha256": execution["stdout_raw_sha256"],
                "stdout_bytes": execution["stdout_raw_bytes"],
                "stderr_tail": stderr[-2000:],
                "at": now_iso(),
            }
        result["output_limit_exceeded"] = execution.get(
            "output_limit_exceeded", False,
        )
        result["stdout_retained_bytes"] = execution.get(
            "stdout_retained_bytes", execution["stdout_raw_bytes"],
        )
        result["stderr_sha256"] = execution["stderr_raw_sha256"]
        result["stderr_bytes"] = execution["stderr_raw_bytes"]
        result["containment"] = "landlock-write-deny"
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result = _verification_failure_record(
            f"strict command containment unavailable: {exc}", command,
        )
        result["containment"] = "landlock-unavailable-fail-closed"
    finally:
        access_restored, access_errors = _restore_managed_access_modes(
            before, ROOT,
        )
        after = _live_managed_manifest(ROOT)
        changed = sorted(set(access_restored) | set(
            _managed_manifest_changes(before, after)
        ))
        if changed or access_errors:
            restore_errors = [*access_errors]
            restore_errors.extend(_restore_managed_metadata(
                before, after, changed, ROOT,
            ))
            detail = (
                "live managed files changed across a Landlock-confined "
                "verification; restorable metadata was reset, but no byte "
                "restoration is claimed for content/topology changes:\n  "
                + "\n  ".join(changed or ["after-scan access failure"])
            )
            if restore_errors:
                detail += "\nrestoration errors:\n  " + "\n  ".join(
                    restore_errors
                )
            result = {
                "exit_code": 1,
                "timeout": False,
                "stdout_sha256": "",
                "stdout_bytes": 0,
                "stderr_tail": detail[-2000:],
                "at": now_iso(),
                "live_mutation_detected": changed,
                "containment": "landlock-write-deny",
            }
        if before_root is not None:
            root_access_restored, root_access_errors = (
                _restore_managed_access_modes(before_root, root)
            )
            after_root = _stable_managed_manifest(root)
            root_changed = sorted(set(root_access_restored) | set(
                _managed_manifest_changes(
                    before_root, after_root, ignore_directory_times=True,
                )
            ))
            if root_changed or root_access_errors:
                detail = (
                    "verification changed managed files in its disposable "
                    "evaluator tree; result rejected:\n  "
                    + "\n  ".join(
                        root_changed or ["after-scan access failure"]
                    )
                )
                if root_access_errors:
                    detail += "\naccess restoration errors:\n  " + "\n  ".join(
                        root_access_errors
                    )
                result = {
                    "exit_code": 1,
                    "timeout": False,
                    "stdout_sha256": "",
                    "stdout_bytes": 0,
                    "stderr_tail": detail[-2000:],
                    "at": now_iso(),
                    "sandbox_mutation_blocked": root_changed,
                }
    return result


def _sandbox_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", ".ssh_github", "__pycache__"}
    if Path(directory).name == "auto_snapshots":
        retained_evidence = {
            "evaluator_blobs", "outcome_blobs", "file_backups",
        }
        ignored.update(name for name in names if name not in retained_evidence)
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def verification_sandbox_for(prop: dict[str, Any]) -> tempfile.TemporaryDirectory | None:
    patch = prop.get("patch", [])
    if not patch:
        return None
    td = tempfile.TemporaryDirectory(prefix="auto-research-verify-")
    try:
        sandbox = Path(td.name) / "work"
        shutil.copytree(ROOT, sandbox, ignore=_sandbox_ignore)
        state_path = sandbox / ".research" / "auto_research.json"
        if state_path.exists():
            trial = load_state(state_path)
            apply_raw_patch(trial, patch, write_files=False)
            blocks, _ = validate(trial)
            if blocks:
                raise RuntimeError("patched state is invalid: " + "; ".join(blocks))
            state_path.write_text(json.dumps(trial, indent=2, sort_keys=False) + "\n")
        if file_patch_ops(patch):
            apply_file_patch_text(combined_file_patch(patch), sandbox)
        return td
    except Exception:
        td.cleanup()
        raise


def _verification_failure_record(message: str, command: str) -> dict[str, Any]:
    encoded = message.encode("utf-8", "replace")
    return {
        "exit_code": 1,
        "timeout": False,
        "stdout_sha256": "",
        "stdout_bytes": 0,
        "stderr_sha256": hashlib.sha256(encoded).hexdigest(),
        "stderr_bytes": len(encoded),
        "stderr_tail": message[-2000:],
        "at": now_iso(),
        "sandbox": "blocked-before-run",
    }


def cmd_verify(args: argparse.Namespace) -> int:
    state = load_state()
    if args.track != "B":
        raise SystemExit("BLOCKED: proposal verification is a B-track responsibility")
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    require_proposal_origin(state, prop)
    if prop["status"] != "passed_critic":
        raise SystemExit(
            f"BLOCKED: proposal {args.proposal} is {prop['status']}; "
            "it must pass critique before hard verification"
        )
    require_current_review_binding(state, prop)
    planned_command = prop.get("verification_command")
    if (
        prop.get("inquiry_id")
        and args.command is not None
        and args.command != planned_command
    ):
        raise SystemExit(
            "BLOCKED: linked proposal verification is frozen at critique time; "
            "--command may not replace the reviewed plan"
        )
    command = args.command or planned_command
    if not command or not command.strip():
        raise SystemExit("BLOCKED: no verification command; pass --command")

    sandbox: tempfile.TemporaryDirectory | None = None
    sandbox_label = None
    try:
        if prop.get("patch"):
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
                        guard_live=True,
                        guard_root=True,
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
        "requested_timeout_seconds": args.timeout,
    })
    state.setdefault("verifications", []).append(record)
    state["counters"]["verification"] = len(state["verifications"])
    prop["verification"] = {
        "level": "hard",
        "track": args.track,
        "proposal": args.proposal,
        "command": command,
        "exit_code": record["exit_code"],
        "timeout": record["timeout"],
        "stdout_sha256": record["stdout_sha256"],
        "stdout_bytes": record.get("stdout_bytes", 0),
        "stderr_sha256": record.get("stderr_sha256", ""),
        "stderr_bytes": record.get("stderr_bytes", 0),
        "output_limit_exceeded": record.get(
            "output_limit_exceeded", False,
        ),
        "containment": record.get("containment"),
        "requested_timeout_seconds": args.timeout,
        "at": record["at"],
        "proposal_review_sha256": proposal_review_sha256(prop),
        "proposal_critique_sha256": proposal_critique_sha256(
            prop.get("critic")
        ),
        "inquiry_review_sha256": prop.get("inquiry_review_sha256"),
        "evaluator_snapshot_id": prop.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": prop.get(
            "evaluator_manifest_sha256"
        ),
        "state_basis_sha256": prop.get("preflight", {}).get(
            "state_basis_sha256"
        ),
    }
    if sandbox_label:
        prop["verification"]["sandbox"] = sandbox_label
    prop["verification"]["hard_verification_sha256"] = (
        hard_verification_sha256(prop["verification"])
    )
    record.update({
        key: copy.deepcopy(value)
        for key, value in prop["verification"].items()
    })
    passed = not record.get("timeout") and record.get("exit_code") == 0
    prop["status"] = "verified" if passed else "failed_verification"
    if passed and prop.get("inquiry_id"):
        inquiry = state["inquiries"][prop["inquiry_id"]]
        inquiry["status"] = "awaiting_outcome"
        inquiry["outcome_proposal_id"] = prop["id"]
        inquiry["outcome"] = None
        inquiry["updated_at"] = now_iso()
    elif not passed:
        detail = record.get("stderr_tail") or (
            "Hard verification failed or timed out under the frozen command."
        )
        settle_linked_proposal_failure(
            state, prop, "verification", str(detail),
        )
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
    if passed and prop.get("inquiry_id"):
        print(
            f"Next: assess-inquiry --track B --inquiry {prop['inquiry_id']} "
            "--file outcome.json --assessor '<independent assessor>'"
        )
    if record["stderr_tail"]:
        print(record["stderr_tail"].rstrip())
    return 0 if passed else 1


def materialize_frozen_evaluator(
    candidate_root: Path,
    snapshot: dict[str, Any],
) -> None:
    """Replace evaluator roots in one candidate tree with B-pinned bytes."""
    for relative in snapshot["roots"]:
        destination = candidate_root / relative
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
    blob_dir = SNAP_DIR / "evaluator_blobs"
    for entry in snapshot["entries"]:
        blob_path = blob_dir / entry["sha256"]
        payload = blob_path.read_bytes()
        if (
            len(payload) != entry["size"]
            or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        ):
            raise RuntimeError(
                f"missing or corrupt pinned evaluator blob {entry['sha256']}"
            )
        destination = candidate_root / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        destination.chmod(entry["mode"])


def run_pre_apply_self_test(
    state: dict[str, Any],
    prop: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    """Run three evaluator legs in isolated, sequential candidate trees.

    A future leg's tree must not exist while candidate code is running in the
    current leg.  Otherwise candidate code can locate and rewrite sibling
    temporary trees before their evaluator bytes are loaded.
    """
    if not prop.get("patch"):
        return _verification_failure_record(
            "proposal has no patch candidate for pre-apply self-test", "",
        )
    try:
        candidate_state = copy.deepcopy(state)
        apply_raw_patch(candidate_state, prop["patch"], write_files=False)
        candidate_command = candidate_state.get("self_test_command")
    except Exception as exc:
        return _verification_failure_record(
            f"could not derive patched candidate state: {exc}", "",
        )
    snapshot_id = prop.get("evaluator_snapshot_id")
    snapshot_blocks = evaluator_snapshot_runtime_blocks(state, snapshot_id)
    if snapshot_blocks:
        return _verification_failure_record(
            "B-frozen evaluator snapshot is unavailable or drifted:\n"
            + "\n".join(snapshot_blocks),
            "",
        )
    snapshot = state["evaluator_snapshots"][snapshot_id]
    original_frozen_command = snapshot.get("frozen_self_test_command")
    trusted_frozen_command = snapshot.get("trusted_frozen_command")
    protected_command = snapshot.get("protected_command")
    if not isinstance(candidate_command, str) or not candidate_command.strip():
        return _verification_failure_record(
            "patched candidate has no configured self-test command", "",
        )
    if (
        not isinstance(original_frozen_command, str)
        or not original_frozen_command.strip()
        or not isinstance(trusted_frozen_command, str)
        or not trusted_frozen_command.strip()
    ):
        return _verification_failure_record(
            "pinned evaluator has no frozen pre-patch self-test command", "",
        )
    if not isinstance(protected_command, str) or not protected_command.strip():
        return _verification_failure_record(
            "pinned evaluator has no protected command", "",
        )
    frozen_command = (
        trusted_frozen_command + " && " + original_frozen_command
    )

    legs = [
        ("protected", protected_command, True),
        ("frozen_pre_patch", frozen_command, True),
        ("candidate_post_patch", candidate_command, False),
    ]
    checks: list[dict[str, Any]] = []
    for label, command, use_frozen_evaluator in legs:
        sandbox: tempfile.TemporaryDirectory | None = None
        try:
            sandbox = verification_sandbox_for(prop)
            if sandbox is None:
                raise RuntimeError("proposal has no patch candidate")
            check_root = Path(sandbox.name) / "work"
            if use_frozen_evaluator:
                materialize_frozen_evaluator(check_root, snapshot)
            check = run_verification_command(
                command,
                timeout,
                root=check_root,
                guard_live=True,
                guard_root=True,
            )
        except Exception as exc:
            check = _verification_failure_record(
                f"could not build isolated {label} evaluator tree: {exc}",
                command,
            )
        finally:
            if sandbox is not None:
                sandbox.cleanup()
        check["label"] = label
        check["command"] = command
        checks.append(check)

    failed = next(
        (
            check for check in checks
            if check.get("timeout") or check.get("exit_code") != 0
        ),
        None,
    )
    digest_material = json.dumps(
        [check.get("stdout_sha256") for check in checks],
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "exit_code": failed.get("exit_code") if failed else 0,
        "timeout": bool(failed and failed.get("timeout")),
        "stdout_sha256": hashlib.sha256(digest_material).hexdigest(),
        "stdout_bytes": sum(int(check.get("stdout_bytes", 0)) for check in checks),
        "stderr_tail": "\n".join(
            f"[{check['label']}] {check.get('stderr_tail', '')}".rstrip()
            for check in checks if check.get("stderr_tail")
        )[-2000:],
        "at": now_iso(),
        "track": "B",
        "proposal": prop.get("id"),
        "level": "pre_apply_self_test",
        "command": " && ".join(command for _, command, _ in legs),
        "sandbox": "sequential-temporary-copies",
        "evaluator_snapshot_id": snapshot_id,
        "evaluator_manifest_sha256": snapshot.get("manifest_sha256"),
        "frozen_evaluator_paths": list(snapshot["roots"]),
        "checks": checks,
    }


def require_fresh_apply_link(state: dict[str, Any], prop: dict[str, Any]) -> None:
    inquiry_id = prop.get("inquiry_id")
    if not inquiry_id:
        return
    inquiry = state.get("inquiries", {}).get(inquiry_id)
    if not inquiry or inquiry.get("status") != "supported":
        raise SystemExit(
            f"BLOCKED: linked inquiry {inquiry_id!r} has no supported "
            "outcome at apply time"
        )
    if state.get("active_inquiry_id") != inquiry_id:
        raise SystemExit(
            f"BLOCKED: linked inquiry {inquiry_id!r} is not the active inquiry"
        )
    if prop.get("recommendation_id") != inquiry.get("selected_recommendation_id"):
        raise SystemExit(
            "BLOCKED: proposal no longer matches the B-selected recommendation"
        )
    if prop.get("recommendation_sha256") != inquiry.get(
        "selected_recommendation_sha256"
    ) or proposal_contract_blocks(prop):
        raise SystemExit(
            "BLOCKED: proposal no longer satisfies the frozen recommendation contract"
        )
    if prop.get("id") not in inquiry.get("proposal_ids", []):
        raise SystemExit(
            f"BLOCKED: inquiry {inquiry_id!r} has no backlink to {prop.get('id')}"
        )
    outcome = inquiry.get("outcome")
    if (
        inquiry.get("outcome_proposal_id") != prop.get("id")
        or not isinstance(outcome, dict)
        or outcome.get("verdict") != "supported"
        or outcome.get("plan_sha256") != prop.get("outcome_plan_sha256")
    ):
        raise SystemExit(
            "BLOCKED: apply requires a supported B outcome bound to this "
            "proposal and its frozen outcome plan"
        )
    outcome_blocks = outcome_record_blocks(inquiry, outcome, prop)
    if outcome_blocks:
        raise SystemExit(
            "BLOCKED: supported outcome artifact is unavailable or changed:\n  "
            + "\n  ".join(outcome_blocks)
        )


def require_current_review_binding(
    state: dict[str, Any],
    prop: dict[str, Any],
) -> None:
    """Fail closed if proposal/evaluator bytes differ from B's reviewed digest."""
    if not prop.get("inquiry_id"):
        raise SystemExit(
            "BLOCKED: migrated open proposal has no acceptance-time evaluator "
            "snapshot or semantic B-review binding; historical terminal records "
            "remain readable, but this proposal cannot continue"
        )
    inquiry = state.get("inquiries", {}).get(prop.get("inquiry_id"))
    if not isinstance(inquiry, dict):
        raise SystemExit("BLOCKED: linked B-reviewed inquiry is missing")
    inquiry_blocks = inquiry_review_binding_blocks(inquiry)
    if (
        inquiry_blocks
        or prop.get("inquiry_review_sha256")
        != inquiry.get("inquiry_review_sha256")
    ):
        raise SystemExit(
            "BLOCKED: B-reviewed inquiry changed after acceptance: "
            + "; ".join(inquiry_blocks or ["digest mismatch"])
        )
    snapshot = state.get("evaluator_snapshots", {}).get(
        prop.get("evaluator_snapshot_id")
    )
    if (
        not isinstance(snapshot, dict)
        or prop.get("evaluator_manifest_sha256")
        != inquiry.get("evaluator_manifest_sha256")
        or prop.get("evaluator_manifest_sha256")
        != snapshot.get("manifest_sha256")
    ):
        raise SystemExit(
            "BLOCKED: full evaluator manifest changed after B acceptance"
        )
    expected_digest = proposal_review_sha256(prop)
    preflight = prop.get("preflight")
    critic = prop.get("critic")
    if (
        not isinstance(preflight, dict)
        or preflight.get("status") != "ready_for_B"
        or preflight.get("proposal_review_sha256") != expected_digest
        or preflight.get("inquiry_review_sha256")
        != prop.get("inquiry_review_sha256")
        or not isinstance(critic, dict)
        or critic.get("mode") != "semantic_B"
        or critic.get("verdict") != "pass"
        or critic.get("proposal_review_sha256") != expected_digest
        or critic.get("inquiry_review_sha256")
        != prop.get("inquiry_review_sha256")
        or critic.get("evaluator_snapshot_id")
        != prop.get("evaluator_snapshot_id")
        or preflight.get("evaluator_manifest_sha256")
        != prop.get("evaluator_manifest_sha256")
        or critic.get("evaluator_manifest_sha256")
        != prop.get("evaluator_manifest_sha256")
        or not _meaningful_text(preflight.get("state_basis_sha256"))
        or critic.get("state_basis_sha256")
        != preflight.get("state_basis_sha256")
        or critic.get("proposal_critique_sha256")
        != proposal_critique_sha256(critic)
    ):
        raise SystemExit(
            "BLOCKED: proposal or evaluator changed after the semantic B review"
        )
    if preflight.get("state_basis_sha256") != state_basis_sha256(state):
        raise SystemExit(
            "BLOCKED: graph/policy state changed after semantic B review"
        )
    verification = prop.get("verification")
    if isinstance(verification, dict) and (
        verification.get("proposal") != prop.get("id")
        or verification.get("proposal_review_sha256") != expected_digest
        or verification.get("proposal_critique_sha256")
        != proposal_critique_sha256(critic)
        or verification.get("inquiry_review_sha256")
        != prop.get("inquiry_review_sha256")
        or verification.get("evaluator_snapshot_id")
        != prop.get("evaluator_snapshot_id")
        or verification.get("evaluator_manifest_sha256")
        != prop.get("evaluator_manifest_sha256")
        or verification.get("state_basis_sha256")
        != preflight.get("state_basis_sha256")
        or verification.get("hard_verification_sha256")
        != hard_verification_sha256(verification)
    ):
        raise SystemExit(
            "BLOCKED: hard verification belongs to a different proposal context"
        )
    current = proposal_mechanical_preflight(state, prop.get("id", ""))
    if current.get("status") != "ready_for_B":
        raise SystemExit(
            "BLOCKED: current mechanical preflight failed: "
            + str(current.get("reason"))
        )


def cmd_apply(args: argparse.Namespace) -> int:
    state = load_state()
    if args.track != "B":
        raise SystemExit("BLOCKED: proposal application is a B-track responsibility")
    prop = state["proposals"].get(args.proposal)
    if not prop:
        raise SystemExit(f"Unknown proposal {args.proposal}")
    require_proposal_origin(state, prop)
    if prop["status"] != "verified":
        raise SystemExit(
            f"BLOCKED: proposal {args.proposal} is {prop['status']}; "
            "it must pass hard verification before application"
        )
    if not prop.get("patch"):
        raise SystemExit(f"BLOCKED: proposal {args.proposal} has no patch")
    require_fresh_apply_link(state, prop)
    require_current_review_binding(state, prop)
    closure_record = run_pre_apply_self_test(state, prop, args.timeout)
    state.setdefault("verifications", []).append(closure_record)
    state["counters"]["verification"] = len(state["verifications"])
    closure_passed = (
        not closure_record.get("timeout")
        and closure_record.get("exit_code") == 0
    )
    prop["pre_apply_self_test"] = {
        key: closure_record.get(key)
        for key in (
            "command", "exit_code", "timeout", "stdout_sha256", "at", "sandbox",
        )
    }
    if not closure_passed:
        prop["status"] = "failed_verification"
        reason = closure_record.get("stderr_tail") or (
            "The patched candidate failed its configured pre-apply self-test."
        )
        settle_linked_proposal_failure(
            state, prop, "pre_apply", str(reason),
        )
        append_event(state, "pre_apply_self_test_failed", {
            "track": args.track,
            "proposal": args.proposal,
            "exit_code": closure_record.get("exit_code"),
        })
        save_state(state, "pre_apply_self_test_failed")
        print(
            f"{args.proposal}: pre-apply self-test failed "
            f"(exit {closure_record.get('exit_code')}); live patch not applied"
        )
        return 1
    state["last_self_test"] = {
        "passed": True,
        "exit_code": 0,
        "stdout_sha256": closure_record.get("stdout_sha256"),
        "at": closure_record.get("at"),
        "candidate": True,
        "proposal": args.proposal,
    }
    append_event(state, "pre_apply_self_test_passed", {
        "track": args.track,
        "proposal": args.proposal,
        "sandbox": "temporary-copy",
    })
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
    if args.track != "B":
        raise SystemExit("BLOCKED: invalidation is a B-track responsibility")
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
    if args.track != "B":
        raise SystemExit("BLOCKED: revalidation is a B-track responsibility")
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
    if args.json:
        print(json.dumps({
            "mode": "self-test",
            "passed": passed,
            "record": record,
        }, indent=2, sort_keys=True))
    else:
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


def cmd_snapshot_reindex(_: argparse.Namespace) -> int:
    index = rebuild_snapshot_index()
    print(f"Snapshot index rebuilt: {len(index.get('files', {}))} entries")
    return 0


def cmd_file_backup_stats(args: argparse.Namespace) -> int:
    state = load_state()
    entries = state.get("file_backups", [])
    total_files = sum(len(entry.get("files", {})) for entry in entries)
    total_bytes = 0
    for entry in entries:
        backup_dir = ROOT / entry.get("backup_dir", "")
        if backup_dir.exists():
            total_bytes += sum(p.stat().st_size for p in backup_dir.rglob("*") if p.is_file())
    data = {
        "mode": "file-backup-stats",
        "entries": len(entries),
        "files": total_files,
        "bytes": total_bytes,
    }
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print("FILE BACKUPS")
    print(f"  entries={len(entries)} files={total_files} bytes={total_bytes}")
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
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print_health_report(report)
        if args.once:
            if report["critical"]:
                return 2
            if args.strict and report.get("warnings"):
                return 1
            return 0
        time.sleep(max(1, args.interval))


def verified_rollback_snapshot(path: Path) -> dict[str, Any]:
    """Load one exact, indexed, structurally valid snapshot for rollback."""
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("snapshot must be a regular non-symlink file")
        resolved = path.resolve(strict=True)
        resolved.relative_to(SNAP_DIR.resolve(strict=True))
        payload = path.read_bytes()
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"BLOCKED: rollback snapshot is unsafe: {exc}") from exc
    digest = hashlib.sha256(payload).hexdigest()
    index_entry = load_snapshot_index().get("files", {}).get(path.name)
    if not isinstance(index_entry, dict) or index_entry.get("sha256") != digest:
        raise SystemExit(
            "BLOCKED: rollback snapshot bytes do not match the verified index"
        )
    try:
        restored = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"BLOCKED: rollback snapshot is not valid JSON: {exc}") from exc
    if not isinstance(restored, dict):
        raise SystemExit("BLOCKED: rollback snapshot state must be an object")
    normalized = normalize_loaded_state(copy.deepcopy(restored))
    blocks, _ = validate(normalized)
    if blocks:
        raise SystemExit(
            "BLOCKED: rollback snapshot state is invalid:\n  "
            + "\n  ".join(blocks)
        )
    return restored


def _enforce_bootstrap_rollback_boundary(
    current_raw: dict[str, Any],
    current: dict[str, Any],
    restored: dict[str, Any],
    target_path: Path,
) -> bool:
    """Return true only for the exact recorded schema-1 bootstrap floor."""
    if current_raw.get("schema_version", 1) == 1 and SCHEMA_VERSION > 1:
        raise SystemExit(
            "BLOCKED: schema-1 state is an unsealed bootstrap poststate; use "
            "transition-seal with the retained manifest digest"
        )
    transition = current.get("bootstrap_transition")
    target_schema = restored.get("schema_version", 1)
    if target_schema == 1:
        if not isinstance(transition, dict):
            raise SystemExit(
                "BLOCKED: schema-1 rollback requires a recorded bootstrap transition"
            )
        payload_digest = hashlib.sha256(target_path.read_bytes()).hexdigest()
        if (
            target_path.name != transition.get("rollback_floor")
            or payload_digest != transition.get("rollback_floor_sha256")
        ):
            raise SystemExit(
                "BLOCKED: rollback may cross schema 2 only at its exact bootstrap floor"
            )
        return True
    if isinstance(transition, dict):
        target_transition = restored.get("bootstrap_transition")
        if (
            not isinstance(target_transition, dict)
            or target_transition.get("manifest_sha256")
            != transition.get("manifest_sha256")
        ):
            raise SystemExit(
                "BLOCKED: schema-2 rollback target belongs to another transition"
            )
    return False


def _bootstrap_snapshot_archive_plan(
    current: dict[str, Any],
    floor_path: Path,
    index_bytes: bytes,
) -> dict[str, Any]:
    """Preflight the downgrade archive without moving a single snapshot."""
    transition = current.get("bootstrap_transition")
    digest = (
        transition.get("manifest_sha256")
        if isinstance(transition, dict)
        else None
    )
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(
            "BLOCKED: bootstrap rollback has no safe transition digest for "
            "snapshot archival"
        )
    archive_root = SNAP_DIR / "bootstrap_archives" / digest
    if archive_root.exists() or archive_root.is_symlink():
        raise SystemExit(
            "BLOCKED: bootstrap snapshot archive already exists; inspect or "
            "recover it before retrying the schema downgrade"
        )
    try:
        raw_index = json.loads(index_bytes)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"BLOCKED: snapshot index cannot be archived: {exc}"
        ) from exc
    index_files = raw_index.get("files") if isinstance(raw_index, dict) else None
    if not isinstance(index_files, dict):
        raise SystemExit("BLOCKED: snapshot index has no valid files map")
    floor_entry = index_files.get(floor_path.name)
    floor_digest = hashlib.sha256(floor_path.read_bytes()).hexdigest()
    if (
        not isinstance(floor_entry, dict)
        or floor_entry.get("sha256") != floor_digest
    ):
        raise SystemExit(
            "BLOCKED: bootstrap floor is not exactly bound by the snapshot index"
        )

    items: list[dict[str, Any]] = []
    for path in snapshot_files():
        if path == floor_path:
            continue
        if path.is_symlink() or not path.is_file():
            raise SystemExit(
                f"BLOCKED: snapshot archive refuses unsafe path: {path.name}"
            )
        payload = path.read_bytes()
        item_digest = hashlib.sha256(payload).hexdigest()
        index_entry = index_files.get(path.name)
        if (
            not isinstance(index_entry, dict)
            or index_entry.get("sha256") != item_digest
        ):
            raise SystemExit(
                "BLOCKED: snapshot archive requires every root snapshot to "
                f"match the verified index: {path.name}"
            )
        items.append({
            "name": path.name,
            "source": path,
            "destination": archive_root / "snapshots" / path.name,
            "sha256": item_digest,
            "size": len(payload),
            "index_entry": copy.deepcopy(index_entry),
        })
    return {
        "archive_root": archive_root,
        "floor_path": floor_path,
        "floor_entry": copy.deepcopy(floor_entry),
        "index_bytes": index_bytes,
        "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
        "transition_manifest_sha256": digest,
        "items": items,
        "moved": [],
        "created": False,
    }


def _archive_snapshot_move(source: Path, destination: Path) -> None:
    """Narrow replace seam used by rollback recovery tests."""
    os.replace(source, destination)


def _apply_bootstrap_snapshot_archive(plan: dict[str, Any]) -> None:
    archive_root = plan["archive_root"]
    snapshots_root = archive_root / "snapshots"
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(exist_ok=False)
    plan["created"] = True
    snapshots_root.mkdir()
    _atomic_replace_bytes(
        archive_root / "original_index.json",
        plan["index_bytes"],
        mode=0o444,
    )
    for item in plan["items"]:
        _archive_snapshot_move(item["source"], item["destination"])
        plan["moved"].append(item)
    persistent_manifest = {
        "version": 1,
        "transition_manifest_sha256": plan["transition_manifest_sha256"],
        "floor": plan["floor_path"].name,
        "floor_sha256": hashlib.sha256(
            plan["floor_path"].read_bytes()
        ).hexdigest(),
        "original_index": "original_index.json",
        "original_index_sha256": plan["index_sha256"],
        "archived_at": now_iso(),
        "snapshots": [
            {
                key: copy.deepcopy(item[key])
                for key in ("name", "sha256", "size", "index_entry")
            }
            for item in plan["items"]
        ],
    }
    _atomic_replace_bytes(
        archive_root / "archive_manifest.json",
        (
            json.dumps(persistent_manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        mode=0o444,
    )
    save_snapshot_index({
        "version": 1,
        "files": {
            plan["floor_path"].name: copy.deepcopy(plan["floor_entry"]),
        },
    })
    visible = snapshot_files()
    if visible != [plan["floor_path"]]:
        raise RuntimeError(
            "bootstrap downgrade archive did not leave exactly the floor visible"
        )
    visible_index = load_snapshot_index().get("files", {})
    if list(visible_index) != [plan["floor_path"].name]:
        raise RuntimeError(
            "bootstrap downgrade index did not retain exactly the floor"
        )


def _recover_bootstrap_snapshot_archive(plan: dict[str, Any]) -> None:
    """Undo every completed archive move after a synchronous rollback error."""
    for item in reversed(plan.get("items", [])):
        source = item["source"]
        destination = item["destination"]
        source_exists = source.exists() or source.is_symlink()
        destination_exists = (
            destination.exists() or destination.is_symlink()
        )
        if source_exists and not destination_exists:
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size != item["size"]
                or hashlib.sha256(source.read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise RuntimeError(
                    f"unmoved snapshot drifted during recovery: {item['name']}"
                )
            continue
        if not source_exists and destination_exists:
            if (
                destination.is_symlink()
                or not destination.is_file()
                or destination.stat().st_size != item["size"]
                or hashlib.sha256(destination.read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise RuntimeError(
                    f"archived snapshot is corrupt: {item['name']}"
                )
            os.replace(destination, source)
            continue
        if source_exists and destination_exists:
            raise RuntimeError(
                f"snapshot exists at both archive endpoints: {item['name']}"
            )
        raise RuntimeError(
            f"snapshot is missing at both archive endpoints: {item['name']}"
        )
    archive_root = plan["archive_root"]
    if plan.get("created") and archive_root.exists():
        shutil.rmtree(archive_root)


def cmd_rollback(args: argparse.Namespace) -> int:
    files = list_snapshots()
    if not files:
        if args.dry_run:
            print("ROLLBACK PREVIEW")
            print("  no snapshots available")
            return 0
        raise SystemExit("No snapshots available")
    idx = args.to - 1
    if idx < 0 or idx >= len(files):
        raise SystemExit(f"Snapshot index must be 1..{len(files)}")
    restored = verified_rollback_snapshot(files[idx])
    _, current_raw = _read_raw_auto_state()
    current = load_state()
    current_blocks, _ = validate(current)
    if current_blocks:
        raise SystemExit(
            "BLOCKED: current state is invalid; rollback cannot establish a "
            "transactional baseline:\n  " + "\n  ".join(current_blocks)
        )
    bootstrap_floor_target = _enforce_bootstrap_rollback_boundary(
        current_raw, current, restored, files[idx],
    )
    undo_entries = rollback_file_backup_suffix(current, restored)
    # A preview is also a hard feasibility check: resolve and hash every backup
    # before claiming that the selected rollback can be executed.
    for entry in reversed(undo_entries):
        file_backup_preimages(entry)
    if args.dry_run:
        files_to_restore = sorted({
            rel
            for entry in undo_entries
            for rel in (
                set(entry.get("files", {}))
                | set(entry.get("topology", {}))
            )
        })
        print("ROLLBACK PREVIEW")
        print(f"  target snapshot : {files[idx].name}")
        print("  direction       : backward-only ancestor rollback")
        print(f"  state           : proposals {len(current.get('proposals', {}))} -> {len(restored.get('proposals', {}))}")
        print(f"  file backups to undo: {len(undo_entries)}")
        print(f"  files touched   : {files_to_restore or 'none'}")
        return 0
    # Separately capture the exact live path topology. This covers nested
    # untracked paths that a repository-level manifest may not enumerate.
    rollback_paths = {
        rel
        for entry in undo_entries
        for rel in (
            set(entry.get("files", {}))
            | set(entry.get("topology", {}))
        )
    }
    rollback_preimages = _capture_path_preimages(rollback_paths)
    before_state_bytes = STATE_PATH.read_bytes()
    snapshot_index_path = SNAP_DIR / SNAP_INDEX_NAME
    before_snapshot_index = snapshot_index_path.read_bytes()
    before_snapshots = set(snapshot_files())
    snapshot_archive = (
        _bootstrap_snapshot_archive_plan(
            current, files[idx], before_snapshot_index,
        )
        if bootstrap_floor_target
        else None
    )
    state_tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    index_tmp = snapshot_index_path.with_suffix(".tmp")
    before_state_tmp = state_tmp.read_bytes() if state_tmp.is_file() else None
    before_index_tmp = index_tmp.read_bytes() if index_tmp.is_file() else None
    try:
        if snapshot_archive is not None:
            _apply_bootstrap_snapshot_archive(snapshot_archive)
        restore_files_to_target(current, restored)
        if not bootstrap_floor_target:
            append_event(
                restored, "rolled_back",
                {"from_snapshot": files[idx].name, "to_snapshot_index": args.to},
            )
        post_blocks, _ = validate(
            normalize_loaded_state(copy.deepcopy(restored))
        )
        if post_blocks:
            raise RuntimeError(
                "post-rollback state validation failed: " + "; ".join(post_blocks)
            )
        if bootstrap_floor_target:
            exact_floor = files[idx].read_bytes()
            _atomic_replace_bytes(STATE_PATH, exact_floor)
            if STATE_PATH.read_bytes() != exact_floor:
                raise RuntimeError("live state journal differs from exact bootstrap floor")
        else:
            committed_snapshot = save_state(
                restored, "rolled_back", prune_snapshot=False,
            )
            require_snapshot_commit(committed_snapshot, restored)
            if json.loads(STATE_PATH.read_bytes()) != restored:
                raise RuntimeError("live state journal differs from rollback commit")
    except BaseException as original_error:
        recovery_errors: list[str] = []
        try:
            _restore_file_preimages(rollback_preimages)
        except BaseException as exc:
            recovery_errors.append(f"exact file preimages: {exc}")

        def restore_optional_file(path: Path, payload: bytes | None) -> None:
            if payload is None:
                path.unlink(missing_ok=True)
                return
            _atomic_replace_bytes(path, payload)

        try:
            restore_optional_file(STATE_PATH, before_state_bytes)
        except BaseException as exc:
            recovery_errors.append(f"state journal: {exc}")
        try:
            for path in set(snapshot_files()) - before_snapshots:
                path.unlink(missing_ok=True)
        except BaseException as exc:
            recovery_errors.append(f"snapshot journal: {exc}")
        if snapshot_archive is not None:
            try:
                _recover_bootstrap_snapshot_archive(snapshot_archive)
            except BaseException as exc:
                recovery_errors.append(f"bootstrap snapshot archive: {exc}")
        try:
            restore_optional_file(snapshot_index_path, before_snapshot_index)
        except BaseException as exc:
            recovery_errors.append(f"snapshot index: {exc}")
        for label, path, payload in (
            ("state temporary", state_tmp, before_state_tmp),
            ("index temporary", index_tmp, before_index_tmp),
        ):
            try:
                restore_optional_file(path, payload)
            except BaseException as exc:
                recovery_errors.append(f"{label}: {exc}")
        try:
            if STATE_PATH.read_bytes() != before_state_bytes:
                recovery_errors.append("state journal bytes still differ")
            if snapshot_index_path.read_bytes() != before_snapshot_index:
                recovery_errors.append("snapshot index bytes still differ")
            if set(snapshot_files()) != before_snapshots:
                recovery_errors.append("snapshot file set still differs")
        except BaseException as exc:
            recovery_errors.append(f"journal recovery verification: {exc}")
        if recovery_errors:
            raise RuntimeError(
                "explicit rollback recovery was incomplete after "
                f"{type(original_error).__name__}: "
                + "; ".join(recovery_errors)
            ) from original_error
        raise
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
    inquiries = state.get("inquiries", {})
    by_status: dict[str, int] = {}
    for node in nodes.values():
        by_status[node.get("status", "?")] = by_status.get(node.get("status", "?"), 0) + 1
    proposal_rows = [
        {
            "id": pid,
            "track": prop.get("track", "?"),
            "status": prop.get("status", "?"),
            "title": prop.get("title", ""),
            "inquiry_id": prop.get("inquiry_id"),
            "recommendation_id": prop.get("recommendation_id"),
            "legacy_unlinked": not bool(prop.get("inquiry_id")),
        }
        for pid, prop in sorted(state.get("proposals", {}).items())
    ]
    inquiry_rows = [
        {
            "id": inquiry_id,
            "track": inquiry.get("track", "?"),
            "status": inquiry.get("status", "?"),
            "disposition": inquiry.get("disposition", "?"),
            "question": inquiry.get("question_after", ""),
            "selected_recommendation_id": inquiry.get("selected_recommendation_id"),
        }
        for inquiry_id, inquiry in sorted(inquiries.items())
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
            "inquiries": len(inquiries),
            "inquiry_rows": inquiry_rows,
            "active_inquiry_id": state.get("active_inquiry_id"),
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
    print(
        f"Inquiries: {len(inquiries)}  "
        f"Active: {state.get('active_inquiry_id') or 'none'}"
    )
    for inquiry_id, inquiry in sorted(inquiries.items()):
        selected = inquiry.get("selected_recommendation_id") or "-"
        print(
            f"  {inquiry_id:8s} [{inquiry.get('track','?')}] "
            f"{inquiry.get('status','?'):18s} rec={selected:8s} "
            f"{inquiry.get('question_after','')[:60]}"
        )
    print(f"Proposals: {len(state.get('proposals', {}))}")
    for pid, prop in sorted(state.get("proposals", {}).items()):
        link = prop.get("inquiry_id") or "legacy-unlinked"
        print(
            f"  {pid:8s} [{prop.get('track','?')}] "
            f"{prop.get('status','?'):18s} inquiry={link} "
            f"{prop.get('title','')[:60]}"
        )
    print(f"Retrospectives: {len(state.get('retrospectives', {}))} "
          f"(open: {open_retros or 'none'})  File backups: {len(state.get('file_backups', []))}")
    if state.get("last_self_test"):
        last = state["last_self_test"]
        print(f"Last self-test: {'PASS' if last.get('passed') else 'FAIL'} at {last.get('at')}")
    return 0


def pending_role_actions(
    state: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Map lifecycle state to reachable A/B commands in one canonical place."""
    a_actions: list[str] = []
    b_actions: list[str] = []
    inquiries = state.get("inquiries", {})
    proposals = state.get("proposals", {})
    for inquiry_id, inquiry in sorted(inquiries.items()):
        status = inquiry.get("status")
        if status == "proposed":
            recommendation_hint = (
                " --recommendation '<id>'"
                if inquiry.get("disposition") == "propose"
                else ""
            )
            b_actions.append(
                f"review-inquiry --track B --inquiry {inquiry_id} "
                "--verdict pass|challenge|reject --reviewer '<reviewer>' "
                f"--reason '<reason>'{recommendation_hint}"
            )
        elif status == "challenged":
            a_actions.append(
                f"revise-inquiry --inquiry {inquiry_id} --file revised-inquiry.json "
                "--note '<response to B>'"
            )
        elif status == "accepted":
            linked_open = any(
                proposal.get("inquiry_id") == inquiry_id
                and proposal.get("status") in (
                    "proposed", "challenged", "passed_critic", "verified",
                )
                for proposal in proposals.values()
            )
            if not linked_open:
                a_actions.append(
                    "propose --track A "
                    f"--inquiry {inquiry_id} "
                    f"--recommendation {inquiry.get('selected_recommendation_id')} "
                    "--title '<implement selected recommendation>' "
                    "--statement '<bounded rationale>' --patch-file patch.json "
                    "--verification '<exit-0 command>'"
                )
        elif status == "awaiting_outcome":
            b_actions.append(
                f"assess-inquiry --track B --inquiry {inquiry_id} "
                "--file outcome.json --assessor '<independent assessor>'"
            )

    legacy_ids = set(state.get("legacy_proposal_ids", []))
    for pid, prop in sorted(proposals.items()):
        status = prop.get("status")
        legacy = not prop.get("inquiry_id") and pid in legacy_ids
        if legacy and status in (
            "proposed", "challenged", "passed_critic", "verified",
        ):
            b_actions.append(
                f"critique --proposal {pid} --track B --verdict reject "
                "--critic '<reviewer>' --reason '<retire migration-frozen "
                "proposal; no acceptance-time evaluator binding>'"
            )
            continue
        if legacy and status in ("failed_verification", "rejected"):
            a_actions.append(
                "inquire --track A --file inquiry.json  # use "
                f"migration-frozen {status} {pid} as evidence"
            )
            continue
        if status == "proposed":
            b_actions.append(
                f"critique --proposal {pid} --track B "
                "--verdict pass|challenge|reject --critic '<critic>' "
                "--reason '<reason>'"
            )
        elif status == "challenged":
            a_actions.append(
                f"revise --proposal {pid} --note '<A revision>'"
            )
        elif status == "failed_verification":
            a_actions.append(
                "inquire --track A --file inquiry.json  # use failed linked "
                f"verification {pid} as evidence; its one attempt is consumed"
            )
        elif status == "passed_critic":
            b_actions.append(f"verify --proposal {pid} --track B")
        elif status == "verified":
            inquiry = inquiries.get(prop.get("inquiry_id") or "")
            if inquiry and inquiry.get("status") == "supported":
                b_actions.append(f"apply --proposal {pid} --track B")
        elif status == "rejected" and prop.get("inquiry_id"):
            a_actions.append(
                "inquire --track A --file inquiry.json  # use rejected "
                f"linked proposal {pid} as evidence; its one attempt is consumed"
            )
    if not a_actions and not b_actions:
        active_retro_ids = {
            prop.get("retrospective_id")
            for prop in proposals.values()
            if prop.get("retrospective_id")
            and prop.get("status") in (
                "proposed", "challenged", "passed_critic", "verified",
            )
        }
        open_retros = sorted(
            rid for rid, retro in state.get("retrospectives", {}).items()
            if retro.get("status") == "open" and rid not in active_retro_ids
        )
        if open_retros:
            a_actions.append(
                f"retro next  # use {open_retros[0]} as inquiry evidence; "
                "do not convert it directly"
            )
        else:
            deprecated = sorted(
                nid for nid, node in state.get("nodes", {}).items()
                if node.get("status") == "deprecated"
            )
            drafts = sorted(
                nid for nid, node in state.get("nodes", {}).items()
                if node.get("status") == "draft"
            )
            if deprecated:
                b_actions.append(
                    f"revalidate --node {deprecated[0]} --track B --level hard "
                    "--command '<reproducible verification>'"
                )
            elif drafts:
                b_actions.append(
                    f"revalidate --node {drafts[0]} --track B --level syntax"
                )
            else:
                a_actions.append(
                    "self-observe --json  # then inquire --track A "
                    "--file inquiry.json"
                )
    return a_actions, b_actions


def cmd_next(_: argparse.Namespace) -> int:
    state = load_state()
    a_actions, b_actions = pending_role_actions(state)
    out = [*b_actions, *a_actions]
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
        print("A must use this item as inquiry evidence or record why it is not actionable.")
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
    final_goal = state.get("final_goal", {})
    print(f"  FINAL GOAL       : {final_goal.get('statement')}")
    print(f"  structural valid : {'PASS' if not blocks else 'BLOCKED'}")
    for block in blocks[:5]:
        print(f"    - {block}")
    print(f"  health           : {'OK' if report['ok'] else 'CRITICAL: ' + ', '.join(report['critical'])}")
    for warning in report.get("warnings", []):
        print(f"    warning: {warning}")

    role = args.role
    if role == "auto":
        _, b_actions = pending_role_actions(state)
        role = "B" if b_actions else "A"
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
    role_prompts = state.get("launch_prompts", {}).get(role, [])
    print(f"  launch prompts   : {len(role_prompts)} configured for role {role}")
    for index, prompt in enumerate(role_prompts, 1):
        print(f"    {index}. {prompt}")
    print("  session prompt   : run ab-status, ab-next, self-observe;")
    print("                     A first records a grounded inquiry or justified no_change;")
    print("                     B reviews the question and selects at most one recommendation;")
    print("                     only then may A propose; B critiques and verifies operability,")
    print("                     assesses the frozen outcome, and applies only if supported.")

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
    index = load_snapshot_index()
    snapshot_blocks = snapshot_index_integrity_blocks(index)
    proposals = state.get("proposals", {})
    nodes = state.get("nodes", {})
    retros = state.get("retrospectives", {})
    inquiries = state.get("inquiries", {})

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
    final_goal = state.get("final_goal", {})
    print(f"  FINAL GOAL      : {final_goal.get('statement')}")
    open_inquiries = sorted(
        inquiry_id for inquiry_id, inquiry in inquiries.items()
        if inquiry.get("status") in (
            "proposed", "challenged", "accepted", "implementing",
            "awaiting_outcome", "supported",
        )
    )
    print(f"  open inquiries    : {open_inquiries or 'none'}")
    print(f"  active inquiry    : {state.get('active_inquiry_id') or 'none'}")
    print(f"  open proposals    : {open_props or 'none'}")
    print(f"  open retrospectives: {open_retros or 'none'}")
    print(f"  draft nodes       : {drafts or 'none'}")
    print(f"  deprecated nodes  : {deprecated or 'none'}")
    snapshots = list_snapshots()
    print(f"  snapshots         : {len(snapshots)} files, {len(index.get('files', {}))} indexed")
    print(
        "  snapshot index    : "
        + ("PASS" if not snapshot_blocks else "BLOCKED")
    )
    for block in snapshot_blocks[:6]:
        print(f"    - {block}")
    if snapshot_blocks:
        print(
            "    inspect drift before the explicit trust action: snapshot-reindex"
        )
    print("  patch vocabulary  : " + ", ".join(sorted(PATCH_OPS)))
    print("  protected paths   : " + ", ".join(sorted(PROTECTED_PATCH_FILES | set(PROTECTED_PATCH_PREFIXES))))
    print("  last proposals    :")
    for pid in sorted(proposals, key=lambda x: int(x[2:]) if x.startswith("P-") and x[2:].isdigit() else 0)[-5:]:
        prop = proposals[pid]
        ops = ",".join(op.get("op", "?") for op in prop.get("patch", []))
        print(f"    {pid} [{prop.get('track','?')}] {prop.get('status','?'):12s} ops={ops or '-'} {prop.get('title','')[:70]}")
    a_actions, b_actions = pending_role_actions(state)
    if a_actions:
        next_action = a_actions[0]
    elif b_actions:
        next_action = "wait for B: " + b_actions[0]
    else:
        next_action = "run self-observe, then record one inquiry or justified no_change"
    print(f"  next A action     : {next_action}")
    return 2 if snapshot_blocks else 0


def cmd_a_check(args: argparse.Namespace) -> int:
    state = load_state()
    patch = load_patch(args)
    if not patch:
        raise SystemExit("BLOCKED: a-check requires --patch-file or --patch")
    targets = [item.strip() for item in (args.targets or "").split(",") if item.strip()]
    gate_blocks: list[str] = []
    inquiry_id = state.get("active_inquiry_id")
    inquiry = state.get("inquiries", {}).get(inquiry_id or "")
    recommendation = None
    if not inquiry or inquiry.get("status") != "accepted":
        gate_blocks.append(
            "a-check requires an active accepted inquiry before proposal preflight"
        )
    else:
        recommendation_id = inquiry.get("selected_recommendation_id")
        recommendation = next(
            (
                item for item in inquiry.get("recommendations", [])
                if isinstance(item, dict)
                and item.get("id") == recommendation_id
            ),
            None,
        )
        if not isinstance(recommendation, dict):
            gate_blocks.append(
                "active inquiry has no B-selected recommendation"
            )
        else:
            gate_blocks.extend(proposal_contract_blocks({
                "patch": patch,
                "targets": targets,
                "verification_command": args.verification or "",
                "recommendation_snapshot": copy.deepcopy(recommendation),
                "recommendation_sha256": recommendation_sha256(
                    recommendation
                ),
            }))
    if not args.verification or not args.verification.strip():
        gate_blocks.append(
            "verification command is required before proposal admission"
        )
    print("A PREFLIGHT CHECK")
    print(f"  inquiry    : {inquiry_id or 'none'}")
    print(
        "  selected recommendation: "
        + (
            str(inquiry.get("selected_recommendation_id"))
            if isinstance(inquiry, dict)
            else "none"
        )
    )
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
        print("  verification command: BLOCKED: MISSING")
    else:
        print(f"  verification command: {args.verification}")
    print(f"  inquiry contract: {'PASS' if not gate_blocks else 'BLOCKED'}")
    for block in gate_blocks:
        print(f"    - {block}")

    if novelty or blocks or gate_blocks:
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
    print(f"  inquiry    : {prop.get('inquiry_id')}")
    print(f"  recommendation: {prop.get('recommendation_id')}")
    print(f"  outcome plan digest: {prop.get('outcome_plan_sha256')}")
    print(f"  patch ops  : {[op.get('op') for op in prop.get('patch', [])]}")
    print("  patch      :")
    print(json.dumps(prop.get("patch", []), indent=4))
    print(f"  verification command: {prop.get('verification_command')}")
    print(f"  critic     : {json.dumps(prop.get('critic') or {}, sort_keys=True)}")
    print(f"  verification record : {json.dumps(prop.get('verification') or {}, sort_keys=True)}")
    print(f"  modification node   : {prop.get('modification_node')}")
    print(f"  affected nodes      : {prop.get('affected_nodes')}")
    return 0


def _manifest_path_excluded(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if "__pycache__" in parts or relative.suffix == ".pyc":
        return True
    return False


FS_IOC_GETFLAGS = 0x80086601
FS_IOC_SETFLAGS = 0x40086602


def _managed_inode_flags(path: Path, kind: str) -> int | None:
    """Read Linux inode flags without following symlinks or file contents."""
    if kind not in ("file", "dir"):
        return None
    open_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if kind == "dir":
        open_flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, open_flags)
        try:
            payload = bytearray(struct.pack("I", 0))
            fcntl.ioctl(fd, FS_IOC_GETFLAGS, payload, True)
            return struct.unpack("I", payload)[0]
        finally:
            os.close(fd)
    except OSError as exc:
        if exc.errno in (
            errno.ENOTTY,
            errno.EOPNOTSUPP,
            errno.ENOSYS,
            errno.EINVAL,
        ):
            return None
        raise RuntimeError(
            f"could not inventory inode flags for {path}: {exc}"
        ) from exc


def _set_managed_inode_flags(path: Path, kind: str, flags: int | None) -> None:
    """Restore Linux inode flags when that filesystem exposes them."""
    if flags is None or kind not in ("file", "dir"):
        return
    open_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if kind == "dir":
        open_flags |= getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, open_flags)
    try:
        fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("I", flags))
    finally:
        os.close(fd)


def _chmod_managed_path(path: Path, kind: str, mode: int) -> None:
    """Change a non-symlink inode mode through a no-follow descriptor."""
    if kind not in ("file", "dir", "special"):
        return
    open_flags = (
        getattr(os, "O_PATH", os.O_RDONLY)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    fd = os.open(path, open_flags)
    try:
        info = os.fstat(fd)
        actual_kind = (
            "dir" if stat.S_ISDIR(info.st_mode)
            else "file" if stat.S_ISREG(info.st_mode)
            else "special"
        )
        if actual_kind != kind:
            raise RuntimeError(
                f"managed path changed kind before chmod: {path}"
            )
        # fchmod rejects O_PATH descriptors, whereas the procfs handle names
        # the already-opened inode and therefore neither requires read
        # permission nor follows a newly swapped path component.
        proc_handle = Path("/proc/self/fd") / str(fd)
        os.chmod(proc_handle, mode)
    finally:
        os.close(fd)


def _managed_xattrs(path: Path) -> tuple[tuple[str, bytes], ...]:
    if not hasattr(os, "listxattr"):
        return ()
    try:
        names = sorted(os.listxattr(path, follow_symlinks=False))
        return tuple(
            (
                name,
                os.getxattr(path, name, follow_symlinks=False),
            )
            for name in names
        )
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return ()
        raise RuntimeError(f"could not inventory xattrs for {path}: {exc}") from exc


def _managed_inventory_entry(
    path: Path,
    info: os.stat_result,
    kind: str,
    target: bytes = b"",
) -> tuple[Any, ...]:
    return (
        kind,
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_gid,
        info.st_dev,
        info.st_ino,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        target,
        _managed_xattrs(path),
        _managed_inode_flags(path, kind),
    )


def _live_managed_manifest(
    root: Path = ROOT,
) -> dict[str, tuple[Any, ...]]:
    """Inventory every managed path without reading regular-file contents.

    File ctime catches same-size rewrites even when a command restores mtime on
    local Linux filesystems. Direct child writes are prevented by Landlock; this
    metadata pass is defense in depth and a concurrent-writer detector, not a
    byte-preimage store.
    """
    root = root.resolve(strict=True)
    root_info = root.lstat()
    manifest: dict[str, tuple[Any, ...]] = {
        ".": _managed_inventory_entry(root, root_info, "dir"),
    }
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise RuntimeError(
                f"could not inventory managed directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root)
            if _manifest_path_excluded(relative):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(
                    f"could not inventory managed path {relative}: {exc}"
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                try:
                    target = os.fsencode(os.readlink(path))
                except OSError as exc:
                    raise RuntimeError(
                        f"could not read managed symlink {relative}: {exc}"
                    ) from exc
                value = _managed_inventory_entry(
                    path, info, "symlink", target,
                )
            elif stat.S_ISDIR(info.st_mode):
                value = _managed_inventory_entry(path, info, "dir")
                stack.append(path)
            else:
                kind = "file" if stat.S_ISREG(info.st_mode) else "special"
                value = _managed_inventory_entry(path, info, kind)
            manifest[relative.as_posix()] = value
    return manifest


def _stable_managed_manifest(
    root: Path,
) -> dict[str, tuple[Any, ...]]:
    """Capture candidate-controlled files, excluding Python runtime caches."""
    return {
        rel: value
        for rel, value in _live_managed_manifest(root).items()
        if "__pycache__" not in Path(rel).parts and not rel.endswith(".pyc")
    }


def _managed_manifest_changes(
    before: dict[str, tuple[Any, ...]],
    after: dict[str, tuple[Any, ...]],
    *,
    ignore_directory_times: bool = False,
    ignore_ctime: bool = False,
) -> list[str]:
    def comparable(value: tuple[Any, ...] | None) -> Any:
        if value is None:
            return None
        if ignore_directory_times and value[0] == "dir":
            return value[:4] + value[11:13]
        if ignore_ctime:
            return value[:9] + value[10:]
        return value

    return sorted(
        rel for rel in set(before) | set(after)
        if comparable(before.get(rel)) != comparable(after.get(rel))
    )


def _restore_managed_access_modes(
    before: dict[str, tuple[Any, ...]],
    root: Path,
) -> tuple[list[str], list[str]]:
    """Restore access modes shallow-first so the after inventory can run."""
    restored: list[str] = []
    errors: list[str] = []
    for rel in sorted(before, key=lambda item: (len(Path(item).parts), item)):
        wanted = before[rel]
        if wanted[0] == "symlink":
            continue
        path = root if rel == "." else root / rel
        try:
            current = path.lstat()
            current_kind = (
                "dir" if stat.S_ISDIR(current.st_mode)
                else "file" if stat.S_ISREG(current.st_mode)
                else "special"
            )
            if current_kind != wanted[0]:
                continue
            current_mode = stat.S_IMODE(current.st_mode)
            if current_mode != wanted[1]:
                _chmod_managed_path(path, wanted[0], wanted[1])
                restored.append(rel)
        except (OSError, RuntimeError) as exc:
            errors.append(f"could not restore managed access mode {rel}: {exc}")
    return restored, errors


def _restore_managed_metadata(
    before: dict[str, tuple[Any, ...]],
    after: dict[str, tuple[Any, ...]],
    changed: Iterable[str],
    root: Path,
) -> list[str]:
    """Restore metadata rights Landlock ABI 4 does not mediate.

    Content/topology changes are deliberately not called restored: without a
    byte preimage they indicate an external/concurrent writer or a containment
    failure outside this child-filesystem boundary.
    """
    errors: list[str] = []
    for rel in sorted(set(changed), key=lambda item: (len(Path(item).parts), item)):
        wanted = before.get(rel)
        current = after.get(rel)
        if wanted is None or current is None:
            errors.append(f"unrecoverable path topology change at {rel}")
            continue
        # kind/dev/inode/nlink/size/symlink target must remain exact. mtime,
        # mode, ownership and xattrs are metadata that can be restored below.
        identity_indexes = (0, 4, 5, 6, 7, 10)
        if any(wanted[index] != current[index] for index in identity_indexes):
            errors.append(f"unrecoverable content/topology identity change at {rel}")
            continue
        path = root if rel == "." else root / rel
        try:
            if current[12] != wanted[12] and current[12] is not None:
                # Clear flags that can block the remaining restoration before
                # setting the complete wanted flag word at the end.
                _set_managed_inode_flags(
                    path, wanted[0], current[12] & ~(0x10 | 0x20),
                )
            if (current[2], current[3]) != (wanted[2], wanted[3]):
                os.chown(
                    path, wanted[2], wanted[3], follow_symlinks=False,
                )
            if wanted[0] != "symlink" and current[1] != wanted[1]:
                _chmod_managed_path(path, wanted[0], wanted[1])
            if current[11] != wanted[11]:
                current_attrs = {name: value for name, value in current[11]}
                wanted_attrs = {name: value for name, value in wanted[11]}
                for name in sorted(set(current_attrs) - set(wanted_attrs)):
                    os.removexattr(path, name, follow_symlinks=False)
                for name, value in sorted(wanted_attrs.items()):
                    if current_attrs.get(name) != value:
                        os.setxattr(
                            path, name, value, follow_symlinks=False,
                        )
            if current[8] != wanted[8]:
                restored_atime = path.stat(
                    follow_symlinks=False,
                ).st_atime_ns
                os.utime(
                    path,
                    ns=(restored_atime, wanted[8]),
                    follow_symlinks=False,
                )
            if current[12] != wanted[12]:
                _set_managed_inode_flags(path, wanted[0], wanted[12])
        except (OSError, RuntimeError, NotImplementedError) as exc:
            errors.append(f"could not restore managed metadata {rel}: {exc}")
    try:
        restored = _live_managed_manifest(root)
        remaining = _managed_manifest_changes(
            before, restored, ignore_ctime=True,
        )
        if remaining:
            errors.append(
                "managed metadata/topology still differs at: "
                + ", ".join(remaining)
            )
    except Exception as exc:
        errors.append(f"could not verify managed metadata restoration: {exc}")
    return errors
def run_agent_command(
    template: str,
    prompt: str,
    timeout: int,
    cwd: Path = ROOT,
    attempt: int = 1,
) -> dict[str, Any]:
    """Run one agent in a disposable copy and guard the live managed files.

    This staging boundary prevents accidental writes from being retained and detects
    writes back to the live managed roots. It is not an OS sandbox against a
    malicious process that deliberately escapes staging or races the final check.
    """
    if not template.strip():
        raise SystemExit("BLOCKED: run requires a non-empty proposer/critic command")
    live_root = ROOT.resolve()
    live_cwd = cwd.expanduser()
    if not live_cwd.is_absolute():
        live_cwd = live_root / live_cwd
    live_cwd = live_cwd.resolve()
    try:
        cwd_relative = live_cwd.relative_to(live_root)
    except ValueError as exc:
        raise SystemExit(
            f"BLOCKED: --agent-cwd must resolve within repository ROOT {live_root}"
        ) from exc
    if live_cwd.exists() and not live_cwd.is_dir():
        raise SystemExit("BLOCKED: --agent-cwd must name a directory")

    before = _live_managed_manifest(live_root)
    execution: dict[str, Any]
    try:
        with tempfile.TemporaryDirectory(prefix="auto-research-agent-") as td:
            staging_root = Path(td) / "repo"
            scratch = Path(td) / "scratch"
            scratch.mkdir()
            shutil.copytree(live_root, staging_root, ignore=_sandbox_ignore)
            staging_cwd = staging_root / cwd_relative
            staging_cwd.mkdir(parents=True, exist_ok=True)
            staged_template = template.replace(str(live_root), str(staging_root))
            command = staged_template.rstrip() + " " + shlex.quote(prompt)
            env = os.environ.copy()
            env["RESEARCH_CLOSURE_ROOT"] = str(staging_root)
            env["AUTO_RESEARCH_AGENT_ATTEMPT"] = str(attempt)
            env.update({
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
                "XDG_CACHE_HOME": str(scratch / "xdg-cache"),
                "PYTHONPYCACHEPREFIX": str(scratch / "pycache"),
            })
            try:
                execution = _run_shell_process(
                    command,
                    timeout,
                    staging_cwd,
                    env,
                    writable_roots=(staging_root, scratch),
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise SystemExit(
                    f"BLOCKED: strict agent containment unavailable: {exc}"
                ) from exc
    finally:
        access_restored, access_errors = _restore_managed_access_modes(
            before, live_root,
        )
        after = _live_managed_manifest(live_root)
        changed = sorted(set(access_restored) | set(
            _managed_manifest_changes(before, after)
        ))
        if changed or access_errors:
            restore_errors = [*access_errors]
            restore_errors.extend(_restore_managed_metadata(
                before, after, changed, live_root,
            ))
            message = (
                "BLOCKED: live managed files changed across a Landlock-confined "
                "agent run; restorable metadata was reset, but no byte "
                "restoration is claimed for content/topology changes:\n  "
                + "\n  ".join(changed or ["after-scan access failure"])
            )
            if restore_errors:
                message += "\nRestoration errors:\n  " + "\n  ".join(
                    restore_errors
                )
            raise SystemExit(message)
    if execution["timeout"]:
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=execution["stdout"],
            stderr=execution["stderr"],
        )
    if execution["returncode"] != 0:
        raise SystemExit(
            "BLOCKED: agent command failed:\n"
            f"stdout: {execution['stdout'][-800:]}\n"
            f"stderr: {execution['stderr'][-800:]}"
        )
    out = execution["stdout"].strip()
    if out.startswith("```"):
        lines = out.splitlines()
        out = "\n".join(lines[1:-1] if len(lines) > 2 else lines[1:])
    start = out.find("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        raise SystemExit("BLOCKED: agent output is not JSON")
    try:
        data = json.loads(out[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SystemExit("BLOCKED: invalid agent JSON")
    if not isinstance(data, dict):
        raise SystemExit("BLOCKED: agent JSON must be an object")
    return data


def run_cli(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RESEARCH_CLOSURE_ROOT"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_inquiry_prompt(state: dict[str, Any], role: str) -> str:
    final_goal = state.get("final_goal", copy.deepcopy(FINAL_GOAL))
    observation = build_self_observation(state)
    recent = [
        {
            "id": inquiry_id,
            "status": inquiry.get("status"),
            "question_after": inquiry.get("question_after"),
            "disposition": inquiry.get("disposition"),
            "outcome": inquiry.get("outcome"),
        }
        for inquiry_id, inquiry in sorted(state.get("inquiries", {}).items())[-5:]
    ]
    return (
        "IMMUTABLE FINAL GOAL / TERMINAL REWARD:\n"
        + final_goal.get("statement", "") + "\n"
        + "Criteria: " + "; ".join(final_goal.get("criteria", [])) + "\n"
        f"You are auto-research role {role} in SELF-INQUIRY PHASE. "
        "Do not propose or apply a patch. Inspect the engine source, tests, state history, "
        "and this observation, then either reframe the research question or record a justified no_change.\n"
        f"Self-observation: {json.dumps(observation, default=str, sort_keys=True)}\n"
        f"Recent inquiries: {json.dumps(recent, default=str)}\n"
        "A self-inquiry is a research judgment, never evidence that capability improved. "
        "If a measured outcome exists, copy its next_question exactly into "
        "question_before and cite its pinned outcome blob locator in evidence; "
        "then reframe it into question_after. "
        "Cite concrete evidence. Separate what may change from the protected evaluator, name a baseline and measure, "
        "and give a falsifier, evaluator, success rule, guardrails, and fixed budget. "
        "A propose disposition may recommend changes but may not contain patch, targets, or verification fields. "
        "A no_change disposition must include no_change_reason and revisit_when and must use an empty recommendations list. "
        "Use action inquire for a reframed question or action no_change for an evidence-backed stop. "
        "Required JSON fields: action, scope, diagnosis, evidence (objects with kind/locator/observation), "
        "question_before, question_after, why_better, hypothesis (claim/falsifier), "
        "evaluation_boundary (mutable/protected lists, baseline, measure, evaluator, "
        "success_rule, guardrails, budget), recommendations, disposition, "
        "no_change_reason, revisit_when. Recommendation fields are id, title, target, change, rationale, validation, "
        "and implementation_contract. The contract must contain allowed_ops, allowed_paths, required_targets, "
        "and required_verification_terms; keep each list as narrow and machine-checkable as possible. "
        "You have explicit file access and may use Read, Bash, and Edit tools for inspection, but do not edit tracked files. "
        "Do not use markdown fences. Return only one JSON object."
    )


def run_proposer_prompt(
    state: dict[str, Any],
    role: str,
) -> str:
    active_inquiry_id = state.get("active_inquiry_id")
    active_inquiry = state.get("inquiries", {}).get(active_inquiry_id or "")
    if not (
        active_inquiry and active_inquiry.get("status") == "accepted"
    ):
        return run_inquiry_prompt(state, role)
    open_retros = sorted(
        rid for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    )
    recent_full = sorted(
        state.get("proposals", {}).items(),
        key=lambda item: int(item[0][2:]) if item[0].startswith("P-") and item[0][2:].isdigit() else 0,
    )[-6:]
    recent = []
    for pid, prop in recent_full:
        recent.append({
            "id": pid,
            "track": prop.get("track"),
            "status": prop.get("status"),
            "title": prop.get("title"),
            "ops": [op.get("op") for op in prop.get("patch", [])],
            "paths": [op.get("path") for op in prop.get("patch", []) if op.get("op") == "patch_file"],
        })
    all_proposals = sorted(
        state.get("proposals", {}).items(),
        key=lambda item: int(item[0][2:]) if item[0].startswith("P-") and item[0][2:].isdigit() else 0,
    )
    recent_applied = [
        prop for _, prop in all_proposals
        if prop.get("status") == "applied"
    ][-20:]
    op_counts: dict[str, int] = {}
    for prop in recent_applied:
        for op in prop.get("patch", []):
            name = op.get("op", "?")
            op_counts[name] = op_counts.get(name, 0) + 1
    top_op = max(op_counts, key=op_counts.get) if op_counts else None
    top_ratio = (op_counts[top_op] / sum(op_counts.values())) if top_op else 0.0
    concentration_hint = ""
    if top_ratio > 0.8:
        concentration_hint = (
            f"Patch-op concentration guard is near: {top_op} is {top_ratio:.0%} of the last 20 applied patches. "
            f"Do not propose {top_op} alone; use patch_file or at least two different op kinds.\n"
        )
    final_goal = state.get("final_goal", copy.deepcopy(FINAL_GOAL))
    selected_id = active_inquiry.get("selected_recommendation_id")
    selected = next(
        (
            recommendation
            for recommendation in active_inquiry.get("recommendations", [])
            if recommendation.get("id") == selected_id
        ),
        {},
    )
    inquiry_context = (
        f"ACTIVE ACCEPTED INQUIRY {active_inquiry_id}: "
        f"{active_inquiry.get('question_after')}\n"
        f"B-selected recommendation {selected_id}: {json.dumps(selected, default=str)}\n"
        "The proposal must name this exact inquiry and recommendation.\n"
    )
    link_example = (
        f',"inquiry":"{active_inquiry_id}","recommendation":"{selected_id}"'
    )
    return (
        "IMMUTABLE FINAL GOAL / TERMINAL REWARD:\n"
        + final_goal.get("statement", "") + "\n"
        + "Criteria: " + "; ".join(final_goal.get("criteria", [])) + "\n"
        + inquiry_context
        + f"You are auto-research role {role}. Implement exactly one small recommendation as a local self-modification.\n"
        "Launch prompts:\n" + "\n".join(f"- {p}" for p in state.get("launch_prompts", {}).get(role, [])) + "\n"
        f"Open retrospectives: {open_retros or 'none'}.\n"
        f"Recent proposals: {json.dumps(recent, default=str)}.\n"
        f"Patch vocabulary: {sorted(PATCH_OPS)}.\n"
        f"{concentration_hint}"
        f"Existing graph nodes you may target: {[nid for nid in RUNNER_TARGET_WHITELIST if nid in state.get('nodes', {})]}.\n"
        "Valid patch examples:\n"
        '{"op":"set_node_statement","node":"<existing-id>","statement":"..."}\n'
        '{"op":"set_layer_policy","layer":"L1","policy":"..."}\n'
        '{"op":"set_launch_prompt","role":"A","prompts":["..."]}\n'
        '{"op":"add_node","node":{"id":"N-<topic>","type":"assumption","statement":"...","layer":"L4","status":"draft"}}\n'
        '{"op":"add_edge","from":"<existing-id>","to":"<existing-id>","kind":"dependency"}\n'
        "Rules: at most two patch operations; every proposal needs one or two graph targets and an explicit exit-0 verification command. "
        "Use patch_file only if you can emit a complete unified diff in its patch field. "
        "If you cannot emit a complete unified diff, choose a state-only op with all required fields exactly. "
        "Verification must use python3 and exercise at least one proposal-specific intended effect. "
        "Contract terms are only mechanical scope bindings, not proof that the command asserts the effect. "
        "The state file path is always .research/auto_research.json; verification must reference that exact path or run python3 -m unittest discover -s tests. "
        "State nodes are a dict mapping node id to node object: access d['nodes']['N-...']['statement'], never iterate d['nodes'] as a list. "
        "Example verification: python3 -c \"import json; d=json.load(open('.research/auto_research.json')); assert d['nodes']['N-TARGET']['statement']=='WRITTEN-TEXT'\". "
        "You have explicit file access and may use Read, Bash, and Edit tools. "
        "Prefer executable engine changes: inspect the target file, write a candidate under .research/tmp/candidates/, "
        "run python3 tools/auto_research.py patch-make --target <file> --candidate <candidate> --out <patch.json>, "
        "and return a patch_file op containing the exact generated diff. "
        "Do not edit tracked engine files directly; only produce proposal JSON. "
        "Do not use markdown fences. Reply in one short message under 300 words. "
        "Return only one JSON object with action exactly equal to propose, for example:\n"
        '{"action":"propose","title":"...","statement":"...","targets":["N-..."],'
        '"patch":[{"op":"set_node_statement","node":"N-...","statement":"..."}],'
        '"verification":"python3 -m unittest discover -s tests","retro":null'
        + link_example + "}"
    )


def run_critic_prompt(state: dict[str, Any], proposal_id: str) -> str:
    prop = state.get("proposals", {}).get(proposal_id, {})
    role = "B"
    patch_text = json.dumps(prop.get("patch", []), default=str)
    compact = {
        "id": prop.get("id"),
        "track": prop.get("track"),
        "title": prop.get("title"),
        "statement": prop.get("statement"),
        "targets": prop.get("targets"),
        "retrospective_id": prop.get("retrospective_id"),
        "inquiry_id": prop.get("inquiry_id"),
        "recommendation_id": prop.get("recommendation_id"),
        "patch_ops": [op.get("op") for op in prop.get("patch", [])],
        "patch_preview": patch_text[:6000],
        "patch_chars": len(patch_text),
        "verification_command": prop.get("verification_command"),
    }
    inquiry = state.get("inquiries", {}).get(prop.get("inquiry_id") or "")
    if inquiry:
        selected_id = inquiry.get("selected_recommendation_id")
        selected_recommendation = next(
            (
                item for item in inquiry.get("recommendations", [])
                if item.get("id") == selected_id
            ),
            None,
        )
        compact["inquiry"] = {
            "question": inquiry.get("question_after"),
            "hypothesis": inquiry.get("hypothesis"),
            "evaluation_boundary": inquiry.get("evaluation_boundary"),
            "outcome_plan": inquiry.get("outcome_plan"),
            "selected_recommendation_id": inquiry.get(
                "selected_recommendation_id"
            ),
            "selected_recommendation": selected_recommendation,
        }
    final_goal = state.get("final_goal", copy.deepcopy(FINAL_GOAL))
    return (
        "IMMUTABLE FINAL GOAL / TERMINAL REWARD:\n"
        + final_goal.get("statement", "") + "\n"
        + "Criteria: " + "; ".join(final_goal.get("criteria", [])) + "\n"
        "You are auto-research role B. Criticise this proposal specifically. "
        "Judge whether the verification actually exercises the claimed effect; "
        "mere string occurrence or exit 0 is not semantic evidence. "
        "You have explicit file access and may use Read, Bash, and Edit tools to inspect the patch. "
        "Do not use markdown fences. Reply in one short message under 300 words. "
        "Return only one JSON object with action exactly equal to critique and verdict one of pass, challenge, or reject, for example:\n"
        '{"action":"critique","verdict":"pass","reason":"specific reason naming patch ops, verification command, or closure risk"}.\n'
        "Launch prompts:\n" + "\n".join(f"- {p}" for p in state.get("launch_prompts", {}).get(role, [])) + "\n"
        f"Proposal: {json.dumps(compact, default=str)}"
    )


def proposal_review_sha256(prop: dict[str, Any]) -> str:
    material = {
        "id": prop.get("id"),
        "track": prop.get("track"),
        "title": prop.get("title"),
        "statement": prop.get("statement"),
        "targets": prop.get("targets"),
        "patch": prop.get("patch"),
        "target_preimage_sha256": prop.get("target_preimage_sha256"),
        "verification_command": prop.get("verification_command"),
        "inquiry_id": prop.get("inquiry_id"),
        "recommendation_id": prop.get("recommendation_id"),
        "recommendation_sha256": prop.get("recommendation_sha256"),
        "retrospective_id": prop.get("retrospective_id"),
        "inquiry_review_sha256": prop.get("inquiry_review_sha256"),
        "evaluator_snapshot_id": prop.get("evaluator_snapshot_id"),
        "evaluator_manifest_sha256": prop.get(
            "evaluator_manifest_sha256"
        ),
        "outcome_plan_sha256": prop.get("outcome_plan_sha256"),
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def proposal_critique_sha256(critic: Any) -> str | None:
    """Bind the complete semantic B record, excluding its own digest."""
    if not isinstance(critic, dict):
        return None
    material = {
        key: copy.deepcopy(value)
        for key, value in critic.items()
        if key != "proposal_critique_sha256"
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def state_basis_sha256(state: dict[str, Any]) -> str:
    """Hash state whose drift can change a reviewed patch's apply semantics."""
    material = {
        "schema_version": state.get("schema_version"),
        "meta_goal": copy.deepcopy(state.get("meta_goal")),
        "final_goal": copy.deepcopy(state.get("final_goal")),
        "nodes": copy.deepcopy(state.get("nodes")),
        "edges": copy.deepcopy(state.get("edges")),
        "layers": copy.deepcopy(state.get("layers")),
        "tracks": copy.deepcopy(state.get("tracks")),
        "trust_decay": state.get("trust_decay"),
        "affected_trust_decay": state.get("affected_trust_decay"),
        "revalidation_threshold": state.get("revalidation_threshold"),
        "self_test_command": state.get("self_test_command"),
        "snapshot_retention": copy.deepcopy(state.get("snapshot_retention")),
        "health_limits": copy.deepcopy(state.get("health_limits")),
        "launch_prompts": copy.deepcopy(state.get("launch_prompts")),
        "retrospectives": copy.deepcopy(state.get("retrospectives")),
        "legacy_proposal_ids": copy.deepcopy(state.get("legacy_proposal_ids")),
        "legacy_file_backup_ids": copy.deepcopy(
            state.get("legacy_file_backup_ids")
        ),
        "bootstrap_transition": copy.deepcopy(
            state.get("bootstrap_transition")
        ),
        "file_backup_ids": [
            file_backup_identity(entry)
            for entry in state.get("file_backups", [])
            if isinstance(entry, dict)
        ],
        "counters": {
            key: state.get("counters", {}).get(key)
            for key in ("node", "proposal", "inquiry", "retrospective")
        },
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def proposal_mechanical_preflight(
    state: dict[str, Any],
    proposal_id: str,
) -> dict[str, Any]:
    """Return structural preflight without impersonating semantic B review."""
    prop = state.get("proposals", {}).get(proposal_id, {})
    patch = prop.get("patch", [])
    targets = prop.get("targets") or []
    title = str(prop.get("title", ""))
    statement = str(prop.get("statement", ""))
    digest = proposal_review_sha256(prop)
    state_basis = state_basis_sha256(state)

    def report(status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "proposal_review_sha256": digest,
            "inquiry_review_sha256": prop.get("inquiry_review_sha256"),
            "evaluator_snapshot_id": prop.get("evaluator_snapshot_id"),
            "evaluator_manifest_sha256": prop.get(
                "evaluator_manifest_sha256"
            ),
            "state_basis_sha256": state_basis,
            "at": now_iso(),
        }

    if prop.get("inquiry_id"):
        inquiry = state.get("inquiries", {}).get(prop.get("inquiry_id"))
        if not isinstance(inquiry, dict):
            return report("blocked", "linked inquiry is missing")
        review_blocks = inquiry_review_binding_blocks(inquiry)
        if review_blocks or prop.get("inquiry_review_sha256") != inquiry.get(
            "inquiry_review_sha256"
        ):
            return report(
                "blocked",
                "B-reviewed inquiry unavailable or changed: "
                + "; ".join(review_blocks or ["digest mismatch"]),
            )
        snapshot = state.get("evaluator_snapshots", {}).get(
            prop.get("evaluator_snapshot_id")
        )
        if (
            not isinstance(snapshot, dict)
            or prop.get("evaluator_manifest_sha256")
            != inquiry.get("evaluator_manifest_sha256")
            or prop.get("evaluator_manifest_sha256")
            != snapshot.get("manifest_sha256")
        ):
            return report(
                "blocked", "full evaluator manifest binding has drifted",
            )
        contract_blocks = proposal_contract_blocks(prop)
        if contract_blocks:
            return report(
                "blocked",
                "selected recommendation contract mismatch: "
                + "; ".join(contract_blocks),
            )
        try:
            current_preimage = patch_target_preimage_sha256(patch)
        except OSError as exc:
            return report("blocked", f"could not read patch target preimage: {exc}")
        if prop.get("target_preimage_sha256") != current_preimage:
            return report(
                "blocked", "patch target bytes or ancestor topology changed",
            )
        snapshot_blocks = evaluator_snapshot_runtime_blocks(
            state, prop.get("evaluator_snapshot_id"),
        )
        if snapshot_blocks:
            return report(
                "blocked",
                "B-frozen evaluator snapshot unavailable or drifted: "
                + "; ".join(snapshot_blocks),
            )
        plan_blocks = outcome_plan_blocks(
            inquiry, inquiry.get("outcome_plan"),
        )
        plan = inquiry.get("outcome_plan")
        if (
            plan_blocks
            or not isinstance(plan, dict)
            or prop.get("outcome_plan_sha256") != plan.get("sha256")
        ):
            return report(
                "blocked",
                "B-frozen outcome plan unavailable or changed: "
                + "; ".join(plan_blocks or ["digest mismatch"]),
            )
    else:
        return report(
            "blocked",
            "legacy open proposal has no acceptance-time evaluator snapshot; "
            "reject it and re-propose through a reviewed inquiry",
        )

    prior_state = copy.deepcopy(state)
    prior_state.get("proposals", {}).pop(proposal_id, None)
    novelty = novelty_blocks(prior_state, title, statement, patch)
    if novelty:
        return report("blocked", "novelty guard blocked: " + "; ".join(novelty))

    blocks = validate_patch(state, patch)
    if blocks:
        return report("blocked", "patch validation blocked: " + "; ".join(blocks))

    trial = copy.deepcopy(state)
    try:
        apply_raw_patch(trial, patch, write_files=False)
    except SystemExit as exc:
        return report("blocked", str(exc))
    existing = set(trial.get("nodes", {}))
    missing = [target for target in targets if target not in existing]
    if missing:
        return report("blocked", "targets missing after patch: " + ", ".join(missing))

    command = str(prop.get("verification_command", ""))
    if not command.strip():
        return report("blocked", "verification command is missing")
    return report(
        "ready_for_B",
        "mechanical preflight passed (contract, evaluator snapshot, patch, targets, novelty, and non-empty command); "
        "an independent B must still judge recommendation fidelity and whether the "
        "verification semantically exercises the claimed effect",
    )


def run_revalidate_all() -> tuple[int, str]:
    state = load_state()
    ids = [
        nid for nid, node in state.get("nodes", {}).items()
        if nid != META_GOAL_ID and node.get("status") != "validated"
    ]
    for nid in sorted(ids):
        proc = run_cli("revalidate", "--track", "B", "--node", nid, "--level", "syntax")
        if proc.returncode != 0:
            return proc.returncode, nid
    return 0, ""


def run_log_entry(epoch: int, role: str, event: str, detail: dict[str, Any]) -> None:
    path = ROOT / ".research" / "auto_run.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps({
            "at": now_iso(),
            "epoch": epoch,
            "role": role,
            "event": event,
            "detail": detail,
        }, default=str) + "\n")


def run_agent_with_retries(
    template: str,
    prompt: str,
    timeout: int,
    attempts: int,
    cwd: Path,
) -> dict[str, Any]:
    last_error = "unknown agent failure"
    for attempt in range(1, attempts + 1):
        try:
            return run_agent_command(
                template, prompt, timeout, cwd=cwd, attempt=attempt,
            )
        except subprocess.TimeoutExpired:
            last_error = f"agent timed out after {timeout}s (attempt {attempt}/{attempts})"
        except SystemExit as exc:
            raise
    raise SystemExit("BLOCKED: " + last_error)


def runner_propose_args(
    payload: dict[str, Any],
    role: str,
    state: dict[str, Any],
) -> list[str]:
    patch = payload.get("patch")
    if not isinstance(patch, list) or not patch:
        raise SystemExit("BLOCKED: proposer returned no patch list")
    targets = payload.get("targets") or []
    if not isinstance(targets, list):
        targets = [str(targets)]
    args = [
        "propose", "--track", role,
        "--title", str(payload.get("title", "runner proposal")),
        "--statement", str(payload.get("statement", "runner proposal")),
        "--targets", ",".join(str(item) for item in targets),
        "--patch", json.dumps(patch),
    ]
    inquiry_id = state.get("active_inquiry_id")
    inquiry = state.get("inquiries", {}).get(inquiry_id or "")
    recommendation_id = inquiry.get("selected_recommendation_id") if inquiry else None
    if not inquiry or inquiry.get("status") != "accepted":
        raise SystemExit("BLOCKED: proposal phase requires an active accepted inquiry")
    if payload.get("inquiry") != inquiry_id:
        raise SystemExit(
            f"BLOCKED: proposer must bind inquiry {inquiry_id}, got {payload.get('inquiry')!r}"
        )
    if payload.get("recommendation") != recommendation_id:
        raise SystemExit(
            "BLOCKED: proposer must bind the recommendation selected by B"
        )
    args.extend([
        "--inquiry", str(inquiry_id),
        "--recommendation", str(recommendation_id),
    ])
    verification = payload.get("verification")
    if verification:
        args.extend(["--verification", str(verification)])
    retro = payload.get("retro")
    if retro:
        args.extend(["--retro", str(retro)])
    return args


def cmd_run(args: argparse.Namespace) -> int:
    deprecated = []
    if args.alternate:
        deprecated.append("--alternate")
    if args.stop_on_warning:
        deprecated.append("--stop-on-warning")
    if args.commit:
        deprecated.append("--commit")
    if deprecated:
        raise SystemExit(
            "BLOCKED: deprecated runner option(s) are unavailable in the "
            "inquiry-first loop: " + ", ".join(deprecated) + ". The runner "
            "always uses A for inquiry/proposal authorship and stops before "
            "outcome assessment or live apply."
        )
    state = load_state()
    report = build_health_report(state)
    if report["critical"]:
        print("BLOCKED: health critical before run: " + ", ".join(report["critical"]))
        return 2
    agent_cwd = Path(args.agent_cwd).expanduser()
    if not agent_cwd.is_absolute():
        agent_cwd = ROOT / agent_cwd
    agent_cwd = agent_cwd.resolve()
    try:
        agent_cwd.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(
            f"BLOCKED: --agent-cwd must resolve within repository ROOT {ROOT.resolve()}"
        ) from exc
    if agent_cwd.exists() and not agent_cwd.is_dir():
        raise SystemExit("BLOCKED: --agent-cwd must name a directory")
    for epoch in range(1, args.epochs + 1):
        state = load_state()
        role = "A"
        print(f"EPOCH {epoch}/{args.epochs} role={role}")
        awaiting = sorted(
            inquiry_id
            for inquiry_id, inquiry in state.get("inquiries", {}).items()
            if inquiry.get("status") in ("proposed", "challenged")
        )
        if awaiting:
            inquiry_id = awaiting[0]
            status = state["inquiries"][inquiry_id].get("status")
            next_command = (
                f"review-inquiry --track B --inquiry {inquiry_id} ..."
                if status == "proposed"
                else f"revise-inquiry --inquiry {inquiry_id} ..."
            )
            run_log_entry(
                epoch,
                role,
                "epoch_waiting_for_inquiry",
                {"inquiry": inquiry_id, "status": status},
            )
            print(
                f"STOP: {inquiry_id} is {status}; the runner will not bypass "
                f"the inquiry gate. Next: {next_command}"
            )
            return 0
        outcome_waiting = sorted(
            inquiry_id
            for inquiry_id, inquiry in state.get("inquiries", {}).items()
            if inquiry.get("status") == "awaiting_outcome"
        )
        if outcome_waiting:
            inquiry_id = outcome_waiting[0]
            run_log_entry(
                epoch,
                role,
                "epoch_waiting_for_outcome",
                {"inquiry": inquiry_id},
            )
            print(
                f"STOP: {inquiry_id} awaits independent measured outcome "
                "assessment; resume with assess-inquiry."
            )
            return 0
        active_inquiry_id = state.get("active_inquiry_id")
        active_links = sorted(
            (
                proposal_id,
                proposal.get("status"),
            )
            for proposal_id, proposal in state.get("proposals", {}).items()
            if proposal.get("inquiry_id") == active_inquiry_id
            and proposal.get("status") in (
                "proposed", "challenged", "passed_critic", "verified",
            )
        ) if active_inquiry_id else []
        if active_links:
            proposal_id, proposal_status = active_links[0]
            next_action = {
                "proposed": "critique",
                "challenged": "revise",
                "passed_critic": "verify",
                "verified": (
                    "apply"
                    if state["inquiries"][active_inquiry_id].get("status")
                    == "supported"
                    else "assess-inquiry"
                ),
            }[proposal_status]
            run_log_entry(
                epoch,
                role,
                "epoch_waiting_for_proposal",
                {
                    "inquiry": active_inquiry_id,
                    "proposal": proposal_id,
                    "status": proposal_status,
                },
            )
            print(
                f"STOP: {proposal_id} already implements {active_inquiry_id} "
                f"and is {proposal_status}; resume with {next_action}."
            )
            return 0
        proposer_prompt = run_proposer_prompt(
            state,
            role,
        )
        if args.dry_run:
            print(proposer_prompt[:800])
            continue
        proposal = run_agent_with_retries(
            args.proposer_cmd,
            proposer_prompt,
            args.agent_timeout,
            args.max_propose_attempts,
            agent_cwd,
        )
        action = proposal.get("action")
        if action in ("inquire", "no_change"):
            if state.get("active_inquiry_id"):
                raise SystemExit(
                    "BLOCKED: an accepted inquiry is active; implement or resolve it before opening another"
                )
            inquiry_payload = copy.deepcopy(proposal)
            inquiry_payload.pop("action", None)
            expected_disposition = (
                "no_change" if action == "no_change" else "propose"
            )
            if inquiry_payload.get("disposition") != expected_disposition:
                raise SystemExit(
                    f"BLOCKED: action {action!r} requires disposition "
                    f"{expected_disposition!r}; contradictory agent output is not canonicalized"
                )
            inquiry_id = record_inquiry(
                state,
                inquiry_payload,
                track=role,
                source="runner",
            )
            event = (
                "epoch_no_change_proposed"
                if inquiry_payload.get("disposition") == "no_change"
                else "epoch_inquiry_proposed"
            )
            run_log_entry(epoch, role, event, {"inquiry": inquiry_id})
            print(
                f"{inquiry_id} recorded; runner stopped before proposal, verification, or apply."
            )
            return 0
        if action != "propose":
            run_log_entry(epoch, role, "bad_proposer_action", proposal)
            raise SystemExit(
                "BLOCKED: proposer action must be inquire, no_change, or propose"
            )
        propose_args = runner_propose_args(
            proposal,
            role,
            state,
        )
        proc = run_cli(*propose_args)
        attempt = 1
        while proc.returncode != 0 and attempt < args.max_propose_attempts:
            attempt += 1
            feedback = (
                "Your previous proposal JSON was rejected by the engine:\n"
                + (proc.stdout + proc.stderr)[-2000:]
                + "\nCorrect the schema and return only the corrected JSON object."
            )
            proposal = run_agent_with_retries(
                args.proposer_cmd,
                proposer_prompt + "\n" + feedback,
                args.agent_timeout,
                args.max_propose_attempts,
                agent_cwd,
            )
            if proposal.get("action") != "propose":
                proc = subprocess.CompletedProcess([], 2, "", "proposer returned no patch list")
                break
            propose_args = runner_propose_args(
                proposal,
                role,
                state,
            )
            proc = run_cli(*propose_args)
        if proc.returncode != 0:
            run_log_entry(epoch, role, "propose_failed", {"stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
            print(proc.stdout, proc.stderr)
            return proc.returncode
        match = re.search(r"Proposed (P-\d+):", proc.stdout)
        if not match:
            raise SystemExit("BLOCKED: could not parse proposal id from propose output")
        pid = match.group(1)

        state = load_state()
        preflight_report = proposal_mechanical_preflight(state, pid)
        state["proposals"][pid]["preflight"] = preflight_report
        append_event(state, "proposal_preflighted", {
            "proposal": pid,
            "status": preflight_report["status"],
            "proposal_review_sha256": preflight_report[
                "proposal_review_sha256"
            ],
        })
        save_state(state, "proposal_preflighted")
        if args.deterministic_critic:
            preflight = preflight_report["status"]
            reason = preflight_report["reason"]
            run_log_entry(
                epoch,
                role,
                "mechanical_preflight_" + preflight,
                {"proposal": pid, "reason": reason},
            )
            print(
                f"STOP: {pid} remains proposed. {reason}. "
                "Resume with an independent B critique or rerun with --llm-critic."
            )
            return 0 if preflight == "ready_for_B" else 2
        else:
            if preflight_report["status"] != "ready_for_B":
                reason = preflight_report["reason"]
                run_log_entry(
                    epoch, role, "mechanical_preflight_blocked",
                    {"proposal": pid, "reason": reason},
                )
                print(f"STOP: {pid} failed mechanical preflight: {reason}")
                return 2
            critic = run_agent_with_retries(
                args.critic_cmd,
                run_critic_prompt(state, pid),
                args.agent_timeout,
                2,
                agent_cwd,
            )
            verdict = critic.get("verdict")
            reason = critic.get("reason")
            if (
                critic.get("action") != "critique"
                or verdict not in CRITIC_VERDICTS
                or not _substantive_text(reason)
            ):
                run_log_entry(epoch, role, "bad_critic", critic)
                raise SystemExit(
                    "BLOCKED: critic must return action=critique, a valid verdict, "
                    "and a substantive reason"
                )
            reason = str(reason)
        proc = run_cli("critique", "--track", "B", "--proposal", pid, "--verdict", verdict, "--critic", "auto-runner-critic", "--reason", reason)
        if proc.returncode != 0 or verdict != "pass":
            run_log_entry(epoch, role, "critic_" + verdict, {"proposal": pid, "reason": reason})
            print(proc.stdout)
            if verdict == "challenge":
                print(
                    f"STOP: {pid} remains challenged. A must respond with "
                    "revise; the runner will not fabricate a revision or consume "
                    "the inquiry by auto-rejecting it."
                )
                return 0
            if args.stop_on_reject and verdict == "reject":
                return 1
            continue
        proc = run_cli("verify", "--track", "B", "--proposal", pid, "--timeout", str(args.timeout), timeout=args.timeout + 60)
        if proc.returncode != 0:
            run_log_entry(epoch, role, "verify_failed", {"proposal": pid, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
            print(proc.stdout, proc.stderr)
            continue
        run_log_entry(
            epoch, role, "epoch_awaiting_outcome", {"proposal": pid},
        )
        print(
            f"STOP: {pid} is hard-verified but not research-supported. "
            "Run the frozen outcome plan, pin its artifact, then use assess-inquiry."
        )
        return 0
    return 0


def cmd_ab_status(args: argparse.Namespace) -> int:
    state = load_state()
    proposals = state.get("proposals", {})
    inquiries = state.get("inquiries", {})
    nodes = state.get("nodes", {})
    drafts = sorted(nid for nid, n in nodes.items() if n.get("status") == "draft")
    deprecated = sorted(nid for nid, n in nodes.items() if n.get("status") == "deprecated")
    a_props = [
        pid for pid, prop in proposals.items()
        if prop.get("track", "A") == "A"
        and prop.get("status") in (
            "proposed", "challenged", "passed_critic", "verified",
        )
    ]
    b_queue = sorted(
        pid for pid, prop in proposals.items()
        if prop.get("status") in ("proposed", "passed_critic", "verified")
    )
    open_retros = sorted(
        rid for rid, retro in state.get("retrospectives", {}).items()
        if retro.get("status") == "open"
    )
    a_inquiry_queue = sorted(
        inquiry_id for inquiry_id, inquiry in inquiries.items()
        if inquiry.get("status") in ("challenged", "accepted")
    )
    b_inquiry_queue = sorted(
        inquiry_id for inquiry_id, inquiry in inquiries.items()
        if inquiry.get("status") == "proposed"
    )
    b_outcome_queue = sorted(
        inquiry_id for inquiry_id, inquiry in inquiries.items()
        if inquiry.get("status") == "awaiting_outcome"
    )
    if args.json:
        print(json.dumps({
            "mode": "ab-status",
            "active_inquiry_id": state.get("active_inquiry_id"),
            "a_inquiry_queue": a_inquiry_queue,
            "b_inquiry_queue": b_inquiry_queue,
            "b_outcome_queue": b_outcome_queue,
            "draft_nodes": drafts,
            "deprecated_nodes": deprecated,
            "a_proposals": a_props,
            "b_queue": b_queue,
            "open_retrospectives": open_retros,
            "file_backups": len(state.get("file_backups", [])),
        }, indent=2, sort_keys=True))
        return 0

    print("A/B AUTO-RESEARCH STATUS")
    print("A (fast): diagnose, reframe, recommend; implement only a selected recommendation")
    print(f"  inquiry queue    : {a_inquiry_queue or 'none'}")
    print(f"  active inquiry   : {state.get('active_inquiry_id') or 'none'}")
    print(f"  draft nodes      : {drafts or 'none'}")
    print(f"  deprecated nodes : {deprecated or 'none'}")
    print(f"  A proposals      : {a_props or 'none'}")
    for pid in a_props:
        prop = proposals[pid]
        print(f"    {pid:8s} {prop.get('status', '?'):18s} {prop.get('title', '')[:60]}")

    print("B (slow): review inquiries; criticise, hard-verify, consolidate, apply")
    print(f"  inquiry reviews  : {b_inquiry_queue or 'none'}")
    print(f"  outcome reviews  : {b_outcome_queue or 'none'}")
    print(f"  B queue          : {b_queue or 'none'}")
    for pid in b_queue:
        prop = proposals[pid]
        action = {
            "proposed": "critique",
            "passed_critic": "verify",
        }.get(prop["status"])
        if prop["status"] == "verified":
            linked = inquiries.get(prop.get("inquiry_id") or "")
            action = (
                "apply" if linked and linked.get("status") == "supported"
                else "assess outcome"
            )
        print(f"    {pid:8s} {prop.get('status', '?'):18s} -> {action}")

    print(f"  open retrospectives: {open_retros or 'none'}")
    print(f"  file backups      : {len(state.get('file_backups', []))}")
    print("Loop contract")
    print("  A inquires -> B reviews/selects -> A proposes -> B criticises")
    print("  -> B hard-verifies -> independent outcome assessment -> supported only -> B applies")
    print("  -> trust decay + dependency closure -> A revises or opens a new candidate")
    return 0


def cmd_ab_next(args: argparse.Namespace) -> int:
    state = load_state()
    a_actions, b_actions = pending_role_actions(state)

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
    sp.add_argument(
        "--goal", default=META_GOAL_STATEMENT,
        help="compiled immutable L0 meta-goal (the default is the only accepted value)",
    )
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

    sp = sub.add_parser("inquire", help="A-track: record a structured self-inquiry before proposing")
    sp.add_argument("--track", choices=TRACKS, default="A")
    sp.add_argument("--file", required=True, help="JSON inquiry artifact")
    sp.add_argument("--source", default="manual")
    sp.set_defaults(func=cmd_inquire)

    sp = sub.add_parser("review-inquiry", help="B-track: review a research question and recommendation")
    sp.add_argument("--track", choices=TRACKS, default="B")
    sp.add_argument("--inquiry", required=True)
    sp.add_argument("--verdict", required=True, choices=INQUIRY_REVIEW_VERDICTS)
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--recommendation", help="recommendation id selected on pass")
    sp.set_defaults(func=cmd_review_inquiry)

    sp = sub.add_parser(
        "assess-inquiry",
        help="B-track: bind measured outcome evidence before live apply",
    )
    sp.add_argument("--track", choices=TRACKS, default="B")
    sp.add_argument("--inquiry", required=True)
    sp.add_argument("--file", required=True, help="JSON outcome report")
    sp.add_argument("--assessor", required=True)
    sp.set_defaults(func=cmd_assess_inquiry)

    sp = sub.add_parser("revise-inquiry", help="replace the fields of a challenged inquiry")
    sp.add_argument("--inquiry", required=True)
    sp.add_argument("--file", required=True)
    sp.add_argument("--note", required=True)
    sp.set_defaults(func=cmd_revise_inquiry)

    sp = sub.add_parser("inquiry-list", help="list self-inquiry artifacts")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_inquiry_list)

    sp = sub.add_parser("inquiry-show", help="show one self-inquiry artifact")
    sp.add_argument("--inquiry", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_inquiry_show)

    sp = sub.add_parser("self-observe", help="summarize evidence for self-inquiry without mutating state")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_self_observe)

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
    sp.add_argument("--inquiry", help="accepted inquiry id")
    sp.add_argument("--recommendation", help="B-selected recommendation id")
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
    sp.add_argument("--timeout", type=int, default=300,
                    help="pre-apply candidate self-test timeout seconds")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser(
        "transition-capture",
        help="B-track: freeze the exact schema-1 to schema-2 bootstrap boundary",
    )
    sp.add_argument("--track", choices=TRACKS, default="B")
    sp.add_argument("--proposal", required=True)
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_transition_capture)

    sp = sub.add_parser(
        "transition-install",
        help="B-track: invoke the frozen schema-1 apply then seal schema 2",
    )
    sp.add_argument("--track", choices=TRACKS, default="B")
    sp.add_argument("--manifest-sha256", required=True)
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--timeout", type=int, default=300)
    sp.set_defaults(func=cmd_transition_install)

    sp = sub.add_parser(
        "transition-seal",
        help="B-track: recover by sealing an already-applied exact bootstrap poststate",
    )
    sp.add_argument("--track", choices=TRACKS, default="B")
    sp.add_argument("--manifest-sha256", required=True)
    sp.add_argument("--reviewer", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_transition_seal)

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

    sp = sub.add_parser(
        "run",
        help="run supervised A/B epochs through staged headless agent commands",
        description=(
            "Run every proposer/critic call in a disposable repository copy. "
            "Live source, tests, docs, harness skills/instructions, and the "
            "auto-research state are manifest-monitored while Landlock denies "
            "ordinary writes outside disposable roots."
        ),
        epilog=(
            "This Linux-only cooperative boundary is not an OS sandbox. The "
            "manifest may best-effort reset selected metadata, but stores no "
            "byte preimages and does not repair content/topology. Concurrent "
            "same-UID writers are unsupported."
        ),
    )
    sp.add_argument("--epochs", type=int, required=True)
    sp.add_argument("--proposer-cmd", required=True, help="shell command; the prompt is appended quoted")
    sp.add_argument("--critic-cmd", default="", help="shell command for LLM critic; required only with --llm-critic")
    sp.add_argument("--deterministic-critic", dest="deterministic_critic", action="store_true", default=True, help="run mechanical preflight, then stop for independent B review (default)")
    sp.add_argument("--llm-critic", dest="deterministic_critic", action="store_false", help="use the configured headless critic command")
    sp.add_argument("--timeout", type=int, default=300, help="verification timeout seconds")
    sp.add_argument("--agent-timeout", type=int, default=120, help="proposer/critic timeout seconds")
    sp.add_argument(
        "--agent-cwd",
        default=".",
        help=(
            "working directory inside ROOT; it is mapped into each disposable "
            "staging copy (default: repository root)"
        ),
    )
    sp.add_argument(
        "--alternate", action="store_true",
        help="deprecated and rejected; inquiry/proposal authorship is always A",
    )
    sp.add_argument(
        "--no-alternate", dest="alternate", action="store_false",
        help="compatibility flag; inquiry/proposal authorship is always A",
    )
    sp.add_argument("--stop-on-reject", action="store_true", help="stop the run when a proposal is rejected")
    sp.add_argument("--max-propose-attempts", type=int, default=2, help="proposer repair attempts per epoch")
    sp.add_argument(
        "--stop-on-warning", action="store_true",
        help="deprecated and rejected; the runner stops before outcome/apply",
    )
    sp.add_argument(
        "--commit", action="store_true",
        help="deprecated and rejected; the runner never applies or commits",
    )
    sp.add_argument("--dry-run", action="store_true", help="print prompts without invoking agents")
    sp.set_defaults(func=cmd_run)

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
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_self_test)

    sp = sub.add_parser("snapshot", help="write a manual snapshot")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("snapshot-stats", help="show snapshot journal size and retention")
    sp.set_defaults(func=cmd_snapshot_stats)

    sp = sub.add_parser("snapshot-prune", help="apply snapshot retention policy now")
    sp.set_defaults(func=cmd_snapshot_prune)

    sp = sub.add_parser(
        "snapshot-reindex",
        help="explicitly trust and rebuild snapshot hashes after manual audit",
    )
    sp.set_defaults(func=cmd_snapshot_reindex)

    sp = sub.add_parser("file-backup-stats", help="summarize patch_file rollback backups")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_file_backup_stats)

    sp = sub.add_parser("health", help="run auto-research health checks")
    sp.add_argument("--json", action="store_true", help="emit a JSON report")
    sp.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("watch", help="watch auto-research health")
    sp.add_argument("--interval", type=int, default=30, help="seconds between checks")
    sp.add_argument("--once", action="store_true", help="run one check and exit")
    sp.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    sp.add_argument("--json", action="store_true", help="emit one JSON report per check")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("rollback", help="roll back to a snapshot (1 = oldest)")
    sp.add_argument("--to", type=int, required=True)
    sp.add_argument("--dry-run", action="store_true", help="preview without restoring")
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
    if sys.argv[1:2] == ["__landlock_exec__"]:
        raise SystemExit(_landlock_exec_main())
    raise SystemExit(main())
