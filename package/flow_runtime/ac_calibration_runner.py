"""Bounded current-version calibration; no agents or DUT selection."""
from pathlib import Path
from .ac_measurements import MODES, frequencies
from .ac_calibration import protocols
from .spice import Simulator, validate_protocol, measure
from .state import Store, Fault, BudgetEnd, digest, fingerprint, save, file_lock

MODEL = {"entry": "Oracle", "ports": ["P", "N"], "declared_ports": ["P", "N"]}


def fixtures():
    rows = []
    for mode in MODES:
        for n, (p, expected) in enumerate(protocols(mode), 1):
            rows.append({"id": mode + "_" + str(n), "protocol": p, "expected": expected})
    p, expected = protocols("ac_series_capacitance")[0]
    p["analysis"] = {"kind": "ac", "sweep": "lin", "points": 11, "start_Hz": 100, "stop_Hz": 10000}
    rows.append({"id": "ac_sweep_complete", "protocol": p, "expected": expected,
                 "reference": [(f, expected) for f in frequencies(p["analysis"])]})
    p, expected = protocols("ac_resistance")[0]
    p["components"][0]["nodes"] = ["P", "N"]
    for c in p["components"][1:]:
        c["nodes"] = ["N" if n == "0" else n for n in c["nodes"]]
    p["components"].append({"kind": "V", "name": "VCM", "nodes": ["N", "0"], "value": {"dc": 2, "ac": .7}})
    p["measurement"]["signal"] = "v(p,n)"
    rows.append({"id": "differential_nonzero_common_mode", "protocol": p, "expected": expected})
    return rows


def run(runner_path, output, check_only=False, resume=False, transport=None):
    runner_path, output = Path(runner_path).resolve(), Path(output).resolve()
    if not runner_path.is_file():
        raise Fault("input", "校准运行器不存在")
    rows = fixtures()
    for row in rows:
        validate_protocol(row["protocol"], MODEL)
    code = {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")}
    runner = {"argv": [str(runner_path), "-b", "-ascii", "{circuit}"], "timeout_seconds": 120}
    limits = {"api_calls": 0, "simulations": len(rows), "repairs": 0, "seconds": 2400}
    identity = fingerprint({"code": code, "runner_sha256": digest(runner_path), "fixtures": rows,
                            "offline_transport": transport is not None})
    if check_only:
        if output.exists():
            raise Fault("input", "请使用新校准预检目录")
        report = {"status": "prepared", "fixtures": len(rows), "api_calls": 0, "simulations": 0,
                  "maximum_simulations": len(rows), "current_calibration_passed": False, "code": code}
        save(output / "summary.json", report)
        return report
    with file_lock(output / ".workflow.lock"):
        store = Store(output, identity, limits, resume)
        path = output / "oracle.lib"
        text = ".subckt Oracle P N\nR1 P N 1000\n.ends Oracle\n"
        if not path.exists():
            path.write_text(text)
        elif path.read_text() != text:
            raise Fault("cache_corrupt", "校准模型已修改")
        sim = Simulator(store, runner, MODEL, transport=transport)
        results, budget_fault = [], None
        for row in rows:
            print("交流校准：" + row["id"], flush=True)
            receipt = {"id": row["id"], "protocol_sha256": fingerprint(row["protocol"]), "expected": row["expected"]}
            try:
                data, folder = sim.run(row["protocol"], path, row["id"])
                result = measure(row["protocol"], data, row.get("reference"))
                tolerance = max(abs(row["expected"]) * 1e-4, 1e-12)
                error = result["metrics"]["max_absolute_error"] if "metrics" in result else abs(result["value"] - row["expected"])
                receipt.update(status="pass" if error <= tolerance else "fail", result=result,
                               tolerance=tolerance, folder=str(folder),
                               artifact_hashes=store.get("simulations", folder.name)["hashes"])
            except BudgetEnd as e:
                budget_fault = e.record()
                receipt.update(status="budget_exhausted", fault=budget_fault)
            except Fault as e:
                if e.kind == "budget":
                    budget_fault = e.record()
                receipt.update(status="budget_exhausted" if budget_fault else "failed", fault=e.record())
            results.append(receipt)
            save(output / "progress.json", results)
            if budget_fault:
                break
        ok = len(results) == len(rows) and all(r["status"] == "pass" for r in results)
        status = "budget_exhausted" if budget_fault else "calibrated" if ok else "calibration_failed"
        report = {"schema": "ac-calibration-1", "status": status, "code": code, "identity": identity,
            "results": results, "api_calls": 0, "simulations": store.data["usage"]["simulations"],
            "test_backend": transport is not None, "current_calibration_passed": ok and transport is None,
            "budget_fault": budget_fault, "qualified_device_fixture": False, "full_device_acceptance": False}
        save(output / "summary.json", report)
        store.finish(status)
        return report
