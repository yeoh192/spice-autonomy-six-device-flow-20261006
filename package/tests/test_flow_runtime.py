"""Offline fault injection. Fake agents/simulator never call APIs or LTspice."""
import concurrent.futures
import copy
import json
import math
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from flow_runtime.agents import Agents
from flow_runtime.spice import (Simulator, acceptance, raw_data, measure, render,
                                validate_protocol, validate_model)
from flow_runtime.state import BudgetEnd, Fault, Store, digest, file_lock, read, save
from flow_runtime.task import component, DEFAULT_POLICY
from flow_runtime.contracts import extracted_contract
from flow_runtime.workflow import Workflow, retention


def encode_raw(folder, xs, columns, complex_values=False):
    names = ["frequency" if complex_values else "time"] + list(columns)
    lines = ["Title: offline fixture", "Flags: " + ("complex" if complex_values else "real"),
             "No. Variables: %d" % len(names), "No. Points: %d" % len(xs), "Variables:"]
    lines += ["%d %s voltage" % (i, n) for i, n in enumerate(names)]
    lines.append("Values:")
    for i, x in enumerate(xs):
        def fmt(v):
            v = complex(v)
            return "%.17g,%.17g" % (v.real, v.imag) if complex_values else "%.17g" % v.real
        lines.append("%d\t%s" % (i, fmt(x)))
        lines += ["\t" + fmt(v[i]) for v in columns.values()]
    (folder / "test.raw").write_text("\n".join(lines) + "\n", encoding="ascii")
    (folder / "test.log").write_text("offline analytic fixture completed\n")


class AnalyticBackend:
    def __init__(self):
        self.count = 0
        self.failures = 0
        self.lock = threading.Lock()

    def __call__(self, protocol, model_path, folder):
        with self.lock:
            self.count += 1
            if self.failures and protocol["device_nodes"]:
                self.failures -= 1
                (folder / "test.log").write_text("Injected launch failure\n")
                raise Fault("execution", "injected launch failure")
        a, m, cs = protocol["analysis"], protocol["measurement"], protocol["components"]
        target = m.get("target")
        requested = {m["signal"].lower()} | {c["signal"].lower() for c in protocol.get("checks", [])}
        if target:
            requested.add(target["signal"].lower())
        if a["kind"] == "ac":
            xs = [a["frequency_Hz"]]
            c = next(c["value"] for c in cs if c["kind"] == "C")
            columns = {s: [-1j * 2 * math.pi * xs[0] * c] for s in requested}
            encode_raw(folder, xs, columns, True)
            return
        if a["kind"] == "tran":
            stop = a["stop_s"]
            xs = [i * stop / 1200 for i in range(1201)]
            xs = sorted(set(xs + [1e-6, 11e-6]))
            c = next(c["value"] for c in cs if c["kind"] == "C")
            columns = {s: [min(10, max(0, (x-1e-6)*1e6)) if s.startswith("v(") else (-c*1e6 if 1e-6 <= x <= 11e-6 else 0) for x in xs] for s in requested}
            encode_raw(folder, xs, columns)
            return
        count = round((a["stop"] - a["start"]) / a["step"])
        xs = [a["start"] + (a["stop"] - a["start"]) * i / count for i in range(count + 1)]
        if protocol["device_nodes"]:
            text = Path(model_path).read_text()
            r = float(re.search(r"(?m)^RCORE\s+\S+\s+\S+\s+([\d.]+)", text)[1])
        else:
            r = next(c["value"] for c in cs if c["kind"] == "R")
        source = next(c for c in cs if c["name"] == a["source"])
        current = [x if source["kind"] == "I" else x / r for x in xs]
        columns = {s: [i * r for i in current] if s.startswith("v(") else [-i for i in current] for s in requested}
        encode_raw(folder, xs, columns)


