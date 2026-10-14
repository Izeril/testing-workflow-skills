import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import business_impact_gate
import business_impact_translator
import code_analysis_admission
import codegraph_impact_provider
import git_snapshot_manager
import requirement_commit_scope
import uat_merge_gate
import workflow_testpoint_facade


class AdmissionAndSnapshotTests(unittest.TestCase):
    def test_shared_branch_without_scope_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".git").mkdir()
            result = code_analysis_admission.assess_code_analysis_admission(
                {
                    "target_id": "shared",
                    "repo_path": str(repo),
                    "branch": "feat/shared",
                    "baseline": "origin/master",
                    "analysis_mode": "development",
                    "shared_branch": True,
                }
            )
        self.assertEqual("blocked_ambiguous_commit_scope", result["status"])

    def test_formal_snapshot_fetches_dev_master_and_uat_together(self):
        shas = {
            "origin/feature": "a" * 40,
            "origin/master": "b" * 40,
            "origin/uat_merge": "c" * 40,
        }

        def fake_git(_repo, args, timeout=90):
            if args[0] == "rev-parse":
                ref = args[-1].removesuffix("^{commit}")
                return 0, shas.get(ref, "") + "\n", ""
            if args[0] == "fetch":
                return 0, "", ""
            return 1, "", "unexpected"

        with patch.object(git_snapshot_manager, "_git", side_effect=fake_git) as git_call:
            snapshot = git_snapshot_manager.capture_git_snapshot(
                {
                    "target_id": "formal",
                    "repo_path": "repo",
                    "branch": "feature",
                    "baseline": "master",
                    "uat_branch": "uat_merge",
                    "analysis_mode": "uat",
                    "refresh_remote_refs": True,
                }
            )

        fetch_args = next(call.args[1] for call in git_call.call_args_list if call.args[1][0] == "fetch")
        self.assertIn("+refs/heads/feature:refs/remotes/origin/feature", fetch_args)
        self.assertIn("+refs/heads/master:refs/remotes/origin/master", fetch_args)
        self.assertIn("+refs/heads/uat_merge:refs/remotes/origin/uat_merge", fetch_args)
        self.assertTrue(snapshot["formal_ready"])

    def test_failed_fetch_with_changed_ref_is_partial_and_not_formal(self):
        before = {
            "origin/feature": "a" * 40,
            "origin/master": "b" * 40,
            "origin/uat_merge": "c" * 40,
        }
        after = dict(before, **{"origin/feature": "d" * 40})
        state = {"fetched": False}

        def fake_git(_repo, args, timeout=90):
            if args[0] == "fetch":
                state["fetched"] = True
                return 1, "", "network interrupted"
            if args[0] == "rev-parse":
                ref = args[-1].removesuffix("^{commit}")
                values = after if state["fetched"] else before
                return 0, values[ref] + "\n", ""
            return 1, "", ""

        with patch.object(git_snapshot_manager, "_git", side_effect=fake_git):
            snapshot = git_snapshot_manager.capture_git_snapshot(
                {
                    "repo_path": "repo",
                    "branch": "feature",
                    "baseline": "master",
                    "uat_branch": "uat_merge",
                    "analysis_mode": "uat",
                    "refresh_remote_refs": True,
                }
            )
        self.assertTrue(snapshot["partial_update"])
        self.assertFalse(snapshot["formal_ready"])
        self.assertEqual("failed", snapshot["fetch_status"])


class ScopeAndUatGateTests(unittest.TestCase):
    def test_explicit_commit_range_is_used_without_keyword_filtering(self):
        def fake_git(_repo, args, timeout=60):
            if args[0] == "rev-list":
                return 0, "1" * 40 + "\n", ""
            if args[0] == "diff":
                return 0, "M\tsrc/A.java\nA\tsrc/B.java\n", ""
            if args[0] == "merge-base":
                return 0, "0" * 40 + "\n", ""
            return 1, "", ""

        with patch.object(requirement_commit_scope, "_git", side_effect=fake_git):
            scope = requirement_commit_scope.resolve_requirement_commit_scope(
                "repo",
                {"target_id": "t", "commit_range": "abc..def"},
                {"baseline_ref": "origin/master", "development_ref": "origin/feature"},
            )
        self.assertEqual("explicit_commit_range", scope["strategy"])
        self.assertEqual({"src/A.java", "src/B.java"}, {item["path"] for item in scope["raw_changed_files"]})

    def test_directly_merged_commits_are_fully_merged(self):
        def fake_git(_repo, args, timeout=90):
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return 0, "", ""
            if args[0] == "cherry":
                return 0, "", ""
            if args[0] == "rev-list":
                return 0, "", ""
            if args[:2] == ["diff", "--quiet"]:
                return 0, "", ""
            return 0, "", ""

        with patch.object(uat_merge_gate, "_git", side_effect=fake_git), patch.object(
            uat_merge_gate, "_patch_id", return_value=""
        ):
            gate = uat_merge_gate.assess_uat_merge(
                "repo",
                {
                    "target_id": "t",
                    "commits": ["1" * 40],
                    "raw_changed_files": [{"path": "src/A.java"}],
                    "comparison_range": "origin/master...origin/feature",
                },
                {
                    "development_ref": "origin/feature",
                    "baseline_ref": "origin/master",
                    "uat_ref": "origin/uat_merge",
                    "development_sha": "a" * 40,
                    "uat_sha": "b" * 40,
                },
            )
        self.assertEqual("fully_merged", gate["status"])
        self.assertTrue(gate["formal_ready"])


