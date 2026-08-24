"""End-to-end tests for the self-evolved research harness engine.

The engine is the deterministic substrate of the auto-research loop. The LLM
proposer/critic is intentionally outside the subprocess tests; here we drive
the proposal pipeline directly and assert the mechanical guarantees: immutable
L0, dependency closure invalidation, trust decay, hard verification and
rollback.
"""
import json
import hashlib
import fcntl
import os
import shlex
import subprocess
import struct
import sys
import tempfile
import textwrap
import time
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
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_candidate_repository(self):\n"
            "        self.assertTrue(True)\n"
        )
        (self.repo / "tests" / "protected").mkdir()
        (self.repo / "tests" / "protected" / "test_guard.py").write_text(
            "import unittest\n\n"
            "class ProtectedSmoke(unittest.TestCase):\n"
            "    def test_protected_evaluator(self):\n"
            "        self.assertTrue(True)\n"
        )
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

    def inquiry_payload(self, disposition="propose", required_targets=None):
        payload = {
            "scope": "The harness question-generation and proposal boundary",
            "diagnosis": (
                "The current loop rewards producing an applicable patch before "
                "establishing whether its research question is useful."
            ),
            "evidence": [{
                "kind": "source",
                "locator": "tools/auto_research.py:cmd_run",
                "observation": (
                    "The runner historically required a proposal-shaped response "
                    "at the start of every epoch."
                ),
            }],
            "question_before": "Which patch should the harness apply next?",
            "question_after": (
                "Under a protected evaluator, does an inquiry gate reduce "
                "false acceptance without suppressing valid changes?"
            ),
            "why_better": (
                "It makes problem selection falsifiable and separates research "
                "judgment from implementation throughput."
            ),
            "hypothesis": {
                "claim": "The inquiry gate lowers false acceptance at equal budget.",
                "falsifier": (
                    "False acceptance does not decrease or valid-change yield "
                    "falls beyond the declared tolerance."
                ),
            },
            "evaluation_boundary": {
                "mutable": ["question-generation policy", "proposal scheduler"],
                "protected": ["held-out cases", "scoring implementation"],
                "baseline": "The direct-propose runner before the inquiry gate",
                "measure": "False-accept rate and valid-change yield at equal budget",
                "evaluator": (
                    "Pinned mutable and protected unit tests plus a result artifact"
                ),
                "success_rule": (
                    "The candidate lowers false acceptance while preserving valid yield."
                ),
                "guardrails": [
                    "Valid-change yield declines by no more than five percentage points."
                ],
                "budget": (
                    "Equal token, wall-time, and one-proposal budget per condition."
                ),
            },
            "recommendations": [{
                "id": "REC-1",
                "title": "Add an inquiry gate",
                "target": "tools/auto_research.py",
                "change": "Require a reviewed research question before proposal creation.",
                "rationale": "This makes stopping and reframing first-class outcomes.",
                "validation": "Compare sentinel decisions with and without the gate.",
                "implementation_contract": {
                    "allowed_ops": [
                        "add_node", "remove_node", "set_node_status",
                        "set_node_statement", "add_edge", "remove_edge",
                        "set_layer_policy", "set_trust_decay",
                        "set_affected_trust_decay", "set_revalidation_threshold",
                        "set_self_test_command", "patch_file", "set_launch_prompt",
                    ],
                    "allowed_paths": ["tools/", "tests/"],
                    "required_targets": list(required_targets or ["N1"]),
                    "required_verification_terms": ["python3"],
                },
            }],
            "disposition": disposition,
            "no_change_reason": None,
            "revisit_when": None,
        }
        if disposition == "no_change":
            payload["recommendations"] = []
            payload["no_change_reason"] = (
                "The observation does not justify changing the protected evaluator."
            )
            payload["revisit_when"] = (
                "Revisit after ten new decisions expose a measurable false acceptance."
            )
        state_path = self.repo / ".research" / "auto_research.json"
        if state_path.exists():
            measured = []
            for inquiry_id, inquiry in self.state().get("inquiries", {}).items():
                outcome = inquiry.get("outcome")
                if isinstance(outcome, dict):
                    measured.append((int(inquiry_id.split("-")[-1]), inquiry_id, outcome))
            if measured:
                _, prior_id, prior_outcome = max(measured)
                payload["question_before"] = prior_outcome["next_question"]
                payload["evidence"].append({
                    "kind": "prior measured outcome",
                    "locator": (
                        ".research/auto_snapshots/outcome_blobs/"
                        + prior_outcome["artifact_sha256"]
                    ),
                    "observation": (
                        f"{prior_id} measured {prior_outcome['verdict']} and "
                        "pre-registered this next question."
                    ),
                })
        return payload

    def inquiry_file(self, payload=None, name="inquiry.json"):
        path = self.repo / name
        path.write_text(json.dumps(payload or self.inquiry_payload()))
        return str(path)

    def record_self_inquiry(self, disposition="propose", required_targets=None):
        return self.assertOk(self.run_auto(
            "inquire", "--track", "A",
            "--file", self.inquiry_file(
                self.inquiry_payload(disposition, required_targets)
            ),
            "--source", "test",
        ))

    def accept_inquiry(self, inquiry="INQ-001", recommendation="REC-1"):
        args = [
            "review-inquiry", "--track", "B", "--inquiry", inquiry,
            "--verdict", "pass", "--reviewer", "test-reviewer",
            "--reason", "Evidence, falsifier, and evaluator boundary are explicit.",
        ]
        if recommendation:
            args.extend(["--recommendation", recommendation])
        return self.assertOk(self.run_auto(*args))

    def proposal_link_args(self, required_targets=None):
        st = self.state()
        inquiry_id = st.get("active_inquiry_id")
        if inquiry_id:
            inquiry = st["inquiries"][inquiry_id]
            self.assertEqual(inquiry["status"], "accepted")
            return [
                "--inquiry", inquiry_id,
                "--recommendation", inquiry["selected_recommendation_id"],
            ]
        self.record_self_inquiry(required_targets=required_targets)
        inquiry_id = sorted(self.state()["inquiries"])[-1]
        self.accept_inquiry(inquiry=inquiry_id)
        return ["--inquiry", inquiry_id, "--recommendation", "REC-1"]

    def inject_legacy_proposal(self, node="N1", statement="legacy-after",
                               as_schema_one=True):
        st = self.state()
        st["counters"]["proposal"] = max(st["counters"].get("proposal", 0), 1)
        st["proposals"]["P-001"] = {
            "id": "P-001",
            "track": "A",
            "title": "schema-one legacy proposal",
            "statement": "created before inquiry links existed",
            "targets": [node],
            "patch": [{
                "op": "set_node_statement",
                "node": node,
                "statement": statement,
            }],
            "verification_command": PASS_CMD,
            "retrospective_id": None,
            "status": "proposed",
            "critic": None,
            "verification": None,
            "revisions": [],
            "proposed_at": "2026-01-01T00:00:00+00:00",
        }
        if as_schema_one:
            st["schema_version"] = 1
            st.pop("legacy_proposal_ids", None)
        (self.repo / ".research" / "auto_research.json").write_text(
            json.dumps(st, indent=2) + "\n"
        )

    def propose_ready_modification(self, node="N1", new_statement="s-v2",
                                  verification=PASS_CMD):
        patch = self.patch_file(
            [{"op": "set_node_statement", "node": node, "statement": new_statement}]
        )
        args = [
            "propose", *self.proposal_link_args([node]),
            "--title", f"revise {node}",
            "--statement", "candidate",
            "--targets", node, "--patch-file", patch,
        ]
        if verification:
            args.extend(["--verification", verification])
        return self.assertOk(self.run_auto(*args))

    def drive_pipeline(self, proposal="P-001", verification=None):
        self.assertOk(self.run_auto(
            "critique", "--proposal", proposal, "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The bounded proposal matches the accepted recommendation.",
        ))
        verify_args = ["verify", "--proposal", proposal]
        if verification is not None:
            verify_args.extend(["--command", verification])
        self.assertOk(self.run_auto(*verify_args))
        inquiry = self.state()["proposals"][proposal]["inquiry_id"]
        self.assess_inquiry(inquiry=inquiry)
        return self.assertOk(self.run_auto("apply", "--proposal", proposal))

    def outcome_payload(self, inquiry="INQ-001", verdict="supported",
                        artifact="outcome-artifact.json"):
        st = self.state()
        guardrails = st["inquiries"][inquiry]["outcome_plan"]["guardrails"]
        artifact_path = self.repo / artifact
        artifact_path.write_text(json.dumps({
            "baseline_false_acceptance": 0.30,
            "candidate_false_acceptance": 0.10,
            "baseline_valid_yield": 0.75,
            "candidate_valid_yield": 0.73,
            "budget": "equal",
        }, sort_keys=True))
        return {
            "verdict": verdict,
            "evidence": [{
                "kind": "held-out evaluation",
                "locator": artifact,
                "observation": (
                    "The preregistered evaluator recorded both baseline and candidate."
                ),
            }],
            "artifact_locator": artifact,
            "baseline_result": (
                "False acceptance 0.30 and valid-change yield 0.75 at equal budget."
            ),
            "candidate_result": (
                "False acceptance 0.10 and valid-change yield 0.73 at equal budget."
            ),
            "budget_used": (
                "Both conditions used one proposal and the same time and token limits."
            ),
            "guardrail_results": [{
                "guardrail": guardrail,
                "result": "Valid-change yield declined by two percentage points.",
                "passed": verdict == "supported",
            } for guardrail in guardrails],
            "reason": (
                "The primary result satisfies the frozen rule and all guardrails."
                if verdict == "supported" else
                "The primary result does not satisfy the frozen decision rule."
            ),
            "next_question": (
                "Does the observed reduction replicate on new held-out repositories?"
            ),
        }

    def assess_inquiry(self, inquiry="INQ-001", verdict="supported",
                       payload=None, name="outcome.json"):
        outcome = payload or self.outcome_payload(inquiry, verdict)
        path = self.repo / name
        path.write_text(json.dumps(outcome))
        return self.assertOk(self.run_auto(
            "assess-inquiry", "--track", "B", "--inquiry", inquiry,
            "--file", str(path), "--assessor", "independent-test-assessor",
        ))

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


