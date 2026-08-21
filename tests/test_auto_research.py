"""End-to-end tests for the self-evolved research harness engine.

The engine is the deterministic substrate of the auto-research loop. The LLM
proposer/critic is intentionally outside the subprocess tests; here we drive
the proposal pipeline directly and assert the mechanical guarantees: immutable
L0, dependency closure invalidation, trust decay, hard verification and
rollback.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "auto_research.py"
PASS_CMD = "python3 -c \"print('verified')\""
FAIL_CMD = "python3 -c \"raise SystemExit(1)\""


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.run_auto("init")

    def run_auto(self, *args):
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)}
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
        )

    def assertOk(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def assertBlocked(self, proc, needle=None):
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        if needle:
            self.assertIn(needle, proc.stdout + proc.stderr)
        return proc

    def state(self):
        return json.loads((self.repo / ".research" / "auto_research.json").read_text())

    def add_node(self, nid, statement="s", layer="L4", ntype="assumption",
                 status="validated", trust=None):
        args = ["add-node", "--id", nid, "--type", ntype,
                "--statement", statement, "--layer", layer, "--status", status]
        if trust is not None:
            args.extend(["--trust", str(trust)])
        return self.assertOk(self.run_auto(*args))

    def add_edge(self, src, dst, kind="dependency"):
        return self.run_auto("add-edge", "--from", src, "--to", dst, "--kind", kind)

    def patch_file(self, ops):
        path = self.repo / "patch.json"
        path.write_text(json.dumps(ops))
        return str(path)

    def propose_ready_modification(self, node="N1", new_statement="s-v2",
                                  verification=None):
        patch = self.patch_file(
            [{"op": "set_node_statement", "node": node, "statement": new_statement}]
        )
        args = [
            "propose", "--title", f"revise {node}", "--statement", "candidate",
            "--targets", node, "--patch-file", patch,
        ]
        if verification:
            args.extend(["--verification", verification])
        return self.assertOk(self.run_auto(*args))

    def drive_pipeline(self, proposal="P-001", verification=PASS_CMD):
        self.assertOk(self.run_auto(
            "critique", "--proposal", proposal, "--verdict", "pass",
            "--critic", "test-critic", "--reason", "local change is safe",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", proposal,
                                    "--command", verification))
        return self.assertOk(self.run_auto("apply", "--proposal", proposal))

    def make_file_patch(self, target, content, out="patch_file.json"):
        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        candidate = candidate_dir / f"{Path(out).stem}.candidate"
        candidate.write_text(content)
        patch_path = self.repo / out
        self.assertOk(self.run_auto(
            "patch-make", "--target", target,
            "--candidate", str(candidate), "--out", out,
        ))
        return str(patch_path)


class TestImmutableCore(RepoCase):
    def test_init_creates_l0_meta_goal(self):
        st = self.state()
        self.assertEqual(st["meta_goal"]["id"], "M0")
        self.assertTrue(st["meta_goal"]["immutable"])
        self.assertEqual(st["nodes"]["M0"]["layer"], "L0")
        self.assertEqual(st["nodes"]["M0"]["trust"], 1.0)

    def test_only_l0_node_may_be_created_by_init(self):
        proc = self.run_auto(
            "add-node", "--id", "L0B", "--type", "assumption",
            "--statement", "another core", "--layer", "L0",
        )
        self.assertBlocked(proc, "only L0 node")

    def test_no_edge_may_target_m0(self):
        self.add_node("N1")
        proc = self.add_edge("N1", "M0", "dependency")
        self.assertBlocked(proc, "immutable L0 core")

    def test_patch_cannot_modify_m0(self):
        patch = self.patch_file(
            [{"op": "set_node_statement", "node": "M0", "statement": "corrupted"}]
        )
        proc = self.run_auto(
            "propose", "--title", "change core", "--statement", "bad",
            "--targets", "M0", "--patch-file", patch,
        )
        self.assertBlocked(proc, "immutable L0 core")


class TestGraphValidation(RepoCase):
    def test_dependency_cycle_is_blocked(self):
        self.add_node("N1")
        self.add_node("N2")
        self.assertOk(self.add_edge("N1", "N2", "dependency"))
        proc = self.add_edge("N2", "N1", "dependency")
        self.assertBlocked(proc, "cycle")

    def test_modification_edge_requires_modification_source(self):
        self.add_node("N1")
        self.add_node("N2")
        proc = self.add_edge("N1", "N2", "modification")
        self.assertBlocked(proc, "modification edge")

    def test_unknown_edge_endpoint_is_blocked(self):
        self.add_node("N1")
        proc = self.add_edge("N1", "MISSING", "dependency")
        self.assertBlocked(proc, "undeclared node")


class TestProposalPipeline(RepoCase):
    def test_verify_requires_critic_pass_first(self):
        self.add_node("N1")
        self.propose_ready_modification(verification="python3 -c 'print(1)'")
        proc = self.run_auto("verify", "--proposal", "P-001")
        self.assertBlocked(proc, "pass critique")

    def test_apply_requires_hard_verification_first(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.run_auto("critique", "--proposal", "P-001", "--verdict", "pass",
                      "--critic", "test-critic", "--reason", "ok")
        proc = self.run_auto("apply", "--proposal", "P-001")
        self.assertBlocked(proc, "hard verification")

    def test_reject_stops_the_pipeline(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "reject",
            "--critic", "test-critic", "--reason", "too broad",
        ))
        self.assertEqual(self.state()["proposals"]["P-001"]["status"], "rejected")

    def test_challenge_then_revise_reopens_critique(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "challenge",
            "--critic", "test-critic", "--reason", "needs narrower patch",
        ))
        self.assertOk(self.run_auto("revise", "--proposal", "P-001",
                                    "--note", "narrowed"))
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"], "proposed")
        self.assertEqual(len(st["proposals"]["P-001"]["revisions"]), 1)


class TestModificationMechanics(RepoCase):
    def test_apply_invalidates_dependency_closure_and_decays_trust(self):
        self.add_node("A", "assumption A", "L1", status="validated", trust=1.0)
        self.add_node("B", "inference from A", "L4", status="validated", trust=1.0)
        self.add_node("C", "unaffected verification", "L4", status="validated", trust=1.0)
        self.assertOk(self.add_edge("A", "B", "dependency"))
        self.propose_ready_modification("A", "assumption A v2")
        self.drive_pipeline()

        st = self.state()
        nodes = st["nodes"]
        self.assertEqual(nodes["A"]["status"], "deprecated")
        self.assertEqual(nodes["B"]["status"], "deprecated")
        self.assertEqual(nodes["C"]["status"], "validated")
        self.assertAlmostEqual(nodes["C"]["trust"], 0.9)
        self.assertEqual(nodes["M0"]["trust"], 1.0)
        self.assertEqual(st["proposals"]["P-001"]["status"], "applied")
        mod_id = st["proposals"]["P-001"]["modification_node"]
        self.assertEqual(nodes[mod_id]["type"], "modification")
        self.assertEqual(nodes[mod_id]["trust"], 1.0)
        modification_targets = {
            e["to"] for e in st["edges"] if e["kind"] == "modification"
        }
        self.assertIn("A", modification_targets)

    def test_failed_hard_verification_does_not_apply(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.run_auto("critique", "--proposal", "P-001", "--verdict", "pass",
                      "--critic", "test-critic", "--reason", "ok")
        proc = self.run_auto("verify", "--proposal", "P-001",
                             "--command", FAIL_CMD)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.state()["proposals"]["P-001"]["status"],
                         "failed_verification")
        self.assertBlocked(self.run_auto("apply", "--proposal", "P-001"))


class TestRevalidationAndSelfTest(RepoCase):
    def test_hard_revalidation_restores_trust(self):
        self.add_node("N1", status="deprecated", trust=0.2)
        self.assertOk(self.run_auto(
            "revalidate", "--node", "N1", "--level", "hard",
            "--command", PASS_CMD,
        ))
        node = self.state()["nodes"]["N1"]
        self.assertEqual(node["status"], "validated")
        self.assertEqual(node["trust"], 1.0)

    def test_self_test_records_pass_and_fail(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        self.assertTrue(self.state()["last_self_test"]["passed"])
        proc = self.run_auto("self-test", "--command", FAIL_CMD)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.state()["last_self_test"]["passed"])



class TestABTracks(RepoCase):
    def test_proposal_records_a_track_and_ab_next_routes_to_b(self):
        self.add_node("N1")
        self.propose_ready_modification()
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["track"], "A")
        status = self.assertOk(self.run_auto("ab-status")).stdout
        self.assertIn("A (fast)", status)
        self.assertIn("P-001", status)
        nxt = self.assertOk(self.run_auto("ab-next")).stdout
        self.assertIn("critique --proposal P-001 --track B", nxt)
        self.assertIn("A NEXT (fast layer)", nxt)
        self.assertIn("B NEXT (slow layer)", nxt)

    def test_critic_and_verification_record_b_track(self):
        self.add_node("N1")
        self.propose_ready_modification(verification=PASS_CMD)
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "ok",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["critic"]["track"], "B")
        self.assertEqual(st["proposals"]["P-001"]["verification"]["track"], "B")

    def test_failed_verification_can_be_revised_by_a(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.run_auto("critique", "--proposal", "P-001", "--verdict", "pass",
                      "--critic", "test-critic", "--reason", "ok")
        self.run_auto("verify", "--proposal", "P-001", "--command", FAIL_CMD)
        self.assertEqual(self.state()["proposals"]["P-001"]["status"],
                         "failed_verification")
        self.assertOk(self.run_auto("revise", "--proposal", "P-001",
                                    "--note", "fixed command"))
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"], "proposed")
        self.assertIsNone(st["proposals"]["P-001"]["verification"])

class TestSnapshotsAndRollback(RepoCase):
    def test_rollback_restores_earlier_self_graph(self):
        baseline = self.state()
        self.add_node("N1")
        self.add_node("N2")
        self.assertEqual(len(self.state()["nodes"]), 3)
        proc = self.assertOk(self.run_auto("rollback", "--to", "1"))
        self.assertIn("Rolled back", proc.stdout)
        restored = self.state()
        self.assertEqual(restored["nodes"], baseline["nodes"])
        self.assertEqual(restored["edges"], baseline["edges"])
        self.assertEqual(
            len(restored["events"]), len(baseline.get("events", [])) + 1
        )
        self.assertEqual(restored["events"][-1]["event"], "rolled_back")


class TestPatchFileCapability(RepoCase):
    def test_patch_make_proposes_verifies_in_sandbox_and_applies(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        patch = self.make_file_patch(
            "tools/new_file.py", "print('patched')\n",
        )
        self.assertOk(self.run_auto(
            "propose", "--title", "add engine file", "--statement",
            "small file addition", "--targets", "N1", "--patch-file", patch,
            "--verification",
            "python3 -c \"assert open('tools/new_file.py').read().startswith('print')\"",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "one new allowlisted file",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        # Verification ran in a temporary copy; the real working tree is clean.
        self.assertFalse((self.repo / "tools" / "new_file.py").exists())
        self.assertOk(self.run_auto("apply", "--proposal", "P-001"))
        self.assertEqual(
            (self.repo / "tools" / "new_file.py").read_text(),
            "print('patched')\n",
        )
        st = self.state()
        self.assertEqual(len(st["file_backups"]), 1)
        self.assertEqual(
            st["file_backups"][0]["files"]["tools/new_file.py"], None
        )

    def test_patch_file_requires_declared_targets(self):
        self.add_node("N1")
        patch = self.make_file_patch("tools/new_file.py", "x\n")
        proc = self.run_auto(
            "propose", "--title", "missing targets", "--statement", "bad",
            "--patch-file", patch, "--verification", PASS_CMD,
        )
        self.assertBlocked(proc, "must declare")

    def test_patch_file_cannot_touch_protected_state(self):
        self.add_node("N1")
        target = self.repo / ".research" / "auto_research.json"
        candidate = self.repo / ".research" / "tmp" / "state.candidate"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(target.read_text().replace("M0", "M9", 1))
        proc = self.run_auto(
            "patch-make", "--target", ".research/auto_research.json",
            "--candidate", str(candidate), "--out", "bad.json",
        )
        self.assertBlocked(proc, "protected")

    def test_patch_file_cannot_delete_core_engine_file(self):
        self.add_node("N1")
        (self.repo / "tools").mkdir(exist_ok=True)
        target = self.repo / "tools" / "auto_research.py"
        target.write_text("core\n")
        patch_path = self.repo / "delete_core.json"
        patch_path.write_text(json.dumps([{
            "op": "patch_file",
            "patch": "--- a/tools/auto_research.py\n+++ /dev/null\n"
                     "@@ -1 +0,0 @@\n-core\n",
        }]))
        proc = self.run_auto(
            "propose", "--title", "delete core", "--statement", "bad",
            "--targets", "N1", "--patch-file", str(patch_path),
        )
        self.assertBlocked(proc, "may not delete core engine file")

    def test_rollback_restores_file_contents(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        (self.repo / "tools").mkdir(exist_ok=True)
        target = self.repo / "tools" / "file.txt"
        target.write_text("v1\n")

        first_patch = self.make_file_patch("tools/file.txt", "v2\n", "p1.json")
        self.assertOk(self.run_auto(
            "propose", "--title", "file v1->v2", "--statement", "local edit",
            "--targets", "N1", "--patch-file", first_patch,
            "--verification",
            "python3 -c \"assert open('tools/file.txt').read() == 'v2\\n'\"",
        ))
        self.drive_pipeline()
        self.assertEqual(target.read_text(), "v2\n")

        snapshots = sorted((self.repo / ".research" / "auto_snapshots").glob("*.json"))
        first_applied = next(
            p for p in snapshots
            if p.name.endswith("_modification_applied.json")
            and json.loads(p.read_text()).get("proposals", {}).get("P-001", {}).get("status") == "applied"
        )
        first_applied_index = snapshots.index(first_applied) + 1

        self.add_node("N2", "second target", "L4", ntype="verify",
                      status="validated")
        second_patch = self.make_file_patch("tools/file.txt", "v3\n", "p2.json")
        self.assertOk(self.run_auto(
            "propose", "--title", "file v2->v3", "--statement", "local edit",
            "--targets", "N2", "--patch-file", second_patch,
            "--verification",
            "python3 -c \"assert open('tools/file.txt').read() == 'v3\\n'\"",
        ))
        self.drive_pipeline("P-002")
        self.assertEqual(target.read_text(), "v3\n")

        self.assertOk(self.run_auto("rollback", "--to", str(first_applied_index)))
        self.assertEqual(target.read_text(), "v2\n")
        st = self.state()
        self.assertNotIn("P-002", st["proposals"])


class TestRetrospectiveBacklog(RepoCase):
    def test_retro_add_next_and_auto_close_on_apply(self):
        self.add_node("N1")
        retro = self.repo / "retro.json"
        retro.write_text(json.dumps({
            "observation": "dashboard crashes without snapshots",
            "class": "defect",
            "source": "human-meta-review",
            "evidence": ["IndexError"],
            "suggested": {
                "title": "dashboard fallback",
                "targets": ["N1"],
                "patch_intent": "patch dashboard",
                "verification": PASS_CMD,
            },
        }))
        self.assertOk(self.run_auto("retro", "add", "--file", str(retro)))
        nxt = self.assertOk(self.run_auto("retro", "next")).stdout
        self.assertIn("R-001", nxt)
        self.assertIn("dashboard crashes", nxt)

        patch = self.patch_file(
            [{"op": "set_node_statement", "node": "N1", "statement": "v2"}]
        )
        self.assertOk(self.run_auto(
            "propose", "--title", "convert retro", "--statement", "candidate",
            "--targets", "N1", "--patch-file", patch, "--verification", PASS_CMD,
            "--retro", "R-001",
        ))
        self.drive_pipeline()
        st = self.state()
        self.assertEqual(st["retrospectives"]["R-001"]["status"], "converted")
        self.assertEqual(st["retrospectives"]["R-001"]["proposal_id"], "P-001")
        self.assertEqual(st["proposals"]["P-001"]["retrospective_id"], "R-001")
        nxt = self.assertOk(self.run_auto("retro", "next")).stdout
        self.assertIn("none open", nxt)

    def test_retro_converted_requires_applied_proposal(self):
        self.add_node("N1")
        self.assertOk(self.run_auto(
            "retro", "add", "--observation", "one finding",
            "--class", "defect", "--source", "test",
        ))
        proc = self.run_auto(
            "retro", "close", "--id", "R-001",
            "--disposition", "converted", "--proposal", "P-999",
        )
        self.assertBlocked(proc)


class TestSnapshotRetention(RepoCase):
    def test_index_dedups_non_adjacent_states_and_auto_prunes(self):
        script = r"""
