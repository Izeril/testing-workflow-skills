"""Observable parity of the auditor workflow API against both archived skills.

All repositories in this suite are disposable synthetic Git fixtures. Imports
are scoped and restored because the archived analyzer has flat module names,
whereas the auditor uses relative imports under its own ``scripts`` package.
"""

import copy
import importlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


SKILLS_DIR = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = SKILLS_DIR.parent / "skills-archive"
REFERENCE_ANALYZER = ARCHIVE_DIR / "code-analyzer"
REFERENCE_COMPARATOR = ARCHIVE_DIR / "req-code-comparator"
AUDITOR = SKILLS_DIR / "requirement-code-auditor"


@contextmanager
def isolated_skill_module(skill_dir, module_name):
    """Restore imports and search paths even if another suite loaded old names."""
    original_path = list(sys.path)
    original_modules = dict(sys.modules)
    collision_names = {
        path.stem
        for root in (REFERENCE_ANALYZER, REFERENCE_COMPARATOR)
        for path in root.glob("*.py")
    }
    for name in list(sys.modules):
        if name in collision_names or name == "scripts" or name.startswith("scripts."):
            sys.modules.pop(name, None)
    sys.path.insert(0, str(skill_dir))
    try:
        yield importlib.import_module(module_name)
    finally:
        sys.path[:] = original_path
        for name in set(sys.modules) - set(original_modules):
            sys.modules.pop(name, None)
        for name, module in original_modules.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module


def call_skill(skill_dir, module_name, function, *args, **kwargs):
    with isolated_skill_module(skill_dir, module_name) as module:
        return copy.deepcopy(getattr(module, function)(*args, **kwargs))


def compare_both(requirement, code, dimensions=None):
    args = (requirement, code)
    kwargs = {"dimensions": dimensions} if dimensions is not None else {}
    old = call_skill(REFERENCE_COMPARATOR, "comparator", "compare_structured", *args, **kwargs)
    new = call_skill(AUDITOR, "scripts.workflow_api", "compare_structured", *args, **kwargs)
    return old, new


CONTROLLER = '''
@RestController
public class OrderController {
    private final OrderService orderService;
    @GetMapping("/orders")
    public ResponseEntity<OrderVO> getOrder(Long id) {
        return ResponseEntity.ok(orderService.getOrder(id));
    }
}
'''
SERVICE = '''
public class OrderService {
    private final OrderMapper orderMapper;
    @Value("${orders.page.limit:50}")
    private Integer pageLimit;
    public static final String MODE = "READY";
    public OrderVO getOrder(Long id) {
        if (id == null) {
            throw new IllegalArgumentException("order id required");
        }
        validateId(id);
        OrderEntity order = orderMapper.findOrder(id);
        OrderVO vo = new OrderVO();
        vo.setTotal(order.getAmount());
        return vo;
    }
    @XxlJob("orderSync")
    @Scheduled(cron = "0 0 1 * * ?")
    public void syncOrders() {
        getOrder(1L);
    }
}
'''
DTO = '''
public class OrderVO {
    private BigDecimal total;
    private String unused;
}
'''
OUTPUT_MAPPER = '''
<mapper namespace="OrderMapper">
  <resultMap id="orderMap" type="OrderEntity">
    <result column="total_amount" property="amount"/>
  </resultMap>
  <select id="findOrder" resultMap="orderMap">
    SELECT total_amount FROM t_order WHERE id = #{id}
  </select>
</mapper>
'''
DIRECT_CONTROLLER = '''
@RestController
public class DirectController {
    @GetMapping("/direct")
    public ResponseEntity<DirectVO> detail(Long id) {
        DirectVO response = new DirectVO();
        response.setValue(id);
        return ResponseEntity.ok(response);
    }
}
'''
UNRESOLVED_CONTROLLER = '''
@RestController
public class UnresolvedController {
    @GetMapping("/unresolved")
    public ResponseEntity<UnresolvedVO> detail(Long id) {
        UnresolvedVO response = new UnresolvedVO();
        response.setValue(id);
        if (id == null) return ResponseEntity.ok(other);
        return ResponseEntity.ok(response);
    }
}
'''
QUERY_MAPPER = '''
<mapper namespace="QueryMapper">
  <select id="first" resultType="map">
    SELECT id FROM t_first WHERE enabled = 1 ORDER BY id
  </select>
  <select id="second" resultType="map">
    SELECT id FROM t_second WHERE archived = 0
  </select>
  <update id="batchUpdate">
    UPDATE t_order SET status = #{status} WHERE id = #{id}
  </update>
  <insert id="archive">
    INSERT INTO t_archive(id) SELECT id FROM t_order WHERE status = 'DONE'
  </insert>
</mapper>
'''
PYTHON_WORKER = '''
@app.post("/collect")
def collect(payload):
    assert payload is not None
    validate_payload(payload)
    if not payload:
        raise ValueError("missing")
    return payload
'''


