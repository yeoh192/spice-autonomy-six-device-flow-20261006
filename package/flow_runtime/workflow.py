"""One-launch development, diagnosis, optimization, regression and delivery routing."""
import concurrent.futures
import copy
import difflib
import re
import shutil
from pathlib import Path
from .state import Fault, BudgetEnd, digest, fingerprint, read, save
from .spice import (acceptance, load_reference, measure, model_text, render,
                    validate_model, validate_protocol, validate_measurement_unit)
from .task import component
from .contracts import can_extract, extracted_contract
from .development_interfaces import interface_evidence

INTERFACES = {
    "circuit": "R/C/L, independent V/I (DC/AC/PWL), numeric diode and voltage-controlled release switch fixtures, one DUT with task-defined pins",
    "analysis": "DC sweep, single-frequency or bounded linear AC sweep, transient",
    "measurement": "transient_frequency/transient_duty (at least 3 full cycles), rising/falling10-90% edge time, triggered delay, event-time bias sample, bounded DC/transient span; sample, dc_current_max (max absolute input current), dc_current_difference (actual probe difference), dc_slope, dc_sensitivity_error_percent, dc_linearity_percent, curve, ratio_curve (actual V/I), capacitance, complex V/I impedance/ESR/C/L/loss and voltage-ratio gain/dB/phase, integral_to_crossing with explicit integration start and target",
    "forbidden": "arbitrary Python/shell, external model files, changes to immutable reference/acceptance/ports",
}


def calibration_protocols(case):
    """Trusted analytic oracles; expected values are NOT supplied by an agent."""
    mode = case["protocol"]["measurement"]["mode"]
    from . import pulse_metrology
    if mode in pulse_metrology.MODES:
        return pulse_metrology.oracles(case)
    if mode in ("transient_peak","transient_recovery"):
        from .transient_metrology import oracles
        return [(p,e) for p,e,_ in oracles(case)]
    if mode in ('dc_current_max','dc_current_difference'):
        from .static_metrology import current_oracles
        return [(p,e) for p,e,_ in current_oracles(case)]
    if mode in ('dc_slope','dc_sensitivity_error_percent','dc_linearity_percent'):
        from .dc_transfer import oracles
        return [(p,e) for p,e,_ in oracles(case)]
    from .ac_measurements import MODES
    if mode in MODES:
        from .ac_calibration import protocols as ac_protocols
        return ac_protocols(mode)
    protocols = []
    for factor in (1, 2):
        base = {"device_nodes": {}, "temperature_C": 25, "components": [], "checks": []}
        if mode == "capacitance":
            capacitance = factor * 1e-9
            base["components"] = [component("C", "CCAL", "P", "0", capacitance),
                                  component("V", "VCAL", "P", "0", {"dc": 0, "ac": 1})]
            base["analysis"] = {"kind": "ac", "frequency_Hz": 1e6}
            base["measurement"] = {"mode": mode, "signal": "i(vcal)", "sign": -1}
            expected = capacitance
        elif mode == "integral_to_crossing":
            capacitance = factor * 1e-9
            base["components"] = [component("C", "CCAL", "P", "0", capacitance),
                component("V", "VCAL", "P", "0", {"pwl": [[0, 0], [1e-6, 0], [11e-6, 10], [12e-6, 10]]})]
            base["analysis"] = {"kind": "tran", "stop_s": 12e-6, "max_step_s": 1e-8}
            base["measurement"] = {"mode": mode, "signal": "i(vcal)", "sign": -1, "start_s": 1e-6,
                "target": {"signal": "v(p)", "value": 10, "direction": "rising"}}
            base["checks"] = [{"signal": "v(p)", "from": 0, "to": 1e-6, "min": -1e-9, "max": 1e-9}]
            expected = capacitance * 10
        else:
            resistance, current = 1000, factor * .00025
            base["components"] = [component("R", "RCAL", "P", "0", resistance),
                                  component("I", "ICAL", "0", "P", {"dc": 0})]
            base["analysis"] = {"kind": "dc", "source": "ICAL", "start": 0, "stop": .001, "step": .00005}
            base["measurement"] = {"mode": "sample", "signal": "v(p)", "at": current}
            expected = resistance * current
        protocols.append((base, expected))
    return protocols


def result_cost(case, result):
    if result.get("execution") != "completed":
        return 1e6
    if "metrics" in result:
        return result["metrics"].get("MAE_over_reference_span_percent") or result["metrics"]["MAE"]
    value, e = result["value"], case["expectation"]
    if e.get("typical") not in (None, 0):
        return abs(value / e["typical"] - 1) * 100
    limits = e.get("limits", {})
    return sum(max(0, (v-value) / max(abs(v), 1e-15) * 100) if k == "min" else max(0, (value-v) / max(abs(v), 1e-15) * 100) for k, v in limits.items())


def retention(cases, before, after, policy):
    reasons = []
    old = {r["test"]: r for r in before}
    new = {r["test"]: r for r in after}
    if set(new) != {c["id"] for c in cases}:
        reasons.append("完整回归缺少测试")
    for c in cases:
        a, b = old.get(c["id"], {}), new.get(c["id"], {})
        if b.get("execution") != "completed" and (not policy.get("continuous_until_acceptance") or a.get("execution") == "completed"):
            reasons.append(c["id"] + ":执行失败")
        if a.get("acceptance") == "pass" and b.get("acceptance") != "pass":
            reasons.append(c["id"] + ":已通过特性退化")
        if "metrics" in a and "metrics" in b:
            for k in ("MAE", "max_absolute_error"):
                maximum = a["metrics"][k] * (1 + policy["max_metric_regression_percent"] / 100) + 1e-15
                if b["metrics"][k] > maximum:
                    reasons.append(c["id"] + ":曲线退化" + k)
        if c["expectation"].get("unit") == "F" and c["expectation"].get("typical", 0) and b.get("value", 1) <= 0:
            reasons.append(c["id"] + ":电容响应损坏")
    old_failed = sum(r.get("execution") != "completed" or r.get("acceptance") == "fail" for r in before)
    new_failed = sum(r.get("execution") != "completed" or r.get("acceptance") == "fail" for r in after)
    old_loss = sum(result_cost(c, old.get(c["id"], {})) for c in cases)
    new_loss = sum(result_cost(c, new.get(c["id"], {})) for c in cases)
    improved = new_failed < old_failed or (new_failed == old_failed and new_loss < old_loss - max(1e-9, old_loss * 1e-6))
    if not improved:
        reasons.append("无可验证增益")
    return not reasons, reasons, {"before_cost": old_loss, "after_cost": new_loss}