class CodegraphAndBusinessImpactTests(unittest.TestCase):
    def test_strict_codegraph_rejects_index_sha_mismatch(self):
        plan = {
            "targets": [
                {
                    "target_id": "t",
                    "target_sha": "a" * 40,
                    "indexed_sha": "b" * 40,
                    "index_aligned": False,
                    "codegraph_policy": "strict",
                    "max_files": 30,
                }
            ]
        }
        result = codegraph_impact_provider.validate_codegraph_results(plan, {})
        self.assertEqual("blocked_index_mismatch", result["targets"][0]["status"])

    def test_codegraph_accepts_bounded_aligned_result(self):
        sha = "a" * 40
        plan = {
            "targets": [
                {
                    "target_id": "t",
                    "target_sha": sha,
                    "indexed_sha": sha,
                    "index_aligned": True,
                    "max_files": 30,
                }
            ]
        }
        result = codegraph_impact_provider.validate_codegraph_results(
            plan,
            {
                "targets": [
                    {
                        "target_id": "t",
                        "target_sha": sha,
                        "relations": [{"caller": "A", "callee": "B", "hop": 1}],
                        "files": ["A.java", "B.java"],
                    }
                ]
            },
        )
        self.assertEqual("ready", result["targets"][0]["status"])

    def test_translator_emits_confirmed_traceable_impact(self):
        requirement_facts = {
            "items": [
                {
                    "fact_id": "RQ-001",
                    "dimension": "scenarios",
                    "content": "提交申报",
                }
            ]
        }
        target = {
            "target_id": "t",
            "analysis_mode": "uat",
            "services": ["申报服务"],
            "development_sha": "a" * 40,
            "uat_sha": "b" * 40,
            "uat_merge_gate": {"status": "fully_merged"},
            "selected_files": [{"path": "ApplyService.java"}],
            "analysis": {
                "files_changed": ["ApplyService.java"],
                "api_endpoints": [{"path": "/apply", "name": "提交申报"}],
                "business_rules": ["待申报状态允许提交"],
                "sql_analysis": ["update apply_task set status = 'PROCESSING'"],
                "technology_coverage": {"complete": True, "gaps": []},
            },
        }
        graph = {
            "targets": [
                {
                    "target_id": "t",
                    "status": "ready",
                    "relations": [{"caller": "ApplyController", "callee": "ApplyService", "hop": 1}],
                    "files": ["ApplyController.java"],
                }
            ]
        }
        payload = business_impact_translator.translate_business_impacts(
            requirement_facts,
            [target],
            graph,
            {"project_name": "申报优化"},
        )
        impact = payload["items"][0]
        self.assertEqual("confirmed", impact["confidence"])
        self.assertTrue(any(value.startswith("CE-") for value in impact["evidence_ids"]))
        self.assertIn("RQ-001", impact["evidence_ids"])

    def test_formal_gate_blocks_unready_codegraph(self):
        impact = {
            "items": [
                {
                    "impact_id": "BI-001",
                    "business_domain": "申报",
                    "affected_actor": ["企业"],
                    "business_scenario": "提交",
                    "trigger": "接口",
                    "preconditions": ["待申报"],
                    "new_behavior": "允许提交",
                    "confidence": "high",
                    "evidence_ids": ["RQ-001", "CE-001"],
                }
            ]
        }
        targets = [
            {
                "target_id": "t",
                "analysis_mode": "uat",
                "uat_merge_gate": {"status": "fully_merged"},
                "codegraph_status": "awaiting_mcp_result",
                "codegraph_policy": "strict",
                "analysis": {"files_changed": ["ApplyService.java"], "technology_coverage": {"complete": True, "gaps": []}},
            }
        ]
        gate = business_impact_gate.assess_business_impact_gate(impact, targets, True)
        self.assertEqual("blocked", gate["summary"]["status"])
        self.assertTrue(any(item["gate_id"] == "BI-G4" and item["blocking"] for item in gate["gates"]))

    def test_business_impact_generates_positive_negative_and_resilience_points(self):
        points = workflow_testpoint_facade._impact_testpoints(
            {
                "items": [
                    {
                        "impact_id": "BI-001",
                        "business_scenario": "提交申报",
                        "trigger": "提交接口",
                        "preconditions": ["企业处于待申报状态"],
                        "new_behavior": "提交后进入受理中状态",
                        "exception_impact": ["下游超时"],
                        "confidence": "confirmed",
                        "evidence_ids": ["RQ-001", "CE-001", "CG-001"],
                    }
                ]
            }
        )
        self.assertEqual(3, len(points))
        self.assertTrue(all("BI-001" in point["coverage_ids"] for point in points))


if __name__ == "__main__":
    unittest.main()
