# -*- coding: utf-8 -*-
"""ModelingContext 步骤边界取消语义测试。"""

import unittest
from unittest.mock import patch

from catia_copilot.catia.modeling import ModelingCancelledError, ModelingContext


class TestModelingCancellation(unittest.TestCase):
    def test_existing_part_snapshot_is_recorded_once(self):
        ctx = ModelingContext()
        ctx._part = object()
        with patch('catia_copilot.catia.modeling.list_features', return_value=['凸台.1']) as snapshot:
            ctx._run('probe', lambda: None)
        self.assertEqual(ctx.steps[-1]['features_after'], ['凸台.1'])
        snapshot.assert_called_once()

    def test_cancelled_before_next_step_does_not_call_com_function(self):
        called = []
        ctx = ModelingContext(cancel_check=lambda: True)

        with self.assertRaises(ModelingCancelledError):
            ctx._run("second_step()", lambda: called.append(True))

        self.assertEqual(called, [])
        self.assertEqual(ctx.steps, [])

    def test_cancel_is_checked_between_steps(self):
        checks = {"count": 0}

        def cancel_check():
            checks["count"] += 1
            return checks["count"] > 1

        ctx = ModelingContext(cancel_check=cancel_check)
        self.assertEqual(ctx._run("first_step()", lambda: "ok"), "ok")
        with self.assertRaises(ModelingCancelledError):
            ctx._run("second_step()", lambda: "should not run")
        self.assertEqual(len(ctx.steps), 1)
        self.assertEqual(ctx.steps[0]["step"], "first_step()")
