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


if __name__ == "__main__":
    unittest.main()
