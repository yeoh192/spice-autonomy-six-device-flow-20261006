"""Fault injection for GPT recipes -> Qwen checks -> calibrated registration. No live calls."""
import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_flow_runtime import Harness, encode_raw
from flow_runtime.state import Fault, Store, digest, fingerprint, read, save
from flow_runtime.agents import Agents
from flow_runtime.spice import Simulator, measure, render, validate_protocol, validate_measurement_unit, interpolate
from flow_runtime.contracts import extracted_contract
from flow_runtime.test_library import catalog, describe, plan, qualify_one
from flow_runtime.test_methods import build, adjusted, defaults, gate_charge_oracles
from flow_runtime.test_scope import expand_buk
from flow_runtime.workflow import Workflow


def item(key="total_gate_charge", conditions="ID=5 A; VDS=48 V; VGS=10 V; Tj=25°C", unit="nC", values=None):
    return {"id": "parameter:qg", "kind": "test", "label": key, "evidence": ["offline-handbook-row"],
            "reference_evidence": {"key": key, "conditions": conditions, "unit": unit, "values": values or {"typ": 9.2}},
            "bindings": [], "binding_complete": False}


class ReleaseBackend:
    """Analytic waveforms exercise parsers/control flow, not real MOSFET physics."""
    def __init__(self, failures=0, calibration_bad=False):
        self.failures, self.calibration_bad, self.count = failures, calibration_bad, 0

    def __call__(self, p, model, folder):
        self.count += 1
        a, m = p["analysis"], p["measurement"]
        requested = {m["signal"].lower()} | {c["signal"].lower() for c in p.get("checks", [])}
        if m.get("target"):
            requested.add(m["target"]["signal"].lower())
        if m.get("denominator"):
            requested.add(m["denominator"].lower())
        if a["kind"] == "dc":
            n = round((a["stop"] - a["start"]) / a["step"])
            xs = [a["start"] + (a["stop"]-a["start"]) * j / n for j in range(n + 1)]
            resistance = next(c["value"] for c in p["components"] if c["kind"] == "R")
            columns = {s: [x * resistance if s.startswith("v(") else x for x in xs] for s in requested}
            encode_raw(folder, xs, columns)
            return
        components = {c["name"]: c for c in p["components"]}
        xs = {j * a["stop_s"] / 6000 for j in range(6001)}
        for c in components.values():
            if c["kind"] in "VI" and "pwl" in c["value"]:
                xs.update(t for t, v in c["value"]["pwl"])
        xs.update([m["start_s"]] + [t for c in p.get("checks", []) for t in (c["from"], c["to"])])
        xs = sorted(xs)
        dut = bool(p["device_nodes"])
        cap = .92e-9 if dut else components["CCAL"]["value"]
        if "VCAL" in components:
            voltage = [min(10, max(0, (x - 1e-6) * 1e6)) for x in xs]
            current = [-cap * 1e6 if 1e-6 <= x <= 11e-6 else 0 for x in xs]
        else:
            name = "IGATE" if dut else "ICAL"
            pwl = components[name]["value"]["pwl"]
            current = [interpolate([q[0] for q in pwl], [q[1] for q in pwl], x) for x in xs]
            charge = [0]
            for j in range(1, len(xs)):
                charge.append(charge[-1] + (current[j-1] + current[j]) * (xs[j]-xs[j-1])/2)
            voltage = [min(12, q / cap) if dut else q / cap for q in charge]
        bad = dut and self.failures > 0
        if bad:
            self.failures -= 1
        columns = {}
        for s in requested:
            if s in ("v(g)", "v(p)"):
                columns[s] = voltage
            elif s == "v(d)":
                columns[s] = [35 if bad else 48 for x in xs]
            elif s == "i(iload)":
                pwl = components["ILOAD"]["value"]["pwl"]
                columns[s] = [interpolate([q[0] for q in pwl], [q[1] for q in pwl], x) for x in xs]
            else:
                columns[s] = [v * (2 if self.calibration_bad and not dut else 1) for v in current]
        encode_raw(folder, xs, columns)


class TestLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = Harness(self.root, ports=("D", "G", "S"), cases=False)

    def workflow(self, reference=None, replies=None, backend=None, name="out", shared=None):
        t = copy.deepcopy(self.h.task)
        t["cases"] = []
        t["inventory"]["items"] = [reference or item()]
        t["test_library"] = {"enabled": True, "catalog_version": "0.1.0"}
        if shared:
            t["capability_library"] = str(shared)
        calls = []
        def transport(role, route, context, tokens):
            calls.append((role, copy.deepcopy(context)))
            if replies:
                answer = replies(role, context, calls)
                if answer is not None:
                    return answer, {}
            if role == "test_library_adapter":
                return {"adjustments": {"clamp_n": 1.2}}, {}
            return {"decision": "approve", "approved_protocol_sha256": context.get("protocol_sha256"),
                    "conditions_complete": True, "measurement_correct": True}, {}
        limits = {"api_calls": 30, "simulations": 30, "repairs": 10, "seconds": 60}
        store = Store(self.root / name, name, limits)
        agents = Agents(store, t["routes"], transport)
        sim = Simulator(store, t["runner"], t["model"], backend or ReleaseBackend())
        w = Workflow(t, store, agents, sim)
        return w, calls

    def test_catalog_is_draft_not_pretend_live_qualified(self):
        manifest, methods = catalog()
        self.assertEqual(len(methods), 21)
        self.assertEqual(sum(m["stage"] == "executable_draft" for m in methods), 12)
        self.assertEqual(manifest["qualified_methods"], 0)
        self.assertFalse(any(m["live_validated"] for m in methods))

    def test_missing_curve_data_does_not_produce_a_fake_case(self):
        r = item("subthreshold_current", "Tj=25°C; VDS=5 V", "A", {"typ": 1e-3})
        self.assertEqual(describe(r, self.h.model)["status"], "evidence_or_adapter_required")

    def test_switching_conditions_do_not_silently_change_rl(self):
        r = item("turn_on_delay", "VDS=48 V; RL=5 Ω; VGS=10 V; ID=5 A; Tj=25°C", "ns", {"typ": 4.3})
        self.assertEqual(describe(r, self.h.model)["status"], "interface_or_definition_required")

    def test_thermal_reference_does_not_create_a_fake_electrical_thermal_port(self):
        r = item("thermal_resistance_junction_mounting_base", "", "K/W", {"max": 4.68})
        self.assertEqual(describe(r, self.h.model)["status"], "interface_or_definition_required")

    def test_total_charge_protocol_freezes_handbook_and_verifies_preparation(self):
        r = item()
        d = build("gate_charge_total", r, self.h.model)
        p = d["protocol"]
        validate_protocol(p, self.h.model)
        validate_measurement_unit({"protocol": p, "expectation": {"unit": "nC"}})
        contract = extracted_contract(r, d)
        self.assertEqual(contract["measurement"]["target"]["value"], 10)
        self.assertIn("S", [c["kind"] for c in p["components"]])
        self.assertIn("i(igate)", [c["signal"] for c in p["checks"]])
        self.assertIn(".model HOLD_SW SW(", render(p, self.h.model))
        self.assertIn("DCLAMP D BUS CLAMP_D", render(p, self.h.model))

    def test_invalid_switch_parameters_are_rejected(self):
        p = build("gate_charge_total", item(), self.h.model)["protocol"]
        p["models"]["HOLD_SW"]["ron"] = 0
        with self.assertRaises(Fault):
            validate_protocol(p, self.h.model)

    def test_switch_diode_type_confusion_is_rejected(self):
        p = build("gate_charge_total", item(), self.h.model)["protocol"]
        next(c for c in p["components"] if c["name"] == "DCLAMP")["model"] = "HOLD_SW"
        with self.assertRaises(Fault):
            validate_protocol(p, self.h.model)

    def test_tuning_cannot_change_vds_or_acceptance(self):
        for changes in ({"vds_V": 30}, {"clamp_n": .01}, {"release_s": 1e-6, "load_ramp_s": 1e-6}):
            with self.assertRaises(Fault):
                adjusted(defaults(), changes)

    def test_gate_leakage_polarities_and_nanoampere_units(self):
        for gate in (-20, 20):
            r = item("gate_leakage_current", f"VGS={gate} V; VDS=0 V; Tj=25°C", "nA", {"max": 100})
            d = build("gate_leakage", r, self.h.model)
            validate_protocol(d["protocol"], self.h.model)
            validate_measurement_unit({"protocol": d["protocol"], "expectation": {"unit": "nA"}})
            self.assertEqual(d["protocol"]["measurement"]["scale"], 1e9)
            self.assertEqual(d["protocol"]["components"][1]["value"]["dc"], gate)

    def test_resistance_ratio_uses_actual_saved_current(self):
        p = {"temperature_C": 25, "device_nodes": {}, "components": [
            {"kind": "I", "name": "ICAL", "nodes": ["0", "P"], "value": {"dc": 0}}],
            "analysis": {"kind": "dc", "source": "ICAL", "start": .001, "stop": .002, "step": .001},
            "measurement": {"mode": "ratio_curve", "signal": "v(p)", "denominator": "i(ical)"}}
        validate_protocol(p, self.h.model)
        validate_measurement_unit({"protocol": p, "expectation": {"unit": "ohm"}})
        data = {"axis": [.001, .002], "signals": {"v(p)": [1, 2], "i(ical)": [.001, .002]}}
        result = measure(p, data, [(.001, 1000), (.002, 1000)])
        self.assertEqual(result["metrics"]["max_absolute_error"], 0)
        self.assertIn("i(ical)", render(p, self.h.model))
        data["signals"]["i(ical)"][0] = 0
        with self.assertRaises(Fault):
            measure(p, data, [(.001, 1000), (.002, 1000)])

    def test_qwen_approval_without_protocol_hash_never_runs(self):
        def replies(role, context, calls):
            if role == "test_library_validator":
                return {"decision": "approve", "conditions_complete": True, "measurement_correct": True}
        backend = ReleaseBackend()
        w, calls = self.workflow(replies=replies, backend=backend)
        result = qualify_one(w, w.inventory["items"][0])
        self.assertNotIn("case", result)
        self.assertEqual(backend.count, 0)

    def test_independent_rejection_never_runs(self):
        def replies(role, context, calls):
            if role == "test_reviewer":
                return {"decision": "revise", "issues": ["invalid fixture"]}
        backend = ReleaseBackend()
        w, calls = self.workflow(replies=replies, backend=backend)
        self.assertNotIn("case", qualify_one(w, w.inventory["items"][0]))
        self.assertEqual(backend.count, 0)

    def test_known_capacitor_failure_prevents_registration(self):
        w, calls = self.workflow(backend=ReleaseBackend(calibration_bad=True))
        result = qualify_one(w, w.inventory["items"][0])
        self.assertNotIn("case", result)
        self.assertEqual(result["last_fault"]["kind"], "calibration")
        self.assertFalse((w.store.folder / "capabilities").exists())

    def test_fixture_failure_automatically_feedbacks_and_then_registers(self):
        w, calls = self.workflow(backend=ReleaseBackend(failures=1))
        result = qualify_one(w, w.inventory["items"][0])
        self.assertIn("case", result)
        adapters = [c for r, c in calls if r == "test_library_adapter"]
        self.assertTrue(adapters)
        self.assertIn("35", str(adapters[-1]["feedback"]))
        self.assertEqual(result["trial"]["acceptance"], "pending")
        self.assertTrue(list((w.store.folder / "capabilities").glob("*.json")))

    def test_dual_channel_scope_is_not_silently_marked_complete(self):
        r = item(conditions="FET1 and FET2; ID=5 A; VDS=48 V; VGS=10 V; Tj=25°C")
        w, calls = self.workflow(reference=r)
        w.develop_missing()
        self.assertEqual(len(w.cases), 1)
        self.assertFalse(w.inventory["items"][0]["binding_complete"])
        self.assertIn("channel_scope_pending", [g["kind"] for g in w.gaps])

    def test_exact_contract_registry_reuse_recalibrates_without_new_api(self):
        shared = self.root / "library"
        w, calls = self.workflow(shared=shared)
        first = qualify_one(w, w.inventory["items"][0])
        self.assertIn("case", first)
        backend = ReleaseBackend()
        again, calls = self.workflow(name="second", shared=shared, backend=backend)
        second = qualify_one(again, again.inventory["items"][0])
        self.assertIn("case", second)
        self.assertEqual(calls, [])
        self.assertGreater(backend.count, 0)

    def test_tampered_registry_is_not_accepted_or_rerun(self):
        shared = self.root / "library"
        w, calls = self.workflow(shared=shared)
        qualify_one(w, w.inventory["items"][0])
        f = next(shared.glob("*.json")); f.write_text(f.read_text() + " ")
        backend = ReleaseBackend()
        again, calls = self.workflow(name="second", shared=shared, backend=backend)
        with self.assertRaises(Fault) as error:
            qualify_one(again, again.inventory["items"][0])
        self.assertEqual(error.exception.kind, "cache_corrupt")
        self.assertEqual(backend.count, 0)

    def test_row_and_series_expansion_preserves_pending_review(self):
        original = read(Path(__file__).resolve().parent.parent / "test_library_assets/examples/buk_inventory_before_expansion.json")
        expanded = expand_buk(original)
        ids = {i["id"] for i in expanded["items"]}
        self.assertNotIn("parameter:igss", ids)
        self.assertTrue({"parameter:igss_minus20", "parameter:igss_plus20", "parameter:is", "parameter:ism"} <= ids)
        for figure in (9, 10):
            self.assertTrue({f"figure:{figure}:{c}" for c in ("min", "typ", "max")} <= ids)
        self.assertNotEqual(expanded["review_status"], "reviewed_complete")
        for i in expanded["items"]:
            if i["id"] in ("parameter:is", "parameter:ism"):
                self.assertEqual(i["kind"], "constraint")
        self.assertIn("parameter:igss", {i["id"] for i in original["items"]})


if __name__ == "__main__":
    unittest.main()
