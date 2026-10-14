import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer import business_extractor
from scripts.analyzer.output_layer_analyzer import analyze_output_layer


CONTROLLER = """
@RequestMapping("/orders")
public class OrderController {
    @GetMapping("/{id}")
    public ResponseEntity<OrderVO> getOrder(Long id) {
        OrderVO response = new OrderVO();
        response.setTotal(order.getAmount());
        return ResponseEntity.ok(response);
    }
}
"""
DTO = """
public class OrderVO {
    private BigDecimal total;
    private String unused;
}
"""
MAPPER = """
<mapper>
  <resultMap id="orderMap" type="OrderVO">
    <result column="total_amount" property="total"/>
  </resultMap>
  <select id="findOrder" resultMap="orderMap">
    SELECT total_amount FROM t_order
  </select>
</mapper>
"""


class OutputLayerTests(unittest.TestCase):
    def test_returned_dto_setter_is_confirmed_and_declaration_is_only_candidate(self):
        result = analyze_output_layer(
            {"src/OrderController.java": CONTROLLER},
            ["src/OrderController.java", "src/OrderVO.java"],
            lambda path: DTO if path == "src/OrderVO.java" else "",
        )
        self.assertEqual("/orders/{id}", result["endpoints"][0]["endpoint"])
        self.assertEqual(["/orders/{id}.total"], [
            item["field_key"] for item in result["verified_fields"]
        ])
        self.assertEqual("order.getAmount()", result["verified_fields"][0]["source_expression"])
        self.assertEqual({"total", "unused"}, {
            item["field"] for item in result["declared_fields"]
        })
        self.assertEqual("partial", result["coverage_status"])

    def test_controller_return_traces_one_service_method(self):
        controller = """
        public class OrderController {
            private final OrderService orderService;
            @GetMapping("/orders")
            public ResponseEntity<OrderVO> getOrder(Long id) {
                return ResponseEntity.ok(orderService.getOrder(id));
            }
        }
        """
        service = """
        public class OrderService {
            public OrderVO getOrder(Long id) {
                OrderVO vo = new OrderVO();
                vo.setTotal(order.getAmount());
                return vo;
            }
        }
        """
        sources = {
            "src/OrderController.java": controller,
            "src/OrderService.java": service,
            "src/OrderVO.java": DTO,
        }
        result = analyze_output_layer(
            {"src/OrderController.java": controller},
            list(sources),
            lambda path: sources[path],
        )
        field = result["verified_fields"][0]
        self.assertEqual("/orders.total", field["field_key"])
        self.assertEqual("src/OrderService.java", field["mapping_file"])
        self.assertEqual("order.getAmount()", field["source_expression"])

    def test_unique_response_type_without_vo_suffix_is_resolved(self):
        result = analyze_output_layer(
            {"src/OrderController.java": CONTROLLER.replace("OrderVO", "OrderDetail")},
            ["src/OrderDetail.java"],
            lambda path: DTO.replace("OrderVO", "OrderDetail"),
        )
        self.assertEqual("OrderDetail", result["endpoints"][0]["dto_type"])
        self.assertEqual(1, len(result["verified_fields"]))

    def test_ambiguous_dto_path_is_not_assumed(self):
        result = analyze_output_layer(
            {"src/OrderController.java": CONTROLLER},
            ["a/OrderVO.java", "b/OrderVO.java"],
            lambda path: DTO,
        )
        self.assertEqual([], result["verified_fields"])
        self.assertTrue(any("不唯一" in warning for warning in result["warnings"]))

    def test_interface_injection_traces_unique_service_impl(self):
        controller = """
        public class OrderController {
            private final OrderService orderService;
            @GetMapping("/orders")
            public ResponseEntity<OrderVO> getOrder(Long id) {
                return ResponseEntity.ok(orderService.getOrder(id));
            }
        }
        """
        interface = "public interface OrderService { OrderVO getOrder(Long id); }"
        implementation = """
        public class OrderServiceImpl implements OrderService {
            public OrderVO getOrder(Long id) {
                OrderVO vo = new OrderVO();
                vo.setTotal(order.getAmount());
                return vo;
            }
        }
        """
        sources = {
            "src/OrderController.java": controller,
            "src/OrderService.java": interface,
            "src/OrderServiceImpl.java": implementation,
            "src/OrderVO.java": DTO,
        }
        result = analyze_output_layer(
            {"src/OrderController.java": controller, "src/OrderServiceImpl.java": implementation},
            list(sources),
            lambda path: sources[path],
        )
        self.assertEqual(
            "src/OrderServiceImpl.java",
            result["verified_fields"][0]["mapping_file"],
        )

    def test_mapper_column_traces_through_service_to_response_field(self):
        controller = """
        public class OrderController {
            private final OrderService orderService;
            @GetMapping("/orders")
            public ResponseEntity<OrderVO> getOrder(Long id) {
                return ResponseEntity.ok(orderService.getOrder(id));
            }
        }
        """
        service = """
        public class OrderService {
            private final OrderMapper orderMapper;
            public OrderVO getOrder(Long id) {
                OrderEntity order = orderMapper.findOrder(id);
                OrderVO vo = new OrderVO();
                vo.setTotal(order.getAmount());
                return vo;
            }
        }
        """
        mapper = MAPPER.replace('property="total"', 'property="amount"')
        sources = {
            "src/OrderController.java": controller,
            "src/OrderService.java": service,
            "src/OrderVO.java": DTO,
            "src/OrderMapper.xml": mapper,
        }
        result = analyze_output_layer(
            {"src/OrderController.java": controller},
            list(sources),
            lambda path: sources[path],
        )
        field = result["verified_fields"][0]
        self.assertEqual("total_amount", field["source_column"])
        self.assertEqual("src/OrderMapper.xml", field["mapper_file"])

    def test_multiple_return_objects_do_not_create_confirmed_field(self):
        controller = CONTROLLER.replace(
            "return ResponseEntity.ok(response);",
            "if (id == null) return ResponseEntity.ok(other);\n"
            "        return ResponseEntity.ok(response);",
        )
        result = analyze_output_layer(
            {"src/OrderController.java": controller},
            ["src/OrderVO.java"],
            lambda path: DTO,
        )
        self.assertEqual([], result["verified_fields"])
        self.assertEqual("unresolved", result["endpoints"][0]["status"])

    def test_mybatis_result_map_keeps_column_to_property_evidence(self):
        result = analyze_output_layer({"src/OrderMapper.xml": MAPPER}, [], lambda path: "")
        mapping = result["sql_results"][0]["field_mappings"][0]
        self.assertEqual(("total_amount", "total"), (mapping["column"], mapping["field"]))

    def test_real_git_snapshot_follows_unchanged_output_dependencies(self):
        controller = """
        public class OrderController {
            private final OrderService orderService;
            @GetMapping("/orders")
            public ResponseEntity<OrderVO> getOrder(Long id) {
                return ResponseEntity.ok(orderService.getOrder(id));
            }
        }
        """
        service = """
        public class OrderService {
            private final OrderMapper orderMapper;
            public OrderVO getOrder(Long id) {
                OrderEntity order = orderMapper.findOrder(id);
                OrderVO vo = new OrderVO();
                vo.setTotal(order.getAmount());
                return vo;
            }
        }
        """
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            source_dir = repo / "src"
            source_dir.mkdir(parents=True)
            def git(*args):
                completed = subprocess.run(
                    ["git", *args], cwd=repo, capture_output=True, text=True,
                    encoding="utf-8", check=True,
                )
                return completed.stdout.strip()
            git("init")
            git("config", "user.name", "Codex Test")
            git("config", "user.email", "codex@example.invalid")
            (source_dir / "OrderController.java").write_text(
                "public class OrderController {}\n", encoding="utf-8"
            )
            (source_dir / "OrderService.java").write_text(service, encoding="utf-8")
            (source_dir / "OrderVO.java").write_text(DTO, encoding="utf-8")
            (source_dir / "OrderMapper.xml").write_text(
                MAPPER.replace('property="total"', 'property="amount"'),
                encoding="utf-8",
            )
            git("add", "src")
            git("commit", "-m", "baseline")
            git("branch", "-M", "master")
            git("checkout", "-b", "feature")
            (source_dir / "OrderController.java").write_text(controller, encoding="utf-8")
            git("add", "src/OrderController.java")
            git("commit", "-m", "change controller")
            result = business_extractor.extract_business_logic(
                str(repo), "feature", "master", selected_files=["src/OrderController.java"]
            )
        self.assertEqual(["src/OrderController.java"], list(result["file_changes"]))
        field = result["output_layer"]["verified_fields"][0]
        self.assertEqual("total_amount", field["source_column"])
        self.assertEqual("src/OrderVO.java", field["file"])

    def test_service_only_change_reaches_unchanged_controller_output(self):
        controller = """
        @RestController
        public class OrderController {
            private final OrderService orderService;
            @GetMapping("/orders")
            public ResponseEntity<OrderVO> getOrder(Long id) {
                return ResponseEntity.ok(orderService.getOrder(id));
            }
        }
        """
        service = """
        public class OrderService {
            private final OrderMapper orderMapper;
            public OrderVO getOrder(Long id) {
                OrderEntity order = orderMapper.findOrder(id);
                OrderVO vo = new OrderVO();
                vo.setTotal(order.getAmount());
                return vo;
            }
        }
        """
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            source_dir = repo / "src"
            source_dir.mkdir(parents=True)
            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=repo, capture_output=True, text=True,
                    encoding="utf-8", check=True,
                ).stdout.strip()
            git("init")
            git("config", "user.name", "Codex Test")
            git("config", "user.email", "codex@example.invalid")
            (source_dir / "OrderController.java").write_text(controller, encoding="utf-8")
            (source_dir / "OrderService.java").write_text(
                service.replace("order.getAmount()", "BigDecimal.ZERO"), encoding="utf-8"
            )
            (source_dir / "OrderVO.java").write_text(DTO, encoding="utf-8")
            (source_dir / "OrderMapper.xml").write_text(
                MAPPER.replace('property="total"', 'property="amount"'), encoding="utf-8"
            )
            git("add", "src")
            git("commit", "-m", "baseline")
            git("branch", "-M", "master")
            git("checkout", "-b", "feature")
            (source_dir / "OrderService.java").write_text(service, encoding="utf-8")
            git("add", "src/OrderService.java")
            git("commit", "-m", "change output amount")
            result = business_extractor.extract_business_logic(
                str(repo), "feature", "master", selected_files=["src/OrderService.java"]
            )
        self.assertEqual(["src/OrderService.java"], list(result["file_changes"]))
        self.assertEqual(
            ["src/OrderController.java"], result["output_layer"]["context_files"]
        )
        field = result["output_layer"]["verified_fields"][0]
        self.assertEqual("total_amount", field["source_column"])
        self.assertEqual("/orders.total", field["field_key"])

    def test_authoritative_entry_reads_unchanged_dto_from_branch_snapshot(self):
        sources = {"src/OrderController.java": CONTROLLER, "src/OrderVO.java": DTO}
        with patch.object(business_extractor, "get_file_changes", return_value={"src/OrderController.java": "M"}), \
             patch.object(business_extractor, "get_file_content", side_effect=lambda _repo, _branch, path: sources[path]), \
             patch.object(business_extractor, "run_git_command", return_value="src/OrderController.java\nsrc/OrderVO.java"):
            result = business_extractor.extract_business_logic("repo", "feature", "main")
        self.assertEqual("src/OrderVO.java", result["output_layer"]["endpoints"][0]["dto_file"])
        self.assertEqual(1, len(result["output_layer"]["verified_fields"]))
        self.assertEqual("src/OrderController.java", result["call_chains"][0]["file"])
        self.assertIn("call_graph", result["call_chains"][0])


if __name__ == "__main__":
    unittest.main()