@unittest.skipUnless(
    (REFERENCE_ANALYZER / "business_extractor.py").is_file()
    and (REFERENCE_COMPARATOR / "comparator.py").is_file(),
    "归档实现不随仓库分发；仅在本机测试专用目录进行等价对照",
)
class AuditorFeatureParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="auditor-feature-parity-")
        cls.addClassCleanup(cls.temp_dir.cleanup)
        cls.repo = Path(cls.temp_dir.name) / "repo"
        cls.repo.mkdir()
        cls.git("init")
        cls.git("config", "user.name", "Feature Parity Test")
        cls.git("config", "user.email", "feature-parity@example.invalid")
        baseline = {
            "src/OrderController.java": CONTROLLER,
            "src/OrderService.java": SERVICE.replace("order.getAmount()", "BigDecimal.ZERO"),
            "src/OrderVO.java": DTO,
            "src/OrderMapper.xml": OUTPUT_MAPPER,
            "src/DirectController.java": "public class DirectController {}\n",
            "src/DirectVO.java": "public class DirectVO {\n    private Long value;\n    private String unused;\n}\n",
            "src/UnresolvedController.java": "public class UnresolvedController {}\n",
            "src/UnresolvedVO.java": "public class UnresolvedVO {\n    private Long value;\n}\n",
            "jobs/worker.py": "def collect(payload):\n    return payload\n",
            "db/QueryMapper.xml": "<mapper><select id=\"first\">SELECT id FROM t_first</select></mapper>\n",
            "db/sync.sql": "SELECT id FROM t_order;\n",
            "config/application.properties": "orders.page.limit=10\n",
            "ui/panel.ts": "export const ready = false;\n",
            "native/handler.rs": "fn ready() -> bool { false }\n",
            "src/Empty.java": "class Empty {}\n",
            "legacy/Deleted.java": "class Deleted {}\n",
        }
        cls.write_files(baseline)
        cls.git("add", ".")
        cls.git("commit", "-m", "synthetic baseline")
        cls.git("branch", "-M", "baseline")
        cls.git("checkout", "-b", "feature")
        cls.changed_files = {
            "src/OrderService.java": SERVICE,
            "src/DirectController.java": DIRECT_CONTROLLER,
            "src/UnresolvedController.java": UNRESOLVED_CONTROLLER,
            "jobs/worker.py": PYTHON_WORKER,
            "db/QueryMapper.xml": QUERY_MAPPER,
            "db/sync.sql": "INSERT INTO t_target(id) SELECT id FROM t_source WHERE id > 0;\n",
            "config/application.properties": "orders.page.limit=50\nspring.datasource.url=jdbc:test\n",
            "ui/panel.ts": "export const ready = true;\n",
            "native/handler.rs": "fn ready() -> bool { true }\n",
            "src/Empty.java": "",
        }
        cls.write_files(cls.changed_files)
        (cls.repo / "legacy/Deleted.java").unlink()
        cls.git("add", "-A")
        cls.git("commit", "-m", "synthetic feature")
        # The branch snapshot must win over a different uncommitted worktree DTO.
        (cls.repo / "src/OrderVO.java").write_text("class OrderVO {}\n", encoding="utf-8")
        cls.before_git = cls.git("status", "--porcelain")
        cls.before_head = cls.git("rev-parse", "HEAD")
        cls.old_logic, cls.new_logic = cls.extract_both()

    @classmethod
    def git(cls, *args):
        return subprocess.run(
            ["git", *args], cwd=cls.repo, check=True, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
        ).stdout.strip()

    @classmethod
    def write_files(cls, sources):
        for relative, content in sources.items():
            path = cls.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @classmethod
    def extract_both(cls, selected_files=None):
        args = (str(cls.repo), "feature", "baseline")
        kwargs = {"selected_files": selected_files}
        old = call_skill(REFERENCE_ANALYZER, "business_extractor", "extract_business_logic", *args, **kwargs)
        new = call_skill(AUDITOR, "scripts.workflow_api", "extract_business_logic", *args, **kwargs)
        return old, new

    def assert_reference_retained(self, old, new, path="logic"):
        """Permit added capabilities while rejecting any lost reference evidence."""
        if isinstance(old, dict):
            self.assertIsInstance(new, dict, path)
            self.assertTrue(set(old).issubset(new), f"{path}: lost {set(old) - set(new)}")
            for key, value in old.items():
                self.assert_reference_retained(value, new[key], f"{path}.{key}")
        else:
            self.assertEqual(old, new, path)

    def test_mixed_snapshot_retains_every_reference_evidence_key(self):
        self.assert_reference_retained(self.old_logic, self.new_logic)
        self.assertEqual(set(self.changed_files), set(self.new_logic["file_changes"]))
        self.assertEqual(self.before_git, self.git("status", "--porcelain"))
        self.assertEqual(self.before_head, self.git("rev-parse", "HEAD"))

    def test_service_change_reads_unchanged_controller_dto_and_mapper_from_ref(self):
        old, new = self.extract_both(["src/OrderService.java"])
        self.assert_reference_retained(old, new)
        self.assertEqual(["src/OrderService.java"], list(new["file_changes"]))
        self.assertEqual(["src/OrderController.java"], new["output_layer"]["context_files"])
        field = new["output_layer"]["verified_fields"][0]
        self.assertEqual("/orders.total", field["field_key"])
        self.assertEqual("order.getAmount()", field["source_expression"])
        self.assertEqual("total_amount", field["source_column"])
        self.assertEqual("src/OrderVO.java", field["file"])
        self.assertEqual("src/OrderService.java", field["mapping_file"])
        self.assertEqual("src/OrderMapper.xml", field["mapper_file"])
        self.assertTrue(all(field[key] > 0 for key in ("line", "mapping_line", "mapper_line")))

    def test_controller_change_reads_unchanged_dto_without_confirming_declarations(self):
        old, new = self.extract_both(["src/DirectController.java"])
        self.assert_reference_retained(old, new)
        output = new["output_layer"]
        self.assertEqual(["/direct.value"], [item["field_key"] for item in output["verified_fields"]])
        self.assertEqual({"value", "unused"}, {item["field"] for item in output["declared_fields"]})
        self.assertEqual("src/DirectVO.java", output["verified_fields"][0]["file"])
        self.assertEqual("partial", output["coverage_status"])

    def test_mybatis_sql_statements_preserve_tables_filters_and_data_flow(self):
        entry = next(item for item in self.new_logic["sql_analysis"] if item["file"] == "db/QueryMapper.xml")
        self.assertEqual("mybatis-xml", entry["kind"])
        sql = entry["analysis"]
        self.assertEqual({"t_first", "t_second", "t_order"}, set(sql["tables"]["sources"]))
        self.assertEqual({"t_order", "t_archive"}, set(sql["tables"]["targets"]))
        self.assertNotIn("id", sql["tables"]["targets"])
        self.assertTrue(sql["filters"])
        self.assertTrue(all("</select>" not in item["condition"] for item in sql["filters"]))
        self.assertTrue(sql["data_flow"])
        self.assertTrue(any(item.get("source_file") == "db/QueryMapper.xml" for item in self.new_logic["test_cases"]))

    def test_ast_rules_call_chains_config_jobs_and_test_hints_are_retained(self):
        logic = self.new_logic
        python = logic["python_analysis"][0]
        self.assertEqual("python-ast", python["engine"])
        self.assertTrue(python["if_else_rules"])
        self.assertTrue(python["throw_rules"])
        self.assertEqual({"assert", "validate_payload"}, {item["field"] for item in python["validation_rules"]})
        self.assertTrue(any(item["type"] == "python-route" for item in logic["entry_points"]))
        self.assertTrue(any(item["language"] == "java" for item in logic["business_rules"]["throw_rules"]))
        self.assertTrue(any(item["language"] == "python" for item in logic["business_rules"]["throw_rules"]))
        self.assertTrue(all("file" in item and "call_graph" in item for item in logic["call_chains"]))
        annotations = logic["config_values"]["value_annotations"]
        self.assertTrue(any(item["config_key"] == "orders.page.limit" and item["default_value"] == "50" for item in annotations))
        self.assertTrue(any(item["constant_name"] == "MODE" for item in logic["config_values"]["constants"]))
        self.assertTrue(any(item["cron"] == "0 0 1 * * ?" for item in logic["config_values"]["cron_expressions"]))
        self.assertTrue(any(item["name"] == "orderSync" for item in logic["scheduled_tasks"]))
        self.assertTrue(logic["flow_diagram"])
        self.assertTrue(logic["test_cases"])
        java = next(item for item in logic["syntax_analysis"] if item["file"] == "src/OrderService.java")
        if java.get("fallback_reason"):
            coverage = next(item for item in logic["technology_coverage"]["files"] if item["file"] == java["file"])
            self.assertEqual("partial", coverage["status"])
            self.assertEqual(java["fallback_reason"], coverage["fallback_reason"])

    def test_unsupported_unreadable_and_deleted_files_cannot_fake_complete_coverage(self):
        coverage = self.new_logic["technology_coverage"]
        statuses = {item["file"]: item["status"] for item in coverage["files"]}
        self.assertEqual("recognized_unanalyzed", statuses["ui/panel.ts"])
        self.assertEqual("unsupported", statuses["native/handler.rs"])
        self.assertEqual("unreadable", statuses["src/Empty.java"])
        self.assertFalse(coverage["complete"])
        self.assertTrue(self.new_logic["analysis_warnings"])
        # Both original engines analyse existing A/M/R content, never deleted bodies.
        old, new = self.extract_both(["legacy/Deleted.java"])
        self.assert_reference_retained(old, new)
        self.assertEqual({}, new["file_changes"])
        self.assertEqual(0, new["technology_coverage"]["total_changed_files"])
        self.assertFalse(new["technology_coverage"]["complete"])
        self.assertEqual([], new["call_chains"])

    def test_comparator_retains_all_five_classifications_and_evidence(self):
        requirement = {
            "tables": {"items": {"t_order": {"source": "requirement.md:3"}, "t_missing": {"source": "requirement.md:4"}}, "coverage_status": "complete", "comparison_policy": "identity"},
            "rules": {"items": {"ready_gate": {"value": "READY", "source": "requirement.md:8"}, "untraced": {"value": "allow", "source": "requirement.md:9"}}, "coverage_status": "complete"},
        }
        code = {
            "tables": {"items": [{"stable_key": "t_order", "source": "OrderMapper.xml:4"}, {"stable_key": "t_extra", "source": "OtherMapper.xml:3"}], "coverage_status": "complete"},
            "rules": {"items": [{"stable_key": "ready_gate", "value": "BLOCKED", "source": "OrderService.java:8"}, {"stable_key": "untraced", "source": "OtherService.java:9"}], "coverage_status": "complete"},
        }
        old, new = compare_both(requirement, code)
        self.assertEqual(old, new)
        self.assertEqual({"一致", "缺失", "不一致", "额外实现", "证据不足"}, {item["classification"] for item in new["items"]})
        self.assertEqual("1.1.0", new["schema_version"])
        match = next(item for item in new["items"] if item["key"] == "t_order")
        self.assertEqual("requirement.md:3", match["requirement"]["evidence"])
        self.assertEqual("OrderMapper.xml:4", match["code"]["evidence"])
        self.assertEqual("identity", match["comparison_policy"])

    def test_partial_and_absent_evidence_stay_insufficient(self):
        requirement = {"tables": {"items": {"t_required": "t_required"}, "coverage_status": "complete"}, "errors": {"missing_code": "E400"}}
        for status in ("partial", "unknown", "unsupported"):
            with self.subTest(status=status):
                code = {"tables": {"items": [], "coverage_status": status}}
                old, new = compare_both(requirement, code)
                self.assertEqual(old, new)
                self.assertEqual({"证据不足"}, {item["classification"] for item in new["items"]})
                self.assertEqual(0, new["summary"]["缺失"])
                self.assertTrue(any(item["code"] == "partial_dimension_coverage" for item in new["warnings"]))

    def test_all_extended_dimensions_and_partial_requirement_policy_are_retained(self):
        dimensions = (
            "fields", "apis", "tables", "rules", "sorting", "pagination", "errors", "jobs",
            "configs", "side_effects", "actors", "scenarios", "state_transitions", "calculations",
            "integrations", "data_compatibility",
        )
        requirement = {name: {"items": {f"key_{name}": {"value": "required"}}, "coverage_status": "complete"} for name in dimensions}
        code = {name: {"items": {f"key_{name}": {"value": "changed"}}, "coverage_status": "complete"} for name in dimensions}
        old, new = compare_both(requirement, code)
        self.assertEqual(old, new)
        self.assertEqual(set(dimensions), {item["dimension"] for item in new["items"]})
        for item in new["items"]:
            self.assertEqual("一致" if item["dimension"] in {"apis", "tables", "jobs", "actors"} else "不一致", item["classification"])
        old, new = compare_both(
            {"jobs": {"items": {}, "coverage_status": "partial"}},
            {"jobs": {"items": ["extra_job"], "coverage_status": "complete"}},
        )
        self.assertEqual(old, new)
        self.assertEqual("证据不足", new["items"][0]["classification"])

    def test_output_assignment_comparison_keeps_untraced_fields_insufficient(self):
        for logic in (self.old_logic, self.new_logic):
            with self.subTest(engine="reference" if logic is self.old_logic else "auditor"):
                output = logic["output_layer"]
                confirmed = output["verified_fields"]
                self.assertNotIn("/unresolved.value", {item["field_key"] for item in confirmed})
                requirement = {"fields": {"items": {"/orders.total": "total", "/orders.unused": "unused", "/orders.absent": "absent", "/unresolved.value": "value"}, "coverage_status": "partial", "comparison_policy": "identity"}}
                code = {"fields": {"items": [{"stable_key": item["field_key"], "source": f"{item['mapping_file']}:{item['mapping_line']}"} for item in confirmed], "coverage_status": "partial", "comparison_policy": "identity"}}
                old, new = compare_both(requirement, code)
                self.assertEqual(old, new)
                by_key = {item["key"]: item["classification"] for item in new["items"]}
                self.assertEqual("一致", by_key["/orders.total"])
                for key in ("/orders.unused", "/orders.absent", "/unresolved.value"):
                    self.assertEqual("证据不足", by_key[key])
                self.assertEqual(0, new["summary"]["缺失"])

    def test_component_imports_restore_module_identities_and_search_paths(self):
        original_path = list(sys.path)
        names = ("business_extractor", "comparator", "comparator_core", "scripts", "scripts.workflow_api")
        original_modules = {name: sys.modules.get(name) for name in names}
        compare_both({"tables": ["t_order"]}, {"tables": ["t_order"]})
        self.assertEqual(original_path, sys.path)
        for name, module in original_modules.items():
            self.assertIs(module, sys.modules.get(name), name)


if __name__ == "__main__":
    unittest.main()
