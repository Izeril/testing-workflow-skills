import unittest

from scripts.comparator.comparator_core import compare_structured


class ExtendedIdentityPolicyTests(unittest.TestCase):
    def test_actor_dimension_uses_identity_policy(self):
        result = compare_structured(
            {"actors": [{"key": "taxpayer", "value": "纳税人"}]},
            {"actors": [{"key": "taxpayer"}]},
            dimensions=["actors"],
        )

        finding = next(item for item in result["items"] if item["dimension"] == "actors")
        self.assertEqual("identity", finding["comparison_policy"])
        self.assertEqual("一致", finding["classification"])


if __name__ == "__main__":
    unittest.main()
