"""Versioned method catalog -> Qwen qualification -> measured exact-contract registry."""
import copy
from pathlib import Path
from .state import Fault, BudgetEnd, digest, fingerprint, read, save, file_lock
from .spice import validate_protocol, validate_measurement_unit, render
from .contracts import extracted_contract
from .test_methods import build, defaults, adjusted, gate_charge_oracles, TUNABLE
from .project_standard import apply_standard, expectation_with_standard
from .evidence import bundle, phase_evidence

QUALIFICATION_VERSION = 2


def recovery_route(feedback, method_id):
    kind = (feedback or {}).get("kind")
    requested = (feedback or {}).get("evidence", {}).get("repair_adapter")
    if method_id == "gate_charge_total" and (kind in ("fixture", "execution", "calibration") or requested == "fixture_numeric"):
        return "fixture_numeric"
    return "review_revision"


def design_context(workflow, item, draft, expectation, method, feedback=None):
    p = draft["protocol"]
    descriptor = {k: v for k, v in method.items() if k not in ("qwen_qualified", "live_validated", "required")}
    return {"stage": "design_review", "task": "Review the proposal stage only. Return {decision:approve|revise,stage:design_review,approved_protocol_sha256:EXACT_SUPPLIED_HASH,conditions_complete:true|false,measurement_correct:true|false,issues:[],repair_adapter:review_revision|fixture_numeric}. Assess proposed physics and source bindings. Calibration and DUT trials are scheduled AFTER this approval, so their not-yet-run status is not a missing proposal prerequisite. Unverified package/process scope remains a coverage gap, not proof that the declared single nominal trial is invalid. Reject real fixture or measurement defects; never approve only to make progress.",
        "stage_contract": {"current": "design_review", "next": ["known_network_calibration", "dut_trial", "post_trial_review"],
            "not_yet_required": ["calibration_results", "dut_results", "qualified_catalog_flag"],
            "scope_review_separate": True, "registration_before_post_trial_review": False},
        "shared_evidence": bundle(workflow), "method": descriptor, "reference": item,
        "expectation": expectation, "acceptance_standard": workflow.task.get("acceptance_standard"),
        "actual_circuit": render(p, workflow.task["model"]), "protocol": p, "protocol_sha256": fingerprint(p),
        "model_include_binding": {"file": "model.lib", "sha256": digest(workflow.model_path),
            "materialization": "Simulator copies the active model into the execution folder before LTspice; its exact text is in shared_evidence.model."},
        "condition_bindings": draft["condition_bindings"], "coverage_scope": draft["coverage_scope"],
        "package_coverage_complete": draft["scope_complete"], "previous_feedback": feedback}


def assets_root():
    return Path(__file__).resolve().parent.parent / "test_library_assets"


def catalog():
    root = assets_root()
    manifest = read(root / "catalog_manifest.json")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file() or digest(path) != expected:
            raise Fault("input", "测试方法库文件或哈希不符：" + name)
    methods = [read(root / name) for name in manifest["methods"]]
    return manifest, methods


def find(item):
    _, methods = catalog()
    key = item.get("reference_evidence", {}).get("key") or item.get("label", "")
    figure = item["id"].split(":")[1] if item["id"].startswith("figure:") else None
    matches = [m for m in methods if key in m["keys"] or figure in m.get("figures", [])]
    if len(matches) > 1:
        raise Fault("input", "测试方法匹配歧义：" + item["id"])
    return matches[0] if matches else None


def describe(item, model, standard=None):
    method = find(item)
    result = {"reference_id": item["id"], "kind": item["kind"],
              "binding_complete": bool(item.get("binding_complete")), "bindings": item.get("bindings", [])}
    if item["kind"] != "test":
        return {**result, "status": "constraint_audit_required" if item["kind"] == "constraint" else "informational"}
    if item.get("binding_complete") and item.get("bindings"):
        return {**result, "status": "already_configured_not_requalified", "method_id": method["id"] if method else None}
    if not method:
        return {**result, "status": "method_not_registered"}
    result.update(method_id=method["id"], method_stage=method["stage"], required=method["required"])
    if item.get("binding_complete") and item.get("bindings"):
        return {**result, "status": "already_configured_not_requalified"}
    if method["stage"] != "executable_draft":
        return {**result, "status": "interface_or_definition_required", "reason": method["reason"]}
    try:
        draft = build(method["id"], item, model)
        expectation = copy.deepcopy(item.get("test_contract", {}).get("expectation") or {
            "unit": item["reference_evidence"]["unit"],
            "limits": {k: v for k, v in item["reference_evidence"]["values"].items() if k in ("min", "max")},
            "typical": item["reference_evidence"]["values"].get("typ"), "thresholds": {}})
        expectation = expectation_with_standard(expectation, standard,
            draft["protocol"]["measurement"]["mode"] in ("curve", "ratio_curve"))
        validate_protocol(draft["protocol"], model)
        validate_measurement_unit({"protocol": draft["protocol"], "expectation": expectation})
        result.update(status="awaiting_qwen_and_live_qualification", draft=draft, expectation=expectation)
    except (Fault, KeyError) as e:
        result.update(status="evidence_or_adapter_required", reason=str(e))
    return result


