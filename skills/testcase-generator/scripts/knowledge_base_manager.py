#!/usr/bin/env python3
"""
知识库管理器 - 初始化、入库、华库提炼、进化指标追踪
"""

import os
import re
import sys
import json
import yaml
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from workflow_logging import log_console


class KnowledgeBaseManager:
    """知识库管理器"""

    # 华库文件定义
    ESSENCE_FILES = {
        'common_root_causes': '高频根因 Top 10',
        'common_omissions': '高频遗漏场景 Top 10',
        'improvement_patterns': '有效改进措施 Top 10'
    }

    # 进化指标定义
    EVOLUTION_METRICS = [
        '场景遗漏率',
        '评审通过率',
        '评审建议采纳率',
        '用例细化通过率'
    ]

    def __init__(self, project_root: str = None, skill_dir: str = None):
        """
        初始化知识库管理器

        Args:
            project_root: 项目根目录
            skill_dir: Skill 目录路径
        """
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.skill_dir = Path(skill_dir) if skill_dir else self._find_skill_dir()

        # 定位知识库路径
        self.kb_path = self._locate_kb_path()

    def _find_skill_dir(self) -> Path:
        """查找当前 Codex testcase-generator Skill 目录。"""
        candidates = [Path(__file__).resolve().parents[1]]
        for start in (self.project_root, Path.cwd()):
            current = Path(start).resolve()
            for parent in (current, *current.parents):
                candidates.append(parent / ".codex" / "skills" / "testcase-generator")

        seen = set()
        for skill_path in candidates:
            normalized = skill_path.resolve()
            if normalized in seen:
                continue
            seen.add(normalized)
            if (normalized / "SKILL.md").is_file():
                return normalized
        raise FileNotFoundError("未找到 testcase-generator Skill 目录")

    def _locate_kb_path(self) -> Optional[Path]:
        """
        定位知识库路径

        Returns:
            知识库路径，未初始化时返回 None
        """
        skill_dir = self.project_root / '.skill'
        if not skill_dir.exists():
            return None

        # 检查配置文件
        config_file = skill_dir / 'project.yaml'
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            kb_config = config.get('knowledge_base', {})

            # 优先使用绝对路径
            if kb_config.get('absolute_path'):
                return Path(kb_config['absolute_path'])

            # 使用相对路径
            relative_path = kb_config.get('relative_path', '.skill/knowledge-base')
            return self.project_root / relative_path

        return None

    def is_initialized(self) -> bool:
        """检查项目是否已初始化"""
        return self.kb_path is not None and self.kb_path.exists()

    def initialize(self, team: str = '', product: str = '', module_path: str = '') -> Path:
        """
        初始化知识库

        Args:
            team: 项目组
            product: 产品名称
            module_path: 模块路径

        Returns:
            知识库路径
        """
        # 创建 .skill 目录
        skill_dir = self.project_root / '.skill'
        skill_dir.mkdir(parents=True, exist_ok=True)

        # 复制知识库模板
        template_dir = self.skill_dir / 'templates' / 'knowledge-base-template'
        kb_dir = skill_dir / 'knowledge-base'

        if template_dir.exists():
            shutil.copytree(template_dir, kb_dir, dirs_exist_ok=True)
        else:
            # 手动创建目录结构
            kb_dir.mkdir(parents=True, exist_ok=True)
            for subdir in ['patterns', 'patterns/精华库', 'requirements', 'review-reports',
                          'tech-solutions', 'testcases']:
                (kb_dir / subdir).mkdir(parents=True, exist_ok=True)

        # 复制配置模板
        config_template = self.skill_dir / 'templates' / 'project.yaml'
        config_file = skill_dir / 'project.yaml'

        if config_template.exists():
            shutil.copy(config_template, config_file)

        # 更新配置
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

            config.setdefault('project', {})['name'] = self.project_root.name
            config.setdefault('defaults', {})
            if team:
                config['defaults']['team'] = team
            if product:
                config['defaults']['product'] = product
            if module_path:
                config['defaults']['modulePath'] = module_path

            with open(config_file, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        # 更新知识库路径
        self.kb_path = kb_dir

        return kb_dir

    def check_duplicate_archive(self, doc_name: str, doc_type: str, date: str) -> Dict[str, bool]:
        """
        检查重复入库

        Args:
            doc_name: 文档名称
            doc_type: 文档类型
            date: 日期

        Returns:
            各文件是否已存在
        """
        if not self.kb_path:
            return {}

        results = {}

        # 检查各目录
        check_paths = {
            'requirement': self.kb_path / 'requirements' / f'{doc_name}_{date}.md',
            'review_report': self.kb_path / 'review-reports' / f'{doc_name}_{date}_评审报告.md',
            'tech_solution': self.kb_path / 'tech-solutions' / f'TR_{doc_name}_{date}.md',
            'testpoint_json': self.kb_path / 'testcases' / f'{doc_name}_{date}_测试点.json',
            'testcase_json': self.kb_path / 'testcases' / f'{doc_name}_{date}_测试用例.json',
            'testcase_excel': self.kb_path / 'testcases' / f'{doc_name}_{date}_测试用例.xlsx'
        }

        for name, path in check_paths.items():
            results[name] = path.exists()

        return results

    def archive_file(self, source: Path, target_dir: str, new_name: str) -> Path:
        """
        入库文件

        Args:
            source: 源文件路径
            target_dir: 目标目录名
            new_name: 新文件名

        Returns:
            目标文件路径
        """
        if not self.kb_path:
            raise ValueError("知识库未初始化")

        target_path = self.kb_path / target_dir / new_name

        # 复制文件
        shutil.copy2(source, target_path)

        return target_path

    def update_evolution_log(self, record: Dict) -> None:
        """
        更新进化日志

        Args:
            record: 入库记录
        """
        if not self.kb_path:
            return

        log_file = self.kb_path / 'EVOLUTION_LOG.md'

        if not log_file.exists():
            # 创建默认日志文件
            self._create_evolution_log()

        # 读取现有内容
        with open(log_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 追加记录
        new_record = self._format_evolution_record(record)

        # 在"近期记录"部分追加
        if '## 📝 近期记录' in content:
            # 找到近期记录部分的结束位置
            parts = content.split('## 📝 近期记录')
            if len(parts) >= 2:
                # 在第一个近期记录条目后追加
                recent_part = parts[1]
                # 找到下一个 ## 标记前的位置
                next_section_start = recent_part.find('\n## ')
                if next_section_start > 0:
                    new_content = parts[0] + '## 📝 近期记录' + \
                                 new_record + recent_part[next_section_start:] + \
                                 '\n'.join(parts[2:])
                else:
                    new_content = parts[0] + '## 📝 近期记录' + new_record + recent_part
            else:
                new_content = content + '\n' + new_record
        else:
            new_content = content + '\n' + new_record

        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(new_content)

    def _create_evolution_log(self) -> None:
        """创建默认进化日志"""
        if not self.kb_path:
            return

        log_file = self.kb_path / 'EVOLUTION_LOG.md'
        template_file = self.skill_dir / 'templates' / 'knowledge-base-template' / 'EVOLUTION_LOG.md'

        if template_file.exists():
            shutil.copy(template_file, log_file)
        else:
            # 创建基本结构
            content = """# 进化日志

> 记录每次入库的差异分析、根因归纳、改进措施

## 📈 进化指标趋势

| 序号 | 需求名 | 入库日期 | 场景遗漏率 | 评审通过率 | 评审建议采纳率 | 新增模式数 |
|------|--------|---------|-----------|-----------|--------------|-----------|

## 📝 近期记录（最多5条）

## 📚 历史摘要（按季度归档）
"""
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(content)

    def _format_evolution_record(self, record: Dict) -> str:
        """格式化进化记录"""
        date = record.get('date', datetime.now().strftime('%Y-%m-%d'))
        name = record.get('name', '未知需求')

        # 差异统计
        diff_stats = record.get('diff_stats', {})
        added = diff_stats.get('added', 0)
        deleted = diff_stats.get('deleted', 0)
        modified = diff_stats.get('modified', 0)
        confirmed = diff_stats.get('confirmed', 0)
        total_after = added + confirmed + modified

        # 计算进化指标
        missing_rate = (added / total_after * 100) if total_after > 0 else 0
        total_before = confirmed + deleted + modified
        pass_rate = (confirmed / total_before * 100) if total_before > 0 else 0

        # 根因分析
        root_causes = record.get('root_causes', [])

        record_text = f"""
### 第 N 次：{name}（{date})

#### 📊 测试点对比统计

| 类型 | 数量 | 占比 |
|------|------|------|
| 评审前（AI生成） | {total_before} | - |
| 评审后（用户调整） | {total_after} | - |

#### 本次差异统计

| 类型 | 数量 | 占比 |
|------|------|------|
| ➕ 新增 | {added} | {missing_rate:.1f}% |
| ❌ 删除 | {deleted} | {(deleted/total_before*100) if total_before > 0 else 0:.1f}% |
| 🔄 修改 | {modified} | {(modified/total_before*100) if total_before > 0 else 0:.1f}% |
| ✅ 确认 | {confirmed} | {pass_rate:.1f}% |

#### 🔍 根因分析归纳

| 根因分类 | 占比 | 改进措施 |
|---------|------|---------|
"""
        for cause in root_causes:
            record_text += f"| {cause.get('category', '-')} | {cause.get('ratio', '-')}% | {cause.get('improvement', '-')} |\n"

        if not root_causes:
            record_text += "| - | - | - |\n"

        return record_text

    def update_index(self) -> None:
        """更新知识库索引（自动扫描各目录并生成完整索引）"""
        if not self.kb_path:
            return

        index_file = self.kb_path / 'INDEX.md'

        # 统计各目录文档数
        stats = {}
        for dir_name in ['requirements', 'review-reports', 'tech-solutions', 'testcases', 'patterns']:
            dir_path = self.kb_path / dir_name
            if dir_path.exists():
                count = len([f for f in dir_path.iterdir() if f.is_file() and f.suffix in ['.md', '.json', '.xlsx']])
                stats[dir_name] = count
            else:
                stats[dir_name] = 0

        # 统计进化日志条数
        log_file = self.kb_path / 'EVOLUTION_LOG.md'
        log_count = 0
        if log_file.exists():
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
                log_count = content.count('### 第')

        # ---- 扫描各目录生成详细索引 ----
        req_rows = self._scan_requirements()
        review_rows = self._scan_review_reports()
        tech_rows = self._scan_tech_solutions()
        tc_rows = self._scan_testcases()

        # 生成索引内容
        content = f"""# 知识库索引

> 本索引文件记录知识库中的所有文档

---

## 📊 知识库统计

| 目录 | 文档数 | 最后更新 |
|------|--------|---------|
| requirements/ | {stats.get('requirements', 0)} | {datetime.now().strftime('%Y-%m-%d')} |
| review-reports/ | {stats.get('review-reports', 0)} | {datetime.now().strftime('%Y-%m-%d')} |
| tech-solutions/ | {stats.get('tech-solutions', 0)} | {datetime.now().strftime('%Y-%m-%d')} |
| testcases/ | {stats.get('testcases', 0)} | {datetime.now().strftime('%Y-%m-%d')} |
| patterns/ | {stats.get('patterns', 0)} | 初始化 |
| 进化日志 | {log_count} 条 | {datetime.now().strftime('%Y-%m-%d')} |

---

## 📚 需求文档索引

| 编号 | 名称 | 业务领域 | 关键词 | 入库日期 |
|------|------|---------|--------|---------|
{req_rows}

## 📋 评审报告索引

| 编号 | 名称 | 问题数 | 严重问题 | 入库日期 |
|------|------|--------|---------|---------|
{review_rows}

## 🔧 技术方案索引

| 编号 | 名称 | 接口数 | 表数 | 入库日期 |
|------|------|--------|------|---------|
{tech_rows}

## 🧪 测试用例索引

| 编号 | 名称 | 测试点数 | 用例数 | 入库日期 |
|------|------|---------|--------|---------|
{tc_rows}

---

*更新日期：{datetime.now().strftime('%Y-%m-%d')}*
"""

        with open(index_file, 'w', encoding='utf-8') as f:
            f.write(content)

    def _scan_requirements(self) -> str:
        """扫描需求文档目录"""
        req_dir = self.kb_path / 'requirements'
        if not req_dir.exists():
            return "| - | - | - | - | - |\n"
        rows = []
        for f in sorted(req_dir.glob('*.md')):
            name = f.stem
            # 提取编号（如 001）
            match = re.match(r'(\d+)_', name)
            num = match.group(1) if match else '-'
            # 提取关键词
            keywords = name.replace('_', ' ')[:30]
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d')
            rows.append(f"| {num} | {name[:40]} | - | {keywords} | {mtime} |")
        return '\n'.join(rows) if rows else "| - | - | - | - | - |\n"

    def _scan_review_reports(self) -> str:
        """扫描评审报告目录"""
        rev_dir = self.kb_path / 'review-reports'
        if not rev_dir.exists():
            return "| - | - | - | - | - |\n"
        rows = []
        for f in sorted(rev_dir.glob('*.md')):
            name = f.stem
            match = re.match(r'(\d+)_', name)
            num = match.group(1) if match else '-'
            # 尝试读取文件获取问题统计
            issue_count = '-'
            critical_count = '-'
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                    total_m = re.search(r'问题总数[：:]\s*(\d+)', content)
                    crit_m = re.search(r'严重[：:]\s*(\d+)', content)
                    if total_m: issue_count = total_m.group(1)
                    if crit_m: critical_count = crit_m.group(1)
            except Exception:
                pass
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d')
            rows.append(f"| {num} | {name[:40]} | {issue_count} | {critical_count} | {mtime} |")
        return '\n'.join(rows) if rows else "| - | - | - | - | - |\n"

    def _scan_tech_solutions(self) -> str:
        """扫描技术方案目录"""
        tech_dir = self.kb_path / 'tech-solutions'
        if not tech_dir.exists():
            return "| - | - | - | - | - |\n"
        rows = []
        for f in sorted(tech_dir.glob('*.md')):
            name = f.stem
            match = re.match(r'TR_(\d+)_', name)
            num = match.group(1) if match else '-'
            api_count = '-'
            table_count = '-'
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                    api_m = re.findall(r'(?:接口|API|endpoint)', content, re.IGNORECASE)
                    table_m = re.findall(r'(?:t_\w+|表[名]?\s*[:：])', content)
                    if api_m: api_count = str(len(api_m))
                    if table_m: table_count = str(len(table_m))
            except Exception:
                pass
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d')
            rows.append(f"| {num} | {name[:40]} | {api_count} | {table_count} | {mtime} |")
        return '\n'.join(rows) if rows else "| - | - | - | - | - |\n"

    def _scan_testcases(self) -> str:
        """扫描测试用例目录"""
        tc_dir = self.kb_path / 'testcases'
        if not tc_dir.exists():
            return "| - | - | - | - | - |\n"
        rows = []
        # 按项目名分组
        seen = set()
        for f in sorted(tc_dir.glob('*_测试用例.json')):
            name_parts = f.stem.replace('_测试用例', '').split('_')
            proj_id = name_parts[0] if name_parts else f.stem
            if proj_id in seen:
                continue
            seen.add(proj_id)
            tp_count = '-'
            case_count = '-'
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                    meta = data.get('metadata', {})
                    stats = meta.get('statistics', {})
                    tp_count = str(stats.get('total_points', '-'))
                    case_count = str(stats.get('total_cases', '-'))
            except Exception:
                pass
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d')
            rows.append(f"| {proj_id} | {f.stem[:40]} | {tp_count} | {case_count} | {mtime} |")
        return '\n'.join(rows) if rows else "| - | - | - | - | - |\n"

    def extract_essence(self, root_causes: List[Dict]) -> Dict[str, List]:
        """
        提炼华库

        Args:
            root_causes: 本次根因分析结果

        Returns:
            提炼结果
        """
        if not self.kb_path:
            return {}

        essence_dir = self.kb_path / 'patterns' / '精华库'
        if not essence_dir.exists():
            essence_dir.mkdir(parents=True, exist_ok=True)

        results = {}

        for file_key, file_desc in self.ESSENCE_FILES.items():
            file_path = essence_dir / f'{file_key}.md'

            # 读取现有内容
            existing_items = []
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 解析现有条目（简化解析）
                    existing_items = self._parse_essence_items(content, file_key)

            # 合并新条目
            new_items = self._get_new_essence_items(file_key, root_causes)

            # 统计频率并排序
            all_items = existing_items + new_items
            frequency_map = {}
            for item in all_items:
                key = item.get('category') or item.get('type') or item.get('measure')
                if key:
                    frequency_map[key] = frequency_map.get(key, 0) + 1
                    item['frequency'] = frequency_map[key]

            # 按频率排序，取 Top 10
            sorted_items = sorted(all_items, key=lambda x: x.get('frequency', 0), reverse=True)[:10]

            # 写入文件
            self._write_essence_file(file_path, file_key, file_desc, sorted_items)

            results[file_key] = {
                'added': len(new_items),
                'total': len(sorted_items),
                'operation': '新增' if len(new_items) > 0 else '无变化'
            }

        return results

    def _parse_essence_items(self, content: str, file_key: str) -> List[Dict]:
        """解析华库现有条目（简化版）"""
        items = []
        # 简化解析：查找表格行
        lines = content.split('\n')
        for line in lines:
            if line.startswith('|') and not line.startswith('| 序号') and not line.startswith('|---'):
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 5 and parts[1] and parts[1] != '-':
                    item = {
                        'category': parts[1] if file_key == 'common_root_causes' else None,
                        'type': parts[1] if file_key == 'common_omissions' else None,
                        'measure': parts[1] if file_key == 'improvement_patterns' else None,
                        'frequency': int(parts[2]) if parts[2].isdigit() else 1,
                        'scenario': parts[3] if len(parts) > 3 else '',
                        'improvement': parts[4] if len(parts) > 4 else ''
                    }
                    items.append(item)
        return items

    def _get_new_essence_items(self, file_key: str, root_causes: List[Dict]) -> List[Dict]:
        """获取新的华库条目"""
        items = []

        if file_key == 'common_root_causes':
            for cause in root_causes:
                category = cause.get('category')
                if category:
                    items.append({
                        'category': category,
                        'frequency': 1,
                        'scenario': cause.get('scenario', ''),
                        'improvement': cause.get('improvement', '')
                    })

        elif file_key == 'common_omissions':
            # 从根因推导遗漏类型
            for cause in root_causes:
                category = cause.get('category')
                if category in ['隐性需求识别不足', '边界场景遗漏', '异常分支遗漏']:
                    items.append({
                        'type': category,
                        'frequency': 1,
                        'scenario': cause.get('scenario', ''),
                        'improvement': cause.get('improvement', '')
                    })

        return items

    def _write_essence_file(self, file_path: Path, file_key: str, desc: str, items: List[Dict]) -> None:
        """写入华库文件（保留已丰富的内容格式）"""
        title_map = {
            'common_root_causes': '高频根因 Top 10',
            'common_omissions': '高频遗漏场景 Top 10',
            'improvement_patterns': '有效改进措施 Top 10'
        }
        col1_map = {
            'common_root_causes': '根因分类',
            'common_omissions': '遗漏类型',
            'improvement_patterns': '改进措施'
        }

        content = f"""# {title_map[file_key]}

> 记录 {desc}

---

## 🎯 使用说明

- **频率门槛**：出现次数 ≥ 1 写入候选，≥ 3 进入 Top 10
- **Top 10 上限**：超出时更高频率替换最低频率
- **更新时机**：每次入库后自动提炼

---

## 📊 列表

| 序号 | {col1_map[file_key]} | 出现频率 | 典型场景 | 改进措施 |
|------|---------|---------|---------|---------|
"""

        for i, item in enumerate(items, 1):
            key = item.get('category') or item.get('type') or item.get('measure') or '-'
            freq = item.get('frequency', 0)
            scenario = item.get('scenario', '-')
            improvement = item.get('improvement', '-')
            content += f"| {i} | {key} | {freq} | {scenario} | {improvement} |\n"

        # 补充空白行到 10 条
        for i in range(len(items) + 1, 11):
            content += f"| {i} | - | - | - | - |\n"

        content += f"""
---

*更新日期：{datetime.now().strftime('%Y-%m-%d')}*
*总入库次数：{len(items)}*
*下次更新将基于下次入库数据重新排序*
"""

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='知识库管理器')
    parser.add_argument('action', choices=['init', 'check', 'archive', 'update'],
                       help='操作类型')
    parser.add_argument('--project', '-p', help='项目根目录')
    parser.add_argument('--team', '-t', help='项目组')
    parser.add_argument('--product', '-prd', help='产品名称')
    parser.add_argument('--module', '-m', help='模块路径')

    args = parser.parse_args()

    manager = KnowledgeBaseManager(args.project)

    if args.action == 'init':
        kb_path = manager.initialize(args.team or '', args.product or '', args.module or '')
        log_console(f"知识库初始化完成: {kb_path}")

    elif args.action == 'check':
        if manager.is_initialized():
            log_console(f"知识库已初始化: {manager.kb_path}")
        else:
            log_console("知识库未初始化")

    elif args.action == 'archive':
        log_console("请通过 AI 入库流程操作")

    elif args.action == 'update':
        manager.update_index()
        log_console("索引已更新")


if __name__ == '__main__':
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    main()
