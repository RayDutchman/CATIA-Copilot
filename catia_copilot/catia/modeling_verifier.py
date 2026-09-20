# -*- coding: utf-8 -*-
"""建模结果的纯数据验证器，不访问 CATIA COM。"""

from __future__ import annotations

import math


def _finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _valid_scalar_range(value) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("min", "max"):
        if key in value and not _finite_number(value[key]):
            return False
    return not (
        "min" in value
        and "max" in value
        and value["min"] > value["max"]
    )


def _valid_vector_bound(value) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(_finite_number(item) for item in value)
    )


def _valid_vector_range(value) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("min", "max"):
        if key in value and not _valid_vector_bound(value[key]):
            return False
    if "min" in value and "max" in value:
        return all(value["min"][i] <= value["max"][i] for i in range(3))
    return True


def verify_model_state(
    features: list[str],
    mass_props: dict | None,
    expectations: dict | None,
) -> dict:
    """按可选验收条件检查模型状态，返回可序列化结果。"""
    if not expectations:
        return {"status": "not_requested", "passed": None, "checks": []}
    if not isinstance(expectations, dict):
        return {
            "status": "failed",
            "passed": False,
            "checks": [{
                "name": "expectations",
                "passed": False,
                "expected": "object",
                "actual": type(expectations).__name__,
            }],
        }

    checks: list[dict] = []

    required = expectations.get("required_features", [])
    if not isinstance(required, list) or not all(isinstance(token, str) for token in required):
        checks.append({
            "name": "required_features",
            "passed": False,
            "expected": "array of strings",
            "actual": required,
        })
    else:
        for token in required:
            matched = any(token in feature for feature in features)
            checks.append({
                "name": f"required_feature:{token}",
                "passed": matched,
                "actual": features,
            })

    if "feature_count" in expectations:
        expected_count = expectations["feature_count"]
        passed = (
            isinstance(expected_count, int)
            and not isinstance(expected_count, bool)
            and expected_count >= 0
            and len(features) == expected_count
        )
        checks.append({
            "name": "feature_count",
            "passed": passed,
            "expected": expected_count,
            "actual": len(features),
        })

    mass = mass_props.get("mass") if mass_props else None
    mass_range = expectations.get("mass_kg")
    if mass_range is not None:
        passed = (
            _finite_number(mass)
            and _valid_scalar_range(mass_range)
            and ("min" not in mass_range or mass >= mass_range["min"])
            and ("max" not in mass_range or mass <= mass_range["max"])
        )
        checks.append({
            "name": "mass_kg",
            "passed": passed,
            "expected": mass_range,
            "actual": mass,
        })

    cog_range = expectations.get("cog_mm")
    cog = mass_props.get("cog") if mass_props else None
    if cog_range is not None:
        passed = (
            isinstance(cog, (list, tuple))
            and len(cog) == 3
            and all(_finite_number(value) for value in cog)
            and _valid_vector_range(cog_range)
            and all(
                "min" not in cog_range or cog[i] >= cog_range["min"][i]
                for i in range(3)
            )
            and all(
                "max" not in cog_range or cog[i] <= cog_range["max"][i]
                for i in range(3)
            )
        )
        checks.append({
            "name": "cog_mm",
            "passed": passed,
            "expected": cog_range,
            "actual": cog,
        })

    passed = all(check["passed"] for check in checks) if checks else True
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "checks": checks,
    }