def plan(task):
    manifest, _ = catalog()
    rows = [describe(i, task["model"], task.get("acceptance_standard")) for i in task["inventory"]["items"]]
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"schema": "test-library-plan-1", "catalog_version": manifest["version"],
            "device": task["device"], "counts": counts, "items": rows,
            "inventory_review": task["inventory"]["review_status"], "api_calls": 0,
            "simulations": 0, "qualified": False,
            "note": "方法存在不等于参考齐全、实际执行通过或手册覆盖完成"}


def qualify_one(workflow, item):
    """All success paths require Qwen, independent review, analytic calibration and DUT."""
    description = describe(item, workflow.task["model"], workflow.task.get("acceptance_standard"))
    if description["status"] != "awaiting_qwen_and_live_qualification":
        return {"reference_id": item["id"], "kind": "test_library_gap", "detail": description}
    method = find(item)
    identity = fingerprint({"qualification_version": QUALIFICATION_VERSION, "method": method, "item": item, "expectation": description["expectation"], "model_interface": workflow.task["model"]["ports"]})
    old = workflow.store.get("library_qualifications", identity) or {}
    if old.get("status") == "qualified":
        return copy.deepcopy(old["result"])
    library = workflow.task.get("capability_library")
    shared_key = fingerprint({"qualification_version": QUALIFICATION_VERSION, "method": method, "item": item, "expectation": description["expectation"], "model_sha256": digest(workflow.model_path)})
    shared = Path(library) / ("qualified_" + shared_key + ".json") if library else None
    cached = None
    if not old and shared and shared.exists():
        with file_lock(shared.with_suffix(".lock"), timeout=10):
            receipt = shared.with_suffix(".sha256")
            if not receipt.is_file() or digest(shared) != receipt.read_text().strip():
                raise Fault("cache_corrupt", "测试库资格记录哈希不符")
            cached = read(shared)
        if cached.get("qualification_version") != QUALIFICATION_VERSION or cached.get("stage") != "qualified_exact_contract" or cached.get("method_sha256") != fingerprint(method) or cached.get("model_sha256") != digest(workflow.model_path):
            raise Fault("cache_corrupt", "测试库资格记录的模型或方法不一致")
    if cached:
        case = cached["case"]
        validate_protocol(case["protocol"], workflow.task["model"], case["contract"])
        validate_measurement_unit(case)
        if case["expectation"] != description["expectation"]:
            raise Fault("cache_corrupt", "测试库参考或验收标准不一致")
        calibrate_method(workflow, method["id"], case)
        trial = workflow.evaluate_one(case, workflow.model_path, allow_recovery=False)
        if trial.get("execution") != "completed":
            raise Fault("fixture", "测试库复用时当前模型试运行失败", trial)
        result = {"case": case, "binding_scope_complete": cached["package_coverage_complete"],
                  "reference_id": item["id"], "scope": case["verification_scope"], "trial": trial}
        workflow.store.put("library_qualifications", identity, {"status": "qualified", "result": result})
        workflow.store.event("test_library", "qualified_record_reused", {"reference_id": item["id"]})
        return result
    feedback = old.get("feedback")
    tuning = old.get("tuning", {})
    for attempt in range(old.get("attempt", 0), workflow.policy["development_attempts"]):
        if attempt:
            workflow.store.reserve("repairs", "library:" + identity + ":" + str(attempt))
        try:
            route = recovery_route(feedback, method["id"])
            if feedback:
                workflow.store.event("test_recovery", route, {"reference_id": item["id"], "fault_kind": feedback.get("kind")})
            if feedback and route == "fixture_numeric":
                reply = workflow.agents.ask("test_library_adapter", {
                    "task": "Return {adjustments:{...}} using only the allowed numeric fixture controls, or {decision:defer,reason}. Do not change any handbook value, target, acceptance, polarity or topology. Actual previous failure is supplied.",
                    "method": method, "previous_tuning": {**defaults(), **tuning},
                    "allowed_adjustments": TUNABLE,
                    "reference": item, "feedback": feedback, "shared_evidence": bundle(workflow),
                    "stage": "fixture_repair", "expectation": description["expectation"]})
                if reply.get("decision") == "defer":
                    return {"reference_id": item["id"], "kind": "planner_deferred", "reason": reply.get("reason")}
                tuning = adjusted({**defaults(), **tuning}, reply["adjustments"])
            draft = build(method["id"], item, workflow.task["model"], tuning)
            p = draft["protocol"]
            expected = description["expectation"]
            validate_protocol(p, workflow.task["model"])
            validate_measurement_unit({"protocol": p, "expectation": expected})
            proposal = {"protocol": p, "condition_bindings": draft["condition_bindings"],
                        "unresolved_conditions": [], "measurement_rationale": draft["measurement_rationale"]}
            contract = extracted_contract(item, proposal)
            contract["expectation"] = expected
            if item.get("test_contract", {}).get("reference"):
                contract["reference"] = item["test_contract"]["reference"]
            # Freeze the complete generated circuit; validators cannot change it by approval.
            locked = {"fixed": {k: copy.deepcopy(v) for k, v in p.items()}}
            context = design_context(workflow, item, draft, expected, method, feedback)
            workflow.store.put("library_qualifications", identity, {"status": "reviewing", "attempt": attempt,
                "tuning": tuning, "feedback": feedback, "protocol_sha256": fingerprint(p)})
            review = workflow.agents.ask("test_library_validator", context)
            if not (review.get("decision") == "approve" and review.get("conditions_complete") is True
                    and review.get("measurement_correct") is True and review.get("approved_protocol_sha256") == fingerprint(p)
                    and review.get("stage", "design_review") == "design_review"):
                raise Fault("review", "Qwen测试库校验未通过", review)
            independent = workflow.agents.ask("test_reviewer", {**context,
                "task": context["task"] + " Independently review the ACTUAL circuit. Qwen agreement is not evidence. Return the exact supplied protocol hash.",
                "qwen_review": review})
            if not (independent.get("decision") == "approve" and independent.get("conditions_complete") is True
                    and independent.get("measurement_correct") is True
                    and independent.get("approved_protocol_sha256") == fingerprint(p)
                    and independent.get("stage", "design_review") == "design_review"):
                raise Fault("review", "独立测试审查未通过", independent)
            case = {"id": "library_" + identity[:16], "protocol": p, "contract": locked,
                    "expectation": expected, "evidence": item["evidence"], "origin": "gpt_recipe_qwen_qualified",
                    "test_library_method": method["id"], "verification_scope": draft["coverage_scope"]}
            if contract.get("reference"):
                case["reference"] = contract["reference"]
            workflow.store.event("test_library", "design_approved", {"reference_id": item["id"]})
            calibration = calibrate_method(workflow, method["id"], case)
            trial = workflow.evaluate_one(case, workflow.model_path, allow_recovery=False)
            if trial.get("execution") != "completed":
                raise Fault("fixture", "实际器件试运行或准备条件检查失败", trial)
            post_context = {**context, "stage": "post_trial_review", "calibration": calibration,
                "trial": trial, "phase_evidence": phase_evidence(p, trial),
                "task": "Review actual calibration and DUT trial against immutable conditions and the supplied phase evidence. Return {decision:approve|revise,stage:post_trial_review,approved_protocol_sha256:EXACT_SUPPLIED_HASH,conditions_complete:true|false,measurement_correct:true|false,issues:[],repair_adapter:review_revision|fixture_numeric}. Execution success alone is insufficient. Separate preparation, measurement and after-endpoint facts. Electrical fitting pass/fail is a model result, not evidence that the measurement method is invalid. Do not clear unverified package coverage."}
            qualification = workflow.agents.ask("test_reviewer", post_context)
            if not (qualification.get("decision") == "approve" and qualification.get("conditions_complete") is True
                    and qualification.get("measurement_correct") is True
                    and qualification.get("approved_protocol_sha256") == fingerprint(p)
                    and qualification.get("stage", "post_trial_review") == "post_trial_review"):
                raise Fault("qualification_review", "实测方法资格审查未通过", qualification)
            workflow.store.event("test_library", "post_trial_approved", {"reference_id": item["id"]})
            result = {"case": case, "binding_scope_complete": draft["scope_complete"],
                      "reference_id": item["id"], "scope": draft["coverage_scope"], "trial": trial}
            record = {"schema": "qualified-test-method-1", "qualification_version": QUALIFICATION_VERSION, "stage": "qualified_exact_contract",
                      "method_id": method["id"], "method_sha256": fingerprint(method), "case": case,
                      "qwen_review": review, "independent_review": independent, "calibration": calibration,
                      "post_trial_review": qualification, "phase_evidence": post_context["phase_evidence"], "trial": trial,
                      "model_sha256": digest(workflow.model_path), "package_coverage_complete": draft["scope_complete"],
                      "note": "复用仍须重新校准并在当前模型执行；典型值无容差时电气验收仍待定"}
            target = workflow.store.folder / "capabilities" / (identity + ".json")
            save(target, record)
            # Reuse only within the full immutable contract/model signature. Hash receipt is mandatory.
            library = workflow.task.get("capability_library")
            if library:
                key = fingerprint({"qualification_version": QUALIFICATION_VERSION, "method": method, "item": item, "expectation": expected, "model_sha256": record["model_sha256"]})
                target = Path(library) / ("qualified_" + key + ".json")
                with file_lock(target.with_suffix(".lock"), timeout=10):
                    save(target, record)
                    target.with_suffix(".sha256").write_text(digest(target) + "\n")
            workflow.store.put("library_qualifications", identity, {"status": "qualified", "attempt": attempt + 1,
                "result": result, "tuning": tuning})
            workflow.store.event("test_library", "qualified_exact_contract", {"reference_id": item["id"],
                "method": method["id"], "package_coverage_complete": draft["scope_complete"], "electrical_acceptance": trial.get("acceptance")})
            return result
        except BudgetEnd:
            raise
        except Fault as e:
            if e.kind in ("credentials", "authentication", "api_configuration", "cache_corrupt"):
                raise
            feedback = e.record()
        except (KeyError, ValueError, TypeError) as e:
            feedback = Fault("proposal", "测试库校验接口错误：" + str(e)).record()
        workflow.store.put("library_qualifications", identity, {"status": "revision_required", "attempt": attempt + 1,
            "tuning": tuning, "feedback": feedback})
        workflow.store.event("test_library", "revision_required", {"reference_id": item["id"], "feedback": feedback})
    return {"reference_id": item["id"], "kind": "library_qualification_budget", "last_fault": feedback}


