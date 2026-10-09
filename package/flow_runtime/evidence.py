"""Same measured facts and frozen targets for designers and independent reviewers."""
import copy
from pathlib import Path
from .state import Fault, digest, fingerprint, save, artifacts_valid
from .spice import load_reference, model_text, raw_data, render, acceptance, interpolate


def sample_rows(rows, maximum=16):
    if len(rows) <= maximum:
        return copy.deepcopy(rows)
    return [copy.deepcopy(rows[round(i * (len(rows)-1)/(maximum-1))]) for i in range(maximum)]


def residual(result, expectation):
    """Positive always means simulated minus reference, never magnitude-only."""
    rows = result.get("comparison", [])
    if rows:
        ys = [r["reference_y"] for r in rows]
        span = max(ys)-min(ys)
        errors = [r["simulated_y"]-r["reference_y"] for r in rows]
        bins = []
        for j in range(min(6, len(rows))):
            lo, hi = j*len(rows)//min(6, len(rows)), (j+1)*len(rows)//min(6, len(rows))
            chunk = errors[lo:hi]
            mean = sum(chunk)/len(chunk)
            bins.append({"x_first": rows[lo]["x"], "x_last": rows[hi-1]["x"],
                "signed_mean": mean, "mean_over_reference_span_percent": mean/span*100 if span else None,
                "direction": "above_reference" if mean > 0 else "below_reference" if mean < 0 else "equal"})
        peak = max(range(len(errors)), key=lambda i: abs(errors[i]))
        return {"sign_definition": "simulated_y - reference_y", "unit": expectation["unit"],
            "points": len(rows), "reference_span": span, "signed_mean": sum(errors)/len(errors),
            "above_points": sum(e > 0 for e in errors), "below_points": sum(e < 0 for e in errors),
            "peak": {**rows[peak], "signed_error": errors[peak]}, "intervals": bins,
            "comparison_samples": sample_rows(rows)}
    if result.get("value") is not None:
        value, typical = result["value"], expectation.get("typical")
        return {"unit": expectation["unit"], "simulated_value": value, "typical": typical,
            "signed_error": value-typical if typical is not None else None,
            "relative_error_percent": (value-typical)/abs(typical)*100 if typical else None,
            "limit_violations": {k: value-v for k, v in expectation.get("limits", {}).items()
                if (k == "max" and value > v) or (k == "min" and value < v)}}
    return {"status": "no_completed_measurement"}


def case_evidence(case, result, model, inventory):
    if result.get("artifact_hashes") and not artifacts_valid(result.get("artifacts", ""), result["artifact_hashes"]):
        raise Fault("cache_corrupt", "共享证据仿真文件哈希改变：" + case["id"])
    entry = {"test": case["id"], "unit": case["expectation"]["unit"],
        "expectation": copy.deepcopy(case["expectation"]), "contract": copy.deepcopy(case.get("contract")),
        "protocol": copy.deepcopy(case["protocol"]), "actual_test_circuit": render(case["protocol"], model),
        "evidence_ids": case.get("evidence", []),
        "manual_records": [copy.deepcopy(i) for i in inventory["items"] if case["id"] in i.get("bindings", [])],
        "result": {k: v for k, v in result.items() if k != "comparison"},
        "signed_residual": residual(result, case["expectation"])}
    if result.get("execution") == "completed":
        entry["recomputed_acceptance"] = acceptance(result, case["expectation"])
    if case.get("reference"):
        ref = case["reference"]
        if digest(ref["path"]) != ref["sha256"]:
            raise Fault("cache_corrupt", "共享证据参考文件哈希改变：" + case["id"])
        points = load_reference(ref["path"], case["expectation"]["unit"], ref.get("condition"))
        entry["reference"] = {**ref, "points": len(points), "samples": sample_rows(points),
            "reference_span": max(y for x, y in points)-min(y for x, y in points)}
    folder = result.get("artifacts")
    if folder and (Path(folder)/"test.cir").is_file():
        entry["actual_test_circuit"] = (Path(folder)/"test.cir").read_text(encoding="utf-8")
        entry["circuit_sha256"] = digest(Path(folder)/"test.cir")
    return entry


def bundle(workflow, cases=None, results=None, model_path=None):
    cases = workflow.cases if cases is None else cases
    results = workflow.results if results is None else results
    rows = {r["test"]: r for r in results}
    model_path = workflow.model_path if model_path is None else model_path
    text = model_text(model_path)
    value = {"schema": "shared-model-evidence-1", "device": workflow.task["device"],
        "model": {"entry": workflow.task["model"]["entry"], "ports": workflow.task["model"]["ports"],
            "declared_ports": workflow.task["model"]["declared_ports"], "active_sha256": digest(model_path),
            "text": text[:60000], "complete_in_context": len(text) <= 60000,
            "provenance": workflow.task["model"].get("provenance", {})},
        "acceptance_standard": copy.deepcopy(workflow.task.get("acceptance_standard")),
        "retention_policy": {"max_metric_regression_percent": workflow.policy["max_metric_regression_percent"],
            "all_active_cases_required": True, "already_passing_tests_must_remain_pass": True},
        "manual_evidence_status": "supplied_records; full PDF audit separately tracked",
        "tests": [case_evidence(c, rows.get(c["id"], {}), workflow.task["model"], workflow.inventory) for c in cases]}
    key = fingerprint(value)
    save(workflow.store.folder/"evidence"/(key+".json"), value)
    compact = copy.deepcopy(value)
    for test in compact['tests']:
        test['manual_records'] = [{k:r[k] for k in ('id','label','kind','reference_evidence') if k in r} for r in test['manual_records']]
        # Circuit is the executed protocol. Keep one representation, not both.
        test.pop('protocol',None)
        test['result'] = {k:v for k,v in test['result'].items() if k in ('test','execution','acceptance','value','unit','metrics','model_sha256','protocol_sha256')}
        if test.get('contract'):
            test['contract'] = {k:v for k,v in test['contract'].items() if k in ('conditions','expectation','reference_id')}
    return {**compact, "evidence_sha256": key, "full_evidence_file": str(workflow.store.folder/'evidence'/(key+'.json'))}


