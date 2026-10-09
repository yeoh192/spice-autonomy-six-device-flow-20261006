"""Failure cases from shared evidence / stage gating / skeleton-independent repair.
All agents and waveform backends are offline; this is not live Qg validation.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness, encode_raw
import test_flow_test_library as fixtures
from flow_runtime.state import Fault, digest, read
from flow_runtime.evidence import residual, bundle, phase_evidence, compact_history
from flow_runtime.repair_adapters import catalog, number, validate_parameter, validate_rewire
from flow_runtime.model_diagnostics import capabilities
from flow_runtime.test_library import qualify_one


STANDARD = {"typical_tolerance_percent": 10, "curve_mae_percent": 5, "curve_max_error_percent": 10}


class EvidenceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_curve_residual_keeps_opposite_directions_hidden_by_mae(self):
        rows = [{"x": i, "reference_y": i, "simulated_y": i + (2 if i < 6 else -2)} for i in range(12)]
        r = residual({"comparison": rows}, {"unit": "A"})
        self.assertEqual(r["signed_mean"], 0)
        self.assertEqual((r["above_points"], r["below_points"]), (6, 6))
        self.assertEqual(r["intervals"][0]["direction"], "above_reference")
        self.assertEqual(r["intervals"][-1]["direction"], "below_reference")
        self.assertEqual(r["peak"]["signed_error"], 2)

    def test_scalar_residual_distinguishes_typical_target_from_limit(self):
        r = residual({"value": .04}, {"unit": "ohm", "typical": .035, "limits": {"max": .045}})
        self.assertAlmostEqual(r["signed_error"], .005)
        self.assertAlmostEqual(r["relative_error_percent"], 100*.005/.035)
        self.assertEqual(r["limit_violations"], {})
        self.assertEqual(residual({"value": .05}, {"unit": "ohm", "limits": {"max": .045}})["limit_violations"], {"max": .05-.045})

    def test_zero_reference_span_is_reported_without_infinite_normalization(self):
        r = residual({"comparison": [{"x": 0, "reference_y": 1, "simulated_y": 2},
                                    {"x": 1, "reference_y": 1, "simulated_y": 0}]}, {"unit": "A"})
        self.assertIsNone(r["intervals"][0]["mean_over_reference_span_percent"])
        self.assertEqual(r["reference_span"], 0)

    def test_reference_tamper_is_blocked_before_shared_context_is_saved(self):
        h = Harness(self.root)
        ref = self.root / "curve.csv"
        ref.write_text("x,y,y_unit\n0,0,V\n0.001,1,V\n")
        h.case["reference"] = {"path": str(ref), "sha256": digest(ref)}
        w = h.build()
        first = bundle(w)
        self.assertEqual(first["tests"][0]["reference"]["points"], 2)
        ref.write_text("x,y,y_unit\n0,0,V\n0.001,2,V\n")
        with self.assertRaises(Fault) as caught:
            bundle(w)
        self.assertEqual(caught.exception.kind, "cache_corrupt")

    def test_optimizer_and_glm_get_same_active_model_and_frozen_target(self):
        h = Harness(self.root, resistance=1400)
        h.task["acceptance_standard"] = STANDARD
        h.case["expectation"].update(typical=.25, typical_relative_tolerance_percent=10)
        contexts = {}
        def transport(role, route, ctx, tokens):
            if role == "model_optimizer":
                contexts.setdefault(role, copy.deepcopy(ctx))
                if (ctx.get("feedback") or {}).get("results"):
                    delta = ctx["feedback"]["results"][0]["signed_residual"]["signed_error"]
                    self.assertAlmostEqual(delta, .0125)
                return {"decision": "patch", "kind": "parameter", "edits": [{"old": "RCORE A K 1400", "new": "RCORE A K 1050"}]}, {}
            if role == "patch_reviewer":
                contexts.setdefault(role, copy.deepcopy(ctx))
            return h.normal_transport(role, route, ctx, tokens)
        h.transport = transport
        w = h.build()
        w.results = w.evaluate_all(w.model_path, "baseline")
        w.optimize()
        a, b = contexts["model_optimizer"]["shared_evidence"], contexts["patch_reviewer"]["shared_evidence"]
        self.assertEqual(a, b)
        self.assertIn("1400", a["model"]["text"])
        self.assertEqual(a["model"]["active_sha256"], digest(h.model_path))
        self.assertEqual(a["acceptance_standard"], STANDARD)
        self.assertEqual(a["tests"][0]["expectation"]["typical"], .25)
        self.assertGreater(a["tests"][0]["signed_residual"]["signed_error"], 0)

    def test_changed_measured_circuit_is_not_presented_as_verified_evidence(self):
        h = Harness(self.root)
        w = h.build()
        w.results = w.evaluate_all(w.model_path, "baseline")
        circuit = Path(w.results[0]["artifacts"]) / "test.cir"
        circuit.write_text(circuit.read_text().replace("25", "125"))
        with self.assertRaises(Fault) as caught:
            bundle(w)
        self.assertEqual(caught.exception.kind, "cache_corrupt")

    def test_transient_phase_boundaries_interpolate_and_means_are_time_weighted(self):
        folder = self.root / "raw"
        folder.mkdir()
        encode_raw(folder, [0, 1, 1.1, 3], {"v(g)": [0, 1, 1.1, 3], "i(dclamp)": [0, 2, 2.2, 6]})
        r = phase_evidence({"analysis": {"kind": "tran"}}, {"execution": "completed", "artifacts": str(folder),
            "measurement_window": {"start": .5, "end": 2}})
        m = r["phases"]["measurement"]["signals"]["v(g)"]
        self.assertEqual((m["first"], m["last"]), (.5, 2))
        self.assertAlmostEqual(m["time_weighted_mean"], 1.25)
        self.assertEqual(r["phases"]["after_endpoint"]["signals"]["v(g)"]["last"], 3)
        self.assertIn("i(dclamp)", r["phases"]["measurement"]["signals"])

    def test_out_of_waveform_phase_boundary_cannot_be_fabricated(self):
        self.root.mkdir(exist_ok=True)
        encode_raw(self.root, [0, 1], {"v(g)": [0, 1]})
        with self.assertRaises(Fault):
            phase_evidence({"analysis": {"kind": "tran"}}, {"execution": "completed", "artifacts": str(self.root),
                "measurement_window": {"start": .5, "end": 2}})

    def test_history_compaction_retains_residual_and_does_not_mutate_saved_rows(self):
        rows = [{"x": n, "reference_y": n, "simulated_y": n-1} for n in range(200)]
        h = [{"results": [{"test": "curve", "comparison": rows}]}]
        compact = compact_history(h, [{"id": "curve", "expectation": {"unit": "A"}}])
        self.assertNotIn("comparison", compact[0]["results"][0])
        self.assertEqual(compact[0]["results"][0]["signed_residual"]["signed_mean"], -1)
        self.assertEqual(len(compact[0]["results"][0]["signed_residual"]["comparison_samples"]), 16)
        self.assertEqual(len(h[0]["results"][0]["comparison"]), 200)

    def test_numeric_node_and_model_identity_are_not_parameter_edits(self):
        source = ".subckt T 3 4\nR1 3 4 1k\n.model DM D(Is=1n N=1 Level=1)\n.ends T\n"
        for changed in (source.replace("R1 3 4", "R1 3 5"), source.replace("Level=1", "Level=2"),
                        source.replace("DM D", "DM NPN")):
            with self.subTest(changed=changed), self.assertRaises(Fault):
                validate_parameter(source, changed, 25)

    def test_spice_suffixes_and_parameter_step_boundary(self):
        self.assertEqual(number("1M"), .001)
        self.assertEqual(number("1meg"), 1e6)
        source = ".subckt T A K\nR1 A K 1k\n.ends T\n"
        self.assertEqual(validate_parameter(source, source.replace("1k", "750"), 25)[0]["new_value"], 750)
        for new in ("749", "-1000", "0"):
            with self.subTest(new=new), self.assertRaises(Fault):
                validate_parameter(source, source.replace("1k", new), 25)

    def test_structure_rewire_is_scoped_and_cannot_change_parameter(self):
        source = ".subckt T A K\nR1 A X 1k\nR2 X K 2k\n.ends T\n.subckt OTHER P Q\nR3 P Q 1k\n.ends OTHER\n"
        self.assertTrue(validate_rewire(source, source.replace("R1 A X", "R1 A K")))
        for new in ("R1 A P 1k", "R1 A K 2k", "R4 A K 1k"):
            with self.subTest(new=new), self.assertRaises(Fault):
                validate_rewire(source, source.replace("R1 A X 1k", new))

    def test_non_b_model_selects_parameter_probe_then_full_regression_in_one_launch(self):
        h = Harness(self.root, resistance=1400)
        calls = []
        def transport(role, route, ctx, tokens):
            calls.append((role, copy.deepcopy(ctx)))
            if role in ("model_diagnoser", "model_repair_designer"):
                caps = ctx["capabilities"]
                self.assertEqual(caps["editable_sources"], [])
                self.assertIn("parameter", caps["available_adapters"])
                number_of_probes = sum(c[0] in ("model_diagnoser", "model_repair_designer") for c in calls)
                return {"action": "experiment" if number_of_probes == 1 else "patch", "adapter": "parameter",
                    "reason": "declared resistor is too high", "evidence_tests": ["forward"],
                    "edits": [{"old": "RCORE A K 1400", "new": "RCORE A K 1050"}]}, {}
            return h.normal_transport(role, route, ctx, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        history = read(h.store.folder / "model_diagnostics/history.json")
        self.assertEqual([r["status"] for r in history], ["experiment_completed", "repair_retained"])
        self.assertTrue(history[0]["improved"])
        self.assertEqual(history[1]["results"][0]["acceptance"], "pass")
        self.assertIn("1050", Path(result["model"]).read_text())
        self.assertEqual(h.backend.count, 2)  # identical successful probe reused during full regression
        self.assertTrue(any(e["stage"] == "diagnostic_regression_02" and e["status"] == "completed" for e in h.store.data["events"]))

    def test_parameter_repair_cannot_trade_away_an_existing_pass(self):
        h = Harness(self.root, resistance=1400)
        protected = copy.deepcopy(h.case)
        protected["id"] = "protected"
        protected["protocol"]["measurement"]["at"] = .0001
        protected["expectation"] = {"unit": "V", "limits": {"min": .13, "max": .15}}
        h.task["cases"].append(protected)
        h.task["policy"]["diagnostic_attempts"] = 1
        def transport(role, route, ctx, tokens):
            if role in ("model_diagnoser", "model_repair_designer"):
                return {"action": "patch", "adapter": "parameter", "reason": "improve trigger", "evidence_tests": ["forward"],
                    "edits": [{"old": "RCORE A K 1400", "new": "RCORE A K 1050"}]}, {}
            return h.normal_transport(role, route, ctx, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertIn("1400", Path(result["model"]).read_text())
        history = read(h.store.folder / "model_diagnostics/history.json")
        self.assertEqual(history[0]["status"], "repair_rolled_back")
        self.assertEqual(len(history[0]["results"]), 2)

    def test_generic_development_post_trial_rejection_prevents_registration(self):
        h = Harness(self.root, cases=False)
        stages = []
        def transport(role, route, ctx, tokens):
            if role == "test_reviewer":
                stages.append(ctx["stage"])
                if ctx["stage"] == "post_trial_review":
                    return {"decision": "revise", "issues": ["independent measurement defect"]}, {}
            return h.normal_transport(role, route, ctx, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["configured_tests"], 0)
        self.assertEqual(stages[:2], ["design_review", "post_trial_review"])
        self.assertFalse(list((h.store.folder / "capabilities").glob("*.json")))


class LibraryStageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = Harness(self.root, ports=("D", "G", "S"), cases=False)

    def workflow(self, **kw):
        return fixtures.TestLibraryTests.workflow(self, **kw)

    def test_not_yet_run_trial_rejection_routes_back_to_review_not_fixture_tuning(self):
        reviews = []
        def replies(role, ctx, calls):
            if role == "test_library_validator":
                reviews.append(ctx)
                self.assertEqual(ctx["stage"], "design_review")
                self.assertIn("calibration_results", ctx["stage_contract"]["not_yet_required"])
                self.assertIn("RCORE", ctx["shared_evidence"]["model"]["text"])
                self.assertEqual(ctx["model_include_binding"]["sha256"], digest(self.h.model_path))
                if len(reviews) == 1:
                    return {"decision": "revise", "issues": ["no prior live trial record"]}
                self.assertIn("no prior live trial", str(ctx["previous_feedback"]))
        w, calls = self.workflow(replies=replies)
        result = qualify_one(w, w.inventory["items"][0])
        self.assertIn("case", result)
        self.assertFalse(any(role == "test_library_adapter" for role, ctx in calls))
        post = next(ctx for role, ctx in calls if ctx.get("stage") == "post_trial_review")
        self.assertEqual(post["calibration"]["status"], "passed")
        self.assertEqual(post["trial"]["execution"], "completed")
        self.assertIn("after_endpoint", post["phase_evidence"]["phases"])
        statuses = [(e["stage"], e["status"]) for e in w.store.data["events"]]
        self.assertIn(("test_recovery", "review_revision"), statuses)
        self.assertLess(statuses.index(("test_library", "design_approved")), statuses.index(("test_library", "post_trial_approved")))

    def test_preapproval_and_successful_run_do_not_override_real_post_trial_defect(self):
        def replies(role, ctx, calls):
            if ctx.get("stage") == "post_trial_review":
                return {"decision": "revise", "issues": ["clamp diverts declared channel current"],
                    "repair_adapter": "review_revision"}
        w, calls = self.workflow(replies=replies)
        result = qualify_one(w, w.inventory["items"][0])
        self.assertNotIn("case", result)
        self.assertEqual(result["last_fault"]["kind"], "qualification_review")
        self.assertFalse(list((w.store.folder / "capabilities").glob("*.json")))
        self.assertFalse(any(role == "test_library_adapter" for role, ctx in calls))

    def test_post_review_must_approve_the_exact_protocol(self):
        def replies(role, ctx, calls):
            if ctx.get("stage") == "post_trial_review":
                return {"decision": "approve", "conditions_complete": True, "measurement_correct": True,
                    "approved_protocol_sha256": "different-circuit"}
        w, calls = self.workflow(replies=replies)
        self.assertNotIn("case", qualify_one(w, w.inventory["items"][0]))

    def test_qualified_method_keeps_electrical_failure_and_unverified_package_scope(self):
        reference = fixtures.item(values={"typ": 20})
        w, calls = self.workflow(reference=reference)
        w.task["acceptance_standard"] = STANDARD
        result = qualify_one(w, w.inventory["items"][0])
        self.assertIn("case", result)
        self.assertEqual(result["trial"]["acceptance"], "fail")
        self.assertFalse(result["binding_scope_complete"])
        record = read(next((w.store.folder / "capabilities").glob("*.json")))
        self.assertEqual(record["qualification_version"], 2)
        self.assertEqual(record["post_trial_review"]["decision"], "approve")
        self.assertFalse(record["package_coverage_complete"])

    def test_fixture_failure_routes_numeric_repair_then_repeats_both_review_stages(self):
        w, calls = self.workflow(backend=fixtures.ReleaseBackend(failures=1))
        result = qualify_one(w, w.inventory["items"][0])
        self.assertIn("case", result)
        adapter = next(ctx for role, ctx in calls if role == "test_library_adapter")
        self.assertEqual(adapter["stage"], "fixture_repair")
        self.assertIn("RCORE", adapter["shared_evidence"]["model"]["text"])
        self.assertTrue(any(e["stage"] == "test_recovery" and e["status"] == "fixture_numeric" for e in w.store.data["events"]))
