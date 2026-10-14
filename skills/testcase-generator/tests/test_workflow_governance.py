import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))
MAIN_PATH = SCRIPTS_DIR / 'main.py'
SPEC = importlib.util.spec_from_file_location('testcase_generator_main_governance', MAIN_PATH)
MAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAIN)


class WorkflowGovernanceTests(unittest.TestCase):
    def _output_dir(self, root):
        output_dir = Path(root) / '测试分析'
        output_dir.mkdir()
        return output_dir

    def test_skill_stops_instead_of_copying_repo_for_codegraph_mismatch(self):
        skill_text = (SCRIPTS_DIR.parent / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('Codegraph 维护不是需求分析、测试点或 XMind 请求的隐含授权', skill_text)
        self.assertIn('保持 `blocked_index_mismatch` 并停止', skill_text)
        self.assertIn('不得为了绕过门禁自动 clone 仓库、创建 worktree、复制 `.codegraph`', skill_text)
        self.assertIn('预计运行超过 60 秒、写入超过 200 MB', skill_text)
    def test_normalize_moves_process_files_and_restores_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = self._output_dir(temp)
            internal_dir = output_dir / '.ai-internal'
            internal_dir.mkdir()
            (output_dir / '遗留测试点.json').write_text('{}', encoding='utf-8')
            (output_dir / '.code_analysis_cache_demo.json').write_text('{}', encoding='utf-8')
            (internal_dir / '需求分析.md').write_text('# report', encoding='utf-8')

            MAIN._normalize_output_layout(output_dir)

            self.assertTrue((internal_dir / '遗留测试点.json').exists())
            self.assertTrue((internal_dir / '.code_analysis_cache_demo.json').exists())
            self.assertTrue((output_dir / '需求分析.md').exists())
            self.assertFalse((internal_dir / '需求分析.md').exists())

    def test_layout_gate_accepts_only_final_files_in_root(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = self._output_dir(temp)
            internal_dir = output_dir / '.ai-internal'
            internal_dir.mkdir()
            for name in ('需求分析.md', '详细测试点.md', '测试点生成报告.md'):
                (output_dir / name).write_text('# ok', encoding='utf-8')
            for name in ('requirement_facts.json', 'test_design_model.json', 'coverage_matrix.json', 'testpoints.json', 'quality_gate_report.json'):
                (internal_dir / name).write_text('{}', encoding='utf-8')

            report = MAIN._validate_output_layout(output_dir, 'testpoint', required_files=(
                '需求分析.md', '详细测试点.md', '测试点生成报告.md',
                '.ai-internal/requirement_facts.json', '.ai-internal/test_design_model.json',
                '.ai-internal/coverage_matrix.json', '.ai-internal/testpoints.json',
                '.ai-internal/quality_gate_report.json',
            ))

            self.assertEqual('passed', report['status'])
            self.assertTrue((internal_dir / 'artifact_manifest.json').exists())

    def test_testpoint_guard_requires_review_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = self._output_dir(temp)
            internal_dir = output_dir / '.ai-internal'
            internal_dir.mkdir()
            (output_dir / '需求分析.md').write_text('# analysis', encoding='utf-8')
            payloads = {
                'code_impact.json': {
                    'status': 'completed',
                    'selected_files': [{'path': 'src/A.java'}],
                    'selection_evidence': [{'path': 'src/A.java', 'reason': 'requirement'}],
                },
                'code_targets.json': {'targets': [{'target_id': 'service', 'status': 'completed', 'analysis_mode': 'development', 'analysis': {'files_changed': ['src/A.java'], 'entry_points': ['submit']}}]},
                'analysis_context.json': {'project_info': {}},
                'requirement_facts.json': {'items': []},
                'analysis_quality_gate.json': {'summary': {'status': 'passed'}},
                'requirement_code_comparison.json': {'summary': {}, 'items': []},
            }
            for name, payload in payloads.items():
                (internal_dir / name).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding='utf-8'
                )

            with self.assertRaisesRegex(RuntimeError, '评审报告'):
                MAIN._require_testpoint_inputs(output_dir, {'files_changed': ['src/A.java']})

            (output_dir / '需求_评审报告.md').write_text('# review', encoding='utf-8')
            (output_dir / '代码评审报告.md').write_text('# code review', encoding='utf-8')
            (internal_dir / '需求_评审数据.json').write_text('{}', encoding='utf-8')
            from business_impact_translator import translate_business_impacts
            from business_impact_gate import assess_business_impact_gate
            targets = payloads['code_targets.json']['targets']
            impact = translate_business_impacts({}, targets, {}, {})
            gate = assess_business_impact_gate(impact, targets)
            enhanced = {
                'decision_register.json': {'schema_version': '1.0.0', 'items': []},
                'business_impact.json': impact,
                'business_impact_gate.json': gate,
                'codegraph_query_plan.json': {},
                'codegraph_impact.json': {},
                'uat_merge_gate.json': {},
            }
            for name, payload in enhanced.items():
                (internal_dir / name).write_text(json.dumps(payload), encoding='utf-8')
            MAIN._require_testpoint_inputs(output_dir, {'files_changed': ['src/A.java']})

    def test_review_title_expands_table_fields_and_puts_data_in_note(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = self._output_dir(temp)
            point = {
                'title': 'totalShouldCapi按company_partner_pay.stock_capital精确求和',
                'category': '取值优先级',
                'type': '功能',
                'priority': 'P1',
                'coverage_ids': ['RQ-001'],
                'steps': '1. 准备两条company_partner_pay.stock_capital\n2. 调用接口',
                'expected': 'totalShouldCapi等于两条金额之和。',
            }

            path = MAIN._testpoint._write_detailed_testpoints_md(
                output_dir, [point], {'project_name': '测试需求'}
            )
            content = path.read_text(encoding='utf-8')

            self.assertIn(point['title'], content)
            self.assertIn('笔记：覆盖依据：RQ-001', content)
            self.assertIn('测试数据：准备两条company_partner_pay.stock_capital', content)

            bad_point = dict(point)
            bad_point['title'] = '主表认缴为0时执行比例公式'
            with self.assertRaisesRegex(RuntimeError, '未展开术语'):
                MAIN._testpoint._write_detailed_testpoints_md(
                    output_dir, [bad_point], {'project_name': '测试需求'}
                )

    def test_xmind_groups_points_by_variable_then_type(self):
        category = 'partners.totalShouldCapi（认缴累计金额）'
        points = [
            {
                'title': '功能测试点',
                'category': category,
                'type': '功能',
                'priority': 'P1',
                'steps': '1. 准备数据',
                'expected': '返回正确结果。',
            },
            {
                'title': '异常测试点',
                'category': category,
                'type': '异常',
                'priority': 'P1',
                'steps': '1. 准备异常数据',
                'expected': '返回明确异常结果。',
            },
        ]

        groups = MAIN._testpoint._convert_testpoints_to_xmind(points)

        self.assertEqual(1, len(groups))
        self.assertEqual(f'{category}（2个测试点）', groups[0]['title'])
        type_titles = [
            child['title'] for child in groups[0]['children']['attached']
        ]
        self.assertEqual(['功能场景（1个测试点）', '异常场景（1个测试点）'], type_titles)
    def test_review_xmind_kym_uses_curated_readable_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            output_dir = self._output_dir(temp)
            internal_dir = output_dir / '.ai-internal'
            internal_dir.mkdir()
            metadata = {
                'review_metadata': {
                    'core_change': '明确核心变更',
                    'tables': 'company_base、company_partner',
                    'interfaces': 'NS004',
                }
            }
            (internal_dir / 'curated_testpoints.json').write_text(
                json.dumps(metadata, ensure_ascii=False), encoding='utf-8'
            )
            point = {
                'title': 'totalShouldCapi取company_partner.stock_capital',
                'category': '取值优先级',
                'type': '功能',
                'priority': 'P1',
                'steps': '1. 准备数据',
                'expected': '返回指定字段。',
            }

            review_path, _ = MAIN._testpoint.generate_xmind_review(
                temp,
                output_dir,
                {
                    'project_name': '测试需求',
                    'solution': '| --- | **接口** |',
                    'tables': ['错误表'],
                    'interfaces': [],
                    'jobs': [],
                },
                [point],
            )
            gate = MAIN._testpoint._validate_xmind_testpoint_count(
                output_dir, Path(review_path), 1
            )
            self.assertEqual('passed', gate['status'])
            self.assertEqual(1, gate['actual_count'])
            with zipfile.ZipFile(review_path, 'r') as archive:
                content = json.loads(archive.read('content.json').decode('utf-8'))
            titles = []

            def walk(topic):
                titles.append(topic.get('title', ''))
                for child in topic.get('children', {}).get('attached', []):
                    walk(child)

            walk(content[0]['rootTopic'])
            self.assertIn('核心变更：明确核心变更', titles)
            self.assertIn('涉及表：company_base、company_partner', titles)
            self.assertIn('涉及接口：NS004', titles)



    def test_all_active_cli_entrypoints_configure_utf8(self):
        skills_root = Path(__file__).resolve().parents[2]
        violations = []
        entrypoint_count = 0
        for script in skills_root.rglob("*.py"):
            relative_parts = script.relative_to(skills_root).parts
            if ".system" in relative_parts or "tests" in relative_parts:
                continue
            source = script.read_text(encoding="utf-8")
            if "__main__" not in source:
                continue
            entrypoint_count += 1
            configured = (
                'reconfigure(encoding="utf-8"' in source
                or "configure_utf8_stdio()" in source
            )
            if not configured:
                violations.append(str(script))

        self.assertGreater(entrypoint_count, 0, "未发现活动 CLI 入口")
        self.assertEqual([], violations, f"CLI 入口缺少 UTF-8 初始化: {violations}")

if __name__ == '__main__':
    unittest.main()