import copy, json, shutil, sys
sys.path.insert(0, "tools")
import auto_research as ar
for path in ar.SNAP_DIR.glob("*.json"):
    path.unlink()
index = ar.SNAP_DIR / ar.SNAP_INDEX_NAME
if index.exists():
    index.unlink()
state = ar.skeleton()
state["snapshot_retention"] = {"keep_last": 1, "keep_labels": ["init"]}
ar.save_state(state, "init")
assert len(ar.list_snapshots()) == 1, ar.list_snapshots()
changed = copy.deepcopy(state)
changed["last_self_test"] = {"passed": True, "exit_code": 0, "at": "x"}
ar.save_state(changed, "node_revalidated")
# Non-adjacent duplicate state must dedup through the hash index.
ar.save_state(copy.deepcopy(state), "repeated")
assert len(ar.list_snapshots()) == 2, ar.list_snapshots()
for i in range(6):
    changed["last_self_test"] = {"passed": True, "exit_code": 0, "at": str(i)}
    ar.save_state(copy.deepcopy(changed), "node_revalidated")
assert len(ar.list_snapshots()) == 2, ar.list_snapshots()
print("snapshot-dedup-and-prune-ok")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("snapshot-dedup-and-prune-ok", proc.stdout)

    def test_snapshot_stats_and_prune_commands_exist(self):
        stats = self.assertOk(self.run_auto("snapshot-stats")).stdout
        self.assertIn("SNAPSHOT JOURNAL", stats)
        self.assertIn("retention", stats)
        pruned = self.assertOk(self.run_auto("snapshot-prune")).stdout
        self.assertIn("Snapshot prune:", pruned)


