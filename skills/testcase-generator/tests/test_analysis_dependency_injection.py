import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_analysis_facade as facade
import workflow_analysis_legacy as legacy
import workflow_common


class _InjectedStop(RuntimeError):
    pass


class AnalysisDependencyInjectionTests(unittest.TestCase):
    def test_facade_passes_explicit_dependencies_without_mutating_legacy(self):
        originals = {
            "build_code_targets": legacy.build_code_targets,
            "preflight_code_target": legacy.preflight_code_target,
            "build_requirement_facts": legacy.build_requirement_facts,
            "build_code_evidence": legacy.build_code_evidence,
        }

        with patch.object(
            facade,
            "_legacy_run_requirement_analysis",
            side_effect=_InjectedStop("captured"),
        ) as runner:
            with self.assertRaises(_InjectedStop):
                facade.run_requirement_analysis(Path("req"), Path("out"), repo_config={"legacy_workflow": True})

        dependencies = runner.call_args.kwargs["analysis_dependencies"]
        self.assertIs(facade.build_code_targets, dependencies["build_code_targets"])
        self.assertIs(facade.preflight_code_target, dependencies["preflight_code_target"])
        self.assertIs(facade.build_requirement_facts, dependencies["build_requirement_facts"])
        self.assertIs(facade.build_code_evidence, dependencies["build_code_evidence"])
        for name, value in originals.items():
            self.assertIs(value, getattr(legacy, name))


class ConfigCacheIsolationTests(unittest.TestCase):
    def tearDown(self):
        workflow_common.clear_config_cache()

    def test_config_cache_is_keyed_by_project_and_returns_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()

            def fake_load(project_dir=None):
                return {"project_dir": str(Path(project_dir).resolve()) if project_dir else "default"}

            workflow_common.clear_config_cache()
            with patch.object(workflow_common, "load_config", side_effect=fake_load) as loader:
                first_config = workflow_common.get_config(first)
                first_config["mutated"] = True
                first_again = workflow_common.get_config(first)
                second_config = workflow_common.get_config(second)

            self.assertEqual(2, loader.call_count)
            self.assertNotIn("mutated", first_again)
            self.assertNotEqual(first_again["project_dir"], second_config["project_dir"])

    def test_force_reload_refreshes_one_cache_key(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            workflow_common.clear_config_cache()
            with patch.object(
                workflow_common,
                "load_config",
                side_effect=({"version": 1}, {"version": 2}),
            ) as loader:
                self.assertEqual(1, workflow_common.get_config(project)["version"])
                self.assertEqual(2, workflow_common.get_config(project, force_reload=True)["version"])
            self.assertEqual(2, loader.call_count)


if __name__ == "__main__":
    unittest.main()
