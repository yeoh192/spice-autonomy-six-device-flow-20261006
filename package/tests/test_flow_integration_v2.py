"""Indexed selection, diagnostic recovery and verified cache integration; no paid calls."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness, encode_raw
from flow_runtime.state import Fault, Store, digest, read, save
from flow_runtime.spice import Simulator
from flow_runtime.agents import Agents
from flow_runtime.workflow import Workflow
from flow_runtime.templates import retrieve, verify_library, asset_config
from flow_runtime.template_tools.index_templates import build_inventory, write_database
from flow_runtime.model_diagnostics import capabilities, topology_precheck, validate_action, audit_stop


class IntegrationV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def index(self, library=None):
        library = library or self.root
        output = self.root / "index"
        output.mkdir(exist_ok=True)
        p = output / "templates.sqlite"
        write_database(p, build_inventory(library, source_policy="all"))
        return p

    def query(self, index, ports=("A", "K")):
        return {"index": str(index), "library_root": str(self.root),
                "semantic_ports": list(ports), "source_policy": "all", "max_trials": 2}

    def test_bundled_index_and_all_4128_files_are_authentic(self):
        report = verify_library()
        self.assertEqual(report["file_count"], 4128)
        self.assertEqual(report["top_level_entries"], 9068)
        self.assertEqual(report["source_policy"], "all")

    def test_real_classified_index_is_consumed_in_finite_window(self):
        query = {**asset_config(), "category": "mosfet", "subcategory": "n_channel",
                 "semantic_ports": ["D", "G", "S"], "port_aliases": {"D": ["DRAIN"], "G": ["GATE"], "S": ["SOURCE"]}}
        _, pool, receipt = retrieve(query, self.root, "BUK7K52-60E")
        self.assertLessEqual(receipt["retrieved"], 20)
        self.assertTrue(pool)
        self.assertTrue(any(c["entry"] == "BUK7K52-60E" for c in pool))
        self.assertTrue(all(c["provenance"]["kind"] == "indexed_source" for c in pool))

    def test_strict_source_policy_does_not_invent_independent_authorship(self):
        h = Harness(self.root)
        query = self.query(self.index())
        query["source_policy"] = "non_vendor"
        with self.assertRaises(Fault) as error:
            retrieve(query, self.root, "DUT")
        self.assertEqual(error.exception.kind, "template_candidates")

    def test_original_file_hash_mismatch_is_rejected(self):
        h = Harness(self.root)
        query = self.query(self.index())
        h.model_path.write_text(h.model_path.read_text() + "* altered\n")
        with self.assertRaises(Fault) as error:
            retrieve(query, self.root, "DUT")
        self.assertIn("哈希", str(error.exception.evidence))

    def test_port_semantics_are_confirmed_and_order_is_preserved(self):
        (self.root / "sample.lib").write_text(".subckt SAMPLE K A\nRCORE K A 1000\n.ends SAMPLE\n")
        _, pool, _ = retrieve(self.query(self.index()), self.root, "SAMPLE")
        self.assertEqual(pool[0]["ports"], ["K", "A"])
        self.assertEqual(pool[0]["declared_ports"], ["K", "A"])

    def test_unknown_pin_semantics_are_not_guessed(self):
        (self.root / "sample.lib").write_text(".subckt SAMPLE P1 P2\nRCORE P1 P2 1000\n.ends SAMPLE\n")
        with self.assertRaises(Fault):
            retrieve(self.query(self.index()), self.root, "SAMPLE")

    def indexed_harness(self):
        h = Harness(self.root, resistance=1200)
        (self.root / "better.lib").write_text(".subckt BETTER A K\nRCORE A K 1000\n.ends BETTER\n")
        h.case["expectation"].update(typical=.25, typical_relative_tolerance_percent=25)
        s, pool, receipt = retrieve(self.query(self.index()), self.root, "DUT")
        h.model = pool[0]
        h.task.update(model=h.model, candidates=pool, template_selection=s,
                      template_retrieval=receipt, select_candidate=True)
        def transport(role, route, context, tokens):
            if role == "template_selector":
                h.calls.append((role, context))
                return {"ranked_ids": [c["id"] for c in pool], "reason": "compare identical baselines"}, {}
            if role == "template_reviewer":
                h.calls.append((role, context))
                return {"decision": "approve"}, {}
            return h.normal_transport(role, route, context, tokens)
        h.transport = transport
        return h

    def test_agents_rank_then_baselines_choose_and_winner_baseline_is_reused(self):
        h = self.indexed_harness()
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        selected = read(h.store.folder / "template_selection/selected.json")
        self.assertEqual(selected["candidate"]["entry"], "BETTER")
        self.assertEqual(h.backend.count, 2)
        self.assertEqual(h.store.data["usage"]["simulations"], 2)
        self.assertEqual([role for role, _ in h.calls], ["template_selector", "template_reviewer"])

    def test_index_selection_and_completed_baselines_resume_without_calls(self):
        h = self.indexed_harness()
        h.build().run()
        calls = len(h.calls)
        count = h.backend.count
        h.build(resume=True).run()
        self.assertEqual(len(h.calls), calls)
        self.assertEqual(h.backend.count, count)

    def test_non_utf8_template_is_copied_without_invalidating_winner_baseline(self):
        h = self.indexed_harness()
        candidate = next(c for c in h.task["candidates"] if c["entry"] == "BETTER")
        p = Path(candidate["path"])
        # ISO-8859-1 copyright byte; the analytic backend ignores comments.
        p.write_bytes(b'* Copyright \xa9 original\n' + p.read_bytes())
        candidate["sha256"] = digest(p)
        def backend(protocol, model_path, folder):
            temporary = self.root / "analytic_utf8.lib"
            temporary.write_text(Path(model_path).read_bytes().decode("latin1"))
            return h.backend(protocol, temporary, folder)
        workflow = h.build()
        h.simulator.transport = backend
        result = workflow.run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(h.backend.count, 2)
        self.assertEqual(workflow.model_path.read_bytes(), p.read_bytes())

    def test_invalid_shortlist_feedback_is_automatic(self):
        h = self.indexed_harness()
        normal = h.transport
        tries = []
        def transport(role, route, context, tokens):
            if role == "template_selector" and not tries:
                tries.append(True)
                return {"ranked_ids": ["not-in-index"]}, {}
            if role == "template_selector":
                self.assertEqual(context["feedback"]["kind"], "proposal")
            return normal(role, route, context, tokens)
        h.transport = transport
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")

    def test_source_neutral_delivery_retains_vendor_audit(self):
        h = Harness(self.root)
        h.model["provenance"] = {"kind": "vendor", "evidence": ["manufacturer copyright"]}
        result = h.build().run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        self.assertEqual(result["source_policy"], "all")
        self.assertEqual(result["model_provenance"]["kind"], "vendor")
        self.assertEqual(result["non_vendor_provenance"], "not_established")

    def test_cross_run_cache_reuses_raw_but_remeasures_current_expectation(self):
        h = Harness(self.root)
        h.build()
        cache = self.root / "shared"
        a = Simulator(h.store, h.task["runner"], h.model, h.backend, cache=cache)
        a.run(h.protocol, h.model_path, "first")
        store2 = Store(self.root / "second", "second", h.task["budgets"])
        b = Simulator(store2, h.task["runner"], h.model, h.backend, cache=cache)
        data, folder = b.run(h.protocol, h.model_path, "second")
        self.assertEqual(h.backend.count, 1)
        self.assertEqual(store2.data["usage"]["simulations"], 0)
        self.assertTrue((folder / "cache_receipt.json").exists())
        from flow_runtime.spice import measure, acceptance
        measured = measure(h.protocol, data)
        self.assertEqual(acceptance(measured, {"unit": "V", "limits": {"min": .3}}), "fail")

    def test_changed_runner_hash_invalidates_shared_cache(self):
        h = Harness(self.root)
        exe = self.root / "runner"
        exe.write_text("version-one")
        h.task["runner"]["argv"][0] = str(exe)
        h.build()
        cache = self.root / "shared"
        Simulator(h.store, h.task["runner"], h.model, h.backend, cache=cache).run(h.protocol, h.model_path, "first")
        exe.write_text("version-two")
        b = Store(self.root / "second", "second", h.task["budgets"])
        Simulator(b, h.task["runner"], h.model, h.backend, cache=cache).run(h.protocol, h.model_path, "second")
        self.assertEqual(h.backend.count, 2)

    def test_shared_cache_damage_never_launches_another_simulation(self):
        h = Harness(self.root)
        h.build()
        cache = self.root / "shared"
        Simulator(h.store, h.task["runner"], h.model, h.backend, cache=cache).run(h.protocol, h.model_path, "first")
        next(cache.glob("*/test.raw")).write_text("truncated")
        b = Store(self.root / "second", "second", h.task["budgets"])
        with self.assertRaises(Fault) as error:
            Simulator(b, h.task["runner"], h.model, h.backend, cache=cache).run(h.protocol, h.model_path, "second")
        self.assertEqual(error.exception.kind, "cache_corrupt")
        self.assertEqual(h.backend.count, 1)

    def topology_fixture(self):
        model = {"entry": "SWITCH", "ports": ["D", "G", "S"], "declared_ports": ["drain", "gate", "source"]}
        source = ".subckt SWITCH drain gate source\n.param TC1=-0.0015 OTHER=2\nBST_TEMP internal source V=V(gate,source)-TC1*(temp-25)\nLG internal core 1n\n.ends SWITCH\n"
        protocol = {"device_nodes": {"D": "d", "G": "g", "S": "0"}, "components": [
            {"kind": "V", "name": "VG", "nodes": ["g", "0"], "value": {"dc": 0, "ac": 1}}],
            "measurement": {"mode": "capacitance", "signal": "i(vg)"}}
        cases = [{"id": "cap", "protocol": protocol, "expectation": {"unit": "F", "typical": 4e-10}}]
        results = [{"test": "cap", "execution": "completed", "value": 0, "acceptance": "pending"}]
        return source, model, cases, results

    def test_case_insensitive_sensing_is_distinct_from_terminal_connection(self):
        source, model, _, _ = self.topology_fixture()
        caps = capabilities(source, model)
        self.assertIn("gate", caps["sensed_only_ports"])
        self.assertIn("OTHER", caps["existing_parameters"])
        self.assertEqual(caps["editable_sources"][0]["terminals"], ["internal", "source"])

    def test_bad_parallel_source_and_isolated_port_are_rejected(self):
        source, model, cases, results = self.topology_fixture()
        old = "BST_TEMP internal source V=V(gate,source)-TC1*(temp-25)"
        for new in ("BST_TEMP gate source V=-TC1*(temp-25)",
                    "BST_TEMP core source V=V(gate,source)-TC1*(temp-25)"):
            with self.assertRaises(Fault):
                topology_precheck(source, source.replace(old, new), model, cases, results)

    def test_series_source_passes_generic_topology_check(self):
        source, model, cases, results = self.topology_fixture()
        candidate = source.replace("BST_TEMP internal source V=V(gate,source)-TC1*(temp-25)",
                                   "BST_TEMP internal gate V=-TC1*(temp-25)")
        topology_precheck(source, candidate, model, cases, results)

    def test_failed_or_duplicate_experiments_cannot_justify_stopping(self):
        baseline = "before"
        failed = {"status": "experiment_failed", "baseline_sha256": baseline, "model_sha256": "one",
                  "results": [{"execution": "failed"}]}
        self.assertIsNotNone(audit_stop([failed, failed], baseline))
        valid = {**failed, "status": "experiment_completed", "results": [{"execution": "completed"}], "improved": False}
        self.assertIsNotNone(audit_stop([valid, valid], baseline))
        self.assertIsNone(audit_stop([valid, {**valid, "model_sha256": "two"}], baseline))
        self.assertIsNotNone(audit_stop([valid, {**valid, "model_sha256": "two", "improved": True}], baseline))


class DiagnosticLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, fail_probe=False):
        h = Harness(self.root)
        h.model_path.write_text(".subckt DUT A K\nRCORE A K 1000\nBSHIFT A K V=0\n.ends DUT\n")
        h.model["sha256"] = digest(h.model_path)
        # Actual drive is a current source; BSHIFT is not parallel to a voltage source.
        def backend(protocol, model_path, folder):
            h.backend.count += 1
            text = Path(model_path).read_text()
            offset = float(__import__('re').search(r'BSHIFT A K V=([\d.]+)', text)[1])
            if fail_probe and offset == .25:
                (folder / "test.log").write_text("injected over-constrained candidate\n")
                raise Fault("execution", "injected probe failure")
            a = protocol["analysis"]
            xs = [a["start"] + i * a["step"] for i in range(21)]
            encode_raw(folder, xs, {"v(d)": [offset for _ in xs]})
        h.backend = type("Count", (), {"count": 0})()
        h.build()
        h.simulator.transport = backend
        return h

    def test_stop_rejection_probe_feedback_then_full_regression_without_editing(self):
        h = self.fixture()
        actions = []
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                return {"decision": "defer", "reason": "requires diagnostic B-source experiment"}, {}
            if role == "model_diagnoser":
                n = len(actions)
                actions.append(n)
                if n == 0:
                    return {"action": "stop", "reason": "no experiment yet", "evidence_tests": ["forward"], "edits": []}, {}
                if n == 1:
                    self.assertEqual(context["history"][-1]["status"], "stop_rejected")
                return {"action": "experiment" if n == 1 else "patch", "reason": "verify then regress the same hypothesis",
                        "evidence_tests": ["forward"], "edits": [{"old": "BSHIFT A K V=0", "new": "BSHIFT A K V=0.25"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.agents.transport = transport
        result = h.workflow.run()
        self.assertEqual(result["workflow_status"], "delivered_declared_scope")
        history = read(h.store.folder / "model_diagnostics/history.json")
        self.assertEqual([r["status"] for r in history], ["stop_rejected", "experiment_completed", "repair_retained"])
        self.assertIn("V=0.25", Path(result["model"]).read_text())
        self.assertEqual(h.backend.count, 2)

    def test_probe_execution_failure_has_actual_log_and_never_changes_active_model(self):
        h = self.fixture(fail_probe=True)
        h.task["policy"]["diagnostic_attempts"] = 2
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                return {"decision": "defer"}, {}
            if role == "model_diagnoser":
                if context["history"]:
                    prior = context["history"][-1]
                    self.assertEqual(prior["status"], "experiment_failed")
                    self.assertIn("injected over-constrained", str(prior["candidate_diagnostics"]))
                    return {"action": "stop", "reason": "failed probe", "evidence_tests": ["forward"], "edits": []}, {}
                return {"action": "experiment", "reason": "test", "evidence_tests": ["forward"],
                        "edits": [{"old": "BSHIFT A K V=0", "new": "BSHIFT A K V=0.25"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.agents.transport = transport
        result = h.workflow.run()
        self.assertEqual(result["workflow_status"], "finished_with_gaps")
        self.assertIn("V=0\n", Path(result["model"]).read_text())
        history = read(h.store.folder / "model_diagnostics/history.json")
        self.assertEqual(history[-1]["status"], "stop_rejected")

    def test_small_probe_improves_but_full_regression_loss_rolls_back(self):
        h = self.fixture()
        protected = copy.deepcopy(h.case)
        protected["id"] = "protected"
        protected["protocol"]["measurement"]["at"] = .0005
        protected["expectation"] = {"unit": "V", "limits": {"max": .1}}
        protected["contract"]["fixed"]["measurement"]["at"] = .0005
        h.workflow.cases.append(protected)
        h.task["policy"]["diagnostic_attempts"] = 2
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                return {"decision": "defer"}, {}
            if role == "model_diagnoser":
                action = "experiment" if not context["history"] else "patch"
                return {"action": action, "reason": "probe success must pass protected regression",
                        "evidence_tests": ["forward"], "edits": [{"old": "BSHIFT A K V=0", "new": "BSHIFT A K V=0.25"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.agents.transport = transport
        result = h.workflow.run()
        self.assertIn("V=0\n", Path(result["model"]).read_text())
        history = read(h.store.folder / "model_diagnostics/history.json")
        self.assertTrue(history[0]["improved"])
        self.assertEqual(history[1]["status"], "repair_rolled_back")
        self.assertTrue(any("protected" in reason for reason in history[1]["reasons"]))

    def test_interruption_in_full_regression_resumes_existing_proposal(self):
        h = self.fixture()
        calls = []
        def transport(role, route, context, tokens):
            if role == "model_optimizer":
                return {"decision": "defer"}, {}
            if role == "model_diagnoser":
                calls.append(role)
                return {"action": "patch", "reason": "full regression", "evidence_tests": ["forward"],
                        "edits": [{"old": "BSHIFT A K V=0", "new": "BSHIFT A K V=0.25"}]}, {}
            return h.normal_transport(role, route, context, tokens)
        h.agents.transport = transport
        normal = h.workflow.evaluate_all
        interrupted = []
        def evaluate(path, stage):
            if stage.startswith("diagnostic_regression") and not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt()
            return normal(path, stage)
        h.workflow.evaluate_all = evaluate
        first = h.workflow.run()
        self.assertEqual(first["workflow_status"], "interrupted")
        self.assertEqual(len(calls), 1)
        repair_usage = h.store.data["usage"]["repairs"]
        backend = h.simulator.transport
        h.store = Store(h.store.folder, "test-identity", h.task["budgets"], resume=True)
        h.agents = Agents(h.store, h.task["routes"], transport)
        h.simulator = Simulator(h.store, h.task["runner"], h.model, backend)
        h.workflow = Workflow(h.task, h.store, h.agents, h.simulator)
        second = h.workflow.run()
        self.assertEqual(second["workflow_status"], "delivered_declared_scope")
        self.assertEqual(len(calls), 1)
        self.assertEqual(h.store.data["usage"]["repairs"], repair_usage)


if __name__ == "__main__":
    unittest.main()