class Harness:
    def __init__(self, folder, resistance=1000, ports=("A", "K"), cases=True):
        self.root = Path(folder)
        self.model_path = self.root / "original.lib"
        # Three-pin mock represents a different interface, not real MOSFET physics.
        self.model_path.write_text(".subckt DUT " + " ".join(ports) + "\nRCORE " + ports[0] + " " + ports[-1] + " " + str(resistance) + "\n.ends DUT\n")
        self.model = {"path": str(self.model_path), "entry": "DUT", "ports": list(ports), "declared_ports": list(ports),
                      "sha256": digest(self.model_path), "provenance": {"kind": "non_vendor", "evidence": ["synthetic analytic fixture"]}}
        nodes = {p: ("D" if i == 0 else "0") for i, p in enumerate(ports)}
        self.protocol = {"device_nodes": nodes, "temperature_C": 25,
            "components": [component("I", "IDRIVE", "0", "D", {"dc": 0})],
            "analysis": {"kind": "dc", "source": "IDRIVE", "start": 0, "stop": .001, "step": .00005},
            "measurement": {"mode": "sample", "signal": "v(d)", "at": .00025}}
        self.contract = {"fixed": {"device_nodes": nodes, "temperature_C": 25, "components": self.protocol["components"]},
                         "measurement": self.protocol["measurement"], "expectation": {"unit": "V", "limits": {"min": .2, "max": .3}}}
        self.case = {"id": "forward", "protocol": self.protocol, "expectation": self.contract["expectation"],
                     "contract": {"fixed": {**self.contract["fixed"], "measurement": self.contract["measurement"]}}}
        inventory = {"review_status": "reviewed_complete", "items": [{"id": "manual:voltage", "kind": "test", "label": "voltage_at_declared_current",
            "evidence": ["manual-table-row-1"], "bindings": ["forward"] if cases else [], "binding_complete": cases, "test_contract": self.contract}]}
        self.task = {"device": "fixture", "model": self.model, "cases": [self.case] if cases else [], "inventory": inventory,
            "runner": {"argv": ["/usr/bin/true", "{circuit}"]}, "policy": DEFAULT_POLICY.copy(),
            "routes": {"design": {"provider": "qwen", "model": "mock-design"}, "review": {"provider": "glm", "model": "mock-review"}},
            "budgets": {"api_calls": 40, "simulations": 40, "repairs": 20, "seconds": 60}}
        self.calls = []
        self.backend = AnalyticBackend()
        self.transport = self.normal_transport

    def normal_transport(self, role, route, context, tokens):
        self.calls.append((role, context))
        if role == "test_designer":
            value = {"decision": "propose", "protocol": copy.deepcopy(self.protocol)}
        elif role in ("test_reviewer", "patch_reviewer", "diagnosis_reviewer"):
            value = {"decision": "approve", "evidence_ids": ["manual-table-row-1"],
                "conditions_complete": True, "measurement_correct": True,
                "approved_protocol_sha256": context.get("protocol_sha256")}
        elif role == "execution_diagnoser":
            value = {"decision": "retry", "reason": "injected runner fault", "evidence_ids": []}
        else:
            value = {"decision": "defer", "reason": "no further analytic improvement"}
        return value, {"finish_reason": "stop"}

    def build(self, resume=False):
        self.store = Store(self.root / "output", "test-identity", self.task["budgets"], resume)
        self.agents = Agents(self.store, self.task["routes"], self.transport)
        self.simulator = Simulator(self.store, self.task["runner"], self.model, self.backend)
        self.workflow = Workflow(self.task, self.store, self.agents, self.simulator)
        return self.workflow


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_single_launch_develops_reviews_calibrates_runs_and_delivers(self):
        h = Harness(self.root, cases=False)
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(result["configured_tests"], 1)
        self.assertEqual(h.store.data["usage"]["api_calls"], 3)
        self.assertEqual(h.backend.count, 3)  # two known oracles, one DUT (baseline reused)
        self.assertEqual([r[0] for r in h.calls], ["test_designer", "test_reviewer", "test_reviewer"])

    def test_invalid_design_and_rejected_review_route_back_without_human(self):
        h = Harness(self.root, cases=False)
        designs, reviews = 0, 0
        def transport(role, route, context, tokens):
            nonlocal designs, reviews
            if role == "test_designer":
                designs += 1
                p = copy.deepcopy(h.protocol)
                if designs == 1:
                    p["temperature_C"] = 99
                if designs == 3:
                    p["method"] = "gear"  # a real revision changes the reviewed circuit
                if designs > 1:
                    self.assertIsNotNone(context["feedback"])
                return {"decision": "propose", "protocol": p}, {}
            if role == "test_reviewer":
                reviews += 1
                return {"decision": "revise" if reviews == 1 else "approve", "issues": ["injected review revision"],
                    "conditions_complete": True, "measurement_correct": True,
                    "approved_protocol_sha256": context.get("protocol_sha256")}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual((designs, reviews), (3, 3))

    def test_execution_failure_automatically_diagnoses_and_recovers(self):
        h = Harness(self.root)
        h.backend.failures = 1
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(h.backend.count, 2)
        self.assertEqual([c[0] for c in h.calls], ["execution_diagnoser", "diagnosis_reviewer"])

    def test_model_patch_runs_full_regression_before_retention(self):
        h = Harness(self.root, resistance=2000)
        def transport(role, route, context, tokens):
            if role == "model_optimizer" and "2000" in context["model_text"]:
                return {"decision": "patch", "kind": "parameter", "edits": [{"old": "RCORE A K 2000", "new": "RCORE A K 1000"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertIn("1000", Path(result["model"]).read_text())
        self.assertEqual(h.backend.count, 2)
        events = h.store.data["events"]
        retention_event = next(i for i, e in enumerate(events) if e["stage"] == "model_patch" and e["status"] == "retained")
        self.assertTrue(any(e["stage"].startswith("regression_") and e["status"] == "completed" for e in events[:retention_event]))

    def test_patch_that_breaks_passing_feature_rolls_back(self):
        h = Harness(self.root, resistance=2000)
        second = copy.deepcopy(h.case)
        second["id"] = "protected"
        second["expectation"] = {"unit": "V", "limits": {"min": .45, "max": .55}}
        h.task["cases"].append(second)
        h.task["policy"]["optimization_attempts"] = 1
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                return {"decision": "patch", "kind": "parameter", "edits": [{"old": "RCORE A K 2000", "new": "RCORE A K 1000"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertIn("2000", Path(result["model"]).read_text())
        self.assertTrue(any(e["status"] == "rolled_back" for e in h.store.data["events"]))

    def test_resume_reuses_results_and_requests_no_new_calls(self):
        h = Harness(self.root, cases=False)
        first = h.build().run()
        count, calls = h.backend.count, len(h.calls)
        second = h.build(resume=True).run()
        self.assertEqual(first["workflow_status"], second["workflow_status"])
        self.assertEqual((h.backend.count, len(h.calls)), (count, calls))

    def test_unknown_manual_gap_is_reported_not_silently_delivered(self):
        h = Harness(self.root)
        h.task["inventory"]["items"].append({"id": "dynamic", "kind": "test", "label": "unknown dynamic", "evidence": ["figure-9"], "bindings": []})
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertTrue(any(g.get("kind") == "missing_structured_contract" for g in result["gaps"]))
        self.assertEqual(h.calls, [])

    def test_vendor_source_cannot_become_formal_delivery(self):
        h = Harness(self.root)
        h.task["policy"]["require_non_vendor"] = True
        h.model["provenance"] = {"kind": "vendor"}
        result = h.build().run()
        self.assertEqual(result["non_vendor_provenance"], "not_established")
        self.assertTrue(result["model"].endswith("engineering_candidate.lib"))

    def test_three_pin_interface_uses_same_scheduler(self):
        h = Harness(self.root, ports=("D", "G", "S"), cases=False)
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(h.backend.count, 3)

    def test_global_api_budget_ends_with_report_and_candidate(self):
        h = Harness(self.root, cases=False)
        h.task["budgets"]["api_calls"] = 1
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "budget_exhausted")
        self.assertEqual(h.store.data["usage"]["api_calls"], 1)
        self.assertTrue((h.root / "output" / "report.md").exists())

    def test_parallel_calibration_deduplicates_simulation_dispatch(self):
        h = Harness(self.root, cases=False)
        item = copy.deepcopy(h.task["inventory"]["items"][0])
        item["id"] = "manual:second"
        item["label"] = "second measurement"
        h.task["inventory"]["items"].append(item)
        result = h.build().run()
        self.assertEqual(result["configured_tests"], 2)
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(h.backend.count, 3)

    def test_response_length_recovery_is_counted_and_cached(self):
        h = Harness(self.root)
        count = 0
        def transport(role, route, context, tokens):
            nonlocal count
            count += 1
            if count == 1:
                raise Fault("response_incomplete", "length", {"finish_reason": "length"})
            self.assertEqual(tokens, 8192)
            return {"decision": "approve"}, {}
        h.transport = transport
        h.build()
        h.agents.ask("test_reviewer", {"same": True})
        h.agents.ask("test_reviewer", {"same": True})
        self.assertEqual(count, 2)
        self.assertEqual(h.store.data["usage"]["api_calls"], 2)

    def test_authentication_failure_is_not_a_design_retry(self):
        h = Harness(self.root)
        def transport(*args):
            raise Fault("authentication", "HTTP 401")
        h.transport = transport
        h.build()
        for _ in range(2):
            with self.assertRaises(Fault) as caught:
                h.agents.ask("test_designer", {})
            self.assertEqual(caught.exception.kind, "authentication")
        self.assertEqual(h.store.data["usage"]["api_calls"], 1)

    def test_simulation_cache_tamper_never_triggers_paid_rework(self):
        h = Harness(self.root)
        h.build()
        data, folder = h.simulator.run(h.protocol, h.model_path, "baseline")
        (folder / "test.log").write_text("tampered\n")
        with self.assertRaises(Fault) as caught:
            h.simulator.run(h.protocol, h.model_path, "baseline")
        self.assertEqual(caught.exception.kind, "cache_corrupt")
        self.assertEqual(h.backend.count, 1)

    def test_request_cache_tamper_never_calls_remote_again(self):
        h = Harness(self.root)
        h.build()
        h.agents.ask("test_reviewer", {})
        next((h.root / "output" / "requests").glob("*/response.json")).write_text("{}")
        with self.assertRaises(Fault):
            h.agents.ask("test_reviewer", {})
        self.assertEqual(len(h.calls), 1)

    def test_resume_rejects_changed_identity_or_budget(self):
        h = Harness(self.root)
        h.build()
        with self.assertRaises(Fault):
            Store(h.root / "output", "changed", h.task["budgets"], True)
        budgets = h.task["budgets"].copy(); budgets["api_calls"] += 1
        with self.assertRaises(Fault):
            Store(h.root / "output", "test-identity", budgets, True)

    def test_repair_reservation_survives_restart(self):
        h = Harness(self.root)
        h.build()
        h.store.reserve("repairs", "same-operation")
        h.build(resume=True)
        h.store.reserve("repairs", "same-operation")
        self.assertEqual(h.store.data["usage"]["repairs"], 1)

    def test_local_lock_busy_is_typed(self):
        p = self.root / "lock"
        with file_lock(p):
            with self.assertRaises(Fault) as caught:
                with file_lock(p):
                    pass
        self.assertEqual(caught.exception.kind, "lock_busy")

    def test_typical_and_curve_thresholds_missing_remain_pending(self):
        self.assertEqual(acceptance({"value": .25}, {"unit": "V", "limits": {"max": .3}, "typical": .25}), "pending")
        self.assertEqual(acceptance({"metrics": {"MAE": 0}}, {"unit": "A"}), "pending")

    def test_model_external_dependency_and_port_changes_rejected(self):
        h = Harness(self.root)
        for text in (h.model_path.read_text().replace("A K", "K A", 1), h.model_path.read_text() + '.include "vendor.lib"\n'):
            with self.assertRaises(Fault):
                validate_model(text, h.model)

    def test_agent_cannot_relax_immutable_conditions(self):
        h = Harness(self.root)
        p = copy.deepcopy(h.protocol); p["temperature_C"] = 175
        with self.assertRaises(Fault):
            validate_protocol(p, h.model, {"fixed": h.contract["fixed"]})

    def test_partial_raw_exponent_fragment_is_diagnostic_only(self):
        folder = self.root
        encode_raw(folder, [0, 1], {"v(d)": [0, 1]})
        text = (folder / "test.raw").read_text().replace("No. Points: 2", "No. Points: 3") + "2\t5.000000000000000e\n"
        (folder / "test.raw").write_text(text)
        data = raw_data(folder / "test.raw", partial=True)
        self.assertEqual(data["axis"], [0, 1])
        self.assertFalse(data["complete"])
        with self.assertRaises(Fault):
            raw_data(folder / "test.raw")

    def test_preparation_check_prevents_false_charge_acceptance(self):
        p = {"analysis": {"kind": "tran", "stop_s": 2}, "measurement": {"mode": "integral_to_crossing", "signal": "i(g)", "start_s": 1,
             "target": {"signal": "v(g)", "value": 10, "direction": "rising"}},
             "checks": [{"signal": "v(g)", "from": 0, "to": 1, "min": -.01, "max": .01}]}
        with self.assertRaises(Fault) as caught:
            measure(p, {"axis": [0, 1, 2], "signals": {"v(g)": [5, 5, 10], "i(g)": [1, 1, 1]}})
        self.assertEqual(caught.exception.kind, "fixture")

    def test_charge_integrates_only_after_release_to_target(self):
        p = {"analysis": {"kind": "tran", "stop_s": 3}, "measurement": {"mode": "integral_to_crossing", "signal": "i(g)", "start_s": 1,
             "target": {"signal": "v(g)", "value": 10, "direction": "rising"}}}
        data = {"axis": [0, 1, 2, 3], "signals": {"v(g)": [0, 0, 10, 20], "i(g)": [1, 1, 1, 100]}}
        result = measure(p, data)
        self.assertEqual(result["value"], 1)
        self.assertEqual(result["measurement_window"], {"start": 1, "end": 2})

    def test_declared_library_recipe_is_revalidated_on_reuse(self):
        h = Harness(self.root, cases=False)
        h.task["capability_library"] = str(self.root / "library")
        h.build().run()
        self.assertEqual(len(list((self.root / "library").glob("*.json"))), 1)
        # Start another run, not a resume. Same contract reuses recipe, recalibrates and reruns.
        store = Store(self.root / "second", "another-run", h.task["budgets"])
        agents = Agents(store, h.task["routes"], h.transport)
        sim = Simulator(store, h.task["runner"], h.model, h.backend)
        before = len(h.calls)
        result = Workflow(h.task, store, agents, sim).run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(len(h.calls), before)

    def test_manual_text_is_normalized_then_developed_in_one_launch(self):
        h = Harness(self.root, cases=False)
        item = h.task["inventory"]["items"][0]
        item.pop("test_contract")
        item["reference_evidence"] = {"unit": "V", "values": {"min": .2, "max": .3}, "conditions": "IF=250 uA; Tj=25°C"}
        def transport(role, route, context, tokens):
            if role == "contract_normalizer":
                return {"decision": "propose", "protocol": copy.deepcopy(h.protocol), "unresolved_conditions": [],
                    "condition_bindings": [{"quote": "IF=250 uA", "protocol_path": "measurement/at"},
                                           {"quote": "Tj=25°C", "protocol_path": "temperature_C"}]}, {}
            value, meta = h.normal_transport(role, route, context, tokens)
            if role == "test_reviewer":
                value["conditions_complete"] = True
            return value, meta
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(h.store.data["usage"]["api_calls"], 5)

    def test_wrong_unit_and_unbound_manual_conditions_are_rejected(self):
        h = Harness(self.root)
        item = {"reference_evidence": {"unit": "V", "values": {"min": .2}, "conditions": "IF=250 mA; Tj=25°C"}}
        proposal = {"protocol": h.protocol, "condition_bindings": [{"quote": "IF=250 mA", "protocol_path": "measurement/at"}]}
        with self.assertRaises(Fault):
            extracted_contract(item, proposal)
        item["reference_evidence"]["conditions"] = "IF=250 uA; Tj=25°C"
        proposal["condition_bindings"][0]["quote"] = "IF=250 uA"
        with self.assertRaises(Fault):
            extracted_contract(item, proposal)

    def test_model_cannot_override_analysis_to_fake_completion(self):
        h = Harness(self.root)
        with self.assertRaises(Fault):
            validate_model(h.model_path.read_text() + ".tran 1n\n", h.model)

    def test_ac_frequency_mismatch_is_not_accepted_as_capacitance(self):
        p = {"analysis": {"kind": "ac", "frequency_Hz": 1e6}, "measurement": {"mode": "capacitance", "signal": "i(vg)", "sign": -1}}
        with self.assertRaises(Fault):
            measure(p, {"axis": [2e6], "signals": {"i(vg)": [-1j]}})

    def test_interruption_during_model_trial_resumes_same_proposal(self):
        h = Harness(self.root, resistance=2000)
        h.task["policy"]["optimization_attempts"] = 1
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                h.calls.append((role, context))
                return {"decision": "patch", "kind": "parameter", "edits": [{"old": "RCORE A K 2000", "new": "RCORE A K 1000"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        original = h.backend
        interrupted = False
        def backend(p, model, folder):
            nonlocal interrupted
            if p["device_nodes"] and "1000" in Path(model).read_text() and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return original(p, model, folder)
        h.backend = backend
        first = h.build().run()
        self.assertEqual(first["workflow_status"], "interrupted")
        second = h.build(resume=True).run()
        self.assertEqual(second["workflow_status"], "delivered_declared_scope")
        self.assertEqual(sum(role == "model_optimizer" for role, ctx in h.calls), 1)

    def test_failed_review_of_identical_circuit_is_reused_without_new_call(self):
        h = Harness(self.root, cases=False)
        def transport(role, route, context, tokens):
            if role == "test_reviewer":
                h.calls.append((role, context))
                return {"decision": "revise", "issues": ["same circuit remains invalid"]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertEqual(sum(role == "test_reviewer" for role, ctx in h.calls), 1)

    def test_global_simulation_budget_prevents_partial_delivery(self):
        h = Harness(self.root, cases=False)
        h.task["budgets"]["simulations"] = 2  # oracles fit; DUT does not
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "budget_exhausted")
        self.assertEqual(h.store.data["usage"]["simulations"], 2)
        self.assertEqual(result["electrical_acceptance"], "pending")

    def test_parameter_edit_cannot_sneak_in_structure_change(self):
        h = Harness(self.root)
        w = h.build()
        with self.assertRaises(Fault):
            w.apply_patch(h.model_path.read_text(), {"kind": "parameter", "edits": [{"old": "RCORE A K 1000", "new": "RCORE A 0 1000"}]})

    def test_candidate_selection_precedes_development_and_fitting(self):
        h = Harness(self.root, resistance=2000)
        h.task["policy"]["require_non_vendor"] = True
        other = self.root / "better.lib"
        other.write_text(h.model_path.read_text().replace("2000", "1000"))
        first = copy.deepcopy(h.model); first["id"] = "first"
        second = copy.deepcopy(h.model); second.update(id="second", path=str(other), sha256=digest(other))
        vendor = copy.deepcopy(first); vendor.update(id="vendor", provenance={"kind": "vendor"})
        h.task.update(candidates=[first, second, vendor], select_candidate=True)
        def transport(role, route, context, tokens):
            if role == "template_selector":
                self.assertNotIn("vendor", [c["id"] for c in context["candidates"]])
                return {"candidate_id": "second"}, {}
            if role == "template_reviewer":
                self.assertEqual(route["provider"], "glm")
                return {"decision": "approve"}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertIn("1000", Path(result["model"]).read_text())

    def test_only_vendor_candidates_stop_with_source_diagnostic(self):
        h = Harness(self.root)
        h.task["policy"]["require_non_vendor"] = True
        c = copy.deepcopy(h.model); c.update(id="vendor", provenance={"kind": "vendor"})
        h.task.update(candidates=[c], select_candidate=True)
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "stopped_with_evidence")
        self.assertEqual(h.calls, [])

    def test_known_capacitor_oracles_cover_ac_and_charge_primitives(self):
        h = Harness(self.root)
        w = h.build()
        for mode in ("capacitance", "integral_to_crossing"):
            case = copy.deepcopy(h.case)
            case["protocol"]["measurement"]["mode"] = mode
            w.calibrate(case)
        self.assertEqual(h.backend.count, 4)

    def test_empty_or_invalid_unit_scaling_requires_capability_repair(self):
        from flow_runtime.spice import validate_measurement_unit
        h = Harness(self.root)
        case = copy.deepcopy(h.case)
        case["expectation"]["unit"] = "nC"
        with self.assertRaises(Fault):
            validate_measurement_unit(case)

    def test_preflight_does_not_dispatch_agents_or_simulator(self):
        h = Harness(self.root, cases=False)
        result = h.build().run(check_only=True)
        self.assertEqual(result["workflow_status"], "prepared")
        self.assertEqual((h.calls, h.backend.count), ([], 0))

    def test_authentication_fault_blocks_other_requests_on_same_route(self):
        h = Harness(self.root)
        def transport(*args):
            raise Fault("authentication", "HTTP 401")
        h.transport = transport
        h.build()
        for context in ({"reference": 1}, {"reference": 2}):
            with self.assertRaises(Fault):
                h.agents.ask("test_designer", context)
        self.assertEqual(h.store.data["usage"]["api_calls"], 1)

    def test_parallel_budget_failure_preserves_other_worker_registration(self):
        h = Harness(self.root, cases=False)
        item = copy.deepcopy(h.task["inventory"]["items"][0]); item["id"] = "manual:second"
        h.task["inventory"]["items"].append(item)
        h.task["budgets"]["api_calls"] = 5  # only one complete design/pre/post sequence can fit
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "budget_exhausted")
        self.assertEqual(result["configured_tests"], 1)
        self.assertEqual(h.store.data["usage"]["api_calls"], 5)


if __name__ == "__main__":
    unittest.main()