class TestHealthWatch(RepoCase):
    def test_health_json_passes_after_self_test(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        proc = self.assertOk(self.run_auto("health", "--json"))
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertIn("last_self_test", report["checks"])
        self.assertIn("snapshot_journal", report["checks"])

    def test_health_detects_stale_last_event(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        st = self.state()
        st["events"][-1]["at"] = "2000-01-01T00:00:00+00:00"
        (self.repo / ".research" / "auto_research.json").write_text(
            json.dumps(st, indent=2) + "\n"
        )
        proc = self.run_auto("health", "--json")
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertIn("last_event", report["critical"])

    def test_health_default_does_not_fail_on_warning_level_draft(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        self.add_node("N-DRAFT", status="draft")
        proc = self.assertOk(self.run_auto("health", "--json"))
        report = json.loads(proc.stdout)
        self.assertIn("draft_deprecated", report["warnings"])
        strict = self.run_auto("health", "--json", "--strict")
        self.assertNotEqual(strict.returncode, 0)

    def test_watch_once_runs_health(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        out = self.assertOk(self.run_auto("watch", "--once", "--interval", "1")).stdout
        self.assertIn("AUTO-RESEARCH HEALTH", out)
        self.assertIn("HEALTH OK", out)


class TestLaunchPreflight(RepoCase):
    def test_launch_dry_run_prints_preflight(self):
        out = self.assertOk(self.run_auto(
            "launch", "--harness", "dsh", "--role", "A", "--dry-run",
        )).stdout
        self.assertIn("AUTO-RESEARCH LAUNCH PREFLIGHT", out)
        self.assertIn("selected role    : A", out)
        self.assertIn("launch command", out)

    def test_launch_manual_records_event(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        self.assertOk(self.run_auto("launch", "--harness", "manual", "--role", "A"))
        st = self.state()
        self.assertEqual(st["events"][-1]["event"], "auto_research_launch")
        self.assertEqual(st["events"][-1]["payload"]["role"], "A")


class TestSemanticNoveltyGuard(RepoCase):
    def test_rejects_epoch_recycled_set_node_status_title(self):
        self.add_node("N1", "trust accounting", "L4", ntype="inference",
                      status="validated")
        first = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        self.assertOk(self.run_auto(
            "propose", "--title",
            "A optimizes B: trust accounting via inference L4 (epoch 1)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", first,
        ))
        second = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        proc = self.run_auto(
            "propose", "--title",
            "B optimizes A: trust accounting via assumption L2 (epoch 2)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", second,
        )
        self.assertBlocked(proc, "semantic-novelty guard")

    def test_distinct_patch_file_proposals_pass(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        first = self.make_file_patch("tools/helper_a.py", "x = 1\n", "a.json")
        self.assertOk(self.run_auto(
            "propose", "--title", "add helper A", "--statement", "file patch",
            "--targets", "N1", "--patch-file", first, "--verification", PASS_CMD,
        ))
        second = self.make_file_patch("tools/helper_b.py", "x = 2\n", "b.json")
        self.assertOk(self.run_auto(
            "propose", "--title", "add helper B", "--statement", "file patch",
            "--targets", "N1", "--patch-file", second, "--verification", PASS_CMD,
        ))


class TestABrief(RepoCase):
    def test_a_brief_prints_compact_context(self):
        self.add_node("N1", "candidate", "L1", ntype="assumption", status="draft")
        out = self.assertOk(self.run_auto("a-brief")).stdout
        self.assertIn("A BRIEF", out)
        self.assertIn("patch vocabulary", out)
        self.assertIn("N1", out)
        self.assertIn("protected paths", out)

    def test_a_brief_rebuilds_legacy_snapshot_index(self):
        self.add_node("N1")
        index = self.repo / ".research" / "auto_snapshots" / "index.json"
        self.assertTrue(index.exists())
        index.unlink()
        self.assertOk(self.run_auto("a-brief"))
        rebuilt = json.loads(index.read_text())
        self.assertTrue(rebuilt["files"])
        journal = sorted((self.repo / ".research" / "auto_snapshots").glob("*.json"))
        self.assertEqual(len(rebuilt["files"]), len(journal) - 1)


class TestACheck(RepoCase):
    def test_a_check_passes_valid_patch_without_proposing(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        patch = self.make_file_patch("tools/checked.py", "x = 1\n", "check.json")
        out = self.assertOk(self.run_auto(
            "a-check", "--title", "add checked helper",
            "--statement", "file patch", "--targets", "N1",
            "--patch-file", patch, "--verification", PASS_CMD,
        )).stdout
        self.assertIn("A CHECK PASS", out)
        self.assertNotIn("P-001", self.state()["proposals"])

    def test_a_check_blocks_recycled_candidate(self):
        self.add_node("N1", "trust accounting", "L4", ntype="inference",
                      status="validated")
        first = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        self.assertOk(self.run_auto(
            "propose", "--title",
            "A optimizes B: trust accounting via inference L4 (epoch 1)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", first,
        ))
        proc = self.run_auto(
            "a-check", "--title",
            "B optimizes A: trust accounting via assumption L2 (epoch 2)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", first,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("novelty", proc.stdout)


class TestEventsTail(RepoCase):
    def test_events_tail_json(self):
        self.add_node("N1")
        out = self.assertOk(self.run_auto("events", "--json", "--tail", "1")).stdout
        events = json.loads(out)
        self.assertEqual(len(events), 1)

    def test_events_tail_text(self):
        self.add_node("N1")
        out = self.assertOk(self.run_auto("events", "--tail", "1")).stdout
        self.assertIn("EVENT LOG (1 events)", out)

    def test_events_invalid_since_is_blocked(self):
        proc = self.run_auto("events", "--since", "not-a-date")
        self.assertBlocked(proc, "invalid --since")


class TestABJson(RepoCase):
    def test_ab_status_json(self):
        self.add_node("N1", status="draft")
        out = self.assertOk(self.run_auto("ab-status", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "ab-status")
        self.assertIn("N1", data["draft_nodes"])

    def test_ab_next_json(self):
        out = self.assertOk(self.run_auto("ab-next", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "ab-next")
        self.assertIn("a-brief", data["a_actions"])
        self.assertIsInstance(data["b_actions"], list)


if __name__ == "__main__":
    unittest.main()
