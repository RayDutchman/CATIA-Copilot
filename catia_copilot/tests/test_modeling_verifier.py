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
