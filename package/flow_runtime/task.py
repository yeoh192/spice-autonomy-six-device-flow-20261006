"""Portable task contracts and migration from the old configured-test bundle."""
import copy
import json
import re
from pathlib import Path
from .state import Fault, digest, finite, read, save
from .spice import NAME, load_reference, validate_model, model_text, validate_protocol, validate_measurement_unit

DEFAULT_BUDGETS = {"api_calls": 32, "simulations": 96, "repairs": 8, "seconds": 7200}
DEFAULT_POLICY = {"development_attempts": 3, "execution_attempts": 3,
                  "optimization_attempts": 3, "selection_attempts": 3, "workers": 2,
                  "require_non_vendor": False, "allow_structure_edit": True,
                  "continuous_until_acceptance": False, "model_diagnosis_enabled": True, "diagnostic_attempts": 6,
                  "max_patch_bytes": 16000, "max_metric_regression_percent": 5,
                  "max_diagnostic_parameter_step_percent": 25}


def resolve_asset(base, value):
    p = Path(value)
    return p.resolve() if p.is_absolute() else (base / p).resolve()


def load_task(path):
    path = Path(path).resolve()
    task = read(path)
    if task.get("acceptance_standard") is not None:
        from .project_standard import validate_standard
        validate_standard(task["acceptance_standard"])
    if task.get("schema_version") != "flow-1":
        raise Fault("input", "任务包版本必须为flow-1")
    integration = task.get("input_integration")
    if integration is not None:
        if integration.get("schema") != "unified-device-input-1" or integration.get("execution_scope") != "configured_regression_only" or integration.get("model_fitting_ready") is not False:
            raise Fault("input", "资料迁移只能运行已配置范围；完整任务需另行资格验证")
        if task.get("select_candidate") or task.get("template_selection"):
            raise Fault("input", "配置范围回归必须冻结模型，不能混用索引重新选模")
    index_asset = {}
    if task.get("template_selection"):
        from .templates import retrieve
        config, candidates, receipt = retrieve(task["template_selection"], path.parent, task["device"])
        task["template_selection"], task["candidates"], task["template_retrieval"] = config, candidates, receipt
        task["model"] = copy.deepcopy(candidates[0])
        task["select_candidate"] = True
        index_asset[config["index"]] = receipt["index_sha256"]
    if not task.get("model") and task.get("candidates"):
        task["model"] = copy.deepcopy(task["candidates"][0])
        task["select_candidate"] = True
    task["model"]["path"] = str(resolve_asset(path.parent, task["model"]["path"]))
    task["budgets"] = {**DEFAULT_BUDGETS, **task.get("budgets", {})}
    task["policy"] = {**DEFAULT_POLICY, **task.get("policy", {})}
    if set(task["budgets"]) != set(DEFAULT_BUDGETS):
        raise Fault("input", "预算字段未知")
    for k, value in task["budgets"].items():
        finite(value, k)
        if value < 0 or (k != "seconds" and not isinstance(value, int)):
            raise Fault("input", "预算需为非负数；计数预算需为整数")
    for k in ("development_attempts", "execution_attempts", "optimization_attempts", "selection_attempts", "diagnostic_attempts", "workers"):
        v = task["policy"][k]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 8:
            raise Fault("input", "阶段次数/并发数需在1至8之间")
    finite(task["policy"]["max_metric_regression_percent"])
    if task["policy"]["max_metric_regression_percent"] < 0:
        raise Fault("input", "回归保护容差不能为负")
    diagnostic_step = finite(task["policy"]["max_diagnostic_parameter_step_percent"])
    if not 0 < diagnostic_step <= 100:
        raise Fault("input", "诊断参数扰动上限需在0至100%之间（不含0）")
    if not isinstance(task["policy"]["model_diagnosis_enabled"], bool):
        raise Fault("input", "model_diagnosis_enabled必须为布尔值")
    if task.get("template_selection") and task["template_selection"]["source_policy"] == "all" and task["policy"]["require_non_vendor"]:
        raise Fault("input", "来源策略冲突：all检索不能同时要求独立非厂商来源")
    if not isinstance(task["policy"]["continuous_until_acceptance"], bool):
        raise Fault("input", "continuous_until_acceptance必须为布尔值")
    if task["policy"]["continuous_until_acceptance"] and integration:
        raise Fault("input", "仅配置回归的迁移任务不能启用持续验收；需完整flow-1任务")
    ids = [c["id"] for c in task["cases"]]
    if len(set(ids)) != len(ids) or any(not NAME.fullmatch(i) for i in ids):
        raise Fault("input", "测试ID非法或重复")
    model = task["model"]
    if digest(model["path"]) != model["sha256"]:
        raise Fault("input", "模型哈希不符")
    validate_model(model_text(model["path"]), model)
    assets = {model["path"]: model["sha256"], **index_asset}
    candidate_ids = set()
    for candidate in task.get("candidates", []):
        candidate["path"] = str(resolve_asset(path.parent, candidate["path"]))
        if candidate["id"] in candidate_ids or not NAME.fullmatch(candidate["id"]):
            raise Fault("input", "候选模板ID重复或非法")
        candidate_ids.add(candidate["id"])
        if digest(candidate["path"]) != candidate["sha256"] or set(candidate["ports"]) != set(model["ports"]):
            raise Fault("input", "候选模板哈希或语义端口不一致")
        validate_model(model_text(candidate["path"]), candidate)
        assets[candidate["path"]] = candidate["sha256"]
    for c in task["cases"]:
        if task.get("acceptance_standard") is not None:
            from .project_standard import expectation_with_standard
            is_curve = bool(c.get("reference")) or c["protocol"]["measurement"]["mode"] in ("curve", "ratio_curve")
            if c["expectation"] != expectation_with_standard(c["expectation"], task["acceptance_standard"], is_curve):
                raise Fault("input", "测试验收与启动前固定的项目标准不一致：" + c["id"])
        check_expectation(c["expectation"])
        validate_protocol(c["protocol"], model, c.get("contract"))
        validate_measurement_unit(c)
        if c.get("reference"):
            reference = c["reference"]
            reference["path"] = str(resolve_asset(path.parent, reference["path"]))
            if digest(reference["path"]) != reference["sha256"]:
                raise Fault("input", "参考曲线哈希不符：" + c["id"])
            load_reference(reference["path"], c["expectation"]["unit"], reference.get("condition"))
            assets[reference["path"]] = reference["sha256"]
    inventory = task["inventory"]
    item_ids = [i["id"] for i in inventory["items"]]
    if len(set(item_ids)) != len(item_ids):
        raise Fault("input", "手册项目ID重复")
    for item in inventory["items"]:
        if item["kind"] not in ("test", "constraint", "informational") or not item.get("evidence"):
            raise Fault("input", "手册项目分类或证据不完整")
        if item["kind"] == "informational" and not item.get("reason"):
            raise Fault("input", "资料说明项缺少依据")
        if set(item.get("bindings", [])) - set(ids):
            raise Fault("input", "手册项目绑定不存在的测试")
        if item.get("test_contract"):
            contract = item["test_contract"]
            check_expectation(contract["expectation"])
            if contract.get("reference"):
                ref = contract["reference"]
                ref["path"] = str(resolve_asset(path.parent, ref["path"]))
                if digest(ref["path"]) != ref["sha256"]:
                    raise Fault("input", "新测试参考曲线哈希不符")
                load_reference(ref["path"], contract["expectation"]["unit"], ref.get("condition"))
                assets[ref["path"]] = ref["sha256"]
    for source in inventory.get("sources", []):
        source["path"] = str(resolve_asset(path.parent, source["path"]))
        if digest(source["path"]) != source["sha256"]:
            raise Fault("input", "手册证据文件哈希不符")
        assets[source["path"]] = source["sha256"]
    argv = task["runner"]["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv) or not any("{circuit}" in s for s in argv):
        raise Fault("input", "运行器必须为argv数组且包含{circuit}")
    exe = Path(argv[0])
    if not exe.is_file():
        raise Fault("input", "LTspice启动器不存在")
    assets[str(exe.resolve())] = digest(exe)
    for name in ("design", "review"):
        r = task["routes"][name]
        if r["provider"] not in ("qwen", "glm") or not r.get("model"):
            raise Fault("input", "仅支持明确的官方模型路由")
    if task.get("test_library", {}).get("enabled"):
        from .test_library import catalog, assets_root
        manifest, _ = catalog()
        if task["test_library"].get("catalog_version", manifest["version"]) != manifest["version"]:
            raise Fault("input", "测试库版本与任务不一致，请另建任务")
        task["test_library"] = {"enabled": True, "catalog_version": manifest["version"]}
        assets[str(assets_root() / "catalog_manifest.json")] = digest(assets_root() / "catalog_manifest.json")
        for name, expected in manifest["files"].items():
            assets[str(assets_root() / name)] = expected
    if task.get("capability_library"):
        task["capability_library"] = str(resolve_asset(path.parent, task["capability_library"]))
    if task.get("simulation_cache"):
        task["simulation_cache"] = str(resolve_asset(path.parent, task["simulation_cache"]))
    return task, assets


def check_expectation(e):
    if not isinstance(e.get("unit"), str) or not e["unit"]:
        raise Fault("input", "参考单位缺失")
    limits = e.get("limits", {})
    if set(limits) - {"min", "max"}:
        raise Fault("input", "规格边界字段非法")
    for v in limits.values():
        finite(v, "limit")
    if "min" in limits and "max" in limits and limits["min"] > limits["max"]:
        raise Fault("input", "规格上下限倒置")
    if e.get("typical") is not None:
        finite(e["typical"])
    for k in ("typical_absolute_tolerance", "typical_relative_tolerance_percent"):
        if k in e and finite(e[k], k) < 0:
            raise Fault("input", "典型值容差为负")
    allowed = {"MAE", "RMSE", "max_absolute_error", "MAE_over_reference_span_percent", "max_error_over_reference_span_percent"}
    if set(e.get("thresholds", {})) - allowed:
        raise Fault("input", "未知曲线误差指标")
    if any(finite(v) < 0 for v in e.get("thresholds", {}).values()):
        raise Fault("input", "误差阈值为负")


def component(kind, name, plus, minus, value):
    return {"kind": kind, "name": name, "nodes": [plus, minus], "value": value}


def legacy_protocol(cfg, case):
    """A device-family adapter. The scheduler itself has no D/G/S assumptions."""
    if set(cfg["model"]["ports"]) != {"D", "G", "S"}:
        raise Fault("input", "旧格式迁移适配器只支持MOSFET；其他器件用flow-1接口")
    kind = case["kind"]
    nodes = {"D": "D", "G": "G", "S": "0"}
    m = {"mode": "sample", "signal": "v(d)", "at": case.get("current_A", 0)}
    if kind in ("output", "transfer"):
        reference = Path(cfg["device_root"]) / case["reference"]
        points = load_reference(reference, case["y_unit"], case["condition"])
        start, stop = points[0][0], points[-1][0]
        source = "VD" if kind == "output" else "VG"
        components = [component("V", "VG", "G", "0", {"dc": case.get("vgs_V", 0)}),
                      component("V", "VD", "D", "0", {"dc": case.get("vds_V", 0)})]
        m = {"mode": "curve", "signal": "i(vd)", "sign": -1}
        analysis = {"kind": "dc", "source": source, "start": start, "stop": stop,
                    "step": (stop-start) / case.get("steps", 2000)}
    elif kind in ("rdson", "threshold", "body_diode", "dc_current_voltage"):
        current = case["current_A"]
        if kind == "threshold":
            nodes["G"] = "D"
            components = []
        elif kind == "body_diode":
            nodes = {"D": "0", "G": "S", "S": "S"}
            components = []
            m["signal"] = "v(s)"
        else:
            components = [component("V", "VG", "G", "0", {"dc": case.get("vgs_V", case.get("fixed_gate_source_V", 0))})]
        drive = "S" if kind == "body_diode" else "D"
        components.append(component("I", "IDRIVE", "0", drive, {"dc": 0}))
        analysis = {"kind": "dc", "source": "IDRIVE", "start": current*.99, "stop": current*1.01, "step": current*.001}
        if kind == "rdson":
            m["scale"] = 1 / current
    elif kind == "leakage":
        components = [component("V", "VD", "D", "0", {"dc": case["vds_V"]}),
                      component("V", "VG", "G", "0", {"dc": case["vgs_V"]}),
                      component("V", "VDUMMY", "UNUSED", "0", {"dc": 0})]
        analysis = {"kind": "dc", "source": "VDUMMY", "start": 0, "stop": 1, "step": 1}
        m = {"mode": "sample", "at": 0, "signal": "i(vd)" if case["terminal"] == "drain" else "i(vg)", "sign": -1, "absolute": True}
    elif kind == "capacitance":
        gate = case["quantity"] in ("ciss", "crss")
        components = [component("V", "VD", "D", "0", {"dc": case["vds_V"], "ac": 0 if gate else 1}),
                      component("V", "VG", "G", "0", {"dc": case["vgs_V"], "ac": 1 if gate else 0})]
        analysis = {"kind": "ac", "frequency_Hz": case["frequency_Hz"]}
        m = {"mode": "capacitance", "signal": "i(vg)" if case["quantity"] == "ciss" else "i(vd)", "sign": 1 if case["quantity"] == "crss" else -1}
    else:
        raise Fault("input", "旧格式迁移不支持：" + kind)
    return {"temperature_C": case["temperature_C"], "device_nodes": nodes,
            "components": components, "analysis": analysis, "measurement": m}


def import_bundle(bundle, output):
    bundle = Path(bundle).resolve()
    cfg, manifest = read(bundle / "config.json"), read(bundle / "manual_inventory.json")
    if cfg["device"] != manifest["device"]:
        raise Fault("input", "型号不一致")
    known_tests = {c["id"]: c for c in cfg["tests"]}
    if len(known_tests) != len(cfg["tests"]):
        raise Fault("input", "旧测试ID重复")
    for item in manifest["items"]:
        for binding in item.get("bindings", []):
            old = known_tests.get(binding["test_id"])
            if old is None or not binding.get("expected") or any(old.get(k) != v for k, v in binding["expected"].items()):
                raise Fault("input", "旧清单绑定与测试条件不一致：" + binding["test_id"])
    root = Path(cfg["device_root"])
    model = copy.deepcopy(cfg["model"])
    model["path"] = str((root / model["path"]).resolve())
    model["provenance"] = {"kind": "legacy_unverified", "note": "继承旧试验模型；非厂商来源尚未证明"}
    cases = []
    for c in cfg["tests"]:
        protocol = legacy_protocol(cfg, c)
        expectation = {"unit": c.get("y_unit", c.get("unit", "ohm" if c["kind"] == "rdson" else "A" if c["kind"] == "leakage" else "F" if c["kind"] == "capacitance" else "V")),
                       "limits": c.get("limits", {}), "typical": c.get("typical"), "thresholds": c.get("acceptance") or {}}
        case = {"id": c["id"], "protocol": protocol, "expectation": expectation,
                "contract": {"fixed": {"temperature_C": protocol["temperature_C"], "device_nodes": protocol["device_nodes"],
                    "components": protocol["components"], "measurement": protocol["measurement"]}},
                "evidence": c.get("evidence_refs", []), "origin": "migrated_existing_test"}
        if c.get("reference"):
            p = (root / c["reference"]).resolve()
            case["reference"] = {"path": str(p), "sha256": digest(p), "condition": c["condition"]}
        cases.append(case)
    inventory = {"review_status": manifest.get("inventory_review_status", "pending"), "sources": manifest.get("sources", []), "items": []}
    for i in manifest["items"]:
        inventory["items"].append({"id": i["id"], "label": i["label"],
            "kind": {"test_required": "test", "constraint_review_required": "constraint", "informational": "informational"}[i["disposition"]],
            "evidence": i["evidence"], "reference_evidence": i.get("reference", {}), "reason": i.get("reason", ""),
            "bindings": [b["test_id"] for b in i.get("bindings", [])], "binding_complete": i.get("binding_complete", False),
            "legacy_bindings": i.get("bindings", [])})
    task = {"schema_version": "flow-1", "device": cfg["device"], "model": model,
        "runner": {"argv": [str(Path(cfg["runner"]).resolve()), "-b", "-ascii", "{circuit}"], "timeout_seconds": cfg.get("timeout_seconds", 120)},
        "inventory": inventory, "cases": cases,
        "routes": {"design": {"provider": "qwen", "model": "qwen3.8-max"},
                   "review": {"provider": "glm", "model": "glm-5.3"}},
        "budgets": DEFAULT_BUDGETS.copy(), "policy": DEFAULT_POLICY.copy(), "limitations": cfg.get("limitations", [])}
    task["capability_library"] = str(Path.cwd() / "automation" / "flow_capabilities")
    output = Path(output).resolve()
    if output.exists():
        raise Fault("input", "迁移任务文件已存在，拒绝覆盖")
    save(output, task)
    return output