class Workflow:
    def __init__(self, task, store, agents, simulator):
        self.task, self.store, self.agents, self.simulator = task, store, agents, simulator
        self.policy = task["policy"]
        self.store.continuous = self.policy.get("continuous_until_acceptance", False)
        checkpoint = store.get("checkpoints", "workflow")
        if checkpoint:
            self.cases, self.inventory = checkpoint["cases"], checkpoint["inventory"]
            self.model_path = Path(checkpoint["model_path"])
            if digest(self.model_path) != checkpoint["model_sha256"]:
                raise Fault("cache_corrupt", "保留模型已被修改，不能续跑")
            self.results, self.gaps = checkpoint["results"], checkpoint["gaps"]
            self.developed, self.optimization_index = checkpoint["developed"], checkpoint["optimization_index"]
            self.task["model"] = checkpoint["active_model"]
            self.simulator.model = self.task["model"]
            self.selected = checkpoint["selected"]
        else:
            self.cases = copy.deepcopy(task["cases"])
            self.inventory = copy.deepcopy(task["inventory"])
            self.model_path = store.folder / "models" / "input_model.lib"
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(task["model"]["path"], self.model_path)
            self.results, self.gaps, self.developed, self.optimization_index = [], [], [], 0
            self.selected = not task.get("select_candidate", False)
            self.checkpoint()

    def checkpoint(self):
        self.store.put("checkpoints", "workflow", {"cases": self.cases, "inventory": self.inventory,
            "model_path": str(self.model_path), "model_sha256": digest(self.model_path), "results": self.results,
            "gaps": self.gaps, "developed": self.developed, "optimization_index": self.optimization_index,
            "active_model": self.task["model"], "selected": self.selected})

    def preflight(self):
        if self.task.get("template_retrieval"):
            save(self.store.folder / "template_selection/retrieval.json", self.task["template_retrieval"])
            print("索引检索：%d项，结构兼容：%d项" % (self.task["template_retrieval"]["retrieved"], self.task["template_retrieval"]["compatible"]), flush=True)
        required = [i for i in self.inventory["items"] if i["kind"] == "test"]
        gaps = [i["id"] for i in required if not i.get("binding_complete") or not i.get("bindings")]
        save(self.store.folder / "preflight.json", {"tests": len(self.cases), "missing": gaps,
            "constraints": [i["id"] for i in self.inventory["items"] if i["kind"] == "constraint"],
            "inventory_review": self.inventory["review_status"], "interfaces": INTERFACES,
            "model_provenance": self.task["model"].get("provenance"), "budgets": self.task["budgets"],
            "needs_structured_contract": [i["id"] for i in required if i["id"] in gaps and not i.get("test_contract")]})
        if self.task.get("test_library", {}).get("enabled"):
            from .test_library import plan
            save(self.store.folder / "test_library_plan.json", plan(self.task))
        self.store.event("preflight", "completed", {"tests": len(self.cases), "missing": len(gaps)})
        print("配置测试：%d；缺少绑定：%d；清单复核：%s" % (len(self.cases), len(gaps), self.inventory["review_status"]), flush=True)

    def run(self, check_only=False):
        terminal = "finished"
        try:
            self.preflight()
            if check_only:
                self.store.finish("prepared")
                return {"workflow_status": "prepared", "api_calls": 0, "simulations": 0}
            if self.task.get("input_integration", {}).get("execution_scope") == "configured_regression_only":
                # Material migration is not full fixture qualification. Run only
                # the explicit frozen regression scope; don't spend on fitting
                # before the expanded coverage/port/fixture gate is completed.
                self.results = self.evaluate_all(self.model_path, "configured_regression")
                self.store.event("input_scope_gate", "fitting_not_enabled", self.task["input_integration"])
                self.checkpoint()
                return self.audit("finished_with_gaps")
            if self.store.continuous:
                from .continuous import run
                return run(self)
            self.select_template()
            # Preserve evidence for the configured scope before spending development
            # budget. Fitting waits until the complete test plan has been attempted.
            self.results = self.evaluate_all(self.model_path, "initial_known_tests")
            self.checkpoint()
            self.develop_missing()
            self.results = self.evaluate_all(self.model_path, "baseline")
            self.checkpoint()
            self.optimize()
            if self.policy.get("model_diagnosis_enabled"):
                from .model_diagnostics import repair
                repair(self)
        except KeyboardInterrupt:
            terminal = "interrupted"
            self.store.event("workflow", terminal)
        except Fault as e:
            terminal = "budget_exhausted" if isinstance(e, BudgetEnd) else "stopped_with_evidence"
            self.gaps.append({"stage": "workflow", "fault": e.record()})
            self.store.event("workflow", terminal, e.record())
        except Exception as e:
            # Local implementation errors are distinct from a SPICE/model diagnosis.
            terminal = "framework_error"
            self.gaps.append({"stage": "framework", "kind": type(e).__name__, "message": str(e)[:1000]})
            self.store.event("workflow", terminal, self.gaps[-1])
        self.checkpoint()
        return self.audit(terminal)

    def qualify_library(self):
        from .test_library import plan
        terminal = "qualification_completed_with_gaps"
        try:
            self.preflight()
            save(self.store.folder / "test_library_plan.json", plan(self.task))
            self.develop_missing()
        except KeyboardInterrupt:
            terminal = "interrupted"
        except Fault as e:
            terminal = "budget_exhausted" if isinstance(e, BudgetEnd) else "stopped_with_evidence"
            self.gaps.append({"stage": "test_library", "fault": e.record()})
        self.checkpoint()
        save(self.store.folder / "active_cases.json", self.cases)
        save(self.store.folder / "active_inventory.json", self.inventory)
        qualified = [c for c in self.cases if c.get("origin") == "gpt_recipe_qwen_qualified"]
        summary = {"workflow_status": terminal, "device": self.task["device"],
            "qualified_exact_contract_tests": len(qualified), "qualified_test_ids": [c["id"] for c in qualified],
            "missing_bindings": [i["id"] for i in self.inventory["items"] if i["kind"] == "test" and not i.get("binding_complete")],
            "gaps": self.gaps, "model_modified": False, "full_device_acceptance": False,
            "note": "资格测试不做选模或模型优化；使用输入中明确模型，不自动宣布整个器件通过"}
        save(self.store.folder / "summary.json", summary)
        self.store.finish(terminal)
        print("测试库资格验证：", terminal, "；已登记：", len(qualified), flush=True)
        print("汇总：", self.store.folder / "summary.json", flush=True)
        return summary

    def select_template(self):
        if self.selected:
            return
        if self.task.get("template_selection"):
            from .indexed_selection import choose
            return choose(self)
        available = [c for c in self.task.get("candidates", []) if not self.policy["require_non_vendor"] or
            (c.get("provenance", {}).get("kind") == "non_vendor" and c["provenance"].get("evidence"))]
        if not available:
            raise Fault("template_provenance", "候选库没有符合来源要求的模板；禁止改用原厂模板")
        saved = self.store.get("plans", "template_selection") or {}
        feedback = saved.get("feedback")
        for n in range(saved.get("attempt", 0), self.policy["selection_attempts"]):
            if n:
                self.store.reserve("repairs", "template-selection:" + str(n))
            choice = self.agents.ask("template_selector", {"task": "Choose a candidate from the supplied library. Return {candidate_id,reason,evidence_ids}. No downloading/substituting manufacturer models.",
                "device": self.task["device"], "manual": self.inventory,
                "candidates": [{**c, "model_excerpt": model_text(c["path"])[:8000]} for c in available], "feedback": feedback})
            matches = [c for c in available if c["id"] == choice.get("candidate_id")]
            if len(matches) == 1:
                chosen = matches[0]
                review = self.agents.ask("template_reviewer", {"task": "Review candidate selection against library provenance, interface and manual requirements; return decision approve|revise,issues.",
                    "choice": choice, "candidate": chosen, "model_excerpt": model_text(chosen["path"])[:8000], "manual": self.inventory})
                if review.get("decision") == "approve":
                    self.task["model"] = copy.deepcopy(chosen)
                    self.simulator.model = self.task["model"]
                    self.model_path = self.store.folder / "models" / ("selected_" + chosen["sha256"] + ".lib")
                    shutil.copyfile(chosen["path"], self.model_path)
                    for case in self.cases:
                        validate_protocol(case["protocol"], chosen, case.get("contract"))
                    self.selected = True
                    self.checkpoint()
                    self.store.event("template_selection", "completed", {"candidate": chosen["id"]})
                    return
                feedback = {"kind": "review", "review": review, "choice": choice}
            else:
                feedback = {"kind": "proposal", "reason": "候选ID不存在或不符合来源要求", "choice": choice}
            self.store.put("plans", "template_selection", {"attempt": n+1, "feedback": feedback})
            self.store.event("template_selection", "revision_required", feedback)
        raise Fault("template_selection_budget", "模板选择预算内未通过", feedback)

    def develop_missing(self):
        pending = [i for i in self.inventory["items"] if i["kind"] == "test" and (not i.get("bindings") or not i.get("binding_complete")) and i["id"] not in self.developed]
        eligible = []
        for i in pending:
            from .test_library import find
            library_method = self.task.get("test_library", {}).get("enabled") and find(i)
            if not i.get("test_contract") and not can_extract(i) and not library_method:
                self.gaps.append({"reference_id": i["id"], "kind": "missing_structured_contract",
                    "reason": "原资料未提供完整可机读条件/测量定义；不得由程序补猜条件"})
                self.developed.append(i["id"])
            else:
                eligible.append(i)
        self.checkpoint()
        if not eligible:
            return
        self.agents.credentials(["test_designer", "test_reviewer"])
        first_fault = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.policy["workers"]) as pool:
            futures = {pool.submit(self.develop_one, item): item for item in eligible}
            for future in concurrent.futures.as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                except Fault as e:
                    if first_fault is None:
                        first_fault = e
                    result = {"reference_id": item["id"], "kind": e.kind, "fault": e.record()}
                if result.get("case"):
                    case = result["case"]
                    self.cases.append(case)
                    if result.get("trial"):
                        self.results.append(result["trial"])
                    item["bindings"] = [case["id"]]
                    item["binding_complete"] = result.get("binding_scope_complete", True)
                    if not item["binding_complete"]:
                        self.gaps.append({"reference_id": item["id"], "kind": "channel_scope_pending",
                            "reason": "方法已在单个名义DUT上试运行；不能据此证明双通道封装均已验证"})
                    self.store.event("capability_development", "registered", {"reference_id": item["id"], "test": case["id"]})
                else:
                    self.gaps.append(result)
                self.developed.append(item["id"])
                self.checkpoint()
        if first_fault:
            raise first_fault

    def develop_one(self, item):
        if self.task.get("test_library", {}).get("enabled"):
            from .test_library import find, qualify_one
            if find(item):
                return qualify_one(self, item)
        if not item.get("test_contract"):
            extracted = self.normalize_contract(item)
            if not extracted.get("contract"):
                return extracted
            item["test_contract"] = extracted["contract"]
        contract = item["test_contract"]
        if self.task.get("acceptance_standard"):
            from .project_standard import expectation_with_standard
            contract["expectation"] = expectation_with_standard(contract["expectation"],
                self.task["acceptance_standard"], bool(contract.get("reference")))
        # The immutable contract must contain a measurement definition and fixed conditions.
        if not contract.get("fixed") or not contract.get("measurement"):
            return {"reference_id": item["id"], "kind": "missing_structured_contract"}
        identity = fingerprint(item)
        library_key = fingerprint({"qualification_version": 2, "model_sha256": digest(self.model_path), "contract": contract, "label": item.get("label"),
            "ports": self.task["model"]["ports"], "interfaces": INTERFACES})
        library = Path(self.task["capability_library"]) if self.task.get("capability_library") else None
        if library:
            descriptor = library / (library_key + ".json")
            receipt = library / (library_key + ".sha256")
            if descriptor.exists() and receipt.exists() and digest(descriptor) == receipt.read_text().strip():
                saved = read(descriptor)
                case = saved["case"]
                validate_protocol(case["protocol"], self.task["model"], case["contract"])
                if saved.get("qualification_version") != 2 or not saved.get("post_trial_review"):
                    raise Fault("cache_corrupt", "能力库缺少实测资格审查记录")
                if case["expectation"] != contract["expectation"]:
                    raise Fault("cache_corrupt", "能力库参考值不一致")
                self.calibrate(case)
                trial = self.evaluate_one(case, self.model_path)
                if trial["execution"] == "completed":
                    self.store.event("capability_development", "library_reused", {"key": library_key})
                    return {"case": case}
        old = self.store.get("plans", identity)
        if old and old.get("status") == "registered":
            return {"case": old["case"]}
        feedback = (old or {}).get("feedback")
        start = (old or {}).get("attempt", 0)
        for n in range(start, self.policy["development_attempts"]):
            if n:
                self.store.reserve("repairs", "develop:" + identity + ":" + str(n))
            self.store.put("plans", identity, {"status": "planning", "attempt": n, "feedback": feedback})
            try:
                from .evidence import bundle, phase_evidence
                shared = bundle(self)
                context = {"task": "Return {decision:propose,protocol:{...}} or defer with reason. Build a circuit using only the declared interfaces.",
                    "interfaces": INTERFACES, "model_interface": {k: self.task["model"][k] for k in ("entry", "ports", "declared_ports")},
                    "interface_evidence": interface_evidence(self,item),
                    "reference": item, "contract": contract, "feedback": feedback,
                    "shared_evidence": shared, "stage": "design"}
                proposal = self.agents.ask("test_designer", context)
                if proposal.get("decision") == "defer":
                    return {"reference_id": item["id"], "kind": "planner_deferred", "reason": proposal.get("reason")}
                p = proposal["protocol"]
                locked = {"fixed": {**contract["fixed"], "measurement": contract["measurement"]}}
                validate_protocol(p, self.task["model"], locked)
                if not p["device_nodes"]:
                    raise Fault("proposal", "器件测试必须包含DUT")
                case = {"id": "developed_" + identity[:16], "protocol": p, "contract": locked,
                        "expectation": contract["expectation"], "evidence": item["evidence"], "origin": "agent_developed"}
                if contract.get("reference"):
                    case["reference"] = contract["reference"]
                validate_measurement_unit(case)
                circuit = render(p, self.task["model"])
                review = self.agents.ask("test_reviewer", {"task": "Review the proposed circuit against immutable conditions and measurement. Calibration and DUT trial follow approval; absent future trial results are not proposal prerequisites. Return {decision:approve|revise,issues:[],evidence_ids:[]}.",
                    "interface_evidence": interface_evidence(self,item,p),
                    "reference": item, "contract": contract, "actual_circuit": circuit, "protocol": p,
                    "shared_evidence": shared, "stage": "design_review", "protocol_sha256": fingerprint(p),
                    "calibration_oracles": "Trusted independent resistor/capacitor tests verify measurement primitives; they do not prove DUT fixture correctness."})
                if review.get("decision") != "approve":
                    raise Fault("review", "测试审查要求修订", review)
                calibration = self.calibrate(case)
                # Run the actual DUT before capability registration; electrical deviations
                # are model-repair input, whereas execution/fixture errors require redesign.
                result = self.evaluate_one(case, self.model_path, allow_recovery=False)
                if result["execution"] != "completed":
                    raise Fault("fixture", "器件试运行失败", result)
                qualification = self.agents.ask("test_reviewer", {
                    "task": "Review actual calibration and DUT trial against the frozen conditions. Return {decision:approve|revise,approved_protocol_sha256:EXACT_SUPPLIED_HASH,conditions_complete:true|false,measurement_correct:true|false,issues:[]}. Running successfully is insufficient; electrical fitting deviations are model results, not automatic measurement-method rejection.",
                    "stage": "post_trial_review", "shared_evidence": shared, "reference": item,
                    "contract": contract, "actual_circuit": circuit, "protocol": p, "protocol_sha256": fingerprint(p),
                    "calibration": calibration, "trial": result, "phase_evidence": phase_evidence(p, result)})
                if not (qualification.get("decision") == "approve" and qualification.get("conditions_complete") is True
                        and qualification.get("measurement_correct") is True
                        and qualification.get("approved_protocol_sha256") == fingerprint(p)):
                    raise Fault("qualification_review", "实测资格审查未通过", qualification)
                save(self.store.folder / "capabilities" / (identity + ".json"), {"qualification_version": 2,
                    "case": case, "review": review, "post_trial_review": qualification, "calibration": calibration, "trial": result,
                    "scope": "validated for this immutable task contract; not proof for all devices"})
                if library:
                    library.mkdir(parents=True, exist_ok=True)
                    save(library / (library_key + ".json"), {"qualification_version": 2,
                        "case": case, "review": review, "post_trial_review": qualification, "calibration": calibration,
                        "scope": "exact declared contract; recalibrate and rerun on reuse"})
                    (library / (library_key + ".sha256")).write_text(digest(library / (library_key + ".json")) + "\n")
                self.store.put("plans", identity, {"status": "registered", "case": case, "attempt": n+1})
                return {"case": case}
            except BudgetEnd:
                raise
            except (KeyError, TypeError) as e:
                feedback = Fault("proposal", "提案接口错误：" + str(e)).record()
            except Fault as e:
                if e.kind in ("authentication", "credentials", "api_configuration", "cache_corrupt", "request_context"):
                    raise
                if e.kind in ("capability", "missing_data"):
                    return {"reference_id": item["id"], "kind": e.kind, "last_fault": e.record()}
                feedback = e.record()
            self.store.put("plans", identity, {"status": "revision_required", "attempt": n+1, "feedback": feedback})
            self.store.event("capability_development", "revision_required", feedback)
        return {"reference_id": item["id"], "kind": "development_budget", "last_fault": feedback}

    def normalize_contract(self, item):
        from .test_families import mapping
        family_context = mapping({"items": [item]}, self.task["device"])
        from .evidence import bundle
        key = "contract:" + fingerprint(item)
        saved = self.store.get("plans", key)
        if saved and saved.get("contract"):
            return saved
        feedback = (saved or {}).get("feedback")
        start = (saved or {}).get("attempt", 0)
        for n in range(start, self.policy["development_attempts"]):
            if n:
                self.store.reserve("repairs", key + ":" + str(n))
            try:
                proposal = self.agents.ask("contract_normalizer", {"test_family_context": family_context,
                    "task": "Translate supplied evidence into an executable measurement contract. Return {decision:propose, protocol:{...}, condition_bindings:[{quote,protocol_path}], unresolved_conditions:[], measurement_rationale}. The quote must be an exact substring of reference_evidence.conditions. Paths use components@NAME/value/dc, components@NAME/value/pwl/INDEX/1, measurement/at, measurement/target/value or temperature_C. Every numeric manual condition must bind to a real circuit value in SI units. Never invent ports, data, measurement tolerance or unresolved dual-channel/package conditions. Use defer when evidence is insufficient. Output measurement must use the original reference unit (scale integral for nC if necessary).",
                    "reference": item, "interfaces": INTERFACES,
                    "model_interface": {k: self.task["model"][k] for k in ("entry", "ports", "declared_ports")},
                    "feedback": feedback, "interface_evidence": interface_evidence(self,item), "shared_evidence": bundle(self)})
                if proposal.get("decision") == "defer":
                    return {"reference_id": item["id"], "kind": "contract_deferred", "reason": proposal.get("reason")}
                validate_protocol(proposal["protocol"], self.task["model"])
                contract = extracted_contract(item, proposal)
                if self.task.get("acceptance_standard"):
                    from .project_standard import expectation_with_standard
                    contract["expectation"] = expectation_with_standard(contract["expectation"],
                        self.task["acceptance_standard"], proposal["protocol"]["measurement"]["mode"] in ("curve", "ratio_curve"))
                validate_measurement_unit({"protocol": proposal["protocol"], "expectation": contract["expectation"]})
                review = self.agents.ask("test_reviewer", {
                    "task": "Review the evidence interpretation, all qualitative and numeric manual conditions, unit scaling and measurement meaning, against the actual circuit. Return decision approve|revise plus conditions_complete:true only if every supplied manual condition is represented. Do not assume a single-channel model represents a multi-channel package.",
                    "interface_evidence": interface_evidence(self,item,proposal["protocol"]),
                    "shared_evidence": bundle(self), "reference": item, "proposal": proposal, "derived_contract": contract,
                    "actual_circuit": render(proposal["protocol"], self.task["model"])})
                if review.get("decision") != "approve" or review.get("conditions_complete") is not True:
                    raise Fault("review", "手册条件整理未通过独立审查", review)
                result = {"contract": contract, "attempt": n+1}
                self.store.put("plans", key, result)
                self.store.event("contract_normalization", "completed", {"reference_id": item["id"]})
                return result
            except BudgetEnd:
                raise
            except (KeyError, TypeError, ValueError) as e:
                feedback = Fault("proposal", "条件接口错误：" + str(e)).record()
            except Fault as e:
                if e.kind in ("authentication", "credentials", "api_configuration", "cache_corrupt", "request_context"):
                    raise
                if e.kind in ("capability", "missing_data"):
                    return {"reference_id": item["id"], "kind": e.kind, "last_fault": e.record()}
                feedback = e.record()
            self.store.put("plans", key, {"attempt": n+1, "feedback": feedback})
            self.store.event("contract_normalization", "revision_required", feedback)
        return {"reference_id": item["id"], "kind": "contract_budget", "last_fault": feedback}

    def calibrate(self, case):
        receipts = []
        for p, expected in calibration_protocols(case):
            tol = max(abs(expected) * 1e-4, 1e-15)
            oracle = {"id": "calibration_" + fingerprint(p)[:12], "protocol": p,
                "contract": {"fixed": {"temperature_C": p["temperature_C"], "components": p["components"], "measurement": p["measurement"]}},
                "expectation": {"unit": "oracle_native", "limits": {"min": expected-tol, "max": expected+tol}}}
            result = self.evaluate_one(oracle, self.model_path)
            if result["execution"] != "completed" or result["acceptance"] != "pass":
                raise Fault("calibration", "已知电路校准失败", {"actual": result, "expected": expected, "tolerance": tol})
            receipts.append({"expected": expected, "tolerance": tol, "result": result})
        self.store.event("calibration", "passed")
        return receipts

    def evaluate_one(self, case, model_path, allow_recovery=True):
        protocol = copy.deepcopy(case["protocol"])
        reference = None
        if case.get("reference"):
            reference = load_reference(case["reference"]["path"], case["expectation"]["unit"], case["reference"].get("condition"))
        feedback, retry = None, 0
        attempts = self.policy["execution_attempts"] if allow_recovery else 1
        for attempt in range(attempts):
            try:
                data, folder = self.simulator.run(protocol, model_path, case["id"], retry)
                self.store.event("result_comparison", "running", {"test": case["id"]})
                result = measure(protocol, data, reference)
                result.update(test=case["id"], execution="completed", acceptance=acceptance(result, case["expectation"]),
                    model_sha256=digest(model_path), protocol_sha256=fingerprint(protocol), artifacts=str(folder), unit=case["expectation"]["unit"])
                self.store.event("result_comparison", "completed", {"test": case["id"], "acceptance": result["acceptance"]})
                result["artifact_hashes"] = self.store.get("simulations", folder.name)["hashes"]
                if data.get("normalizations"):
                    result["raw_normalization"] = {k: data[k] for k in ("raw_points", "parsed_points", "normalizations")}
                if case["expectation"]["unit"] == "F" and result.get("value", 1) < 0:
                    result.update(acceptance="fail", model_conflict="negative capacitance")
                if result.get("comparison"):
                    save(folder / "comparison.json", result["comparison"])
                save(folder / "result.json", result)
                return result
            except BudgetEnd:
                raise
            except Fault as e:
                if e.kind == "cache_corrupt":
                    raise
                feedback = e.record()
                if attempt + 1 >= attempts:
                    break
                self.store.reserve("repairs", "execution:" + fingerprint({"case": case, "model": digest(model_path), "attempt": attempt}))
                self.store.event("diagnosis", "running", {"test": case["id"], "fault": feedback})
                from .evidence import bundle
                recovery_evidence = bundle(self, [case], [], model_path=model_path)
                diagnosis = self.agents.ask("execution_diagnoser", {"task": "Classify this recorded fault. Choose {decision:retry|numerical_change|defer,method:trap|gear,reason,evidence_ids}. Numerical changes may only change solver method or halve max_step_s; immutable sources, conditions and measurement cannot change.",
                    "fault": feedback, "protocol": protocol, "actual_circuit": render(protocol, self.task["model"]),
                    "contract": case.get("contract"), "tried": {"attempt": attempt, "retry": retry}, "shared_evidence": recovery_evidence})
                if diagnosis.get("decision") == "defer":
                    feedback["diagnosis"] = diagnosis
                    break
                review = self.agents.ask("diagnosis_reviewer", {"task": "Review diagnosis/action against actual circuit and failure evidence. Return decision approve|revise and issues. Do not infer causality from returncode alone.",
                    "fault": feedback, "diagnosis": diagnosis, "actual_circuit": render(protocol, self.task["model"]), "shared_evidence": recovery_evidence})
                if review.get("decision") != "approve":
                    # A rejected diagnosis is fed back to the diagnosing role, not the test designer.
                    feedback["review"] = review
                    diagnosis = self.agents.ask("execution_diagnoser", {"task": "Revise the diagnosis after independent review. Return decision retry|numerical_change|defer; method trap|gear if numerical_change.",
                        "fault": feedback, "previous": diagnosis, "protocol": protocol, "shared_evidence": recovery_evidence})
                    if diagnosis.get("decision") == "defer":
                        break
                    review = self.agents.ask("diagnosis_reviewer", {"task": "Review revised recovery action; approve|revise.", "fault": feedback, "diagnosis": diagnosis, "protocol": protocol, "shared_evidence": recovery_evidence})
                    if review.get("decision") != "approve":
                        feedback["review"] = review
                        break
                decision = diagnosis.get("decision")
                if e.kind == "parser" and decision in ("retry", "numerical_change"):
                    feedback["diagnosis"] = diagnosis
                    feedback["reason"] = "解析失败需要受验证的读取器修复；数值重试不能修复解析代码"
                    self.store.event("execution_recovery", "parser_action_blocked", {"test": case["id"]})
                    break
                if decision == "numerical_change":
                    if protocol["analysis"]["kind"] != "tran":
                        feedback["reason"] = "trap/gear是瞬态积分方法，不能作为DC或AC失败的修复动作"
                        self.store.event("execution_recovery", "irrelevant_method_blocked", {"test": case["id"]})
                        break
                    changed = copy.deepcopy(protocol)
                    changed["method"] = diagnosis.get("method", "gear")
                    if changed["analysis"]["kind"] == "tran":
                        changed["analysis"]["max_step_s"] /= 2
                    validate_protocol(changed, self.task["model"], case.get("contract"))
                    if fingerprint(changed) == fingerprint(protocol):
                        feedback["reason"] = "重复的数值动作"
                        break
                    protocol = changed
                elif decision != "retry":
                    feedback["reason"] = "未支持的诊断动作"
                    break
                retry += 1
                self.store.event("execution_recovery", "retrying", {"test": case["id"], "action": decision})
        return {"test": case["id"], "execution": "failed", "acceptance": "not_evaluated", "fault": feedback, "model_sha256": digest(model_path)}

    def evaluate_all(self, model_path, stage):
        self.store.event(stage, "running", {"tests": len(self.cases), "model_sha256": digest(model_path)})
        results = []
        for case in self.cases:
            result = self.evaluate_one(case, model_path)
            results.append(result)
            save(self.store.folder / (stage + "_results.json"), results)
            # Commit individual baseline progress; rollback candidates remain separate.
            if model_path == self.model_path:
                self.results = results.copy()
                self.checkpoint()
            self.store.event(case["id"], result["execution"], {"acceptance": result["acceptance"]})
        self.store.event(stage, "completed")
        return results

    def optimize(self):
        if not self.cases or (not self.store.continuous and any(r["execution"] != "completed" for r in self.results)):
            self.store.event("model_optimization", "deferred_execution_failure")
            return
        # With missing tolerance, residuals can still guide fitting, but never prove acceptance.
        need = any(r["acceptance"] == "fail" or result_cost(c, r) > 1e-9 for c, r in zip(self.cases, self.results))
        if not need:
            return
        feedback = self.store.get("checkpoints", "optimization_feedback")
        for n in range(self.optimization_index, self.policy["optimization_attempts"]):
            self.store.reserve("repairs", "optimization:" + str(n) + ":" + digest(self.model_path))
            source = model_text(self.model_path)
            from .evidence import bundle, compact_history
            from .model_diagnostics import capabilities, parameter_targets, direction_guard
            from .evidence import optimizer_inputs
            shared, compact_results, compact_cases = optimizer_inputs(self)
            context = {"shared_evidence": shared, "repair_capabilities": capabilities(source, self.task["model"]),
                "task": "Improve the current candidate. Return {decision:patch|defer,kind:parameter|structure,edits:[{old,new}],reason,evidence_ids}. Each old text must appear exactly once. Small reversible edits only; no vendor template replacement or port/reference/threshold changes.",
                "phase": "parameter" if n == 0 else "parameter_or_structure", "interfaces": INTERFACES,
                "model_text": source[:60000], "model_complete_in_context": len(source) <= 60000,
                "results": compact_results,
                "cases": compact_cases,
                "feedback": feedback}
            try:
                proposal = self.agents.ask("model_optimizer", context)
                if proposal.get("decision") == "defer":
                    self.optimization_index = self.policy["optimization_attempts"]
                    self.checkpoint()
                    self.store.event("model_optimization", "planner_deferred", proposal)
                    return
                candidate = self.apply_patch(source, proposal)
                targets = parameter_targets(source, proposal)
                direction_guard(targets, list(self.store.data.get('patches', {}).values()), digest(self.model_path))
                sha = fingerprint(candidate)
                if self.store.get("patches", sha):
                    raise Fault("proposal", "模型补丁重复；必须使用已有反馈")
                diff = "".join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile="before.lib", tofile="candidate.lib"))
                review = self.agents.ask("patch_reviewer", {"task": "PRE-EXECUTION review: authorize a bounded candidate simulation by checking the diff, ports, frozen tests and hypothesis. Supplied results are BASELINE ONLY. Do not require candidate results or proven improvement before simulation. Return decision approve|revise, issues, evidence_ids. Approval is permission to run, not retention.",
                    "planned_test_ids": [c["id"] for c in self.cases], "phase": "pre_execution", "candidate_executed": False, "proposal": proposal, "actual_diff": diff, "model_interface": self.task["model"], "results": context["results"], "shared_evidence": shared,
                    "cases": context["cases"], "acceptance_standard": self.task.get("acceptance_standard")})
                if review.get("decision") != "approve":
                    raise Fault("review", "模型补丁审查要求修订", review)
                folder = self.store.folder / "models" / sha
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "candidate.lib"
                self.store.event("model_materialization", "running")
                path.write_text(candidate, encoding="utf-8")
                self.store.event("model_materialization", "completed", {"path": str(path)})
                (folder / "changes.diff").write_text(diff, encoding="utf-8")
                # Full configured scope always runs BEFORE retention, including new capabilities.
                results = self.evaluate_all(path, "regression_" + sha[:12])
                keep, reasons, gain = retention(self.cases, self.results, results, self.policy)
                from .model_diagnostics import review_results
                post_review = review_results(self, path, self.cases, results, proposal)
                if post_review.get('decision') != 'approve':
                    keep = False
                    reasons = list(reasons) + ['post_execution_review_required']
                feedback = compact_history([{"status": "retained" if keep else "rolled_back", "proposal": proposal, "parameter_targets": targets, "baseline_sha256": digest(self.model_path), "physical_executed": True, "reasons": reasons, "gain": gain,
                    "results": results, "result_review": post_review, "patch_id": sha}], self.cases)[0]
                self.store.put("patches", sha, feedback)
                save(folder / "retention.json", feedback)
                if keep:
                    self.model_path, self.results = path, results
                self.store.event("model_patch", feedback["status"], {"reasons": reasons, "patch_id": sha})
            except BudgetEnd:
                raise
            except (KeyError, TypeError, ValueError) as e:
                feedback = Fault("proposal", str(e)).record()
            except Fault as e:
                if e.kind in ("authentication", "credentials", "api_configuration", "cache_corrupt", "request_context"):
                    raise
                feedback = e.record()
                self.store.event("model_patch", "revision_required", feedback)
            self.store.put("checkpoints", "optimization_feedback", feedback)
            self.optimization_index = n + 1
            self.checkpoint()

    def apply_patch(self, source, proposal):
        kind = proposal.get("kind")
        if kind not in ("parameter", "structure") or (kind == "structure" and not self.policy["allow_structure_edit"]):
            raise Fault("proposal", "模型补丁类型未获允许")
        edits = proposal.get("edits")
        if not isinstance(edits, list) or not 1 <= len(edits) <= 4:
            raise Fault("proposal", "补丁需为1至4处小范围编辑")
        if sum(len(e.get("old", "")) + len(e.get("new", "")) for e in edits) > self.policy["max_patch_bytes"]:
            raise Fault("proposal", "模型补丁超过大小限制")
        candidate = source
        numeric = re.compile(r"(?<![A-Za-z_])[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?(?:[munpfk]|meg)?", re.I)
        for edit in edits:
            old, new = edit["old"], edit["new"]
            if not old or candidate.count(old) != 1:
                raise Fault("proposal", "补丁旧文本非唯一，拒绝模糊替换")
            if kind == "parameter" and numeric.sub("#", old) != numeric.sub("#", new):
                raise Fault("proposal", "参数阶段只能修改数值；结构修改必须显式声明")
            candidate = candidate.replace(old, new, 1)
        validate_model(candidate, self.task["model"])
        if kind == "parameter":
            from .repair_adapters import validate_parameter
            validate_parameter(source, candidate, 100)
        if candidate == source:
            raise Fault("proposal", "补丁未改变模型")
        return candidate

    def audit(self, terminal):
        self.store.event("report_export", "running")
        results = {r["test"]: r for r in self.results}
        rows = []
        for item in self.inventory["items"]:
            bindings = item.get("bindings", [])
            if item["kind"] == "informational":
                status = "informational"
            elif item["kind"] == "constraint":
                status = "constraint_review_pending"
            elif not item.get("binding_complete") or not bindings:
                status = "uncovered"
            elif any(results.get(b, {}).get("execution") == "failed" for b in bindings):
                status = "execution_failed"
            elif any(results.get(b, {}).get("acceptance") == "fail" for b in bindings):
                status = "verification_failed"
            elif all(results.get(b, {}).get("acceptance") == "pass" for b in bindings):
                status = "verified"
            else:
                status = "verification_pending"
            rows.append({**item, "status": status})
        coverage_complete = self.inventory["review_status"] == "reviewed_complete" and all(r["status"] in ("verified", "informational") for r in rows)
        executions = bool(self.cases) and len(results) == len(self.cases) and all(r["execution"] == "completed" for r in results.values())
        electrical = "pass" if executions and all(r["acceptance"] == "pass" for r in results.values()) else "fail" if any(r.get("acceptance") == "fail" for r in results.values()) else "pending"
        provenance = self.task["model"].get("provenance", {})
        provenance_ok = not self.policy["require_non_vendor"] or (provenance.get("kind") == "non_vendor" and bool(provenance.get("evidence")))
        delivered = terminal == "finished" and electrical == "pass" and coverage_complete and provenance_ok
        status = "delivered_declared_scope" if delivered else "finished_with_gaps" if terminal == "finished" else terminal
        output_model = self.store.folder / ("final_model.lib" if delivered else "engineering_candidate.lib")
        output_model.write_text(model_text(self.model_path), encoding="utf-8")
        save(self.store.folder / "active_cases.json", self.cases)
        save(self.store.folder / "active_inventory.json", {**self.inventory, "items": rows})
        summary = {"schema": 1, "workflow_status": status, "device": self.task["device"],
            "acceptance_standard": self.task.get("acceptance_standard"),
            "configured_tests": len(self.cases), "execution_completed": sum(r["execution"] == "completed" for r in results.values()),
            "electrical_acceptance": electrical, "manual_coverage": "complete_declared_inventory" if coverage_complete else "incomplete",
            "source_policy": "non_vendor" if self.policy["require_non_vendor"] else "all",
            "source_policy_acceptance": "pass" if provenance_ok else "not_established",
            "model_provenance": provenance,
            "non_vendor_provenance": "declared_with_evidence" if provenance.get("kind") == "non_vendor" and provenance.get("evidence") else "not_established",
            "model": str(output_model), "model_sha256": digest(output_model), "usage": self.store.data["usage"].copy(),
            "results": self.results, "coverage": rows, "gaps": self.gaps,
            "limitations": self.task.get("limitations", []) + [
                "框架流转成功与器件拟合合格分别判定。",
                "新测试验证针对任务包所声明的条件；不会证明任意器件/未提供的资料。",
                "缺少可机读条件、数字化曲线或新增解析原语时明确报告缺口；不自动运行任意生成代码。"]}
        trials_path = self.store.folder / "template_selection/trial_results.json"
        if trials_path.exists():
            trials = read(trials_path)
            summary["template_trial_statistics"] = {
                "candidates_evaluated": len(trials),
                "test_evaluations": sum(len(t["results"]) for t in trials),
                "execution_completed": sum(r["execution"] == "completed" for t in trials for r in t["results"]),
                "execution_failed": sum(r["execution"] != "completed" for t in trials for r in t["results"]),
                "scope": "选模候选评估次数，与保留模型结果数分别统计"}
        save(self.store.folder / "summary.json", summary)
        lines = ["# 自动化流程报告", "", "流程状态：" + status, "电气验收：" + electrical,
                 "手册覆盖：" + summary["manual_coverage"], "来源策略：" + summary["source_policy"],
                 "实际模板来源：" + str(provenance), "独立非厂商证据：" + summary["non_vendor_provenance"], "",
                 "| 测试 | 执行 | 验收 |", "|---|---|---|"]
        for r in self.results:
            lines.append("| %s | %s | %s |" % (r["test"], r["execution"], r["acceptance"]))
        lines += ["", "## 未完成项目", ""]
        lines += ["- " + r["id"] + "：" + r["status"] for r in rows if r["status"] not in ("verified", "informational")]
        lines += ["", "## 程序诊断", ""] + ["- " + str(g) for g in self.gaps]
        (self.store.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.store.finish(status)
        print("流程：" + status + "；报告：" + str(self.store.folder / "summary.json"), flush=True)
        self.store.event("report_export", "completed")
        return summary
