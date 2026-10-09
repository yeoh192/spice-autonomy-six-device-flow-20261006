"""Integration of indexed starts, DC parsing, test qualification and fixed standards."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness
from test_flow_test_library import ReleaseBackend, item
from flow_runtime.project_standard import apply_standard, expectation_with_standard, validate_standard
from flow_runtime.state import Fault, read, save
from flow_runtime.task import load_task
from flow_runtime.test_library import describe
from flow_runtime.templates import retrieve
from flow_runtime.template_tools.index_templates import build_inventory, write_database


STANDARD = {"typical_tolerance_percent": 10, "curve_mae_percent": 5, "curve_max_error_percent": 10}


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_project_standard_preserves_manual_bounds_and_other_curve_metrics(self):
        e = {"unit": "A", "limits": {"min": 1, "max": 4}, "typical": 2}
        self.assertEqual(expectation_with_standard(e, STANDARD)["limits"], e["limits"])
        self.assertNotIn("typical_relative_tolerance_percent", e)
        e["thresholds"] = {"RMSE": .1}
        updated = expectation_with_standard(e, STANDARD, curve=True)
        self.assertEqual(updated["thresholds"], {"RMSE": .1, "MAE_over_reference_span_percent": 5, "max_error_over_reference_span_percent": 10})

    def test_missing_negative_and_nonfinite_standard_are_rejected(self):
        for standard in ({}, {**STANDARD, "curve_mae_percent": -1}, {**STANDARD, "curve_mae_percent": float("nan")}):
            with self.assertRaises(Fault):
                validate_standard(standard)

    def test_qualification_draft_inherits_project_tolerance(self):
        h = Harness(self.root, ports=("D", "G", "S"), cases=False)
        result = describe(item(), h.model, STANDARD)
        self.assertEqual(result["expectation"]["typical_relative_tolerance_percent"], 10)
        self.assertEqual(result["expectation"]["typical"], 9.2)

    def test_project_standard_is_written_before_loading_and_cannot_be_inconsistent(self):
        h = Harness(self.root)
        task = copy.deepcopy(h.task)
        task["schema_version"] = "flow-1"
        task["cases"][0]["expectation"]["typical"] = .25
        task = apply_standard(task, STANDARD)
        path = self.root / "task.json"
        save(path, task)
        self.assertEqual(load_task(path)[0]["acceptance_standard"], STANDARD)
        task["cases"][0]["expectation"]["typical_relative_tolerance_percent"] = 50
        save(path, task)
        with self.assertRaisesRegex(Fault, "启动前固定"):
            load_task(path)

    def test_one_launch_selects_executable_start_qualifies_then_fits_and_regresses(self):
        # Analytic waveforms test orchestration, not BUK/MOSFET electrical physics.
        h = Harness(self.root, resistance=2000, ports=("D", "G", "S"))
        (self.root / "closer.lib").write_text(".subckt CLOSER D G S\nRCORE D S 1500\n.ends CLOSER\n")
        index = self.root / "index.sqlite"
        write_database(index, build_inventory(self.root, source_policy="all"))
        selection, pool, receipt = retrieve({"index": str(index), "library_root": str(self.root),
            "semantic_ports": ["D", "G", "S"], "source_policy": "all", "max_trials": 2}, self.root, "DUT")
        h.model = pool[0]
        h.task.update(model=h.model, candidates=pool, template_selection=selection,
                      template_retrieval=receipt, select_candidate=True,
                      test_library={"enabled": True, "catalog_version": "0.1.0"})
        h.task["cases"][0]["expectation"].update(typical=.25)
        h.task["inventory"]["items"].append(item())
        h.task = apply_standard(h.task, STANDARD)
        h.task["budgets"].update(simulations=80, api_calls=50)
        release = ReleaseBackend()
        def backend(protocol, model_path, folder):
            if protocol["analysis"]["kind"] == "tran":
                return release(protocol, model_path, folder)
            h.backend(protocol, model_path, folder)
            # Exercise the real DC endpoint peculiarity inside candidate selection.
            p = folder / "test.raw"
            lines = p.read_text().splitlines()
            count = int(next(l.split(":")[1] for l in lines if l.startswith("No. Points:")))
            nv = int(next(l.split(":")[1] for l in lines if l.startswith("No. Variables:")))
            tail = lines[-nv:]
            tail[0] = str(count) + "\t" + tail[0].split(None, 1)[1]
            lines += tail
            text = "\n".join(lines) + "\n"
            text = text.replace("No. Points: " + str(count), "No. Points: " + str(count + 1))
            text = text.replace("Flags:", "Plotname: DC transfer characteristic\nFlags:")
            p.write_text(text)
        roles = []
        def agents(role, route, context, tokens):
            roles.append(role)
            if role == "template_selector":
                return {"ranked_ids": [p["id"] for p in pool]}, {}
            if role == "template_reviewer":
                return {"decision": "approve"}, {}
            if role == "test_library_validator" or role == "test_reviewer":
                return {"decision": "approve", "approved_protocol_sha256": context.get("protocol_sha256"),
                        "conditions_complete": True, "measurement_correct": True}, {}
            if role == "model_optimizer" and "RCORE D S 1500" in context["shared_evidence"]["model"]["text"]:
                return {"decision": "patch", "kind": "parameter", "reason": "offline parameter repair", "evidence_ids": ["forward"], "edits": [{"old": "RCORE D S 1500", "new": "RCORE D S 1000"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = agents
        workflow = h.build()
        h.simulator.transport = backend
        summary = workflow.run()
        self.assertNotIn(summary["workflow_status"], ("framework_error", "stopped_with_evidence", "budget_exhausted"))
        trials = read(h.store.folder / "template_selection/trial_results.json")
        self.assertTrue(all(t["eligible"] and t["electrical_acceptance"] == "fail" for t in trials))
        self.assertTrue(all(t["results"][0].get("raw_normalization") for t in trials))
        selected = read(h.store.folder / "template_selection/selected.json")
        self.assertEqual(selected["candidate"]["entry"], "CLOSER")
        self.assertIn("RCORE D S 1000", Path(summary["model"]).read_text())
        self.assertIn("test_library_validator", roles)
        self.assertNotIn("execution_diagnoser", roles)
        self.assertEqual(summary["configured_tests"], 2)
        self.assertEqual(summary["electrical_acceptance"], "pass")
        self.assertEqual(summary["acceptance_standard"], STANDARD)
        self.assertEqual(summary["manual_coverage"], "incomplete")  # single-core qualification is not package coverage
        events = [(e["stage"], e["status"]) for e in h.store.data["events"]]
        self.assertLess(events.index(("template_selection", "completed")), events.index(("capability_development", "registered")))
        self.assertLess(events.index(("capability_development", "registered")), events.index(("model_patch", "retained")))


if __name__ == "__main__":
    unittest.main()