class TestSelfInquiry(RepoCase):
    def test_inquiry_records_judgment_without_creating_change_evidence(self):
        before = self.state()
        out = self.record_self_inquiry().stdout
        self.assertIn("no proposal or modification was created", out)
        st = self.state()
        inquiry = st["inquiries"]["INQ-001"]
        self.assertEqual(inquiry["status"], "proposed")
        self.assertEqual(inquiry["disposition"], "propose")
        self.assertEqual(inquiry["proposal_ids"], [])
        self.assertEqual(st["nodes"], before["nodes"])
        self.assertEqual(st["proposals"], before["proposals"])
        self.assertEqual(st["verifications"], before["verifications"])
        self.assertIsNone(st["active_inquiry_id"])
        observation = json.loads(
            self.assertOk(self.run_auto("self-observe", "--json")).stdout
        )
        self.assertEqual(observation["inquiries"]["by_status"]["proposed"], 1)

    def test_malformed_inquiry_is_blocked_without_consuming_id(self):
        payload = self.inquiry_payload()
        payload["evidence"] = []
        proc = self.run_auto(
            "inquire", "--file", self.inquiry_file(payload, "bad-inquiry.json")
        )
        self.assertBlocked(proc, "evidence")
        st = self.state()
        self.assertEqual(st["counters"]["inquiry"], 0)
        self.assertEqual(st["inquiries"], {})

    def test_vacuous_and_smuggled_inquiry_content_is_blocked(self):
        vacuous = self.inquiry_payload()
        vacuous["scope"] = "TBD. fill later"
        proc = self.run_auto(
            "inquire", "--file", self.inquiry_file(vacuous, "vacuous.json")
        )
        self.assertBlocked(proc, "scope")
        smuggled = self.inquiry_payload()
        smuggled["recommendations"][0]["patch"] = [{"op": "remove_node"}]
        proc = self.run_auto(
            "inquire", "--file", self.inquiry_file(smuggled, "smuggled.json")
        )
        self.assertBlocked(proc, "unknown fields: patch")
        self.assertEqual(self.state()["inquiries"], {})

    def test_self_observation_exposes_candidate_evaluator_coupling(self):
        self.add_node("N1", "coupling target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        engine = tools / "auto_research.py"
        engine.write_text("ENGINE = 'before'\n")
        test_file = self.repo / "tests" / "test_auto_research.py"
        test_file.write_text("TEST = 'before'\n")
        self.record_self_inquiry(required_targets=["N1"])
        self.accept_inquiry()
        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        engine_candidate = candidate_dir / "engine.candidate"
        engine_candidate.write_text("ENGINE = 'after'\n")
        test_candidate = candidate_dir / "test.candidate"
        test_candidate.write_text("TEST = 'after'\n")
        patch_name = "coupled-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/auto_research.py",
            "--candidate", str(engine_candidate), "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tests/test_auto_research.py",
            "--candidate", str(test_candidate), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "coupled engine and evaluator", "--statement",
            "change engine and mutable tests together", "--targets", "N1",
            "--patch-file", str(self.repo / patch_name),
            "--verification", "python3 -m unittest discover -s tests",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-B", "--reason",
            "Record the coupling as a measured historical fact.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        self.assertOk(self.run_auto("apply", "--proposal", "P-001"))
        observation = json.loads(
            self.assertOk(self.run_auto("self-observe", "--json")).stdout
        )
        coupling = observation["evaluation_coupling"]
        self.assertEqual(coupling["engine_and_tests_same_patch"], 1)
        self.assertIn("not independent evidence", coupling["warning"])
        self.assertEqual(observation["verifications"]["hard_with_unittest"], 1)

    def test_only_b_can_review_and_b_selects_exact_recommendation(self):
        self.add_node("N1", "inquiry target")
        self.record_self_inquiry()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "after gate",
        }])
        premature = self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "premature", "--statement", "not reviewed",
            "--targets", "N1", "--patch-file", patch,
        )
        self.assertBlocked(premature, "active accepted inquiry")
        self.assertBlocked(self.run_auto(
            "review-inquiry", "--track", "A", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "self",
            "--reason", "self review must not count", "--recommendation", "REC-1",
        ), "B-track")
        self.assertBlocked(self.run_auto(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "pass", "--reviewer", "test-reviewer",
            "--reason", "boundary is explicit", "--recommendation", "MISSING",
        ), "requires --recommendation")
        self.accept_inquiry()
        self.assertBlocked(self.run_auto(
            "propose", "--track", "B", "--inquiry", "INQ-001",
            "--recommendation", "REC-1", "--title", "B self-proposal",
            "--statement", "wrong author role", "--targets", "N1",
            "--patch-file", patch,
        ), "authored on track A")
        wrong = self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "MISSING",
            "--title", "wrong recommendation", "--statement", "must be blocked",
            "--targets", "N1", "--patch-file", patch,
        )
        self.assertBlocked(wrong, "selected by B")
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "implement inquiry gate", "--statement", "bounded change",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        st = self.state()
        self.assertEqual(st["active_inquiry_id"], "INQ-001")
        self.assertEqual(st["inquiries"]["INQ-001"]["proposal_ids"], ["P-001"])
        self.assertEqual(st["proposals"]["P-001"]["inquiry_id"], "INQ-001")
        self.assertEqual(st["proposals"]["P-001"]["recommendation_id"], "REC-1")
        self.assertEqual(
            st["proposals"]["P-001"]["recommendation_snapshot"]["id"],
            "REC-1",
        )

    def test_unlinked_proposal_is_blocked_and_no_bypass_flag_exists(self):
        self.add_node("N1")
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "new value",
        }])
        proc = self.run_auto(
            "propose", "--title", "implicit bypass", "--statement", "candidate",
            "--targets", "N1", "--patch-file", patch,
        )
        self.assertBlocked(proc, "requires an accepted --inquiry")
        help_text = self.assertOk(self.run_auto("propose", "--help")).stdout
        self.assertNotIn("direct-propose", help_text)
        self.assertEqual(self.state()["proposals"], {})

    def test_no_change_closes_without_proposal_verification_or_graph_mutation(self):
        before = self.state()
        self.record_self_inquiry("no_change")
        self.accept_inquiry(recommendation=None)
        st = self.state()
        inquiry = st["inquiries"]["INQ-001"]
        self.assertEqual(inquiry["status"], "closed_no_change")
        self.assertEqual(inquiry["review"]["verdict"], "pass")
        self.assertIsNone(st["active_inquiry_id"])
        self.assertEqual(st["nodes"], before["nodes"])
        self.assertEqual(st["proposals"], {})
        self.assertEqual(st["verifications"], [])
        self.assertEqual(st["events"][-1]["event"], "inquiry_closed_no_change")

    def test_no_change_can_close_after_terminal_linked_verification_failure(self):
        self.add_node("N1", "before failure")
        self.propose_ready_modification(verification=FAIL_CMD)
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The failure path has a bounded proposal and evaluator.",
        ))
        self.assertNotEqual(
            self.run_auto("verify", "--proposal", "P-001").returncode, 0
        )
        self.record_self_inquiry("no_change")
        self.accept_inquiry(inquiry="INQ-002", recommendation=None)
        state = self.state()
        self.assertEqual(
            state["proposals"]["P-001"]["status"], "failed_verification"
        )
        self.assertEqual(
            state["inquiries"]["INQ-002"]["status"], "closed_no_change"
        )
        self.assertEqual(state["nodes"]["N1"]["statement"], "before failure")
        self.assertFalse(any(
            node.get("type") == "modification"
            for node_id, node in state["nodes"].items()
            if node_id != "M0"
        ))

    def test_no_change_cannot_coexist_with_open_legacy_proposal(self):
        self.add_node("N1")
        self.inject_legacy_proposal()
        proc = self.run_auto(
            "inquire", "--file", self.inquiry_file(
                self.inquiry_payload("no_change"), "blocked-no-change.json"
            ),
        )
        self.assertBlocked(proc, "resolve open proposals")
        self.assertEqual(self.state()["inquiries"], {})

    def test_schema_one_history_loads_but_open_proposal_fails_closed(self):
        self.add_node("N1", "legacy-before")
        self.inject_legacy_proposal()
        self.assertOk(self.run_auto("validate"))
        proc = self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "attempt legacy continuation",
        )
        self.assertBlocked(proc, "no acceptance-time evaluator snapshot")
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"], "proposed")
        self.assertIsNone(st["proposals"]["P-001"].get("inquiry_id"))
        self.assertEqual(st["nodes"]["N1"]["statement"], "legacy-before")

    def test_schema_one_passed_critic_cannot_execute_verification(self):
        self.add_node("N1", "legacy-before")
        self.inject_legacy_proposal()
        marker = self.repo / "legacy-verification-ran"
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        proposal = state["proposals"]["P-001"]
        proposal["status"] = "passed_critic"
        proposal["critic"] = {
            "track": "B",
            "verdict": "pass",
            "critic": "schema-one-reviewer",
            "reason": "A pre-migration soft judgment.",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.run_auto(
            "verify", "--proposal", "P-001", "--command",
            f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\"",
        )
        self.assertBlocked(proc, "no acceptance-time evaluator snapshot")
        self.assertFalse(marker.exists())
        self.assertEqual(
            self.state()["proposals"]["P-001"]["status"], "passed_critic"
        )

    def test_migration_frozen_open_proposal_has_fail_closed_retirement_route(self):
        self.add_node("N1", "legacy-before")
        self.inject_legacy_proposal()
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        proposal = state["proposals"]["P-001"]
        proposal["status"] = "passed_critic"
        proposal["critic"] = {
            "track": "B",
            "verdict": "pass",
            "critic": "schema-one-reviewer",
            "reason": "A pre-migration soft judgment.",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        for command in ("next", "ab-next"):
            routed = self.assertOk(self.run_auto(command)).stdout
            self.assertIn(
                "critique --proposal P-001 --track B --verdict reject",
                routed,
            )
            self.assertNotIn("verify --proposal P-001", routed)
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--track", "B",
            "--verdict", "reject", "--critic", "migration-reviewer",
            "--reason", (
                "This migration-frozen proposal has no acceptance-time "
                "evaluator binding and must remain historical."
            ),
        ))
        self.assertEqual(
            self.state()["proposals"]["P-001"]["status"], "rejected"
        )
        self.assertBlocked(
            self.run_auto(
                "revise", "--proposal", "P-001",
                "--note", "must not revive migration history",
            ),
            "historical-only",
        )

    def test_migration_frozen_failed_verification_routes_to_new_inquiry(self):
        self.add_node("N1", "legacy-before")
        self.inject_legacy_proposal()
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        proposal = state["proposals"]["P-001"]
        proposal["status"] = "failed_verification"
        proposal["verification"] = {
            "track": "B",
            "command": FAIL_CMD,
            "exit_code": 1,
            "timeout": False,
            "stdout_sha256": "legacy",
            "at": "2026-01-01T00:00:00+00:00",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        for command in ("next", "ab-next"):
            routed = self.assertOk(self.run_auto(command)).stdout
            self.assertIn(
                "use migration-frozen failed_verification P-001 as evidence",
                routed,
            )
            self.assertNotIn("revise --proposal P-001", routed)

    def test_schema_one_verified_proposal_cannot_apply(self):
        self.add_node("N1", "legacy-before")
        self.inject_legacy_proposal()
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        proposal = state["proposals"]["P-001"]
        proposal["status"] = "verified"
        proposal["critic"] = {
            "track": "B",
            "verdict": "pass",
            "critic": "schema-one-reviewer",
            "reason": "A pre-migration soft judgment.",
        }
        proposal["verification"] = {
            "track": "B",
            "command": PASS_CMD,
            "exit_code": 0,
            "timeout": False,
            "stdout_sha256": "legacy",
            "at": "2026-01-01T00:00:00+00:00",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.run_auto("apply", "--proposal", "P-001")
        self.assertBlocked(proc, "no acceptance-time evaluator snapshot")
        self.assertEqual(self.state()["nodes"]["N1"]["statement"], "legacy-before")

    def test_current_schema_rejects_forged_unlinked_legacy_proposal(self):
        self.add_node("N1", "before")
        self.inject_legacy_proposal(as_schema_one=False)
        self.assertBlocked(
            self.run_auto("validate"),
            "unlinked proposal P-001 is not migration-grandfathered",
        )
        before = self.state()
        proc = self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "forged", "--reason", "must not enter the pipeline",
        )
        self.assertBlocked(proc, "not migration-grandfathered")
        self.assertEqual(self.state(), before)

    def test_inquiry_converts_only_after_linked_proposal_is_applied(self):
        self.add_node("N1", "before")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "after gate",
        }])
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "apply selected recommendation", "--statement", "one change",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        self.assertEqual(
            self.state()["inquiries"]["INQ-001"]["status"], "implementing"
        )
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The bounded proposal implements the selected recommendation.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        awaiting = self.state()
        self.assertEqual(
            awaiting["inquiries"]["INQ-001"]["status"], "awaiting_outcome"
        )
        self.assertEqual(awaiting["nodes"]["N1"]["statement"], "before")
        self.assertBlocked(
            self.run_auto("apply", "--proposal", "P-001"),
            "no supported outcome",
        )
        self.assess_inquiry()
        supported = self.state()["inquiries"]["INQ-001"]
        self.assertEqual(supported["status"], "supported")
        digest = supported["outcome"]["artifact_sha256"]
        self.assertTrue((
            self.repo / ".research" / "auto_snapshots" /
            "outcome_blobs" / digest
        ).is_file())
        self.assertOk(self.run_auto("apply", "--proposal", "P-001"))
        st = self.state()
        self.assertEqual(st["inquiries"]["INQ-001"]["status"], "converted")
        self.assertEqual(st["inquiries"]["INQ-001"]["converted_by"], "P-001")
        self.assertIsNone(st["active_inquiry_id"])

    def test_falsified_outcome_terminates_patch_and_drives_next_question(self):
        self.add_node("N1", "before falsification")
        self.propose_ready_modification("N1", "must not be applied")
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The exact proposal is suitable for the frozen outcome test.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        outcome = self.outcome_payload(verdict="falsified")
        next_question = outcome["next_question"]
        self.assess_inquiry(verdict="falsified", payload=outcome)
        st = self.state()
        inquiry = st["inquiries"]["INQ-001"]
        self.assertEqual(inquiry["status"], "falsified")
        self.assertEqual(st["proposals"]["P-001"]["status"], "terminated")
        self.assertEqual(st["nodes"]["N1"]["statement"], "before falsification")
        self.assertIsNone(st["active_inquiry_id"])
        self.assertFalse(any(
            node.get("type") == "modification" for node in st["nodes"].values()
        ))

        observation = json.loads(
            self.assertOk(self.run_auto("self-observe", "--json")).stdout
        )
        self.assertEqual(
            observation["inquiries"]["outcomes_by_verdict"]["falsified"], 1
        )
        recent = observation["inquiries"]["recent_outcomes"][-1]
        self.assertEqual(recent["next_question"], next_question)
        self.assertEqual(
            recent["artifact_sha256"], inquiry["outcome"]["artifact_sha256"]
        )

        disconnected = self.inquiry_payload()
        disconnected["question_before"] = "Which unrelated patch should run next?"
        disconnected["evidence"] = disconnected["evidence"][:1]
        self.assertBlocked(self.run_auto(
            "inquire", "--file", self.inquiry_file(
                disconnected, "disconnected-inquiry.json"
            ),
        ), "latest measured outcome")
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        next_payload = {"action": "inquire", **self.inquiry_payload()}
        agent = self.repo / ".research" / "tmp" / "outcome-aware-agent.py"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "root=Path(os.environ['RESEARCH_CLOSURE_ROOT'])\n"
            "state=json.loads((root/'.research/auto_research.json').read_text())\n"
            "outcome=state['inquiries']['INQ-001']['outcome']\n"
            "blob=root/'.research/auto_snapshots/outcome_blobs'/outcome['artifact_sha256']\n"
            "measured=json.loads(blob.read_text())\n"
            "assert measured['candidate_false_acceptance'] == 0.10\n"
            f"print(json.dumps({next_payload!r}))\n"
        )
        run = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {agent}",
            "--max-propose-attempts", "1", "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("stopped before proposal", run.stdout)
        next_inquiry = self.state()["inquiries"]["INQ-002"]
        self.assertEqual(next_inquiry["question_before"], next_question)
        self.assertTrue(any(
            item["locator"].endswith(inquiry["outcome"]["artifact_sha256"])
            for item in next_inquiry["evidence"]
        ))

    def test_inconclusive_outcome_terminates_without_live_apply(self):
        self.add_node("N1", "before inconclusive result")
        self.propose_ready_modification("N1", "must remain unapplied")
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The exact proposal is suitable for the frozen outcome test.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry(verdict="inconclusive")
        st = self.state()
        self.assertEqual(st["inquiries"]["INQ-001"]["status"], "inconclusive")
        self.assertEqual(st["proposals"]["P-001"]["status"], "terminated")
        self.assertEqual(st["nodes"]["N1"]["statement"], "before inconclusive result")

    def test_corrupt_pinned_outcome_blob_blocks_apply_before_live_change(self):
        self.add_node("N1", "before corrupt evidence")
        self.propose_ready_modification("N1", "must remain unapplied")
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The exact proposal is suitable for the frozen outcome test.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        st = self.state()
        digest = st["inquiries"]["INQ-001"]["outcome"]["artifact_sha256"]
        blob = (
            self.repo / ".research" / "auto_snapshots" /
            "outcome_blobs" / digest
        )
        blob.chmod(0o644)
        blob.write_text("corrupt")
        before_state = (
            self.repo / ".research" / "auto_research.json"
        ).read_bytes()
        self.assertBlocked(
            self.run_auto("apply", "--proposal", "P-001"),
            "outcome artifact blob is corrupt",
        )
        self.assertEqual(
            (self.repo / ".research" / "auto_research.json").read_bytes(),
            before_state,
        )
        self.assertEqual(self.state()["nodes"]["N1"]["statement"],
                         "before corrupt evidence")

    def test_outcome_artifact_may_not_escape_through_symlink_parent(self):
        self.add_node("N1", "outcome symlink boundary")
        self.propose_ready_modification("N1", "candidate statement")
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The exact proposal is suitable for the frozen outcome test.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        payload = self.outcome_payload()
        outside_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_tmp.cleanup)
        outside = Path(outside_tmp.name)
        (outside / "result.json").write_text('{"outside": true}')
        (self.repo / "outcome-link").symlink_to(outside, target_is_directory=True)
        payload["artifact_locator"] = "outcome-link/result.json"
        payload["evidence"][0]["locator"] = "outcome-link/result.json"
        path = self.repo / "symlink-outcome.json"
        path.write_text(json.dumps(payload))
        self.assertBlocked(self.run_auto(
            "assess-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--file", str(path), "--assessor", "independent-test-assessor",
        ), "regular file without symlinks")
        self.assertEqual(
            self.state()["inquiries"]["INQ-001"]["status"],
            "awaiting_outcome",
        )

    def test_rejected_proposal_consumes_single_inquiry_authorization(self):
        self.add_node("N1", "before")
        self.record_self_inquiry()
        self.accept_inquiry()
        first_patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "first attempt",
        }])
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "first implementation", "--statement", "bounded attempt",
            "--targets", "N1", "--patch-file", first_patch,
            "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "reject", "--critic", "independent-B",
            "--reason", "The implementation does not realize the selected recommendation.",
        ))
        failed = self.state()
        self.assertEqual(
            failed["inquiries"]["INQ-001"]["status"], "implementation_failed"
        )
        self.assertIsNone(failed["active_inquiry_id"])
        second_patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "silent retry",
        }])
        proc = self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "unreviewed retry", "--statement", "must be blocked",
            "--targets", "N1", "--patch-file", second_patch,
            "--verification", PASS_CMD,
        )
        self.assertBlocked(proc, "not the active accepted inquiry")
        self.assertEqual(list(self.state()["proposals"]), ["P-001"])
        routed = self.assertOk(self.run_auto("ab-next")).stdout
        self.assertIn(
            "use rejected linked proposal P-001 as evidence; its one attempt is consumed",
            routed,
        )
        generic = self.assertOk(self.run_auto("next")).stdout
        self.assertIn(
            "use rejected linked proposal P-001 as evidence; its one attempt is consumed",
            generic,
        )

    def test_selected_recommendation_contract_blocks_unrelated_patch(self):
        self.add_node("N1", "before")
        payload = self.inquiry_payload()
        payload["recommendations"][0]["implementation_contract"] = {
            "allowed_ops": ["patch_file"],
            "allowed_paths": ["tools/auto_research.py"],
            "required_targets": ["N1"],
            "required_verification_terms": ["unittest"],
        }
        self.assertOk(self.run_auto(
            "inquire", "--track", "A", "--file", self.inquiry_file(payload),
        ))
        self.accept_inquiry()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1",
            "statement": "totally-unrelated-marker-9271",
        }])
        proc = self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "unrelated state patch", "--statement", "must not pass by id",
            "--targets", "N1", "--patch-file", patch,
            "--verification", (
                "python3 -m unittest && python3 -c \"print('totally-unrelated-marker-9271')\""
            ),
        )
        self.assertBlocked(proc, "outside selected recommendation contract")
        self.assertEqual(self.state()["proposals"], {})

    def test_linked_proposal_enforces_b_critique_verify_and_apply(self):
        self.add_node("N1", "role boundary")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "role checked",
        }])
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "role-checked proposal", "--statement", "one change",
            "--targets", "N1", "--patch-file", patch, "--verification", PASS_CMD,
        ))
        self.assertBlocked(self.run_auto(
            "critique", "--track", "A", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "self",
            "--reason", "A may not certify its own linked proposal.",
        ), "B-track responsibility")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The bounded proposal matches the selected recommendation.",
        ))
        self.assertBlocked(self.run_auto(
            "verify", "--track", "A", "--proposal", "P-001",
        ), "B-track responsibility")
        self.assertOk(self.run_auto("verify", "--track", "B", "--proposal", "P-001"))
        self.assertBlocked(self.run_auto(
            "assess-inquiry", "--track", "A", "--inquiry", "INQ-001",
            "--file", str(self.repo / "missing-outcome.json"),
            "--assessor", "self-assessor",
        ), "B-track responsibility")
        self.assess_inquiry()
        self.assertBlocked(self.run_auto(
            "apply", "--track", "A", "--proposal", "P-001",
        ), "B-track responsibility")
        self.assertOk(self.run_auto("apply", "--track", "B", "--proposal", "P-001"))

    def test_linked_verification_plan_cannot_change_after_critique(self):
        self.add_node("N1", "frozen verification target")
        self.propose_ready_modification(verification=PASS_CMD)
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The verification plan is part of the reviewed proposal.",
        ))
        before = self.state()
        proc = self.run_auto(
            "verify", "--track", "B", "--proposal", "P-001",
            "--command", "python3 -c \"print('replacement')\"",
        )
        self.assertBlocked(proc, "verification is frozen at critique time")
        self.assertEqual(self.state(), before)
        self.assertOk(self.run_auto(
            "verify", "--track", "B", "--proposal", "P-001",
        ))

    def test_hard_verification_cannot_mutate_live_repo_by_absolute_path(self):
        self.add_node("N1", "verification guard target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        guard = tools / "verification_guard.py"
        guard.write_text("before\n")
        command = (
            "python3 -c \"from pathlib import Path; "
            f"Path({str(guard)!r}).write_text('tampered\\n'); print('exit-zero')\""
        )
        self.propose_ready_modification(verification=command)
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "adversarial-B",
            "--reason", "Pass deliberately to exercise the mechanical live guard.",
        ))
        proc = self.run_auto(
            "verify", "--track", "B", "--proposal", "P-001",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PermissionError", proc.stdout + proc.stderr)
        self.assertEqual(guard.read_text(), "before\n")
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"], "failed_verification")
        self.assertEqual(
            st["inquiries"]["INQ-001"]["status"], "implementation_failed"
        )

    def test_apply_stale_inquiry_is_side_effect_free(self):
        self.add_node("N1", "file target")
        tools_dir = self.repo / "tools"
        tools_dir.mkdir(exist_ok=True)
        target = tools_dir / "stale.py"
        target.write_text("before\n")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch = self.make_file_patch("tools/stale.py", "after\n", "stale-patch.json")
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "stale inquiry patch", "--statement", "must preflight",
            "--targets", "N1", "--patch-file", patch,
            "--verification",
            "python3 -c \"assert open('tools/stale.py').read() == 'after\\n'\"",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "bounded file patch",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        st = self.state()
        inquiry = st["inquiries"]["INQ-001"]
        inquiry["status"] = "rejected"
        inquiry["review"]["verdict"] = "reject"
        inquiry["review"]["selected_recommendation_id"] = None
        inquiry["selected_recommendation_id"] = None
        inquiry["resolved_at"] = "2026-01-01T00:00:00+00:00"
        st["active_inquiry_id"] = None
        state_path = self.repo / ".research" / "auto_research.json"
        state_path.write_text(json.dumps(st, indent=2) + "\n")
        before_state = state_path.read_bytes()
        backup_root = self.repo / ".research" / "auto_snapshots" / "file_backups"
        before_backups = sorted(
            str(path.relative_to(self.repo)) for path in backup_root.rglob("*")
        ) if backup_root.exists() else []

        proc = self.run_auto("apply", "--proposal", "P-001")
        self.assertBlocked(proc, "no supported outcome at apply time")
        self.assertEqual(target.read_text(), "before\n")
        self.assertEqual(state_path.read_bytes(), before_state)
        after_backups = sorted(
            str(path.relative_to(self.repo)) for path in backup_root.rglob("*")
        ) if backup_root.exists() else []
        self.assertEqual(after_backups, before_backups)

    def test_pre_apply_self_test_failure_leaves_live_patch_unapplied(self):
        self.add_node("N1", "transaction target")
        tools_dir = self.repo / "tools"
        tools_dir.mkdir(exist_ok=True)
        target = tools_dir / "post_apply_guard.py"
        target.write_text("before\n")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch_path = Path(self.make_file_patch(
            "tools/post_apply_guard.py", "after\n", "transaction-patch.json",
        ))
        patch = json.loads(patch_path.read_text())
        patch.append({"op": "set_self_test_command", "command": FAIL_CMD})
        patch_path.write_text(json.dumps(patch))
        original_command = self.state()["self_test_command"]
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "transactional candidate", "--statement", "fail before commit",
            "--targets", "N1", "--patch-file", str(patch_path),
            "--verification", (
                "python3 -c \"assert open('tools/post_apply_guard.py').read() == 'after\\n'\""
            ),
        ))
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The exact patch and frozen verification are bounded.",
        ))
        self.assertOk(self.run_auto(
            "verify", "--track", "B", "--proposal", "P-001",
        ))
        self.assess_inquiry()
        proc = self.run_auto(
            "apply", "--track", "B", "--proposal", "P-001", "--timeout", "60",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("live patch not applied", proc.stdout + proc.stderr)
        self.assertEqual(target.read_text(), "before\n")
        st = self.state()
        self.assertEqual(st["self_test_command"], original_command)
        self.assertEqual(st["proposals"]["P-001"]["status"], "failed_verification")
        self.assertEqual(
            st["inquiries"]["INQ-001"]["status"], "implementation_failed"
        )
        self.assertFalse(any(
            node.get("type") == "modification" for node in st["nodes"].values()
        ))

    def test_candidate_cannot_weaken_frozen_self_test_to_gain_acceptance(self):
        self.add_node("N1", "evaluator target")
        state_path = self.repo / ".research" / "auto_research.json"
        st = self.state()
        st["self_test_command"] = FAIL_CMD
        state_path.write_text(json.dumps(st, indent=2) + "\n")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch = self.patch_file([{
            "op": "set_self_test_command", "command": PASS_CMD,
        }])
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "weaken evaluator", "--statement", "must not self-certify",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "adversarial-B",
            "--reason", "Pass deliberately to test the protected mechanical gate.",
        ))
        self.assertOk(self.run_auto(
            "verify", "--track", "B", "--proposal", "P-001",
        ))
        self.assess_inquiry()
        proc = self.run_auto(
            "apply", "--track", "B", "--proposal", "P-001", "--timeout", "60",
        )
        self.assertNotEqual(proc.returncode, 0)
        after = self.state()
        self.assertEqual(after["self_test_command"], FAIL_CMD)
        self.assertEqual(after["proposals"]["P-001"]["status"], "failed_verification")
        checks = after["verifications"][-1]["checks"]
        self.assertEqual(
            [check["label"] for check in checks],
            ["protected", "frozen_pre_patch", "candidate_post_patch"],
        )
        self.assertNotEqual(checks[1]["exit_code"], 0)

    def test_pre_apply_runs_frozen_old_test_bytes_against_candidate_code(self):
        self.add_node("N1", "frozen byte evaluator target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        behavior = tools / "behavior.txt"
        behavior.write_text("safe\n")
        old_test = self.repo / "tests" / "test_smoke.py"
        old_test.write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_behavior_stays_safe(self):\n"
            "        root = Path(__file__).resolve().parents[1]\n"
            "        self.assertEqual((root / 'tools' / 'behavior.txt').read_text(), 'safe\\n')\n"
        )
        self.assertOk(self.run_auto("self-test"))

        self.record_self_inquiry()
        self.accept_inquiry()
        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        behavior_candidate = candidate_dir / "behavior.candidate"
        behavior_candidate.write_text("unsafe\n")
        weakened_test = candidate_dir / "test_smoke.candidate"
        weakened_test.write_text(
            "import unittest\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_weakened(self):\n"
            "        self.assertTrue(True)\n"
        )
        patch_name = "frozen-byte-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/behavior.txt",
            "--candidate", str(behavior_candidate), "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tests/test_smoke.py",
            "--candidate", str(weakened_test), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "candidate weakens old tests",
            "--statement", "the frozen evaluator must catch the regression",
            "--targets", "N1", "--patch-file", str(self.repo / patch_name),
            "--verification", "python3 -m unittest discover -s tests",
        ))
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "adversarial-B",
            "--reason", "Pass deliberately to exercise frozen evaluator bytes.",
        ))
        self.assertOk(self.run_auto("verify", "--track", "B", "--proposal", "P-001"))
        self.assess_inquiry()
        proc = self.run_auto(
            "apply", "--track", "B", "--proposal", "P-001", "--timeout", "60",
        )
        self.assertBlocked(proc, "live patch not applied")
        self.assertEqual(behavior.read_text(), "safe\n")
        self.assertIn("test_behavior_stays_safe", old_test.read_text())
        checks = self.state()["verifications"][-1]["checks"]
        self.assertEqual(
            [check["label"] for check in checks],
            ["protected", "frozen_pre_patch", "candidate_post_patch"],
        )
        self.assertNotEqual(checks[1]["exit_code"], 0)
        self.assertEqual(checks[2]["exit_code"], 0)

    def test_local_unittest_module_cannot_shadow_trusted_frozen_runner(self):
        self.add_node("N1", "stdlib import boundary target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        behavior = tools / "shadow_behavior.txt"
        behavior.write_text("safe\n")
        live_test = self.repo / "tests" / "test_smoke.py"
        live_test.write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_behavior_stays_safe(self):\n"
            "        root = Path(__file__).resolve().parents[1]\n"
            "        self.assertEqual((root/'tools/shadow_behavior.txt').read_text(), 'safe\\n')\n"
        )
        self.assertOk(self.run_auto("self-test"))
        payload = self.inquiry_payload()
        payload["recommendations"][0]["implementation_contract"][
            "allowed_paths"
        ].append("unittest.py")
        self.assertOk(self.run_auto(
            "inquire", "--file", self.inquiry_file(payload, "shadow-inquiry.json"),
        ))
        self.accept_inquiry()

        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        behavior_candidate = candidate_dir / "shadow_behavior.candidate"
        behavior_candidate.write_text("unsafe\n")
        weakened_test = candidate_dir / "shadow_test.candidate"
        weakened_test.write_text(
            "import unittest\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_vacuous(self): self.assertTrue(True)\n"
        )
        fake_unittest = candidate_dir / "unittest_shadow.candidate"
        fake_unittest.write_text("raise SystemExit(0)\n")
        patch_name = "unittest-shadow-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/shadow_behavior.txt",
            "--candidate", str(behavior_candidate), "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tests/test_smoke.py",
            "--candidate", str(weakened_test), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "unittest.py",
            "--candidate", str(fake_unittest), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "shadow the unittest module",
            "--statement", "trusted runner must ignore repository import shadowing",
            "--targets", "N1", "--patch-file", str(self.repo / patch_name),
            "--verification", "python3 -m unittest discover -s tests",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "adversarial-test-critic",
            "--reason", "Pass deliberately so the trusted frozen runner faces import shadowing.",
        ))
        # The candidate-controlled command is fooled by root/unittest.py.
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        proc = self.run_auto("apply", "--proposal", "P-001", "--timeout", "60")
        self.assertBlocked(proc, "live patch not applied")
        self.assertEqual(behavior.read_text(), "safe\n")
        self.assertFalse((self.repo / "unittest.py").exists())
        checks = self.state()["verifications"][-1]["checks"]
        self.assertNotEqual(checks[1]["exit_code"], 0)

    def test_stdlib_json_skiptest_shadow_is_rejected_before_evaluation(self):
        self.add_node("N1", "stdlib SkipTest boundary")
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        payload = self.inquiry_payload()
        payload["recommendations"][0]["implementation_contract"][
            "allowed_paths"
        ].append("json.py")
        self.assertOk(self.run_auto(
            "inquire", "--file", self.inquiry_file(payload, "json-shadow-inquiry.json"),
        ))
        self.accept_inquiry()
        candidate = self.repo / ".research" / "tmp" / "json-shadow.candidate"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(
            "import unittest\n"
            "raise unittest.SkipTest('skip the frozen evaluator module')\n"
        )
        patch_name = "json-shadow-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "json.py", "--candidate", str(candidate),
            "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "shadow stdlib json", "--statement",
            "candidate must not skip imported frozen test modules",
            "--targets", "N1", "--patch-file", str(self.repo / patch_name),
            "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "adversarial-B", "--reason",
            "Exercise the trusted runner against a non-unittest stdlib shadow.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        proc = self.run_auto("apply", "--proposal", "P-001", "--timeout", "60")
        self.assertBlocked(proc, "live patch not applied")
        self.assertFalse((self.repo / "json.py").exists())
        checks = self.state()["verifications"][-1]["checks"]
        self.assertTrue(any(
            "repository shadows stdlib modules: json" in check.get(
                "stderr_tail", ""
            )
            for check in checks
        ), checks)

    def test_pre_apply_candidate_cannot_rewrite_future_evaluator_siblings(self):
        self.add_node("N1", "sibling isolation target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        behavior = tools / "sibling_behavior.txt"
        behavior.write_text("safe\n")
        attack = tools / "sibling_attack.py"
        attack.write_text("def prepare():\n    pass\n")
        frozen_test = self.repo / "tests" / "test_frozen.py"
        frozen_test.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root))\n"
            "from tools.sibling_attack import prepare\n"
            "prepare()\n"
            "assert (root / 'tools' / 'sibling_behavior.txt').read_text() == 'safe\\n'\n"
        )
        protected = self.repo / "tests" / "protected" / "test_guard.py"
        protected.write_text(
            "import unittest\n"
            "from tools.sibling_attack import prepare\n\n"
            "class Guard(unittest.TestCase):\n"
            "    def test_guard(self):\n"
            "        prepare()\n"
            "        self.assertTrue(True)\n"
        )
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        state["self_test_command"] = "python3 tests/test_frozen.py"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertOk(self.run_auto("self-test"))
        self.record_self_inquiry()
        self.accept_inquiry()

        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        behavior_candidate = candidate_dir / "sibling_behavior.candidate"
        behavior_candidate.write_text("unsafe\n")
        attack_candidate = candidate_dir / "sibling_attack.candidate"
        attack_candidate.write_text(
            "from pathlib import Path\n"
            "import tempfile\n\n"
            "def prepare():\n"
            "    tmp = Path(tempfile.gettempdir())\n"
            "    patterns = (\n"
            "        'auto-research-verify-*/work/tests/test_frozen.py',\n"
            "        'auto-research-frozen-evaluator-*/work/tests/test_frozen.py',\n"
            "    )\n"
            "    for pattern in patterns:\n"
            "        for path in tmp.glob(pattern):\n"
            "            path.write_text('pass\\n')\n"
        )
        weakened_test = candidate_dir / "test_frozen.candidate"
        weakened_test.write_text("pass\n")
        patch_name = "sibling-attack-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/sibling_behavior.txt",
            "--candidate", str(behavior_candidate), "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/sibling_attack.py",
            "--candidate", str(attack_candidate), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tests/test_frozen.py",
            "--candidate", str(weakened_test), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "rewrite future evaluator siblings",
            "--statement", "candidate must not reach a later evaluator tree",
            "--targets", "N1",
            "--patch-file", str(self.repo / patch_name),
            "--verification", "python3 tests/test_frozen.py",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "adversarial-B",
            "--reason", "Pass deliberately to exercise sequential evaluator isolation.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        proc = self.run_auto("apply", "--proposal", "P-001", "--timeout", "60")
        self.assertBlocked(proc, "live patch not applied")
        self.assertEqual(behavior.read_text(), "safe\n")
        checks = self.state()["verifications"][-1]["checks"]
        frozen = next(
            check for check in checks if check["label"] == "frozen_pre_patch"
        )
        self.assertNotEqual(frozen["exit_code"], 0, checks)
        self.assertIn("AssertionError", frozen["stderr_tail"])

    def test_compatible_code_and_test_change_passes_old_and_new_evaluators(self):
        self.add_node("N1", "compatible evaluator target")
        tools = self.repo / "tools"
        tools.mkdir(exist_ok=True)
        behavior = tools / "compatible.txt"
        behavior.write_text("safe\n")
        live_test = self.repo / "tests" / "test_smoke.py"
        live_test.write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_keeps_safe_contract(self):\n"
            "        root = Path(__file__).resolve().parents[1]\n"
            "        self.assertIn('safe', (root / 'tools' / 'compatible.txt').read_text())\n"
        )
        self.assertOk(self.run_auto("self-test"))
        self.record_self_inquiry()
        self.accept_inquiry()

        candidate_dir = self.repo / ".research" / "tmp"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        behavior_candidate = candidate_dir / "compatible.candidate"
        behavior_candidate.write_text("safe-plus\n")
        test_candidate = candidate_dir / "compatible_test.candidate"
        test_candidate.write_text(
            "import unittest\n"
            "from pathlib import Path\n\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_new_contract(self):\n"
            "        root = Path(__file__).resolve().parents[1]\n"
            "        self.assertEqual((root / 'tools' / 'compatible.txt').read_text(), 'safe-plus\\n')\n"
        )
        patch_name = "compatible-two-tree-patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/compatible.txt",
            "--candidate", str(behavior_candidate), "--out", patch_name,
        ))
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tests/test_smoke.py",
            "--candidate", str(test_candidate), "--out", patch_name, "--append",
        ))
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "compatible evaluator evolution",
            "--statement", "new behavior preserves the old contract",
            "--targets", "N1", "--patch-file", str(self.repo / patch_name),
            "--verification", "python3 -m unittest discover -s tests",
        ))
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The new test strengthens behavior while the old contract remains valid.",
        ))
        self.assertOk(self.run_auto("verify", "--track", "B", "--proposal", "P-001"))
        self.assess_inquiry()
        self.assertOk(self.run_auto(
            "apply", "--track", "B", "--proposal", "P-001", "--timeout", "60",
        ))
        self.assertEqual(behavior.read_text(), "safe-plus\n")
        checks = self.state()["verifications"][-1]["checks"]
        self.assertTrue(all(check["exit_code"] == 0 for check in checks))

    def test_b_acceptance_pins_evaluator_before_a_can_propose(self):
        self.add_node("N1", "snapshot boundary target")
        self.record_self_inquiry()
        self.accept_inquiry()
        accepted = self.state()
        inquiry = accepted["inquiries"]["INQ-001"]
        snapshot_id = inquiry["evaluator_snapshot_id"]
        snapshot = accepted["evaluator_snapshots"][snapshot_id]
        self.assertEqual(snapshot["inquiry_id"], "INQ-001")
        self.assertEqual(snapshot["roots"], ["tests"])
        self.assertTrue(snapshot["entries"])
        manifest_before = snapshot["manifest_sha256"]

        smoke = self.repo / "tests" / "test_smoke.py"
        smoke.write_text(smoke.read_text() + "# drift after B review\n")
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "candidate",
        }])
        proc = self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "must not rebase evaluator silently",
            "--statement", "B reviewed a different evaluator",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        )
        self.assertBlocked(proc, "live evaluator drifted")
        after = self.state()
        self.assertEqual(after["proposals"], {})
        self.assertEqual(
            after["evaluator_snapshots"][snapshot_id]["manifest_sha256"],
            manifest_before,
        )

    def test_corrupt_snapshot_blocks_direct_b_pass(self):
        self.add_node("N1", "snapshot corruption target")
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "candidate",
        }])
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "snapshot corruption proposal",
            "--statement", "must fail before semantic approval",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        state = self.state()
        snapshot = state["evaluator_snapshots"][
            state["proposals"]["P-001"]["evaluator_snapshot_id"]
        ]
        digest = snapshot["entries"][0]["sha256"]
        blob = self.repo / ".research" / "auto_snapshots" / "evaluator_blobs" / digest
        blob.chmod(0o644)
        blob.write_bytes(b"corrupt")
        proc = self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "adversarial-B",
            "--reason", "Attempt to bypass a missing evaluator artifact.",
        )
        self.assertBlocked(proc, "failed mechanical preflight")
        proposal = self.state()["proposals"]["P-001"]
        self.assertEqual(proposal["status"], "proposed")
        self.assertIsNone(proposal["critic"])

    def test_verify_rejects_proposal_changed_after_b_review(self):
        self.add_node("N1", "review digest target")
        self.propose_ready_modification("N1", "reviewed value")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The exact proposal and evaluator snapshot are bounded.",
        ))
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        state["proposals"]["P-001"]["verification_command"] = (
            PASS_CMD + " # changed after review"
        )
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.run_auto("verify", "--track", "B", "--proposal", "P-001")
        self.assertBlocked(proc, "changed after the semantic B review")
        self.assertEqual(self.state()["proposals"]["P-001"]["status"], "passed_critic")

    def test_verify_rejects_tampered_semantic_b_record(self):
        self.add_node("N1", "critic digest target")
        self.propose_ready_modification("N1", "reviewed value")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The exact proposal and evaluator are bounded.",
        ))
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        state["proposals"]["P-001"]["critic"]["reason"] = (
            "Rewritten after the semantic decision."
        )
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.run_auto("verify", "--track", "B", "--proposal", "P-001")
        self.assertBlocked(proc, "changed after the semantic B review")

    def test_validate_rejects_proposal_identity_rehang(self):
        self.add_node("N1", "identity binding target")
        self.propose_ready_modification("N1", "reviewed value")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "The proposal identity is part of this review.",
        ))
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        proposal = state["proposals"].pop("P-001")
        proposal["id"] = "P-009"
        state["proposals"]["P-009"] = proposal
        inquiry = state["inquiries"]["INQ-001"]
        inquiry["proposal_ids"] = ["P-009"]
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        proc = self.run_auto("validate")
        self.assertBlocked(proc, "lacks a current passing mechanical preflight")

    def test_assessment_rejects_hard_verification_from_other_context(self):
        self.add_node("N1", "verification identity target")
        self.propose_ready_modification("N1", "verified value")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "Run this exact proposal under its frozen evaluator.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        state_path = self.repo / ".research" / "auto_research.json"
        state = self.state()
        state["proposals"]["P-001"]["verification"]["proposal"] = "P-999"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        payload = self.outcome_payload()
        outcome_path = self.repo / "mixed-verification-outcome.json"
        outcome_path.write_text(json.dumps(payload))
        proc = self.run_auto(
            "assess-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--file", str(outcome_path), "--assessor", "independent-B",
        )
        self.assertBlocked(proc, "hard verification belongs to a different proposal context")

    def test_assessment_rejects_hard_verification_record_tampering(self):
        self.add_node("N1", "verification self-digest target")
        self.propose_ready_modification("N1", "verified value")
        self.assertOk(self.run_auto(
            "critique", "--track", "B", "--proposal", "P-001",
            "--verdict", "pass", "--critic", "independent-B",
            "--reason", "Bind the complete hard-verification record before outcome review.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        state_path = self.repo / ".research" / "auto_research.json"
        pristine = self.state()
        verification = pristine["proposals"]["P-001"]["verification"]
        self.assertRegex(
            verification.get("hard_verification_sha256", ""), r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            pristine["verifications"][-1]["hard_verification_sha256"],
            verification["hard_verification_sha256"],
        )
        payload = self.outcome_payload()
        outcome_path = self.repo / "tampered-verification-outcome.json"
        outcome_path.write_text(json.dumps(payload))

        for field, replacement in (
            ("stdout_sha256", "0" * 64),
            ("at", "2099-01-01T00:00:00+00:00"),
            ("stderr_sha256", "f" * 64),
        ):
            with self.subTest(field=field):
                state = json.loads(json.dumps(pristine))
                state["proposals"]["P-001"]["verification"][field] = replacement
                state_path.write_text(json.dumps(state, indent=2) + "\n")
                proc = self.run_auto(
                    "assess-inquiry", "--track", "B", "--inquiry", "INQ-001",
                    "--file", str(outcome_path), "--assessor", "independent-B",
                )
                self.assertBlocked(
                    proc, "hard verification belongs to a different proposal context",
                )

        state = json.loads(json.dumps(pristine))
        state["verifications"][-1]["stdout_sha256"] = "1" * 64
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertBlocked(
            self.run_auto("validate"), "hard-verification digest has drifted",
        )

        state = json.loads(json.dumps(pristine))
        state["verifications"][-1]["level"] = "light"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertBlocked(
            self.run_auto("validate"), "hard-verification digest has drifted",
        )

        state = json.loads(json.dumps(pristine))
        del state["verifications"][-1]["level"]
        del state["verifications"][-1]["hard_verification_sha256"]
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertBlocked(
            self.run_auto("validate"),
            "must bind exactly one global hard-verification record",
        )

        state = json.loads(json.dumps(pristine))
        extra = json.loads(json.dumps(state["verifications"][-1]))
        extra["command"] = PASS_CMD + " # contradictory duplicate"
        material = {
            field: extra.get(field)
            for field in (
                "level", "track", "proposal", "command", "exit_code",
                "timeout", "requested_timeout_seconds", "stdout_sha256",
                "stdout_bytes", "stderr_sha256", "stderr_bytes",
                "output_limit_exceeded", "containment", "at", "sandbox",
                "proposal_review_sha256", "proposal_critique_sha256",
                "inquiry_review_sha256", "evaluator_snapshot_id",
                "evaluator_manifest_sha256", "state_basis_sha256",
            )
        }
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        extra["hard_verification_sha256"] = hashlib.sha256(encoded).hexdigest()
        state["verifications"].append(extra)
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertBlocked(
            self.run_auto("validate"),
            "must bind exactly one global hard-verification record",
        )

        state = json.loads(json.dumps(pristine))
        extra["level"] = "light"
        material["level"] = "light"
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        extra["hard_verification_sha256"] = hashlib.sha256(encoded).hexdigest()
        state["verifications"].append(extra)
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        self.assertBlocked(
            self.run_auto("validate"),
            "must bind exactly one global hard-verification record",
        )

    def test_patch_cannot_modify_protected_evaluator(self):
        protected = self.repo / "tests" / "protected" / "test_guard.py"
        candidate = self.repo / ".research" / "tmp" / "guard.candidate"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(protected.read_text() + "# weakened\n")
        proc = self.run_auto(
            "patch-make", "--target", "tests/protected/test_guard.py",
            "--candidate", str(candidate), "--out", "guard-patch.json",
        )
        self.assertBlocked(proc, "protected")
        self.assertNotIn("weakened", protected.read_text())

    def test_validate_rejects_contradictory_inquiry_lifecycle(self):
        self.record_self_inquiry("no_change")
        self.accept_inquiry(recommendation=None)
        st = self.state()
        st["inquiries"]["INQ-001"]["review"]["verdict"] = "reject"
        (self.repo / ".research" / "auto_research.json").write_text(
            json.dumps(st, indent=2) + "\n"
        )
        self.assertBlocked(
            self.run_auto("validate"),
            "closed_no_change requires review verdict 'pass'",
        )

    def test_challenged_inquiry_revision_replaces_fields_and_keeps_history(self):
        self.record_self_inquiry()
        self.assertOk(self.run_auto(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "challenge", "--reviewer", "test-reviewer",
            "--reason", "The baseline must name the old runner precisely.",
        ))
        revised = self.inquiry_payload()
        revised["evaluation_boundary"]["baseline"] = (
            "Versioned direct-propose runner at fixture revision abc123"
        )
        revised["question_after"] = (
            "At revision abc123, does review reduce false acceptance at equal budget?"
        )
        self.assertOk(self.run_auto(
            "revise-inquiry", "--inquiry", "INQ-001",
            "--file", self.inquiry_file(revised, "revised-inquiry.json"),
            "--note", "Pinned the comparison baseline.",
        ))
        inquiry = self.state()["inquiries"]["INQ-001"]
        self.assertEqual(inquiry["status"], "proposed")
        self.assertIsNone(inquiry["review"])
        self.assertEqual(len(inquiry["revisions"]), 1)
        self.assertIn("before the inquiry gate", inquiry["revisions"][0]["previous"]["evaluation_boundary"]["baseline"])

    def test_revision_cannot_be_noop_or_replace_question_with_final_goal(self):
        self.record_self_inquiry()
        self.assertOk(self.run_auto(
            "review-inquiry", "--track", "B", "--inquiry", "INQ-001",
            "--verdict", "challenge", "--reviewer", "test-reviewer",
            "--reason", "The question needs a more operational comparison.",
        ))
        before = self.state()
        noop = self.run_auto(
            "revise-inquiry", "--inquiry", "INQ-001",
            "--file", self.inquiry_file(self.inquiry_payload(), "noop.json"),
            "--note", "Only changed the note.",
        )
        self.assertBlocked(noop, "must change the canonical inquiry payload")
        self.assertEqual(self.state(), before)

        protected = self.inquiry_payload()
        protected["question_after"] = before["final_goal"]["statement"]
        proc = self.run_auto(
            "revise-inquiry", "--inquiry", "INQ-001",
            "--file", self.inquiry_file(protected, "protected-question.json"),
            "--note", "Attempted to restate the final goal.",
        )
        self.assertBlocked(proc, "must operationalize")
        self.assertEqual(self.state(), before)

    def test_runner_records_inquiry_then_waits_for_review(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "inquirer.py"
        payload = {"action": "inquire", **self.inquiry_payload()}
        proposer.write_text(
            "import json\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )
        first = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("stopped before proposal", first.stdout)
        self.assertEqual(self.state()["proposals"], {})
        second = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("will not bypass the inquiry gate", second.stdout)
        self.assertEqual(len(self.state()["inquiries"]), 1)

    def test_runner_agent_executes_in_disposable_staging_copy(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "staging_inquirer.py"
        payload = {"action": "inquire", **self.inquiry_payload()}
        proposer.write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['RESEARCH_CLOSURE_ROOT']).resolve()\n"
            "assert Path.cwd().resolve() == root\n"
            "(root / 'tools').mkdir(parents=True, exist_ok=True)\n"
            "(root / 'tools' / 'stage_only.py').write_bytes(b'staged')\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )

        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("stopped before proposal", out.stdout)
        self.assertFalse((self.repo / "tools" / "stage_only.py").exists())
        self.assertEqual(self.state()["inquiries"]["INQ-001"]["status"], "proposed")

    def test_runner_landlock_prevents_live_bytes_and_new_files(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tools = self.repo / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        guard = tools / "live_guard.py"
        guard.write_bytes(b"original-live-bytes\n")
        state_path = self.repo / ".research" / "auto_research.json"
        before_state_bytes = state_path.read_bytes()
        before_counters = self.state()["counters"]

        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "absolute_live_tamper.py"
        payload = {"action": "inquire", **self.inquiry_payload()}
        proposer.write_text(
            "import json\n"
            "from pathlib import Path\n"
            f"live = Path({str(self.repo)!r})\n"
            "(live / 'tools' / 'live_guard.py').write_bytes(b'tampered')\n"
            "(live / 'tools' / 'agent_new.py').write_bytes(b'new')\n"
            "state_path = live / '.research' / 'auto_research.json'\n"
            "state = json.loads(state_path.read_text())\n"
            "state['counters']['proposal'] = 999\n"
            "state_path.write_text(json.dumps(state))\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )

        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        )
        self.assertBlocked(proc, "agent command failed")
        detail = proc.stdout + proc.stderr
        self.assertIn("PermissionError", detail)
        self.assertIn("live_guard.py", detail)
        self.assertEqual(guard.read_bytes(), b"original-live-bytes\n")
        self.assertFalse((tools / "agent_new.py").exists())
        self.assertEqual(state_path.read_bytes(), before_state_bytes)
        self.assertEqual(self.state()["counters"], before_counters)
        self.assertEqual(self.state()["inquiries"], {})

    def test_runner_timeout_kills_setsid_descendants_before_live_manifest_check(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        marker = self.repo / "tools" / "late_agent_write.py"
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "timed_out_agent.py"
        child = (
            "import time; from pathlib import Path; "
            "time.sleep(1.2); "
            f"Path({str(marker)!r}).write_text('escaped')"
        )
        proposer.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
            "time.sleep(30)\n"
        )

        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--agent-timeout", "1", "--max-propose-attempts", "1",
            "--timeout", "60", "--no-alternate",
        )
        self.assertBlocked(proc, "agent timed out after 1s (attempt 1/1)")
        time.sleep(1.5)
        self.assertFalse(marker.exists())

    def test_runner_retries_only_after_timed_out_group_is_reaped(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "retrying_agent.py"
        payload = {"action": "inquire", **self.inquiry_payload()}
        proposer.write_text(
            "import json, os, time\n"
            "attempt = int(os.environ['AUTO_RESEARCH_AGENT_ATTEMPT'])\n"
            "if attempt == 1:\n"
            "    time.sleep(30)\n"
            f"print(json.dumps({payload!r}))\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--agent-timeout", "1", "--max-propose-attempts", "2",
            "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("stopped before proposal", out.stdout)
        self.assertIn("INQ-001", self.state()["inquiries"])

    def test_runner_retries_timeout_even_when_output_is_invalid_utf8(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "invalid_utf_retrying_agent.py"
        payload = {"action": "inquire", **self.inquiry_payload()}
        proposer.write_text(
            "import json, os, time\n"
            "attempt = int(os.environ['AUTO_RESEARCH_AGENT_ATTEMPT'])\n"
            "if attempt == 1:\n"
            "    os.write(1, b'\\xff')\n"
            "    time.sleep(30)\n"
            f"print(json.dumps({payload!r}))\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--agent-timeout", "1", "--max-propose-attempts", "2",
            "--timeout", "60", "--no-alternate",
        ))
        self.assertIn("stopped before proposal", out.stdout)
        self.assertIn("INQ-001", self.state()["inquiries"])

    def test_runner_agent_cwd_must_resolve_within_root(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        before = (self.repo / ".research" / "auto_research.json").read_bytes()
        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", "python3 -c 'print({})'",
            "--agent-cwd", str(self.repo.parent), "--timeout", "60",
            "--no-alternate",
        )
        self.assertBlocked(proc, "--agent-cwd must resolve within repository ROOT")
        self.assertEqual(
            (self.repo / ".research" / "auto_research.json").read_bytes(),
            before,
        )

    def test_runner_help_scopes_staging_guard(self):
        out = self.assertOk(self.run_auto("run", "--help")).stdout
        normalized = " ".join(out.split())
        self.assertIn("disposable repository copy", normalized)
        self.assertIn("not an OS sandbox", normalized)
        self.assertIn("stores no byte preimages", normalized)
        self.assertIn("does not repair content/topology", normalized)
        self.assertIn("Concurrent same-UID writers are unsupported", normalized)

    def test_runner_no_change_does_not_invoke_critic_or_change_evidence(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        before = self.state()
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "no_change.py"
        payload = {"action": "no_change", **self.inquiry_payload("no_change")}
        proposer.write_text(
            "import json\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )
        sentinel = tmp / "critic-was-called"
        critic = tmp / "critic_sentinel.py"
        critic.write_text(
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('called')\n"
            "print('{}')\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--critic-cmd", f"python3 {critic}", "--llm-critic",
            "--timeout", "60", "--no-alternate",
        )).stdout
        self.assertIn("stopped before proposal", out)
        self.assertFalse(sentinel.exists())
        st = self.state()
        self.assertEqual(st["nodes"], before["nodes"])
        self.assertEqual(st["proposals"], before["proposals"])
        self.assertEqual(st["verifications"], before["verifications"])
        self.assertEqual(st["last_self_test"], before["last_self_test"])
        self.assertEqual(st["counters"]["proposal"], before["counters"]["proposal"])
        self.assertEqual(st["inquiries"]["INQ-001"]["status"], "proposed")

    def test_runner_rejects_critic_json_without_critique_action(self):
        self.add_node("N1", "critic schema target")
        self.record_self_inquiry()
        self.accept_inquiry()
        self.assertOk(self.run_auto("self-test"))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "proposal_for_bad_critic.py"
        proposal = {
            "action": "propose",
            "title": "critic response schema",
            "statement": "B output must identify its semantic action",
            "targets": ["N1"],
            "patch": [{
                "op": "set_node_statement", "node": "N1", "statement": "candidate",
            }],
            "verification": PASS_CMD,
            "retro": None,
            "inquiry": "INQ-001",
            "recommendation": "REC-1",
        }
        proposer.write_text(
            "import json\n"
            f"print(json.dumps({proposal!r}))\n"
        )
        critic = tmp / "critic_missing_action.py"
        critic.write_text(
            "import json\n"
            "print(json.dumps({'verdict':'pass', 'reason':"
            "'This sounds detailed but lacks the required action field.'}))\n"
        )
        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--critic-cmd", f"python3 {critic}", "--llm-critic",
            "--timeout", "60", "--no-alternate",
        )
        self.assertBlocked(proc, "critic must return action=critique")
        saved = self.state()["proposals"]["P-001"]
        self.assertEqual(saved["status"], "proposed")
        self.assertIsNone(saved["critic"])
        self.assertEqual(saved["preflight"]["status"], "ready_for_B")

    def test_runner_rejects_proposal_before_an_inquiry_without_state_change(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        before = self.state()
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "premature_proposer.py"
        proposer.write_text(
            "import json\n"
            "print(json.dumps({'action':'propose','title':'too early',"
            "'statement':'no inquiry','targets':['M0'],"
            "'patch':[{'op':'set_node_status','node':'M0','status':'validated'}]}))\n"
        )
        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        )
        self.assertBlocked(proc, "active accepted inquiry")
        after = self.state()
        self.assertEqual(after, before)

    def test_runner_restart_exposes_existing_linked_proposal(self):
        self.add_node("N1", "resume target")
        self.record_self_inquiry()
        self.accept_inquiry()
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "resume value",
        }])
        self.assertOk(self.run_auto(
            "propose", "--inquiry", "INQ-001", "--recommendation", "REC-1",
            "--title", "resumable proposal", "--statement", "one linked change",
            "--targets", "N1", "--patch-file", patch,
            "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        should_not_run = tmp / "should_not_run.py"
        should_not_run.write_text("raise SystemExit(9)\n")
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1",
            "--proposer-cmd", f"python3 {should_not_run}",
            "--timeout", "60", "--no-alternate",
        )).stdout
        self.assertIn("P-001 already implements INQ-001", out)
        self.assertIn("resume with critique", out)

    def test_runner_rejects_contradictory_no_change_output(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "contradiction.py"
        payload = {"action": "no_change", **self.inquiry_payload("propose")}
        proposer.write_text(
            "import json\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )
        before = self.state()
        proc = self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate",
        )
        self.assertBlocked(proc, "contradictory agent output")
        self.assertEqual(self.state(), before)

    def test_legacy_state_loads_and_missing_counter_recovers(self):
        st = self.state()
        st.pop("inquiries")
        st.pop("active_inquiry_id")
        st["counters"].pop("inquiry")
        (self.repo / ".research" / "auto_research.json").write_text(
            json.dumps(st, indent=2) + "\n"
        )
        status = json.loads(self.assertOk(self.run_auto("status", "--json")).stdout)
        self.assertEqual(status["inquiries"], 0)
        self.record_self_inquiry()
        self.assertIn("INQ-001", self.state()["inquiries"])


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
            "propose", *self.proposal_link_args(),
            "--title", "change core",
            "--statement", "bad",
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
                      "--critic", "independent-test-critic",
                      "--reason", "The proposal is bounded by the frozen contract.")
        proc = self.run_auto("apply", "--proposal", "P-001")
        self.assertBlocked(proc, "hard verification")

    def test_reject_stops_the_pipeline(self):
        self.add_node("N1")
        self.propose_ready_modification()
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "reject",
            "--critic", "independent-test-critic",
            "--reason", "The proposed intervention exceeds the selected scope.",
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
        self.propose_ready_modification(verification=FAIL_CMD)
        self.run_auto("critique", "--proposal", "P-001", "--verdict", "pass",
                      "--critic", "independent-test-critic",
                      "--reason", "The failure path needs an otherwise valid review.")
        proc = self.run_auto("verify", "--proposal", "P-001")
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
            "--critic", "independent-test-critic",
            "--reason", "The exact proposal and verification plan are bounded.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["critic"]["track"], "B")
        self.assertEqual(st["proposals"]["P-001"]["verification"]["track"], "B")

    def test_failed_linked_verification_requires_new_inquiry(self):
        self.add_node("N1")
        self.propose_ready_modification(verification=FAIL_CMD)
        self.run_auto("critique", "--proposal", "P-001", "--verdict", "pass",
                      "--critic", "independent-test-critic",
                      "--reason", "The failure path needs an otherwise valid review.")
        self.run_auto("verify", "--proposal", "P-001")
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"],
                         "failed_verification")
        self.assertEqual(st["inquiries"]["INQ-001"]["status"],
                         "implementation_failed")
        self.assertBlocked(
            self.run_auto("revise", "--proposal", "P-001",
                          "--note", "fixed command"),
            "consumed the inquiry's one implementation authorization",
        )
        routed = self.assertOk(self.run_auto("ab-next")).stdout
        self.assertIn("use failed linked verification P-001 as evidence", routed)
        self.assertNotIn("revise --proposal P-001", routed)
        self.record_self_inquiry()
        self.assertIn("INQ-002", self.state()["inquiries"])

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

    def test_rollback_rejects_abandoned_state_only_future_but_keeps_ancestors(self):
        self.add_node("N1")
        self.add_node("N2")
        snapshot_dir = self.repo / ".research" / "auto_snapshots"
        snapshots = sorted(
            path for path in snapshot_dir.glob("*.json")
            if path.name != "index.json"
        )
        decoded = {
            path.name: json.loads(path.read_text()) for path in snapshots
        }
        init_path = next(
            path for path in snapshots
            if set(decoded[path.name]["nodes"]) == {"M0"}
        )
        n1_path = next(
            path for path in snapshots
            if set(decoded[path.name]["nodes"]) == {"M0", "N1"}
        )
        n2_path = next(
            path for path in snapshots
            if set(decoded[path.name]["nodes"]) == {"M0", "N1", "N2"}
        )
        self.assertOk(self.run_auto(
            "rollback", "--to", str(snapshots.index(n1_path) + 1),
        ))
        state_path = self.repo / ".research" / "auto_research.json"
        after_first = state_path.read_bytes()
        visible = sorted(
            path for path in snapshot_dir.glob("*.json")
            if path.name != "index.json"
        )
        self.assertBlocked(
            self.run_auto(
                "rollback", "--to", str(visible.index(
                    snapshot_dir / n2_path.name
                ) + 1),
            ),
            "target event history is not an exact ancestor prefix",
        )
        self.assertEqual(state_path.read_bytes(), after_first)
        visible = sorted(
            path for path in snapshot_dir.glob("*.json")
            if path.name != "index.json"
        )
        self.assertOk(self.run_auto(
            "rollback", "--to", str(visible.index(
                snapshot_dir / init_path.name
            ) + 1),
        ))
        self.assertEqual(set(self.state()["nodes"]), {"M0"})


class TestBootstrapTransition(RepoCase):
    """Regression boundary for the one-time schema-1 -> schema-2 install."""

    SENTINEL = "tests/protected/test_auto_research_sentinels.py"

    def _write_state(self, state):
        (self.repo / ".research" / "auto_research.json").write_text(
            json.dumps(state, indent=2) + "\n"
        )

    def _backup_identity(self, entry):
        material = {
            key: value for key, value in entry.items() if key != "backup_id"
        }
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _index_snapshot(self, name, state, label):
        snapshot_dir = self.repo / ".research" / "auto_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = snapshot_dir / name
        payload = (json.dumps(state, indent=2) + "\n").encode()
        path.write_bytes(payload)
        index_path = snapshot_dir / "index.json"
        index = json.loads(index_path.read_text())
        index.setdefault("files", {})[path.name] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "label": label,
            "at": "2026-08-24T12:00:00+08:00",
        }
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
        return path

    def _prepare_schema_one_capture(self):
        self.add_node("N1", "bootstrap target", "L4", ntype="verify",
                      status="validated")
        engine = self.repo / "tools" / "auto_research.py"
        engine.parent.mkdir(parents=True, exist_ok=True)
        old_engine = textwrap.dedent("""\
            #!/usr/bin/env python3
            import json
            import os
            import shutil
            import subprocess
            import sys
            from pathlib import Path

            root = Path(os.environ["RESEARCH_CLOSURE_ROOT"])
            if sys.argv[1:] != ["apply", "--track", "B", "--proposal", "P-001"]:
                raise SystemExit("minimal schema-one fixture only supports the frozen apply")
            state_path = root / ".research" / "auto_research.json"
            state = json.loads(state_path.read_text())
            proposal = state["proposals"]["P-001"]
            if state.get("schema_version") != 1 or proposal.get("status") != "verified":
                raise SystemExit("fixture prestate is not the captured schema-one proposal")
            at = "2026-08-24T11:00:00+08:00"
            targets = sorted(proposal.get("targets", []))
            pre_existing = set(state["nodes"])
            number = int(state["counters"].get("node", 0)) + 1
            state["counters"]["node"] = number
            mod_id = f"MOD-{number:03d}"
            state["nodes"][mod_id] = {
                "type": "modification",
                "statement": proposal["title"],
                "layer": "L4",
                "status": "validated",
                "trust": 1.0,
                "immutable": False,
                "proposal_id": proposal["id"],
                "created_at": at,
                "updated_at": at,
            }
            backup_dir = (
                root / ".research" / "auto_snapshots" / "file_backups" /
                f"bootstrap_{mod_id}"
            )
            backup_path = backup_dir / "tools" / "auto_research.py"
            backup_path.parent.mkdir(parents=True, exist_ok=False)
            shutil.copy2(Path(__file__), backup_path)
            state.setdefault("file_backups", []).append({
                "at": at,
                "modification_node": mod_id,
                "backup_dir": backup_dir.relative_to(root).as_posix(),
                "files": {
                    "tools/auto_research.py": backup_path.relative_to(root).as_posix(),
                },
            })
            patch_text = "".join(
                item["patch"] if item["patch"].endswith("\\n")
                else item["patch"] + "\\n"
                for item in proposal["patch"] if item.get("op") == "patch_file"
            )
            applied = subprocess.run(
                ["git", "apply"], cwd=root, input=patch_text,
                text=True, capture_output=True,
            )
            if applied.returncode:
                raise SystemExit(applied.stdout + applied.stderr)
            for target in targets:
                state["edges"].append({
                    "from": mod_id, "to": target, "kind": "modification",
                })
            for node_id in pre_existing:
                if node_id == "M0":
                    continue
                node = state["nodes"][node_id]
                node["trust"] = max(
                    0.0, round(float(node.get("trust", 1.0)) * 0.9, 4)
                )
                if node.get("status") == "validated" and node["trust"] < 0.25:
                    node["status"] = "draft"
            for target in targets:
                node = state["nodes"][target]
                node["status"] = "deprecated"
                node["trust"] = max(
                    0.0, round(float(node.get("trust", 1.0)) * 0.5, 4)
                )
                node["updated_at"] = at
            proposal.update({
                "status": "applied",
                "applied_at": at,
                "applied_by_track": "B",
                "modification_node": mod_id,
                "affected_nodes": targets,
            })
            state["events"].append({
                "at": at,
                "event": "modification_applied",
                "payload": {
                    "track": "B",
                    "proposal": proposal["id"],
                    "modification_node": mod_id,
                    "targets": targets,
                    "affected_nodes": targets,
                },
            })
            state_path.write_text(json.dumps(state, indent=2) + "\\n")
            print(f"B (slow) applied P-001 as modification node {mod_id}")
        """).encode()
        engine.write_bytes(old_engine)
        engine.chmod(0o755)
        sentinel = self.repo / self.SENTINEL
        sentinel.write_text(
            "import unittest\n\n"
            "class BootstrapSentinel(unittest.TestCase):\n"
            "    def test_external_trust_floor(self):\n"
            "        self.assertTrue(True)\n"
        )

        patch_path = self.repo / "bootstrap_patch.json"
        self.assertOk(self.run_auto(
            "patch-make", "--target", "tools/auto_research.py",
            "--candidate", str(CLI), "--out", str(patch_path),
        ))
        patch = json.loads(patch_path.read_text())

        # An old-style unlinked proposal must be grandfathered exactly; capture
        # may not manufacture an inquiry link that schema 1 never recorded.
        self.inject_legacy_proposal()
        state = self.state()
        proposal = state["proposals"]["P-001"]
        proposal["title"] = "install the schema-two self-inquiry engine"
        proposal["statement"] = (
            "Install the independently reviewed candidate without weakening M0."
        )
        proposal["patch"] = patch
        proposal["status"] = "verified"
        proposal["critic"] = {
            "track": "B",
            "verdict": "pass",
            "critic": "independent-bootstrap-reviewer",
            "reason": (
                "The exact candidate, old engine preimage, and protected sentinel "
                "are bounded before the one-time transition."
            ),
            "at": "2026-08-24T10:00:00+08:00",
        }
        proposal["verification"] = {
            "track": "B",
            "command": PASS_CMD,
            "exit_code": 0,
            "timeout": False,
            "stdout_sha256": hashlib.sha256(b"verified\n").hexdigest(),
            "at": "2026-08-24T10:01:00+08:00",
        }

        # Keep a non-empty historical leaf-only prefix so seal must preserve it.
        legacy_node = "MOD-LEGACY"
        state["nodes"][legacy_node] = {
            "type": "modification",
            "statement": "historical schema-one file edit",
            "layer": "L4",
            "status": "validated",
            "trust": 1.0,
            "immutable": False,
            "created_at": "2026-08-23T09:00:00+08:00",
            "updated_at": "2026-08-23T09:00:00+08:00",
        }
        legacy_dir = (
            self.repo / ".research" / "auto_snapshots" / "file_backups" /
            "legacy_MOD"
        )
        legacy_backup = legacy_dir / "docs" / "legacy.txt"
        legacy_backup.parent.mkdir(parents=True, exist_ok=True)
        legacy_backup.write_text("legacy preimage\n")
        legacy_entry = {
            "at": "2026-08-23T09:00:00+08:00",
            "modification_node": legacy_node,
            "backup_dir": legacy_dir.relative_to(self.repo).as_posix(),
            "files": {
                "docs/legacy.txt": legacy_backup.relative_to(self.repo).as_posix(),
            },
        }
        state["file_backups"] = [legacy_entry]
        # These fields are derived from raw schema-1 bytes, not trusted inputs.
        state.pop("legacy_proposal_ids", None)
        state.pop("legacy_file_backup_ids", None)
        state.pop("bootstrap_transition", None)
        state["schema_version"] = 1
        self._write_state(state)

        raw_before = (
            self.repo / ".research" / "auto_research.json"
        ).read_bytes()
        pre_floor_snapshot = self._index_snapshot(
            "20260824_095959_997_legacy_pre_floor.json",
            json.loads(raw_before), "legacy_pre_floor",
        )
        proc = self.assertOk(self.run_auto(
            "transition-capture", "--track", "B",
            "--proposal", "P-001",
            "--reviewer", "independent-bootstrap-reviewer",
            "--reason", (
                "Freeze the exact old state, engine preimage, candidate digest, "
                "and external protected sentinel before installation."
            ),
        ))
        self.assertIn("retain", proc.stdout.lower())
        manifests = sorted((
            self.repo / ".research" / "auto_snapshots" /
            "bootstrap_manifests"
        ).glob("*.json"))
        self.assertEqual(len(manifests), 1)
        manifest_path = manifests[0]
        manifest_sha256 = manifest_path.stem
        self.assertEqual(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            manifest_sha256,
        )
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["raw_state_sha256"],
                         hashlib.sha256(raw_before).hexdigest())
        self.assertEqual(manifest["proposal"], "P-001")
        self.assertEqual(manifest["patch_targets"], ["tools/auto_research.py"])
        self.assertEqual(
            manifest["legacy_file_backup_ids"],
            [self._backup_identity(legacy_entry)],
        )
        floor = (
            self.repo / ".research" / "auto_snapshots" /
            manifest["rollback_floor"]
        )
        self.assertEqual(floor.read_bytes(), raw_before)
        self.assertEqual(floor.stat().st_mode & 0o777, 0o444)
        return {
            "manifest": manifest,
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
            "raw_before": raw_before,
            "old_engine": old_engine,
            "pre_floor_snapshot": pre_floor_snapshot,
        }

    def _simulate_old_apply(self, capture, make_snapshot=True):
        """Emit precisely the leaf-only ledger shape of the schema-1 engine."""
        manifest = capture["manifest"]
        state = self.state()
        proposal = state["proposals"][manifest["proposal"]]
        mod_id = manifest["expected_modification_node"]
        at = "2026-08-24T11:00:00+08:00"
        state["counters"]["node"] = int(mod_id.rsplit("-", 1)[1])
        state["nodes"][mod_id] = {
            "type": "modification",
            "statement": proposal["title"],
            "layer": "L4",
            "status": "validated",
            "trust": 1.0,
            "immutable": False,
            "proposal_id": proposal["id"],
            "created_at": at,
            "updated_at": at,
        }
        state["edges"].append({
            "from": mod_id, "to": "N1", "kind": "modification",
        })
        state["nodes"]["N1"]["status"] = "deprecated"
        state["nodes"]["N1"]["trust"] = 0.45
        state["nodes"]["N1"]["updated_at"] = at
        state["nodes"]["MOD-LEGACY"]["trust"] = 0.9
        proposal.update({
            "status": "applied",
            "applied_at": at,
            "applied_by_track": "B",
            "modification_node": mod_id,
            "affected_nodes": ["N1"],
        })

        backup_dir = (
            self.repo / ".research" / "auto_snapshots" / "file_backups" /
            f"bootstrap_{mod_id}"
        )
        backup_path = backup_dir / "tools" / "auto_research.py"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(capture["old_engine"])
        backup_path.chmod(0o755)
        state["file_backups"].append({
            "at": at,
            "modification_node": mod_id,
            "backup_dir": backup_dir.relative_to(self.repo).as_posix(),
            "files": {
                "tools/auto_research.py":
                    backup_path.relative_to(self.repo).as_posix(),
            },
        })
        state["events"].append({
            "at": at,
            "event": "modification_applied",
            "payload": {
                "track": "B",
                "proposal": proposal["id"],
                "modification_node": mod_id,
                "targets": ["N1"],
                "affected_nodes": ["N1"],
            },
        })
        (self.repo / "tools" / "auto_research.py").write_bytes(CLI.read_bytes())
        self._write_state(state)
        unsealed = None
        if make_snapshot:
            unsealed = self._index_snapshot(
                "20990101_000000_998_modification_applied.json",
                state, "modification_applied",
            )
        return {
            "state_bytes": (
                self.repo / ".research" / "auto_research.json"
            ).read_bytes(),
            "backup_path": backup_path,
            "backup_bytes": backup_path.read_bytes(),
            "unsealed_snapshot": unsealed,
        }

    def _seal(self, digest):
        return self.run_auto(
            "transition-seal", "--track", "B",
            "--manifest-sha256", digest,
            "--reviewer", "independent-bootstrap-sealer",
            "--reason", (
                "The old apply suffix, engine postimage, and rollback backup "
                "match the separately retained transition manifest."
            ),
        )

    def test_capture_and_seal_preserve_legacy_prefix_and_bind_new_backup(self):
        capture = self._prepare_schema_one_capture()
        applied = self._simulate_old_apply(capture)
        self.assertOk(self._seal(capture["manifest_sha256"]))

        state = self.state()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(
            state["legacy_file_backup_ids"],
            capture["manifest"]["legacy_file_backup_ids"],
        )
        self.assertIsNone(state["file_backups"][0].get("topology"))
        sealed = state["file_backups"][-1]
        self.assertEqual(
            sealed["topology"]["tools/auto_research.py"]["sha256"],
            hashlib.sha256(capture["old_engine"]).hexdigest(),
        )
        self.assertEqual(sealed["backup_id"], self._backup_identity(sealed))
        transition = state["bootstrap_transition"]
        self.assertEqual(
            transition["manifest_sha256"], capture["manifest_sha256"]
        )
        self.assertEqual(transition["proposal"], "P-001")
        self.assertEqual(
            transition["rollback_floor"],
            capture["manifest"]["rollback_floor"],
        )
        self.assertEqual(
            hashlib.sha256(
                (self.repo / "tools" / "auto_research.py").read_bytes()
            ).hexdigest(),
            capture["manifest"]["engine_after_sha256"],
        )
        self.assertEqual(state["events"][-1]["event"],
                         "bootstrap_transition_sealed")
        self.assertTrue(applied["unsealed_snapshot"].exists())
        self.assertOk(self.run_auto("validate"))

    def test_transition_install_runs_schema_one_apply_then_seals_schema_two(self):
        capture = self._prepare_schema_one_capture()
        proc = self.assertOk(self.run_auto(
            "transition-install", "--track", "B",
            "--manifest-sha256", capture["manifest_sha256"],
            "--reviewer", "independent-bootstrap-installer",
            "--reason", (
                "Run the exact captured schema-one apply, then seal only its "
                "unique state, event, backup, and engine postimage."
            ),
            "--timeout", "30",
        ))
        self.assertIn("Bootstrap transition sealed", proc.stdout)
        self.assertIn("B (slow) applied P-001", proc.stdout)
        state = self.state()
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(
            state["bootstrap_transition"]["manifest_sha256"],
            capture["manifest_sha256"],
        )
        self.assertIn("topology", state["file_backups"][-1])
        self.assertEqual(
            (self.repo / "tools" / "auto_research.py").read_bytes(),
            CLI.read_bytes(),
        )
        self.assertOk(self.run_auto("validate"))

    def test_capture_and_standalone_seal_share_nonblocking_transition_lock(self):
        capture = self._prepare_schema_one_capture()
        lock_path = self.repo / ".research" / "auto_research.bootstrap.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            capture_proc = self.run_auto(
                "transition-capture", "--track", "B", "--proposal", "P-001",
                "--reviewer", "second-bootstrap-reviewer",
                "--reason", "A concurrent capture must not race the frozen manifest.",
            )
            self.assertBlocked(capture_proc, "bootstrap lock")
            applied = self._simulate_old_apply(capture, make_snapshot=False)
            seal_proc = self._seal(capture["manifest_sha256"])
            self.assertBlocked(seal_proc, "bootstrap lock")
            self.assertEqual(
                (self.repo / ".research" / "auto_research.json").read_bytes(),
                applied["state_bytes"],
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        self.assertOk(self._seal(capture["manifest_sha256"]))
        self.assertEqual(self.state()["schema_version"], 2)

    def test_seal_fails_closed_on_manifest_state_event_backup_or_engine_drift(self):
        capture = self._prepare_schema_one_capture()
        applied = self._simulate_old_apply(capture, make_snapshot=False)
        state_path = self.repo / ".research" / "auto_research.json"
        engine_path = self.repo / "tools" / "auto_research.py"
        manifest_path = capture["manifest_path"]
        pristine_manifest = manifest_path.read_bytes()
        pristine_state = applied["state_bytes"]
        pristine_engine = engine_path.read_bytes()
        pristine_backup = applied["backup_bytes"]

        def drift_manifest():
            manifest_path.write_bytes(pristine_manifest + b" ")

        def drift_state():
            state = json.loads(pristine_state)
            state["proposals"]["P-001"]["title"] += " (drifted)"
            self._write_state(state)

        def drift_event():
            state = json.loads(pristine_state)
            state["events"][0]["payload"]["goal"] += " (drifted)"
            self._write_state(state)

        def drift_backup():
            applied["backup_path"].write_bytes(pristine_backup + b"drift")

        def drift_engine():
            engine_path.write_bytes(pristine_engine + b"# drift\n")

        for label, mutate in (
            ("manifest", drift_manifest),
            ("state", drift_state),
            ("event", drift_event),
            ("file-backup", drift_backup),
            ("engine", drift_engine),
        ):
            with self.subTest(drift=label):
                manifest_path.chmod(0o644)
                manifest_path.write_bytes(pristine_manifest)
                state_path.write_bytes(pristine_state)
                engine_path.write_bytes(pristine_engine)
                applied["backup_path"].write_bytes(pristine_backup)
                mutate()
                proc = self.assertBlocked(
                    self._seal(capture["manifest_sha256"]), "BLOCKED"
                )
                self.assertNotIn("bootstrap_transition_sealed", proc.stdout)
                self.assertIsNone(
                    json.loads(state_path.read_bytes()).get(
                        "bootstrap_transition"
                    )
                )

    def test_rollback_rejects_pre_floor_and_unsealed_schema_one_but_allows_floor(self):
        capture = self._prepare_schema_one_capture()
        applied = self._simulate_old_apply(capture)
        self.assertOk(self._seal(capture["manifest_sha256"]))
        snapshots = sorted(
            path for path in (
                self.repo / ".research" / "auto_snapshots"
            ).glob("*.json") if path.name != "index.json"
        )
        floor = (
            self.repo / ".research" / "auto_snapshots" /
            capture["manifest"]["rollback_floor"]
        )
        floor_index = snapshots.index(floor) + 1
        unsealed_index = snapshots.index(applied["unsealed_snapshot"]) + 1
        pre_floor = capture["pre_floor_snapshot"]
        pre_floor_index = snapshots.index(pre_floor) + 1

        self.assertBlocked(
            self.run_auto("rollback", "--to", str(pre_floor_index)),
            "bootstrap",
        )
        self.assertBlocked(
            self.run_auto("rollback", "--to", str(unsealed_index)),
            "bootstrap",
        )
        before_payloads = {
            path.name: path.read_bytes() for path in snapshots
        }
        index_path = (
            self.repo / ".research" / "auto_snapshots" / "index.json"
        )
        before_index = index_path.read_bytes()
        self.assertOk(self.run_auto("rollback", "--to", str(floor_index)))
        raw = json.loads(
            (self.repo / ".research" / "auto_research.json").read_bytes()
        )
        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(
            (self.repo / "tools" / "auto_research.py").read_bytes(),
            capture["old_engine"],
        )
        snapshot_root = self.repo / ".research" / "auto_snapshots"
        old_visible = sorted(
            path for path in snapshot_root.glob("*.json")
            if path.name != "index.json"
        )
        self.assertEqual([path.name for path in old_visible], [floor.name])
        visible_index = json.loads(index_path.read_text())
        self.assertEqual(list(visible_index["files"]), [floor.name])
        archive_root = (
            snapshot_root / "bootstrap_archives" /
            capture["manifest_sha256"]
        )
        self.assertTrue(archive_root.is_dir())
        self.assertEqual(
            (archive_root / "original_index.json").read_bytes(),
            before_index,
        )
        archive_manifest = json.loads(
            (archive_root / "archive_manifest.json").read_text()
        )
        archived_names = {
            item["name"] for item in archive_manifest["snapshots"]
        }
        self.assertEqual(
            archived_names,
            set(before_payloads) - {floor.name},
        )
        for item in archive_manifest["snapshots"]:
            payload = (
                archive_root / "snapshots" / item["name"]
            ).read_bytes()
            self.assertEqual(payload, before_payloads[item["name"]])
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), item["sha256"]
            )
        self.assertEqual(
            hashlib.sha256(before_index).hexdigest(),
            archive_manifest["original_index_sha256"],
        )

    def test_floor_archive_recovers_exactly_from_move_or_restore_exception(self):
        capture = self._prepare_schema_one_capture()
        self._simulate_old_apply(capture)
        self.assertOk(self._seal(capture["manifest_sha256"]))
        snapshot_root = self.repo / ".research" / "auto_snapshots"
        snapshots = sorted(
            path for path in snapshot_root.glob("*.json")
            if path.name != "index.json"
        )
        floor = snapshot_root / capture["manifest"]["rollback_floor"]
        floor_index = snapshots.index(floor) + 1
        state_path = self.repo / ".research" / "auto_research.json"
        engine_path = self.repo / "tools" / "auto_research.py"
        index_path = snapshot_root / "index.json"
        before_state = state_path.read_bytes()
        before_engine = engine_path.read_bytes()
        before_index = index_path.read_bytes()
        before_snapshots = {
            path.name: path.read_bytes() for path in snapshots
        }

        for mode in ("move", "restore"):
            with self.subTest(mode=mode):
                script = textwrap.dedent(f"""
                    import argparse
                    import importlib.util

                    spec = importlib.util.spec_from_file_location(
                        "candidate_auto_research", {str(CLI)!r}
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    mode = {mode!r}
                    if mode == "move":
                        original = module._archive_snapshot_move
                        calls = {{"count": 0}}
                        def injected_move(source, destination):
                            calls["count"] += 1
                            result = original(source, destination)
                            if calls["count"] == 2:
                                raise RuntimeError(
                                    "injected post-move return failure"
                                )
                            return result
                        module._archive_snapshot_move = injected_move
                    else:
                        def injected_restore(current, restored):
                            raise RuntimeError("injected file restore failure")
                        module.restore_files_to_target = injected_restore
                    try:
                        module.cmd_rollback(argparse.Namespace(
                            to={floor_index}, dry_run=False
                        ))
                    except RuntimeError as exc:
                        if "injected" not in str(exc):
                            raise
                    else:
                        raise AssertionError("injected rollback failure was not raised")
                """)
                proc = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=self.repo,
                    env={
                        **os.environ,
                        "RESEARCH_CLOSURE_ROOT": str(self.repo),
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    proc.returncode, 0, proc.stdout + proc.stderr
                )
                self.assertEqual(state_path.read_bytes(), before_state)
                self.assertEqual(engine_path.read_bytes(), before_engine)
                self.assertEqual(index_path.read_bytes(), before_index)
                after_snapshots = {
                    path.name: path.read_bytes()
                    for path in snapshot_root.glob("*.json")
                    if path.name != "index.json"
                }
                self.assertEqual(after_snapshots, before_snapshots)
                self.assertFalse((
                    snapshot_root / "bootstrap_archives" /
                    capture["manifest_sha256"]
                ).exists())


