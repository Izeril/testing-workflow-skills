import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'main.py'
SPEC = importlib.util.spec_from_file_location('testcase_generator_main', MAIN_PATH)
MAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAIN)


class CodeTargetTests(unittest.TestCase):
    def _write_doc(self, root, name, content):
        path = root / name
        path.write_text(content, encoding='utf-8')
        return path

    def test_multi_target_without_registry_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            java_doc = self._write_doc(root, '提测-java.md', '涉及服务：tax-collect\n涉及开发分支：feat_260624_tax_pwd_reset_limit_lzj')
            python_doc = self._write_doc(root, '提测-python.md', '涉及服务：python-etax、plus-python-etax\n涉及开发分支：dev_cgl_0624_mima')
            targets, errors = MAIN.build_code_targets([java_doc, python_doc], root, {'path': 'D:/fixtures/default-repo', 'baseline': 'master'})

            self.assertEqual([], errors)
            self.assertEqual({'blocked'}, {target['status'] for target in targets})
            self.assertTrue(all('repo_path' not in target for target in targets))

    def test_registry_maps_each_target_by_service_and_language(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            java_doc = self._write_doc(root, '提测-java.md', '涉及服务：tax-collect\n涉及开发分支：feat_260624_tax_pwd_reset_limit_lzj')
            python_doc = self._write_doc(root, '提测-python.md', '涉及服务：python-etax\n涉及开发分支：dev_cgl_0624_mima')
            config = {'baseline': 'master', 'targets': [
                {'key': 'java-tax', 'path': 'D:/repos/java', 'languages': ['java'], 'services': ['tax-collect']},
                {'key': 'python-etax', 'path': 'D:/repos/python', 'languages': ['python'], 'services': ['python-etax']},
            ]}
            targets, errors = MAIN.build_code_targets([java_doc, python_doc], root, config)

            self.assertEqual([], errors)
            self.assertEqual({'D:/repos/java', 'D:/repos/python'}, {target['repo_path'] for target in targets})
            self.assertEqual({'registry'}, {target['mapping_source'] for target in targets})

    def test_repository_language_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'pom.xml').write_text('<project/>', encoding='utf-8')
            (root / 'pyproject.toml').write_text('[project]', encoding='utf-8')
            self.assertEqual(['java', 'python'], MAIN.detect_repository_languages(str(root)))

    def test_language_mismatch_is_blocked_before_branch_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            (root / 'pyproject.toml').write_text('[project]', encoding='utf-8')
            result = MAIN.preflight_code_target({
                'target_id': 'java-target', 'repo_path': str(root), 'language': 'java',
                'branch': 'feat_demo', 'baseline': 'master', 'analysis_mode': 'development', 'status': 'pending',
            })
            self.assertEqual('blocked', result['status'])
            self.assertIn('语言不匹配', result['block_reason'])

    def test_runtime_config_is_loaded_from_skill_directory_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill_dir = root / 'shared-skill'
            project_dir = root / 'project'
            skill_dir.mkdir()
            (project_dir / '.skill').mkdir(parents=True)
            (skill_dir / 'config.yaml').write_text('repo:\n  path: bundled-repo\n  baseline: bundled-base\n', encoding='utf-8')
            (project_dir / '.skill' / 'config.yaml').write_text('repo:\n  path: legacy-project-repo\n', encoding='utf-8')

            with patch.object(MAIN, 'SKILL_DIR', skill_dir):
                config = MAIN.load_config(project_dir)

            self.assertEqual('bundled-repo', config['repo']['path'])
            self.assertEqual('bundled-base', config['repo']['baseline'])


    def test_bundled_default_analysis_mode_is_uat(self):
        config = MAIN.load_config()
        self.assertEqual('uat', config['repo']['analysis_mode'])
        self.assertTrue(config['repo']['refresh_remote_refs'])

if __name__ == '__main__':
    unittest.main()
