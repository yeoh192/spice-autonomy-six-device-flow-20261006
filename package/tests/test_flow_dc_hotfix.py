"""DC endpoint evidence, rejection boundaries and independent final acceptance."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness, encode_raw
from flow_runtime.spice import raw_data, measure
from flow_runtime.state import Fault, read, save, digest, artifact_hashes
from flow_runtime.templates import retrieve
from flow_runtime.template_tools.index_templates import build_inventory, write_database


class DCHotfixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def raw(self, xs, ys=None, plot="DC transfer characteristic", complex_values=False):
        ys = ys if ys is not None else [x * 1000 for x in xs]
        encode_raw(self.root, xs, {"v(d)": ys}, complex_values)
        p = self.root / "test.raw"
        p.write_text(p.read_text().replace("Flags:", "Plotname: " + plot + "\nFlags:"))
        return p

    def test_real_dc_terminal_duplicate_is_complete_and_audited(self):
        p = self.raw([.00099, .001, .00101, .00101], [2.9978, 2.9998, 3.001769943, 3.001772401])
        data = raw_data(p)
        self.assertTrue(data["complete"])
        self.assertEqual(data["raw_points"], 4)
        self.assertEqual(data["parsed_points"], 3)
        self.assertEqual(data["signals"]["v(d)"][-1], 3.001772401)
        self.assertEqual(data["normalizations"][0]["source_indices"], [2, 3])
        p = {"analysis": {"kind": "dc", "start": .00099, "stop": .00101},
             "measurement": {"mode": "sample", "signal": "v(d)", "at": .001}}
        self.assertEqual(measure(p, data)["value"], 2.9998)

    def test_transient_duplicate_is_not_normalized(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([0, 1e-6, 1e-6], plot="Transient Analysis"))

    def test_ac_duplicate_is_not_normalized(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([1e6, 1e6], plot="AC Analysis", complex_values=True))

    def test_unknown_plot_duplicate_is_not_normalized(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([0, .001, .001], plot=""))

    def test_interior_dc_duplicate_is_rejected(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([0, .0005, .0005, .001]))

    def test_repeated_terminal_dc_duplicates_are_rejected(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([0, .001, .001, .001]))

    def test_descending_axis_is_rejected(self):
        with self.assertRaises(Fault):
            raw_data(self.raw([0, .001, .0005]))

    def test_conflicting_dc_operating_points_are_rejected(self):
        with self.assertRaisesRegex(Fault, "测量值不一致"):
            raw_data(self.raw([0, .001, .001], [0, 3, 3.1]))

    def test_declared_point_count_is_checked_before_normalization(self):
        p = self.raw([0, .001, .001])
        p.write_text(p.read_text().replace("No. Points: 3", "No. Points: 4"))
        with self.assertRaises(Fault):
            raw_data(p)

    def test_partial_duplicate_missing_value_remains_incomplete(self):
        p = self.raw([0, .001, .001])
        lines = p.read_text().splitlines()
        p.write_text("\n".join(lines[:-1]) + "\n")
        self.assertFalse(raw_data(p, partial=True)["complete"])
        with self.assertRaises(Fault):
            raw_data(p)

    def test_no_duplicate_does_not_change_values(self):
        data = raw_data(self.raw([0, .0005, .001]))
        self.assertEqual(data["axis"], [0, .0005, .001])
        self.assertEqual(data["normalizations"], [])

    def test_dc_endpoint_still_must_match_requested_stop(self):
        data = raw_data(self.raw([0, .0005, .0005]))
        with self.assertRaises(Fault):
            measure({"analysis": {"kind": "dc", "start": 0, "stop": .001},
                     "measurement": {"mode": "sample", "signal": "v(d)", "at": .00025}}, data)

    def test_parser_failure_cannot_trigger_repeated_simulation(self):
        h = Harness(self.root)
        count = []
        def backend(protocol, model_path, folder):
            count.append(1)
            raise Fault("parser", "injected parser failure")
        workflow = h.build()
        h.simulator.transport = backend
        result = workflow.evaluate_one(h.case, h.model_path)
        self.assertEqual(result["execution"], "failed")
        self.assertEqual(len(count), 1)
        self.assertIn("读取器", result["fault"]["reason"])

    def test_dc_fault_cannot_change_transient_integration_method(self):
        h = Harness(self.root)
        count = []
        def backend(protocol, model_path, folder):
            count.append(1)
            raise Fault("execution", "injected DC execution fault")
        def agents(role, route, context, tokens):
            if role == "execution_diagnoser":
                return {"decision": "numerical_change", "method": "gear"}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = agents
        workflow = h.build()
        h.simulator.transport = backend
        result = workflow.evaluate_one(h.case, h.model_path)
        self.assertEqual(len(count), 1)
        self.assertIn("不能作为DC", result["fault"]["reason"])

    def test_fitting_candidate_can_fail_specs_without_passing_delivery(self):
        h = Harness(self.root, resistance=2000)
        index = self.root / "templates.sqlite"
        write_database(index, build_inventory(self.root, source_policy="all"))
        selection, pool, receipt = retrieve({"index": str(index), "library_root": str(self.root),
            "semantic_ports": ["A", "K"], "source_policy": "all", "max_trials": 1}, self.root, "DUT")
        h.model = pool[0]
        h.task.update(model=h.model, candidates=pool, template_selection=selection,
                      template_retrieval=receipt, select_candidate=True)
        def agents(role, route, context, tokens):
            if role == "template_selector":
                return {"ranked_ids": [pool[0]["id"]]}, {}
            if role == "template_reviewer":
                return {"decision": "approve"}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = agents
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertEqual(result["electrical_acceptance"], "fail")
        trial = read(h.store.folder / "template_selection/trial_results.json")[0]
        self.assertTrue(trial["eligible"])
        self.assertEqual(trial["electrical_acceptance"], "fail")
        self.assertEqual(result["template_trial_statistics"]["execution_completed"], 1)

    def replay_fixture(self, parser_failed=False):
        h = Harness(self.root)
        h.model["id"] = "fixture"
        h.task["candidates"] = [h.model]
        runtime = (self.root / "history").resolve()
        folder = runtime / "simulations" / "saved"
        folder.mkdir(parents=True)
        (folder / "model.lib").write_bytes(h.model_path.read_bytes())
        from flow_runtime.spice import render
        (folder / "test.cir").write_text(render(h.protocol, h.model))
        encode_raw(folder, [0, .0005, .001, .001], {"v(d)": [0, .5, 1, 1]})
        p = folder / "test.raw"
        p.write_text(p.read_text().replace("Flags:", "Plotname: DC transfer characteristic\nFlags:"))
        save(folder / "execution.json", {"returncode": 0, "argv": ["/usr/bin/true", str(folder / "test.cir")]})
        record = {"status": "completed", "hashes": artifact_hashes(folder, ("model.lib", "test.cir", "test.raw", "test.log", "execution.json"))}
        result = {"test": "forward", "execution": "completed", "artifacts": str(folder)}
        if parser_failed:
            record = {"status": "failed", "fault": {"kind": "parser"}}
            result = {"test": "forward", "execution": "failed", "fault": {"evidence": {"folder": str(folder)}}}
        save(runtime / "state.json", {"simulations": {"saved": record}})
        save(runtime / "input_snapshot.json", {"task": h.task})
        save(runtime / "template_selection/trial_results.json", [{"candidate_id": "fixture", "model_sha256": digest(h.model_path), "results": [result]}])
        return h, runtime, folder

    def test_offline_recheck_never_changes_source_or_claims_delivery(self):
        from flow_runtime.offline_recheck import recheck
        _, runtime, _ = self.replay_fixture()
        before = {str(p): digest(p) for p in runtime.rglob("*") if p.is_file()}
        result = recheck(runtime, self.root / "rechecked")
        self.assertEqual(result["api_calls"], 0)
        self.assertEqual(result["simulations"], 0)
        self.assertFalse(result["formal_delivery"])
        self.assertEqual(result["counts"]["completed"], 1)
        self.assertEqual(result["trials"][0]["results"][0]["raw_normalization"]["raw_points"], 4)
        self.assertEqual(before, {str(p): digest(p) for p in runtime.rglob("*") if p.is_file()})

    def test_offline_completed_hash_tamper_is_rejected(self):
        from flow_runtime.offline_recheck import recheck
        _, runtime, folder = self.replay_fixture()
        (folder / "test.raw").write_text((folder / "test.raw").read_text() + "\n")
        result = recheck(runtime, self.root / "rechecked")
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["trials"][0]["results"][0]["fault"]["kind"], "cache_corrupt")

    def test_old_failed_raw_is_diagnostic_evidence_not_verified_cache(self):
        from flow_runtime.offline_recheck import recheck
        _, runtime, _ = self.replay_fixture(parser_failed=True)
        result = recheck(runtime, self.root / "rechecked")
        self.assertEqual(result["counts"]["recovered_parser_cases"], 1)
        self.assertIn("diagnostic_replay_only", result["trials"][0]["results"][0]["source_integrity"])
        self.assertEqual(read(runtime / "state.json")["simulations"]["saved"]["status"], "failed")

    def test_recheck_must_use_new_directory_outside_old_runtime(self):
        from flow_runtime.offline_recheck import recheck
        _, runtime, _ = self.replay_fixture()
        with self.assertRaises(Fault):
            recheck(runtime, runtime / "rechecked")

    def test_project_tolerance_does_not_override_manual_minimum(self):
        from flow_runtime.offline_recheck import recheck
        _, runtime, _ = self.replay_fixture()
        snapshot = read(runtime / "input_snapshot.json")
        snapshot["task"]["cases"][0]["expectation"].update(typical=.25, limits={"min": .3})
        save(runtime / "input_snapshot.json", snapshot)
        result = recheck(runtime, self.root / "rechecked", {"typical_tolerance_percent": 10,
            "curve_mae_percent": 5, "curve_max_error_percent": 10})
        measured = result["trials"][0]["results"][0]
        self.assertEqual(measured["acceptance"], "fail")
        self.assertEqual(measured["expectation"]["limits"], {"min": .3})


if __name__ == "__main__":
    unittest.main()