class TestPatchFileCapability(RepoCase):
    def test_patch_make_proposes_verifies_in_sandbox_and_applies(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        patch = self.make_file_patch(
            "tools/new_file.py", "print('patched')\n",
        )
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "add engine file", "--statement",
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
        self.assess_inquiry()
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
            "propose", *self.proposal_link_args(),
            "--title", "missing targets", "--statement", "bad",
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
            "propose", *self.proposal_link_args(),
            "--title", "delete core", "--statement", "bad",
            "--targets", "N1", "--patch-file", str(patch_path),
        )
        self.assertBlocked(proc, "may not delete core engine file")

    def test_patch_file_cannot_create_dangling_symlink(self):
        self.add_node("N1")
        patch_path = self.repo / "dangling-symlink.json"
        patch_path.write_text(json.dumps([{
            "op": "patch_file",
            "patch": (
                "diff --git a/tools/dangling-link b/tools/dangling-link\n"
                "new file mode 120000\n"
                "--- /dev/null\n"
                "+++ b/tools/dangling-link\n"
                "@@ -0,0 +1 @@\n"
                "+missing-target\n"
                "\\ No newline at end of file\n"
            ),
        }]))
        proc = self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "create dangling link", "--statement", "must be blocked",
            "--targets", "N1", "--patch-file", str(patch_path),
            "--verification", PASS_CMD,
        )
        self.assertBlocked(proc, "may not create, delete, or modify symbolic links")
        target = self.repo / "tools" / "dangling-link"
        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())
        self.assertEqual(self.state()["proposals"], {})

    def test_failed_outer_transaction_removes_file_backup_directory(self):
        self.add_node("N1", "transaction rollback target")
        (self.repo / "tools").mkdir(exist_ok=True)
        target = self.repo / "tools" / "topology"
        target.write_text("before\n")
        patch = self.patch_file([{
            "op": "patch_file",
            "patch": (
                "diff --git a/tools/topology b/tools/topology\n"
                "deleted file mode 100644\n"
                "--- a/tools/topology\n"
                "+++ /dev/null\n"
                "@@ -1 +0,0 @@\n"
                "-before\n"
                "diff --git a/tools/topology/child.txt b/tools/topology/child.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/tools/topology/child.txt\n"
                "@@ -0,0 +1 @@\n"
                "+after\n"
            ),
        }])
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "exercise outer rollback", "--statement",
            "file and state must roll back together", "--targets", "N1",
            "--patch-file", patch,
            "--verification", (
                "python3 -c \"assert open('tools/topology/child.txt').read() == 'after\\n'\""
            ),
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "independent-test-critic",
            "--reason", "The exact file patch and verification are transaction-bounded.",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assess_inquiry()
        state_path = self.repo / ".research" / "auto_research.json"
        before_state = state_path.read_bytes()
        backup_root = self.repo / ".research" / "auto_snapshots" / "file_backups"
        before_backups = sorted(
            path.name for path in backup_root.iterdir()
        ) if backup_root.exists() else []
        collision_sentinel = self.repo / "tools" / (
            "topology.auto-research-rollback.tmp"
        )
        collision_sentinel.write_bytes(b"unrelated sibling\n")
        script = (
            "import importlib.util\n"
            f"spec=importlib.util.spec_from_file_location('candidate_engine', {str(CLI)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "state=module.load_state()\n"
            "module.snapshot_state=lambda candidate,label='',**kwargs: None\n"
            "proposal=state['proposals']['P-001']\n"
            "try:\n"
            "    module.apply_modification(state,'P-001',proposal['patch'],proposal['targets'])\n"
            "except RuntimeError as exc:\n"
            "    assert 'without a rollback snapshot' in str(exc)\n"
            "else:\n"
            "    raise AssertionError('failure injection did not fire')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], cwd=self.repo,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(), "before\n")
        self.assertFalse((target / "child.txt").exists())
        self.assertEqual(
            collision_sentinel.read_bytes(), b"unrelated sibling\n",
        )
        self.assertEqual(state_path.read_bytes(), before_state)
        after_backups = sorted(
            path.name for path in backup_root.iterdir()
        ) if backup_root.exists() else []
        self.assertEqual(after_backups, before_backups)

    def test_rollback_restores_file_contents(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        (self.repo / "tools").mkdir(exist_ok=True)
        target = self.repo / "tools" / "file.txt"
        target.write_text("v1\n")

        first_patch = self.make_file_patch("tools/file.txt", "v2\n", "p1.json")
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "file v1->v2", "--statement", "local edit",
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
            "propose", *self.proposal_link_args(["N2"]),
            "--title", "file v2->v3", "--statement", "local edit",
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
            "propose", *self.proposal_link_args(),
            "--title", "convert retro", "--statement", "candidate",
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

    def test_launch_auto_selects_b_for_proposed_inquiry(self):
        self.record_self_inquiry()
        out = self.assertOk(self.run_auto(
            "launch", "--harness", "manual", "--role", "auto", "--dry-run",
        )).stdout
        self.assertIn("selected role    : B", out)
        self.assertIn("assesses the frozen outcome", out)

    def test_launch_auto_selects_b_for_deprecated_revalidation(self):
        self.add_node("N1", status="deprecated")
        out = self.assertOk(self.run_auto(
            "launch", "--harness", "manual", "--role", "auto", "--dry-run",
        )).stdout
        self.assertIn("selected role    : B", out)

    def test_launch_auto_selects_b_for_draft_revalidation(self):
        self.add_node("N1", status="draft")
        out = self.assertOk(self.run_auto(
            "launch", "--harness", "manual", "--role", "auto", "--dry-run",
        )).stdout
        self.assertIn("selected role    : B", out)


class TestSemanticNoveltyGuard(RepoCase):
    def test_rejects_epoch_recycled_set_node_status_title(self):
        self.add_node("N1", "trust accounting", "L4", ntype="inference",
                      status="validated")
        first = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(), "--title",
            "A optimizes B: trust accounting via inference L4 (epoch 1)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", first, "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "reject",
            "--critic", "test-critic",
            "--reason", "Rejected only to exercise the novelty guard on replacement.",
        ))
        second = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        proc = self.run_auto(
            "propose", *self.proposal_link_args(), "--title",
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
            "propose", *self.proposal_link_args(),
            "--title", "add helper A", "--statement", "file patch",
            "--targets", "N1", "--patch-file", first, "--verification", PASS_CMD,
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "reject",
            "--critic", "test-critic",
            "--reason", "Rejected only to compare a distinct replacement file patch.",
        ))
        second = self.make_file_patch("tools/helper_b.py", "x = 2\n", "b.json")
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "add helper B", "--statement", "file patch",
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

    def test_a_brief_refuses_missing_index_without_reblessing(self):
        self.add_node("N1")
        index = self.repo / ".research" / "auto_snapshots" / "index.json"
        self.assertTrue(index.exists())
        index.unlink()
        proc = self.assertBlocked(self.run_auto("a-brief"), "snapshot index")
        self.assertIn("snapshot-reindex", proc.stdout)
        self.assertFalse(index.exists())

    def test_a_brief_does_not_rebless_structurally_valid_snapshot_drift(self):
        self.add_node("N1")
        snapshot_dir = self.repo / ".research" / "auto_snapshots"
        index_path = snapshot_dir / "index.json"
        before_index = index_path.read_bytes()
        snapshots = sorted(
            path for path in snapshot_dir.glob("*.json")
            if path.name != "index.json"
        )
        target = snapshots[0]
        tampered = json.loads(target.read_text())
        tampered["events"][0]["payload"]["goal"] += " drift"
        target.write_text(json.dumps(tampered, indent=2) + "\n")
        proc = self.assertBlocked(
            self.run_auto("a-brief"), "snapshot digest differs from index"
        )
        self.assertIn("snapshot-reindex", proc.stdout)
        self.assertEqual(index_path.read_bytes(), before_index)
        current = sorted(
            path for path in snapshot_dir.glob("*.json")
            if path.name != "index.json"
        )
        self.assertBlocked(
            self.run_auto(
                "rollback", "--to", str(current.index(target) + 1),
            ),
            "do not match the verified index",
        )

    def test_a_brief_routes_implementing_proposal_without_opening_new_inquiry(self):
        self.add_node("N1")
        self.propose_ready_modification()
        proposed = self.assertOk(self.run_auto("a-brief")).stdout
        self.assertIn("wait for B: critique --proposal P-001", proposed)
        self.assertNotIn(
            "next A action     : self-observe", proposed
        )
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "challenge",
            "--critic", "independent-test-critic",
            "--reason", "A precise revision note is needed before B can pass.",
        ))
        challenged = self.assertOk(self.run_auto("a-brief")).stdout
        self.assertIn("next A action     : revise --proposal P-001", challenged)


