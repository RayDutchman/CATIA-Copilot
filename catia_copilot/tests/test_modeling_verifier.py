# -*- coding: utf-8 -*-
"""建模结果纯数据验证测试。"""

import unittest

from catia_copilot.catia.modeling_verifier import verify_model_state


class TestModelingVerifier(unittest.TestCase):
    def test_not_requested(self):
        result = verify_model_state(["凸台.1"], None, None)
        self.assertEqual(result["status"], "not_requested")
        self.assertIsNone(result["passed"])

    def test_features_mass_and_cog_pass(self):
        result = verify_model_state(
            ["凸台.1", "圆角.1"],
            {"mass": 2.5, "cog": [10.0, 20.0, 30.0]},
            {
                "required_features": ["圆角"],
                "feature_count": 2,
                "mass_kg": {"min": 2.0, "max": 3.0},
                "cog_mm": {"min": [9.0, 19.0, 29.0], "max": [11.0, 21.0, 31.0]},
            },
        )
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["checks"]), 4)

    def test_missing_feature_and_mass_fail(self):
        result = verify_model_state(
            ["凸台.1"],
            None,
            {"required_features": ["孔"], "mass_kg": {"min": 1.0}},
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["passed"])
        self.assertFalse(all(check["passed"] for check in result["checks"]))

    def test_malformed_expectations_fail_without_raising(self):
        malformed_cases = [
            {"required_features": "孔"},
            {"required_features": None},
            {"required_features": 5},
            {"feature_count": True},
            {"feature_count": 2.0},
            {"mass_kg": {"min": "10"}},
            {"mass_kg": {"max": True}},
            {"cog_mm": {"min": [0.0, 0.0]}},
            {"cog_mm": {"max": [1.0, 1.0, 1.0, 1.0]}},
            {"cog_mm": {"min": 0}},
        ]

        for expectations in malformed_cases:
            with self.subTest(expectations=expectations):
                result = verify_model_state(
                    ["凸台.1"],
                    {"mass": 2.5, "cog": [0.0, 0.0, 0.0]},
                    expectations,
                )
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["passed"])

    def test_malformed_expectations_with_missing_mass_are_still_structured(self):
        result = verify_model_state(
            ["凸台.1"],
            None,
            {
                "mass_kg": {"min": None},
                "cog_mm": {"max": [1.0, 2.0]},
            },
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["passed"])
        self.assertTrue(all("name" in check and "passed" in check for check in result["checks"]))
