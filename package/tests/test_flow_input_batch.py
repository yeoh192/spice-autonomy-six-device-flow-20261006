"""Offline integration and fault tests; no API or LTspice dispatch."""
import copy
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness, encode_raw
from flow_runtime.state import Fault, BudgetEnd, save, read, digest, fingerprint
from flow_runtime.input_batch import prepare_batch, validate_batch, inventory_from_plan, bind_reference_assets
from flow_runtime.ac_calibration_runner import fixtures, run as run_calibration
from flow_runtime.spice import validate_measurement_unit, validate_protocol, measure, acceptance
from flow_runtime.workflow import calibration_protocols
from flow_runtime.project_standard import apply_standard
from flow_runtime.task import load_task

STANDARD = {"typical_tolerance_percent": 10, "curve_mae_percent": 5, "curve_max_error_percent": 10}


def material_source(root):
    root.mkdir()
    part = root / "Device"
    part.mkdir()
    h = Harness(part)
    h.task["schema_version"] = "flow-1"
    h.task["runner"] = {"argv": ["/usr/bin/true", "{circuit}"]}
    h.task["routes"] = {"design": {"provider": "qwen", "model": "qwen3.8-max"},
                        "review": {"provider": "glm", "model": "glm-5.3"}}
    manual = part / "manual.pdf"
    manual.write_bytes(b"%PDF-test-source")
    inv = {"items": []}
    save(part / "inventory.json", inv)
    for name in ("electrical", "evidence", "ports", "test_plan"):
        save(part / (name + ".json"), {"source": name})
    save(part / "acceptance.json", {"threshold_status": "confirmed_project_engineering_standard",
        "typical_tolerance_percent": 10, "curve_thresholds": {
            "MAE_over_reference_span_percent": 5, "max_error_over_reference_span_percent": 10}})
    item = {"id": "parameter:main", "kind": "test", "label": "main", "evidence": ["p0"],
            "reference_evidence": {}, "pdf_pages": [1], "plan_status": "existing_test_requires_scope_audit",
            "bindings": [h.case["id"]], "binding_complete": True}
    plan = {"device": h.task["device"], "items": [item], "counts": {item["plan_status"]: 1},
        "manual": {"path": str(manual), "sha256": digest(manual)},
        "source_inventory_sha256": digest(part / "inventory.json"), "review_status": "series_review_pending"}
    save(part / "coverage_plan.json", plan)
    save(part / "execution.json", h.task)
    packet = {"schema_version": "device-input-packet-1", "device": h.task["device"],
        "category": "test_resistor", "selected_variant": h.task["device"],
        "manual": "manual.pdf", "execution_task": "execution.json",
        "upfront_coverage_stage": {"plan": "coverage_plan.json"},
        **{k: k + ".json" for k in ("electrical", "evidence", "ports", "inventory", "test_plan", "acceptance")}}
    save(part / "input_packet.json", packet)
    save(root / "batch_manifest.json", {"schema_version": "six-device-input-batch-1", "devices": [
        {"device": h.task["device"], "folder": "Device", "input": "Device/input_packet.json"}]})
    return part, h


class InputBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.part, self.h = material_source(self.source)
        self.output = self.root / "output"

    def test_unified_snapshot_preflight_calls_no_agents_or_simulator(self):
        report = prepare_batch(self.source, self.output)
        self.assertEqual((report["api_calls"], report["simulations"]), (0, 0))
        self.assertEqual(validate_batch(self.output / "batch.json")["status"], "input_integration_preflight_passed_with_gaps")
        v = read(self.output / "Device/device_input.json")
        self.assertFalse(v["model_fitting_ready"])
        i = read(self.output / "Device/inventory.json")["items"][0]
        self.assertFalse(i["binding_complete"])
        self.assertTrue(i["source_record"]["binding_complete"])

    def test_snapshot_remains_valid_after_source_removed_and_output_moved(self):
        prepare_batch(self.source, self.output)
        shutil.rmtree(self.source)
        moved = self.root / "relocated"
        shutil.move(str(self.output), moved)
        self.assertEqual(validate_batch(moved / "batch.json")["devices"][0]["configured_tests"], 1)

    def test_any_input_asset_tamper_fails_before_execution(self):
        prepare_batch(self.source, self.output)
        v = read(self.output / "Device/device_input.json")
        (self.output / "Device" / v["materials"]["ports"]).write_text("{}")
        with self.assertRaises(Fault) as caught:
            validate_batch(self.output / "batch.json")
        self.assertEqual(caught.exception.kind, "cache_corrupt")

    def test_standard_is_frozen_and_manual_limits_unchanged(self):
        self.h.case["expectation"].update(typical=.25, limits={"max": .3})
        self.h.task["cases"] = [self.h.case]
        save(self.part / "execution.json", self.h.task)
        prepare_batch(self.source, self.output)
        task, _ = load_task(self.output / "Device/configured_task.json")
        self.assertEqual(task["acceptance_standard"], STANDARD)
        self.assertEqual(task["cases"][0]["expectation"]["limits"], {"max": .3})
        self.assertEqual(task["cases"][0]["expectation"]["typical_relative_tolerance_percent"], 10)

    def test_stale_source_inventory_is_rejected(self):
        save(self.part / "inventory.json", {"changed": True})
        with self.assertRaises(Fault):
            prepare_batch(self.source, self.output)

    def test_unknown_status_cannot_be_silently_dropped(self):
        p = read(self.part / "coverage_plan.json")
        p["items"][0]["plan_status"] = "unknown"
        p["counts"] = {"unknown": 1}
        with self.assertRaises(Fault):
            inventory_from_plan(p, [self.h.case["id"]])

    def test_series_exclusions_and_statistical_records_all_preserved(self):
        p = read(self.part / "coverage_plan.json")
        for status, kind in [("series_group", "series_group"), ("not_applicable", "not_applicable"),
                             ("scope_or_corner_review", "statistical_envelope"),
                             ("test_development_required", "test_or_constraint_pending_classification")]:
            i = copy.deepcopy(p["items"][0])
            i.update(id=status, kind=kind, plan_status=status, bindings=[], binding_complete=False)
            p["items"].append(i)
            p["counts"][status] = 1
        result = inventory_from_plan(p, [self.h.case["id"]])
        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["items"][3]["source_kind"], "statistical_envelope")
        self.assertEqual(result["items"][3]["kind"], "constraint")
        self.assertTrue(result["items"][4]["classification_review_pending"])

    def test_existing_scope_cannot_trigger_optimization_or_development(self):
        self.h.task["input_integration"] = {"schema": "unified-device-input-1",
            "execution_scope": "configured_regression_only", "model_fitting_ready": False}
        w = self.h.build()
        w.optimize = lambda: self.fail("must not optimize an incomplete migrated plan")
        w.develop_missing = lambda: self.fail("draft integration is not automatic qualification")
        result = w.run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertEqual(self.h.agents.store.data["usage"]["api_calls"], 0)

    def test_undeclared_model_or_reference_is_not_selected(self):
        packet = read(self.part / "input_packet.json")
        packet["execution_task"] = None
        save(self.part / "input_packet.json", packet)
        p = read(self.part / "coverage_plan.json")
        p["items"][0].update(bindings=[], binding_complete=False)
        save(self.part / "coverage_plan.json", p)
        (self.part / "reference_models").mkdir()
        (self.part / "reference_models/vendor.lib").write_bytes(self.h.model_path.read_bytes())
        prepare_batch(self.source, self.output)
        value = read(self.output / "Device/device_input.json")
        self.assertIsNone(value["configured_task"])
        self.assertEqual(value["reference_assets"][0]["role"], "reference_interface_benchmark_only")

    def test_already_prepared_inputs_are_not_overwritten(self):
        prepare_batch(self.source, self.output)
        with self.assertRaises(Fault):
            prepare_batch(self.source, self.output)

    def test_duplicate_batch_devices_fail(self):
        value = read(self.source / "batch_manifest.json")
        value["devices"] *= 2
        save(self.source / "batch_manifest.json", value)
        with self.assertRaises(Fault):
            prepare_batch(self.source, self.output)

    def test_uncertain_draft_not_promoted_into_active_tests(self):
        p = copy.deepcopy(self.h.protocol)
        case = {"id": "draft_only", "reference_ids": ["parameter:main"], "protocol": p,
            "protocol_sha256": fingerprint(p), "model": self.h.model,
            "expectation": self.h.case["expectation"], "contract": self.h.contract,
            "delivery_eligible": True}
        save(self.part / "executable_tests.json", {"cases": [case]})
        prepare_batch(self.source, self.output)
        drafts = read(self.output / "Device/fixture_drafts.json")
        self.assertEqual(drafts["active_test_count"], 0)
        self.assertFalse(drafts["cases"][0]["delivery_eligible"])
        self.assertEqual(read(self.output / "Device/configured_task.json")["cases"][0]["id"], self.h.case["id"])

    def test_unconfigured_digitized_series_retained(self):
        (self.part / "references/curves").mkdir(parents=True)
        (self.part / "references/curves/extra.csv").write_text("x,y,y_unit\n1,2,V\n2,3,V\n")
        prepare_batch(self.source, self.output)
        value = read(self.output / "Device/device_input.json")
        self.assertEqual(value["reference_assets"][0]["source_relative"], "references/curves/extra.csv")

    def test_curve_alias_uses_series_directory_not_just_basename(self):
        inv = {"items": [{"reference_evidence": {"source_sampling": {"csv": "/old/input/curves/fig6/same.csv"}}}]}
        refs = [{"source_relative": "references/curves/fig6/same.csv", "path": "assets/a.csv", "sha256": "a"},
                {"source_relative": "references/curves/fig7/same.csv", "path": "assets/b.csv", "sha256": "b"}]
        bind_reference_assets(inv, refs)
        self.assertEqual(inv["items"][0]["reference_asset_bindings"][0]["path"], "assets/a.csv")
        inv["items"][0]["reference_evidence"]["source_sampling"]["csv"] = "/old/digitization/Device/fig6/same.csv"
        bind_reference_assets(inv, refs)
        self.assertEqual(inv["items"][0]["reference_asset_bindings"][0]["path"], "assets/a.csv")
        bind_reference_assets(inv, refs[1:])
        self.assertEqual(inv["items"][0]["reference_asset_bindings"][0]["status"], "unresolved_reference_locator")

    def test_draft_wrong_protocol_hash_is_rejected(self):
        case = {"protocol": self.h.protocol, "reference_ids": ["parameter:main"], "protocol_sha256": "incorrect"}
        save(self.part / "executable_tests.json", {"cases": [case]})
        with self.assertRaises(Fault):
            prepare_batch(self.source, self.output)

    def test_model_port_map_mismatch_is_rejected(self):
        self.h.task["model"]["declared_ports"] = ["K", "A"]
        save(self.part / "execution.json", self.h.task)
        with self.assertRaises(Fault):
            prepare_batch(self.source, self.output)


class ACMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_ac_sweep_reference_uses_project_curve_thresholds(self):
        p = fixtures()[18]["protocol"]
        case = {"protocol": p, "reference": {"path": "curve.csv"}, "expectation": {"unit": "F"}}
        task = apply_standard({"cases": [case], "inventory": {"items": []}}, STANDARD)
        self.assertEqual(task["cases"][0]["expectation"]["thresholds"]["max_error_over_reference_span_percent"], 10)
        validate_measurement_unit(task["cases"][0])

    def test_sweep_without_reference_or_sampling_cannot_be_scalar_acceptance(self):
        p = fixtures()[18]["protocol"]
        with self.assertRaises(Fault) as caught:
            validate_measurement_unit({"protocol": p, "expectation": {"unit": "F"}})
        self.assertEqual(caught.exception.kind, "missing_data")
        self.assertEqual(acceptance({"samples": [{"value": 2}]}, {"limits": {"max": 3}}), "pending")

    def test_ac_bias_checks_cannot_be_skipped_by_mode_dispatch(self):
        p = fixtures()[0]["protocol"]
        p["checks"] = [{"signal": "v(p)", "from": 1000, "to": 1000, "min": 0, "max": .2}]
        with self.assertRaises(Fault) as caught:
            measure(p, {"axis": [1000], "signals": {"v(p)": [.3], "i(vcal)": [-.0003]}})
        self.assertEqual(caught.exception.kind, "fixture")

    def test_ac_phase_keeps_leading_and_lagging_direction(self):
        p = fixtures()[16]["protocol"]
        for imaginary, expected in [(1, 45), (-1, -45)]:
            r = measure(p, {"axis": [1000], "signals": {"v(o)": [1+imaginary*1j], "v(p)": [2]}})
            self.assertAlmostEqual(r["value"], expected)
        p["measurement"]["absolute"] = True
        with self.assertRaises(Fault):
            validate_protocol(p, self.h_model())

    @staticmethod
    def h_model():
        return {"entry": "Oracle", "ports": ["P", "N"], "declared_ports": ["P", "N"]}

    def test_nine_modes_get_independent_analytic_calibration_protocols(self):
        for row in fixtures()[:18]:
            case = {"protocol": row["protocol"]}
            ps = calibration_protocols(case)
            self.assertEqual(len(ps), 2)
            self.assertEqual(ps[0][0]["measurement"]["mode"], row["protocol"]["measurement"]["mode"])

    def test_calibration_check_only_is_not_a_current_receipt(self):
        result = run_calibration("/usr/bin/true", self.root / "preflight", check_only=True)
        self.assertEqual(result["fixtures"], 20)
        self.assertFalse(result["current_calibration_passed"])

    def test_calibration_budget_stop_preserves_report(self):
        def backend(*args):
            raise BudgetEnd("seconds")
        result = run_calibration("/usr/bin/true", self.root / "budget", transport=backend)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertFalse(result["current_calibration_passed"])
        self.assertEqual(read(self.root / "budget/summary.json")["status"], "budget_exhausted")

    def test_all_twenty_oracles_through_parser_and_resume_no_new_dispatch(self):
        calls = []
        def backend(p, model, folder):
            calls.append(p)
            from flow_runtime.ac_measurements import frequencies
            fs = frequencies(p["analysis"])
            cs = p["components"]
            v = cs[0]["value"]["ac"]
            cm = next((c["value"]["ac"] for c in cs if c["name"] == "VCM"), 0)
            vp = [complex(v + cm)] * len(fs)
            currents, vo = [], []
            for f in fs:
                w = 2 * math.pi * f
                if any(c["kind"] == "L" for c in cs):
                    z = 1j * w * next(c["value"] for c in cs if c["kind"] == "L")
                elif any(c["kind"] == "C" for c in cs):
                    r = next(c["value"] for c in cs if c["kind"] == "R")
                    c = next(c["value"] for c in cs if c["kind"] == "C")
                    parallel = next(x for x in cs if x["kind"] == "R")["nodes"] == ["P", "0"]
                    z = 1 / (1/r + 1j*w*c) if parallel else r + 1/(1j*w*c)
                else:
                    rs = [c["value"] for c in cs if c["kind"] == "R"]
                    z = sum(rs)
                    vo.append(v * rs[1] / z)
                currents.append(-v/z)
            columns = {"v(p)": vp, "i(vcal)": currents}
            if cm:
                columns["v(n)"] = [complex(cm)] * len(fs)
            if vo:
                columns["v(o)"] = vo
            encode_raw(folder, fs, columns, complex_values=True)
        result = run_calibration("/usr/bin/true", self.root / "run", transport=backend)
        self.assertEqual(result["status"], "calibrated")
        self.assertEqual(len(calls), 20)
        self.assertFalse(result["current_calibration_passed"])
        self.assertTrue(result["test_backend"])
        again = run_calibration("/usr/bin/true", self.root / "run", resume=True, transport=backend)
        self.assertEqual(again["status"], "calibrated")
        self.assertEqual(len(calls), 20)


if __name__ == "__main__":
    unittest.main()