def calibrate_method(workflow, method_id, case):
    receipts = []
    if method_id == "gate_charge_total":
        for oracle, value in gate_charge_oracles():
            calibration = {"id": "library_oracle_" + fingerprint(oracle)[:12], "protocol": oracle,
                "expectation": {"unit": "oracle_native", "limits": {"min": value * .999, "max": value * 1.001}}}
            measured = workflow.evaluate_one(calibration, workflow.model_path, allow_recovery=False)
            if measured.get("execution") != "completed" or measured.get("acceptance") != "pass":
                raise Fault("calibration", "释放开关的已知电容校准失败", {"expected": value, "actual": measured})
            receipts.append({"kind": "release_switch_capacitor", "expected": value, "result": measured})
    if case["protocol"]["measurement"]["mode"] == "ratio_curve":
        from .spice import measure
        from .task import component
        for resistance in (1000, 2000):
            oracle = {"temperature_C": 25, "device_nodes": {},
                "components": [component("R", "RCAL", "P", "0", resistance), component("I", "ICAL", "0", "P", {"dc": 0})],
                "analysis": {"kind": "dc", "source": "ICAL", "start": .0001, "stop": .0005, "step": .0001},
                "measurement": {"mode": "ratio_curve", "signal": "v(p)", "denominator": "i(ical)"}}
            data, folder = workflow.simulator.run(oracle, workflow.model_path, "resistance_curve_calibration")
            result = measure(oracle, data, [(.0001, resistance), (.0005, resistance)])
            if result["metrics"]["max_absolute_error"] > resistance * 1e-4:
                raise Fault("calibration", "实际V/I曲线校准失败", {"expected": resistance, "actual": result})
            receipts.append({"kind": "resistance_ratio", "expected": resistance, "result": result})
    receipts += workflow.calibrate(case)
    return {"status": "passed", "trusted_oracles": receipts}


def attach(source, target, corrections=False, standard=None):
    """Create a NEW task; never mutate an active task or claim coverage completion."""
    from .task import load_task
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.exists():
        raise Fault("input", "请使用新的任务输入文件")
    task, _ = load_task(source)
    if corrections:
        if task["device"] != "BUK7K52-60E":
            raise Fault("input", "BUK示例清单修订不适用于其他型号")
        from .test_scope import expand_buk
        task["inventory"] = expand_buk(task["inventory"])
    if standard is not None:
        task = apply_standard(task, standard)
    task["test_library"] = {"enabled": True, "catalog_version": catalog()[0]["version"]}
    task["capability_library"] = str(target.parent.parent / "qualified_tests")
    for field in ("template_retrieval", "candidates"):
        if task.get("template_selection"):
            task.pop(field, None)
    save(target, task)
    return target