class TestACheck(RepoCase):
    def test_a_check_passes_valid_patch_without_proposing(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        self.proposal_link_args(["N1"])
        patch = self.make_file_patch("tools/checked.py", "x = 1\n", "check.json")
        out = self.assertOk(self.run_auto(
            "a-check", "--title", "add checked helper",
            "--statement", "file patch", "--targets", "N1",
            "--patch-file", patch, "--verification", PASS_CMD,
        )).stdout
        self.assertIn("A CHECK PASS", out)
        self.assertNotIn("P-001", self.state()["proposals"])

    def test_a_check_requires_accepted_inquiry_and_verification(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        patch = self.make_file_patch(
            "tools/checked.py", "x = 1\n", "check.json"
        )
        self.assertBlocked(self.run_auto(
            "a-check", "--title", "ungated check",
            "--statement", "must be blocked", "--targets", "N1",
            "--patch-file", patch, "--verification", PASS_CMD,
        ), "active accepted inquiry")
        self.proposal_link_args(["N1"])
        self.assertBlocked(self.run_auto(
            "a-check", "--title", "missing verification",
            "--statement", "must be blocked", "--targets", "N1",
            "--patch-file", patch,
        ), "verification command is required")

    def test_a_check_enforces_selected_recommendation_contract(self):
        self.add_node("N1", "patch target", "L4", ntype="verify",
                      status="validated")
        self.proposal_link_args(["N1"])
        patch = self.make_file_patch(
            "docs/outside_contract.py", "x = 1\n", "outside.json"
        )
        self.assertBlocked(self.run_auto(
            "a-check", "--title", "outside frozen contract",
            "--statement", "must be blocked", "--targets", "N1",
            "--patch-file", patch, "--verification", PASS_CMD,
        ), "outside selected recommendation contract")

    def test_a_check_blocks_recycled_candidate(self):
        self.add_node("N1", "trust accounting", "L4", ntype="inference",
                      status="validated")
        first = self.patch_file(
            [{"op": "set_node_status", "node": "N1", "status": "validated"}]
        )
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(), "--title",
            "A optimizes B: trust accounting via inference L4 (epoch 1)",
            "--statement", "candidate", "--targets", "N1",
            "--patch-file", first, "--verification", PASS_CMD,
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


class TestStatusJson(RepoCase):
    def test_status_json_summary(self):
        self.add_node("N1", "candidate", "L1", ntype="assumption", status="validated")
        out = self.assertOk(self.run_auto("status", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "status")
        self.assertEqual(data["nodes"], 2)
        self.assertEqual(data["status"]["validated"], 2)
        self.assertIsInstance(data["proposal_rows"], list)


class TestProposalShow(RepoCase):
    def test_proposal_show_json(self):
        self.add_node("N1")
        self.propose_ready_modification()
        out = self.assertOk(self.run_auto("proposal-show", "--proposal", "P-001", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["id"], "P-001")
        self.assertEqual(data["status"], "proposed")
        self.assertEqual(data["patch"][0]["op"], "set_node_statement")

    def test_proposal_show_text(self):
        self.add_node("N1")
        self.propose_ready_modification()
        out = self.assertOk(self.run_auto("proposal-show", "--proposal", "P-001")).stdout
        self.assertIn("PROPOSAL P-001", out)
        self.assertIn("set_node_statement", out)


class TestVerificationStats(RepoCase):
    def test_verification_stats_json(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        out = self.assertOk(self.run_auto("verification-stats", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "verification-stats")
        self.assertGreaterEqual(data["total"], 1)
        self.assertIn("self_test", data["by_level"])
        self.assertGreaterEqual(data["self_test_streak"], 1)


class TestRetroImport(RepoCase):
    def test_retro_import_batch(self):
        batch = self.repo / "retro_batch.json"
        batch.write_text(json.dumps([
            {"observation": "first batch item", "class": "defect", "source": "test"},
            {"observation": "second batch item", "class": "risk", "source": "test"},
        ]))
        out = self.assertOk(self.run_auto("retro", "import", "--file", str(batch))).stdout
        self.assertIn("Imported 2 retrospectives", out)
        st = self.state()
        self.assertEqual(st["counters"]["retrospective"], 2)
        self.assertEqual(len(st["retrospectives"]), 2)


class TestSnapshotIndexCli(RepoCase):
    def test_snapshot_reindex_rebuilds_index(self):
        self.add_node("N1")
        index = self.repo / ".research" / "auto_snapshots" / "index.json"
        index.unlink()
        out = self.assertOk(self.run_auto("snapshot-reindex")).stdout
        self.assertIn("rebuilt", out)
        self.assertTrue(json.loads(index.read_text())["files"])

    def test_file_backup_stats_json(self):
        out = self.assertOk(self.run_auto("file-backup-stats", "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "file-backup-stats")
        self.assertEqual(data["entries"], 0)


class TestSelfTestJson(RepoCase):
    def test_self_test_json(self):
        out = self.assertOk(self.run_auto("self-test", "--command", PASS_CMD, "--json")).stdout
        data = json.loads(out)
        self.assertEqual(data["mode"], "self-test")
        self.assertTrue(data["passed"])
        self.assertEqual(data["record"]["exit_code"], 0)


class TestRollbackPreview(RepoCase):
    def test_rollback_dry_run_does_not_mutate(self):
        self.add_node("N1")
        before = self.state()
        out = self.assertOk(self.run_auto("rollback", "--to", "1", "--dry-run")).stdout
        self.assertIn("ROLLBACK PREVIEW", out)
        self.assertEqual(self.state(), before)


class TestWatchJson(RepoCase):
    def test_watch_once_json(self):
        self.assertOk(self.run_auto("self-test", "--command", PASS_CMD))
        out = self.assertOk(self.run_auto("watch", "--once", "--interval", "1", "--json")).stdout
        data = json.loads(out)
        self.assertIn("checks", data)
        self.assertIn("critical", data)
        self.assertTrue(data["ok"])


class TestLaunchPrompts(RepoCase):
    def test_set_launch_prompt_patch_applies(self):
        self.add_node("N1")
        patch = self.patch_file([{
            "op": "set_launch_prompt",
            "role": "A",
            "prompts": ["Think carefully before proposing."],
        }])
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "change A launch prompt",
            "--statement", "launch protocol is research content",
            "--targets", "N1", "--patch-file", patch, "--verification", PASS_CMD,
        ))
        self.drive_pipeline()
        self.assertEqual(
            self.state()["launch_prompts"]["A"],
            ["Think carefully before proposing."],
        )

    def test_launch_dry_run_prints_prompts(self):
        out = self.assertOk(self.run_auto(
            "launch", "--harness", "manual", "--role", "A", "--dry-run",
        )).stdout
        self.assertIn("launch prompts", out)
        self.assertIn("grounded inquiry", out)


class TestRunner(RepoCase):
    def test_runner_rejects_stale_behavior_flags(self):
        for flag in ("--alternate", "--stop-on-warning", "--commit"):
            with self.subTest(flag=flag):
                proc = self.run_auto(
                    "run", "--epochs", "1", "--proposer-cmd", "true", flag,
                )
                self.assertBlocked(proc, "deprecated runner option")
        help_text = self.assertOk(self.run_auto("run", "--help")).stdout
        self.assertIn("deprecated and rejected", help_text)

    def test_runner_prompt_uses_target_whitelist(self):
        self.add_node("RUN-SCHEDULER", "runner", "L1", ntype="verify",
                      status="validated")
        self.proposal_link_args()
        script = r"""
import json, sys
sys.path.insert(0, "tools")
import auto_research as a
state = a.load_state()
prompt = a.run_proposer_prompt(state, "A")
assert "RUN-SCHEDULER" in prompt
assert "MOD-142" not in prompt
assert len(prompt) < 5000, len(prompt)
print("runner-prompt-whitelist-ok")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("runner-prompt-whitelist-ok", proc.stdout)

    def test_run_one_epoch_with_fake_agents(self):
        self.add_node("N1", "runner target", "L4", ntype="assumption", status="validated")
        st = self.state()
        st["self_test_command"] = "python3 -c \"print('ok')\""
        (self.repo / ".research" / "auto_research.json").write_text(json.dumps(st, indent=2) + "\n")
        self.assertOk(self.run_auto("self-test"))
        self.proposal_link_args()
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "proposer.py"
        proposer.write_text(
            "import json\n"
            "print(json.dumps({'action':'propose','title':'runner test proposal',"
            "'statement':'small runner change','targets':['N1'],"
            "'inquiry':'INQ-001','recommendation':'REC-1',"
            "'patch':[{'op':'set_node_statement','node':'N1','statement':'v2'}],"
            "'verification':'python3 -c \"print(1)\"','retro':None}))\n"
        )
        critic = tmp / "critic.py"
        critic.write_text(
            "import json\n"
            "print(json.dumps({'action':'critique','verdict':'pass',"
            "'reason':'local one-node statement change with exit-0 verification'}))\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--critic-cmd", f"python3 {critic}", "--timeout", "60",
            "--no-alternate", "--llm-critic",
        )).stdout
        self.assertIn("EPOCH 1/1", out)
        st = self.state()
        self.assertEqual(st["proposals"]["P-001"]["status"], "verified")
        self.assertEqual(st["inquiries"]["INQ-001"]["status"], "awaiting_outcome")
        self.assertIn("assess-inquiry", out)
        self.assertTrue((self.repo / ".research" / "auto_run.jsonl").exists())

    def test_runner_preserves_llm_challenge_for_a_revision(self):
        self.add_node(
            "N1", "runner target", "L4",
            ntype="assumption", status="validated",
        )
        state = self.state()
        state["self_test_command"] = PASS_CMD
        (
            self.repo / ".research" / "auto_research.json"
        ).write_text(json.dumps(state, indent=2) + "\n")
        self.assertOk(self.run_auto("self-test"))
        self.proposal_link_args()
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "proposer_challenge.py"
        proposer.write_text(
            "import json\n"
            "print(json.dumps({'action':'propose','title':'challenged runner proposal',"
            "'statement':'bounded candidate','targets':['N1'],"
            "'inquiry':'INQ-001','recommendation':'REC-1',"
            "'patch':[{'op':'set_node_statement','node':'N1','statement':'v2'}],"
            "'verification':'python3 -c \"print(1)\"','retro':None}))\n"
        )
        critic = tmp / "critic_challenge.py"
        critic.write_text(
            "import json\n"
            "print(json.dumps({'action':'critique','verdict':'challenge',"
            "'reason':'The rationale needs a proposal-specific revision note.'}))\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1",
            "--proposer-cmd", f"python3 {proposer}",
            "--critic-cmd", f"python3 {critic}",
            "--timeout", "60", "--no-alternate", "--llm-critic",
        )).stdout
        self.assertIn("remains challenged", out)
        state = self.state()
        self.assertEqual(state["proposals"]["P-001"]["status"], "challenged")
        self.assertEqual(state["inquiries"]["INQ-001"]["status"], "implementing")
        self.assertIsNone(
            state["inquiries"]["INQ-001"]["implementation_failure"]
        )


class TestVerifyDryRunSemantics(RepoCase):
    def test_state_only_verify_sees_patched_state(self):
        self.add_node("N1", "before", "L4", ntype="assumption", status="validated")
        patch = self.patch_file(
            [{"op": "set_node_statement", "node": "N1", "statement": "after"}]
        )
        verification = (
            "python3 -c \"import json; d=json.load(open('.research/auto_research.json')); "
            "assert d['nodes']['N1']['statement']=='after', d['nodes']['N1']['statement']; print('patched-state-ok')\""
        )
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "state verify dry run", "--statement",
            "candidate", "--targets", "N1", "--patch-file", patch,
            "--verification", verification,
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "local state change",
        ))
        self.assertOk(self.run_auto("verify", "--proposal", "P-001"))
        self.assertEqual(self.state()["proposals"]["P-001"]["status"], "verified")
        self.assess_inquiry()
        self.assertOk(self.run_auto("apply", "--proposal", "P-001"))

    def test_verification_timeout_kills_setsid_descendants_before_live_guard(self):
        self.add_node("N1", "timeout target", "L4", ntype="assumption", status="validated")
        marker = self.repo / "tools" / "late_verification_write.py"
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        verifier = tmp / "timed_out_verifier.py"
        child = (
            "import time; from pathlib import Path; "
            "time.sleep(1.2); "
            f"Path({str(marker)!r}).write_text('escaped')"
        )
        verifier.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)\n"
            "time.sleep(30)\n"
        )
        patch = self.patch_file([{
            "op": "set_node_statement", "node": "N1", "statement": "after-timeout",
        }])
        self.assertOk(self.run_auto(
            "propose", *self.proposal_link_args(),
            "--title", "verification process containment",
            "--statement", "children must not outlive timeout",
            "--targets", "N1", "--patch-file", patch,
            "--verification", f"python3 {verifier}",
        ))
        self.assertOk(self.run_auto(
            "critique", "--proposal", "P-001", "--verdict", "pass",
            "--critic", "test-critic", "--reason", "exercise timeout containment",
        ))
        proc = self.run_auto("verify", "--proposal", "P-001", "--timeout", "1")
        self.assertNotEqual(proc.returncode, 0)
        time.sleep(1.5)
        self.assertFalse(marker.exists())
        self.assertTrue(self.state()["verifications"][-1]["timeout"])


class TestCommandContainment(RepoCase):
    def test_bootstrap_runner_reaps_setsid_child_after_timeout(self):
        child_script = (
            "import subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)\n"
            "print(child.pid,flush=True)\n"
            "time.sleep(30)\n"
        )
        wrapper = (
            "import importlib.util,json,os,sys\n"
            f"spec=importlib.util.spec_from_file_location('candidate_engine',{str(CLI)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            f"result=module._run_bootstrap_apply_process([sys.executable,'-c',{child_script!r}],1,os.environ.copy())\n"
            "print(json.dumps(result))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", wrapper],
            cwd=self.repo,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["timeout"])
        child_pid = int(result["stdout"].strip())
        self.assertFalse(Path(f"/proc/{child_pid}").exists())

    def test_bootstrap_runner_bounds_output_flood(self):
        flood_script = (
            "import os\n"
            "chunk=b'x'*(64*1024)\n"
            "for _ in range(200): os.write(1,chunk)\n"
        )
        wrapper = (
            "import importlib.util,json,os,sys\n"
            f"spec=importlib.util.spec_from_file_location('candidate_engine',{str(CLI)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            f"result=module._run_bootstrap_apply_process([sys.executable,'-c',{flood_script!r}],30,os.environ.copy())\n"
            "print(json.dumps(result))\n"
        )
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-c", wrapper],
            cwd=self.repo,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertLess(time.monotonic() - started, 10)
        result = json.loads(proc.stdout)
        self.assertTrue(result["output_limit_exceeded"])
        self.assertEqual(result["returncode"], 1)
        self.assertLessEqual(len(result["stdout"].encode()), 1024 * 1024)

    def test_setsid_child_pid_is_reaped_after_timeout(self):
        child_command = (
            f"{shlex.quote(sys.executable)} -c "
            + shlex.quote(
                "import subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)\n"
                "print(child.pid,flush=True)\n"
                "time.sleep(30)\n"
            )
        )
        wrapper = (
            "import importlib.util,json,os,tempfile\n"
            "from pathlib import Path\n"
            f"spec=importlib.util.spec_from_file_location('candidate_engine',{str(CLI)!r})\n"
            "module=importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "with tempfile.TemporaryDirectory() as scratch:\n"
            " env=os.environ.copy()\n"
            f" result=module._run_shell_process({child_command!r},1,Path({str(self.repo)!r}),env,[Path(scratch)])\n"
            " print(json.dumps(result))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", wrapper],
            cwd=self.repo,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["timeout"])
        child_pid = int(result["stdout"].strip())
        self.assertFalse(Path(f"/proc/{child_pid}").exists())

    def test_child_stdin_cannot_reuse_preopened_live_descriptor(self):
        target = self.repo / "stdin-live.txt"
        target.write_bytes(b"original\n")
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)}
        with target.open("r+b") as inherited_stdin:
            proc = subprocess.run(
                [
                    sys.executable, str(CLI), "self-test", "--json",
                    "--command", "printf tampered >&0",
                ],
                cwd=self.repo,
                env=env,
                stdin=inherited_stdin,
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_output_flood_is_bounded_and_rejected(self):
        command = (
            f"{shlex.quote(sys.executable)} -c "
            + shlex.quote(
                "import os\n"
                "chunk=b'x'*(64*1024)\n"
                "for _ in range(200): os.write(1, chunk)\n"
            )
        )
        started = time.monotonic()
        proc = self.run_auto(
            "self-test", "--json", "--timeout", "30", "--command", command,
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertLess(time.monotonic() - started, 10)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["record"]["output_limit_exceeded"])
        self.assertGreater(payload["record"]["stdout_bytes"], 8 * 1024 * 1024)
        self.assertLessEqual(
            payload["record"]["stdout_retained_bytes"], 8 * 1024 * 1024,
        )

    def test_live_chmod_is_detected_and_mode_restored(self):
        target = self.repo / ".research" / "tmp" / "mode-guard.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("guard\n")
        target.chmod(0o640)
        proc = self.run_auto(
            "self-test", "--json", "--command",
            f"chmod 000 {shlex.quote(str(target))}",
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        record = json.loads(proc.stdout)["record"]
        self.assertIn(".research/tmp/mode-guard.txt", record["live_mutation_detected"])

    def test_live_xattr_is_detected_and_restored(self):
        if not all(hasattr(os, name) for name in ("setxattr", "getxattr")):
            self.skipTest("xattrs unavailable")
        target = self.repo / "xattr-guard.txt"
        target.write_text("guard\n")
        attribute = "user.auto_research_guard"
        try:
            os.setxattr(target, attribute, b"before")
        except OSError as exc:
            self.skipTest(f"user xattrs unavailable: {exc}")
        script = (
            "import os; "
            f"os.setxattr({str(target)!r}, {attribute!r}, b'after')"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        proc = self.run_auto("self-test", "--json", "--command", command)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(os.getxattr(target, attribute), b"before")

    def test_live_inode_flags_are_detected_and_restored(self):
        target = self.repo / "inode-flags-guard.txt"
        target.write_text("guard\n")

        def get_flags() -> int:
            fd = os.open(target, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                payload = bytearray(struct.pack("I", 0))
                fcntl.ioctl(fd, 0x80086601, payload, True)
                return struct.unpack("I", payload)[0]
            finally:
                os.close(fd)

        try:
            before = get_flags()
        except OSError as exc:
            self.skipTest(f"inode flags unavailable: {exc}")
        candidate = before ^ 0x40  # FS_NODUMP_FL is unprivileged and reversible.
        script = (
            "import fcntl, os, struct; "
            f"fd=os.open({str(target)!r}, os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW); "
            f"fcntl.ioctl(fd, 0x40086602, struct.pack('I', {candidate})); "
            "os.close(fd)"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        proc = self.run_auto("self-test", "--json", "--command", command)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(get_flags(), before)


class TestDeterministicRunnerCritic(RepoCase):
    def test_deterministic_preflight_cannot_masquerade_as_semantic_B_review(self):
        self.add_node("N1", "before", "L4", ntype="assumption", status="validated")
        st = self.state()
        st["self_test_command"] = "python3 -c \"print('ok')\""
        (self.repo / ".research" / "auto_research.json").write_text(json.dumps(st, indent=2) + "\n")
        self.assertOk(self.run_auto("self-test"))
        self.proposal_link_args()
        tmp = self.repo / ".research" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        proposer = tmp / "det_proposer.py"
        payload = {
            "action": "propose",
            "title": "deterministic critic pass",
            "statement": "visible marker",
            "targets": ["N1"],
            "patch": [{
                "op": "set_node_statement",
                "node": "N1",
                "statement": "visible-verification-marker",
            }],
            "verification": (
                "python3 -c \"print('vacuous exit zero')\" "
                "# .research/auto_research.json visible-verification-marker"
            ),
            "retro": None,
            "inquiry": "INQ-001",
            "recommendation": "REC-1",
        }
        proposer.write_text(
            "import json\n"
            "payload = " + repr(payload) + "\n"
            "print(json.dumps(payload))\n"
        )
        out = self.assertOk(self.run_auto(
            "run", "--epochs", "1", "--proposer-cmd", f"python3 {proposer}",
            "--timeout", "60", "--no-alternate", "--deterministic-critic",
        )).stdout
        self.assertIn("EPOCH 1/1", out)
        self.assertIn("remains proposed", out)
        self.assertIn("independent B", out)
        proposal = self.state()["proposals"]["P-001"]
        self.assertEqual(proposal["status"], "proposed")
        self.assertIsNone(proposal["critic"])
        self.assertIsNone(proposal["verification"])


class TestFinalGoal(RepoCase):
    def test_skeleton_final_goal_is_immutable(self):
        st = self.state()
        self.assertTrue(st["final_goal"]["immutable"])
        self.assertIn("better auto-research", st["final_goal"]["statement"])

    def test_a_brief_and_launch_print_final_goal(self):
        brief = self.assertOk(self.run_auto("a-brief")).stdout
        self.assertIn("FINAL GOAL", brief)
        launch = self.assertOk(self.run_auto(
            "launch", "--harness", "manual", "--role", "A", "--dry-run",
        )).stdout
        self.assertIn("FINAL GOAL", launch)

    def test_runner_prompt_has_final_goal_and_file_tools(self):
        script = r"""
import sys
sys.path.insert(0, "tools")
import auto_research as a
state = a.load_state()
prompt = a.run_proposer_prompt(state, "A")
assert "IMMUTABLE FINAL GOAL" in prompt
assert "Read, Bash, and Edit tools" in prompt
print("final-goal-tools-ok")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("final-goal-tools-ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
