"""Explicit project tolerances frozen before dispatch; manual limits stay intact."""
import copy
from .state import Fault, finite


FIELDS = ("typical_tolerance_percent", "curve_mae_percent", "curve_max_error_percent")


def validate_standard(value):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise Fault("input", "项目验收标准必须含典型值、曲线MAE、曲线最大误差三项")
    if any(finite(value[k], k) < 0 for k in FIELDS):
        raise Fault("input", "项目验收误差不能为负")
    return value


def expectation_with_standard(expectation, standard, curve=False):
    value = copy.deepcopy(expectation)
    if standard is None:
        return value
    validate_standard(standard)
    if curve:
        value["thresholds"] = {**value.get("thresholds", {}),
            "MAE_over_reference_span_percent": standard["curve_mae_percent"],
            "max_error_over_reference_span_percent": standard["curve_max_error_percent"]}
    elif value.get("typical") is not None:
        value.pop("typical_absolute_tolerance", None)
        value["typical_relative_tolerance_percent"] = standard["typical_tolerance_percent"]
    return value


def apply_standard(task, standard):
    value = copy.deepcopy(task)
    validate_standard(standard)
    value["acceptance_standard"] = copy.deepcopy(standard)
    for case in value["cases"]:
        curve = bool(case.get("reference")) or case["protocol"]["measurement"]["mode"] in ("curve", "ratio_curve")
        case["expectation"] = expectation_with_standard(case["expectation"], standard, curve)
    for item in value["inventory"]["items"]:
        contract = item.get("test_contract")
        if contract and contract.get("expectation"):
            contract["expectation"] = expectation_with_standard(contract["expectation"], standard, bool(contract.get("reference")))
    return value