def compact_history(history, cases):
    """Retain feedback direction without resending every curve sample each round."""
    expected = {c["id"]: c["expectation"] for c in cases}
    keep = {'round','status','proposal','fault','reason','reasons','gain','improved','baseline_sha256','model_sha256','results','review','result_review','instruction','candidate_diagnostics','parameter_targets','physical_executed','patch_id','planner_role'}
    answer = [{k:copy.deepcopy(v) for k,v in r.items() if k in keep} for r in history]
    for record in answer:
        if 'candidate_diagnostics' in record:
            record['candidate_diagnostics'] = [{k:v for k,v in d.items() if k in ('test','log_tail','execution_diagnostics','measured_port_current')} for d in record['candidate_diagnostics']]
        for result in record.get("results", []):
            if result.get("test") in expected and ('comparison' in result or 'signed_residual' not in result):
                result["signed_residual"] = residual(result, expected[result["test"]])
            fields = {'test','execution','acceptance','value','unit','metrics','model_sha256','signed_residual','fault'}
            trimmed = {k:v for k,v in result.items() if k in fields}
            result.clear()
            result.update(trimmed)
    return answer


def phase_evidence(protocol, trial):
    """Read actual saved waveforms, separately labelled by measurement phase."""
    if not trial.get("artifacts") or trial.get("execution") != "completed":
        return {"status": "not_executed_or_failed"}
    if trial.get("artifact_hashes") and not artifacts_valid(trial["artifacts"], trial["artifact_hashes"]):
        raise Fault("cache_corrupt", "实测阶段证据哈希改变")
    data = raw_data(Path(trial["artifacts"])/"test.raw")
    xs = data["axis"]
    if protocol["analysis"]["kind"] != "tran":
        return {"status": "completed", "analysis": protocol["analysis"]}
    window = trial.get("measurement_window")
    if not window:
        return {"status": "measurement_window_missing"}
    phases = {}
    for label, low, high in (("preparation", xs[0], window["start"]),
                             ("measurement", window["start"], window["end"]),
                             ("after_endpoint", window["end"], xs[-1])):
        signals = {}
        if not xs[0] <= low <= high <= xs[-1]:
            raise Fault("parser", "阶段边界超出真实波形；禁止外推")
        knots = sorted({low, high} | {x for x in xs if low < x < high})
        for name, ys in data["signals"].items():
            values = [complex(interpolate(xs, ys, x)).real for x in knots]
            if values:
                integral = sum((a+b)*(y-x)/2 for x,y,a,b in zip(knots,knots[1:],values,values[1:]))
                signals[name] = {"minimum": min(values), "maximum": max(values),
                    "first": values[0], "last": values[-1],
                    "time_weighted_mean": integral/(high-low) if high > low else None}
        phases[label] = {"from_s": low, "to_s": high, "signals": signals}
    return {"status": "completed", "raw_sha256": digest(Path(trial["artifacts"])/"test.raw"),
        "measurement_window": window, "phases": phases,
        "note": "After-endpoint values are not the measurement endpoint. Preparation includes ramp and hold; use declared bias-check windows to assess stable bias. Means are time-weighted, not sample averages."}


def planner_evidence(shared, triggers):
    """Detailed failed-test evidence plus every regression guard; full proof stays local."""
    selected = set(triggers)
    value = copy.deepcopy(shared)
    value['tests'] = [t for t in value['tests'] if t['test'] in selected]
    value['regression_guards'] = [{'test': t['test'], 'expectation': t['expectation'],
        'baseline': t['result'], 'reference_sha256': t.get('reference', {}).get('sha256')}
        for t in shared['tests'] if t['test'] not in selected]
    value['active_test_ids'] = [t['test'] for t in shared['tests']]
    value['scope'] = 'Detailed trigger evidence is selected for planning only. A patch still requires ALL active tests before retention.'
    return value


def planner_history(history, cases):
    """Remove duplicate candidate measurements while retaining residual signs and failures."""
    value = compact_history(history, cases)
    for row in value:
        for result in row.get('results', []):
            signed = result.get('signed_residual', {})
            result['signed_residual'] = {k:v for k,v in signed.items() if k != 'comparison_samples'}
    return value
