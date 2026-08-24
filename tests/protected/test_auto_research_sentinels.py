"""Small immutable sentinels for the auto-research trust boundary.

The mutable regression suite may evolve with the engine.  These tests are kept
under tests/protected/, which patch_file cannot modify, and are always run by the
pre-apply gate against the patched candidate tree.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "tools" / "auto_research.py"
PASS_CMD = "python3 -c \"print('protected-pass')\""
FAIL_CMD = "python3 -c \"raise SystemExit(1)\""
EXPECTED_META_GOAL = {
    "id": "M0",
    "statement": "Improve this system's own auto-research capability.",
    "immutable": True,
}
EXPECTED_FINAL_GOAL = {
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
ENGINE_SOURCE = CLI.read_text(errors="replace") if CLI.is_file() else ""
SCHEMA2_ENGINE = (
    "SCHEMA_VERSION = 2" in ENGINE_SOURCE
    and '"transition-capture"' in ENGINE_SOURCE
    and '"inquire"' in ENGINE_SOURCE
)


@unittest.skipUnless(
    SCHEMA2_ENGINE,
    "schema-2 sentinel is preinstalled as an external bootstrap trust floor",
)
class ProtectedAutoResearchSentinels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        guard_dir = self.root / "tests" / "protected"
        guard_dir.mkdir(parents=True)
        (guard_dir / "test_guard.py").write_text(
            "import unittest\n"
            "class Guard(unittest.TestCase):\n"
            "    def test_guard(self): self.assertTrue(True)\n"
        )
        self.assert_ok(self.run_cli("init"))
        self.assert_external_goal_anchor(self.state())

    def run_cli(self, *args):
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.root)}
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
        )

    def assert_ok(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def assert_blocked(self, proc, text):
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(text, proc.stdout + proc.stderr)
        return proc

    def state(self):
        return json.loads(
            (self.root / ".research" / "auto_research.json").read_text()
        )

    def assert_external_goal_anchor(self, state):
        """Anchor M0/final_goal outside the self-modifiable engine and tests."""
        self.assertEqual(state["meta_goal"], EXPECTED_META_GOAL)
        self.assertEqual(state["final_goal"], EXPECTED_FINAL_GOAL)
        m0 = state["nodes"]["M0"]
        self.assertEqual(m0["statement"], EXPECTED_META_GOAL["statement"])
        self.assertEqual(m0["layer"], "L0")
        self.assertEqual(m0["status"], "validated")
        self.assertEqual(m0["trust"], 1.0)
        self.assertTrue(m0["immutable"])

    def test_external_goal_anchor_matches_original_contract(self):
        self.assert_external_goal_anchor(self.state())

    def add_node(self):
        self.assert_ok(self.run_cli(
            "add-node", "--id", "N1", "--type", "assumption",
            "--statement", "protected target", "--layer", "L4",
            "--status", "validated",
        ))

    def inquiry(self, disposition="propose", allowed_ops=None):
        recommendations = []
        no_change_reason = None
        revisit_when = None
        if disposition == "propose":
            recommendations = [{
                "id": "REC-1",
                "title": "Bound one implementation",
                "target": "the proposal pipeline boundary",
                "change": "Apply exactly one reviewed and contracted modification.",
                "rationale": "A machine boundary prevents an unrelated linked patch.",
                "validation": "Run the frozen command and protected sentinels.",
                "implementation_contract": {
                    "allowed_ops": allowed_ops or ["set_node_statement"],
                    "allowed_paths": [],
                    "required_targets": ["N1"],
                    "required_verification_terms": ["python3"],
                },
            }]
        else:
            no_change_reason = (
                "The observed evidence does not justify changing the evaluator."
            )
            revisit_when = (
                "Revisit after a new failure contradicts this stopping decision."
            )
        return {
            "scope": "The self-research question and implementation boundary",
            "diagnosis": (
                "Direct patch production can reward activity before problem selection."
            ),
            "evidence": [{
                "kind": "source",
                "locator": "tools/auto_research.py:cmd_propose",
                "observation": (
                    "Proposal admission is controlled by deterministic state checks."
                ),
            }],
            "question_before": "Which patch can be applied next?",
            "question_after": (
                "Does a reviewed inquiry reduce false acceptance at equal budget?"
            ),
            "why_better": (
                "It makes the research question and failure criterion explicit."
            ),
            "hypothesis": {
                "claim": "The inquiry boundary lowers false acceptance.",
                "falsifier": "False acceptance does not fall at equal budget.",
            },
            "evaluation_boundary": {
                "mutable": ["proposal scheduling policy"],
                "protected": ["protected sentinel evaluator"],
                "baseline": "The direct proposal pipeline before inquiry review",
                "measure": "False acceptance rate at a fixed proposal budget",
                "evaluator": "Pinned unit tests and a held-out result artifact",
                "success_rule": (
                    "The candidate lowers false acceptance at the frozen budget."
                ),
                "guardrails": [
                    "Valid-change yield declines by no more than five percentage points."
                ],
                "budget": "One proposal and equal time and token limits per condition",
            },
            "recommendations": recommendations,
            "disposition": disposition,
            "no_change_reason": no_change_reason,
            "revisit_when": revisit_when,
        }

    def record_and_accept(self, allowed_ops=None):
        inquiry_path = self.root / "inquiry.json"
        inquiry_path.write_text(json.dumps(self.inquiry(allowed_ops=allowed_ops)))
        self.assert_ok(self.run_cli("inquire", "--file", str(inquiry_path)))
        self.assert_ok(self.run_cli(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "protected-reviewer",
            "--reason", "The question, falsifier, and contract are explicit.",
            "--recommendation", "REC-1",
        ))

    def patch_file(self, patch):
        path = self.root / "patch.json"
        path.write_text(json.dumps(patch))
        return str(path)

    def assess_supported(self):
        artifact = self.root / "protected-outcome.json"
        artifact.write_text(json.dumps({
            "baseline_false_acceptance": 0.3,
            "candidate_false_acceptance": 0.1,
        }))
        guardrails = self.state()["inquiries"]["INQ-001"][
            "outcome_plan"
        ]["guardrails"]
        outcome = {
            "verdict": "supported",
            "evidence": [{
                "kind": "held-out result",
                "locator": "protected-outcome.json",
                "observation": "The candidate and baseline used the same frozen budget.",
            }],
            "artifact_locator": "protected-outcome.json",
            "baseline_result": "False acceptance was 0.30 at the fixed budget.",
            "candidate_result": "False acceptance was 0.10 at the fixed budget.",
            "budget_used": "Both conditions used one proposal and equal resource limits.",
            "guardrail_results": [{
                "guardrail": guardrail,
                "result": "Valid-change yield declined by only two percentage points.",
                "passed": True,
            } for guardrail in guardrails],
            "reason": "The frozen primary rule and every guardrail were satisfied.",
            "next_question": "Does the improvement replicate on unseen repositories?",
        }
        path = self.root / "outcome.json"
        path.write_text(json.dumps(outcome))
        self.assert_ok(self.run_cli(
            "assess-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--file", str(path), "--assessor", "protected-independent-assessor",
        ))

    def test_unlinked_proposal_is_blocked(self):
        self.add_node()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "unlinked",
        }])
        self.assert_blocked(self.run_cli(
            "propose", "--title", "bypass", "--statement", "must fail",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ), "accepted --inquiry")

    def test_m0_is_blocked_after_valid_inquiry_acceptance(self):
        payload = self.inquiry()
        payload["recommendations"][0]["implementation_contract"][
            "required_targets"
        ] = ["M0"]
        path = self.root / "m0-inquiry.json"
        path.write_text(json.dumps(payload))
        self.assert_ok(self.run_cli("inquire", "--file", str(path)))
        self.assert_ok(self.run_cli(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "protected-reviewer",
            "--reason", "The boundary is explicit so the immutable guard is isolated.",
            "--recommendation", "REC-1",
        ))
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "M0", "statement": "weakened",
        }])
        self.assert_blocked(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "mutate immutable goal", "--statement", "must fail",
            "--targets", "M0", "--patch-file", patch,
            "--verification", PASS_CMD,
        ), "immutable L0 core")

    def test_protected_test_path_cannot_be_self_modified(self):
        self.add_node()
        payload = self.inquiry(allowed_ops=["patch_file"])
        payload["recommendations"][0]["implementation_contract"][
            "allowed_paths"
        ] = ["tests/protected/"]
        path = self.root / "protected-path-inquiry.json"
        path.write_text(json.dumps(payload))
        self.assert_ok(self.run_cli("inquire", "--file", str(path)))
        self.assert_ok(self.run_cli(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "protected-reviewer",
            "--reason", "The explicit contract isolates the protected path denylist.",
            "--recommendation", "REC-1",
        ))
        diff = self.patch_file([{
            "op": "patch_file",
            "patch": (
                "--- a/tests/protected/test_guard.py\n"
                "+++ b/tests/protected/test_guard.py\n"
                "@@ -1,3 +1,3 @@\n"
                " import unittest\n"
                " class Guard(unittest.TestCase):\n"
                "-    def test_guard(self): self.assertTrue(True)\n"
                "+    def test_guard(self): self.assertTrue(False)\n"
            ),
        }])
        self.assert_blocked(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "rewrite protected evaluator", "--statement", "must fail",
            "--targets", "N1", "--patch-file", diff,
            "--verification", PASS_CMD,
        ), "protected path")

    def test_no_change_creates_no_change_evidence(self):
        path = self.root / "no-change.json"
        path.write_text(json.dumps(self.inquiry("no_change")))
        self.assert_ok(self.run_cli("inquire", "--file", str(path)))
        self.assert_ok(self.run_cli(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "protected-reviewer",
            "--reason", "The stopping condition is explicit and falsifiable.",
        ))
        state = self.state()
        self.assertEqual(state["inquiries"]["INQ-001"]["status"], "closed_no_change")
        self.assertEqual(state["proposals"], {})
        self.assertEqual(state["verifications"], [])

    def test_contract_and_one_attempt_are_enforced(self):
        self.add_node()
        self.record_and_accept()
        wrong = self.patch_file([{
            "op": "set_node_status", "node": "N1", "status": "draft",
        }])
        self.assert_blocked(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "outside contract", "--statement", "must be rejected",
            "--targets", "N1", "--patch-file", wrong,
            "--verification", PASS_CMD,
        ), "outside selected recommendation contract")
        valid = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "attempt one",
        }])
        self.assert_ok(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "one attempt", "--statement", "bounded implementation",
            "--targets", "N1", "--patch-file", valid,
            "--verification", PASS_CMD,
        ))
        self.assert_blocked(self.run_cli(
            "verify", "--track", "B", "--proposal", "P-001",
        ), "must pass critique")
        self.assert_ok(self.run_cli(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "reject", "--critic", "protected-reviewer",
            "--reason", "The implementation does not realize the recommendation.",
        ))
        self.assert_blocked(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "silent retry", "--statement", "must not be admitted",
            "--targets", "N1", "--patch-file", valid,
            "--verification", PASS_CMD,
        ), "not the active accepted inquiry")

    def test_b_acceptance_pins_evaluator_bytes_before_proposal(self):
        self.add_node()
        self.record_and_accept()
        state = self.state()
        inquiry = state["inquiries"]["INQ-001"]
        snapshot_id = inquiry["evaluator_snapshot_id"]
        snapshot = state["evaluator_snapshots"][snapshot_id]
        self.assertEqual(snapshot["inquiry_id"], "INQ-001")
        self.assertTrue(snapshot["entries"])
        for entry in snapshot["entries"]:
            blob = (
                self.root / ".research" / "auto_snapshots" /
                "evaluator_blobs" / entry["sha256"]
            )
            self.assertTrue(blob.is_file())

        guard = self.root / "tests" / "protected" / "test_guard.py"
        guard.write_text(guard.read_text() + "# drift after review\n")
        valid = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "candidate",
        }])
        self.assert_blocked(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "silent evaluator rebase",
            "--statement", "must not replace B's frozen boundary",
            "--targets", "N1", "--patch-file", valid,
            "--verification", PASS_CMD,
        ), "live evaluator drifted")
        self.assertEqual(self.state()["proposals"], {})

    def test_hard_verification_alone_cannot_authorize_live_apply(self):
        self.add_node()
        self.record_and_accept()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1",
            "statement": "must wait for outcome evidence",
        }])
        self.assert_ok(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "verified but not measured",
            "--statement", "operability is not hypothesis support",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        self.assert_ok(self.run_cli(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "protected-independent-reviewer",
            "--reason", "The exact patch is bounded but still needs measured evidence.",
        ))
        self.assert_blocked(self.run_cli(
            "assess-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--file", str(self.root / "not-yet.json"),
            "--assessor", "protected-independent-assessor",
        ), "not awaiting outcome assessment")
        self.assert_ok(self.run_cli("verify", "--track", "B", "--proposal", "P-001"))
        self.assert_blocked(self.run_cli(
            "apply", "--track", "B", "--proposal", "P-001",
        ), "no supported outcome")
        state = self.state()
        self.assertEqual(state["inquiries"]["INQ-001"]["status"], "awaiting_outcome")
        self.assertEqual(state["nodes"]["N1"]["statement"], "protected target")

    def test_frozen_evaluator_cannot_be_weakened(self):
        self.add_node()
        state_path = self.root / ".research" / "auto_research.json"
        state = self.state()
        state["self_test_command"] = FAIL_CMD
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.record_and_accept(allowed_ops=["set_self_test_command"])
        patch = self.patch_file([{
            "op": "set_self_test_command", "command": PASS_CMD,
        }])
        self.assert_ok(self.run_cli(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "weaken evaluator", "--statement", "must not self-certify",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        self.assert_ok(self.run_cli(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "adversarial-reviewer",
            "--reason", "Pass deliberately to exercise the mechanical guard.",
        ))
        self.assert_ok(self.run_cli("verify", "--track", "B", "--proposal", "P-001"))
        self.assess_supported()
        self.assert_blocked(self.run_cli(
            "apply", "--track", "B", "--proposal", "P-001", "--timeout", "60",
        ), "live patch not applied")
        self.assertEqual(self.state()["self_test_command"], FAIL_CMD)

    def test_migrated_intermediate_proposal_cannot_continue(self):
        self.add_node()
        marker = self.root / "legacy-command-ran"
        state_path = self.root / ".research" / "auto_research.json"
        state = self.state()
        state["schema_version"] = 1
        state.pop("legacy_proposal_ids", None)
        state["counters"]["proposal"] = 1
        state["proposals"]["P-001"] = {
            "id": "P-001",
            "track": "A",
            "title": "pre-inquiry intermediate proposal",
            "statement": "must remain historical only",
            "targets": ["N1"],
            "patch": [{
                "op": "set_node_statement",
                "node": "N1",
                "statement": "legacy-after",
            }],
            "verification_command": PASS_CMD,
            "retrospective_id": None,
            "status": "passed_critic",
            "critic": {
                "track": "B",
                "verdict": "pass",
                "critic": "legacy-reviewer",
                "reason": "A pre-migration judgment.",
            },
            "verification": None,
            "revisions": [],
            "proposed_at": "2026-01-01T00:00:00+00:00",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        command = (
            "python3 -c \"from pathlib import Path; "
            f"Path({str(marker)!r}).write_text('ran')\""
        )
        self.assert_blocked(self.run_cli(
            "verify", "--proposal", "P-001", "--command", command,
        ), "no acceptance-time evaluator snapshot")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
