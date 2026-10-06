"""Re-measure saved indexed baselines, without API or simulator dispatch."""
import copy
import re
from pathlib import Path
from .state import Fault, artifact_hashes, artifacts_valid, digest, read, save
from .spice import acceptance, load_reference, measure, raw_data, render, validate_protocol


def recheck(runtime, output, project_standard=None):
    runtime, output = Path(runtime).resolve(), Path(output).resolve()
    if output == runtime or runtime in output.parents or output in runtime.parents:
        raise Fault("input", "离线复核必须使用旧任务之外的新目录")
    if output.exists():
        raise Fault("input", "复核目录已存在，请使用新目录")
    snapshot = read(runtime / "input_snapshot.json")
    state = read(runtime / "state.json")
    task = snapshot["task"]
    trials = read(runtime / "template_selection/trial_results.json")
    cases = {c["id"]: c for c in task["cases"]}
    candidates = {c["id"]: c for c in task["candidates"]}
    reviewed, counts = [], {"completed": 0, "failed": 0, "recovered_parser_cases": 0}
    for trial in trials:
        candidate = candidates[trial["candidate_id"]]
        if trial["model_sha256"] != candidate["sha256"]:
            raise Fault("cache_corrupt", "候选模型哈希与输入快照不一致")
        results = []
        for old in trial["results"]:
            case = copy.deepcopy(cases[old["test"]])
            p = case["protocol"]
            location = old.get("artifacts") or old.get("fault", {}).get("evidence", {}).get("folder")
            try:
                if not location:
                    raise Fault("missing_evidence", "结果未保存仿真目录")
                folder = Path(location).resolve()
                if folder.parent != runtime / "simulations":
                    raise Fault("cache_corrupt", "仿真证据不在来源任务目录中")
                record = state["simulations"].get(folder.name)
                if not record:
                    raise Fault("cache_corrupt", "来源状态中未登记该仿真")
                files = ("model.lib", "test.cir", "test.raw", "test.log", "execution.json")
                if any((folder / f).is_symlink() or not (folder / f).is_file() for f in files):
                    raise Fault("missing_evidence", "仿真证据文件缺失或为符号链接")
                hashes = artifact_hashes(folder, files)
                if record["status"] == "completed":
                    if not artifacts_valid(folder, record.get("hashes")):
                        raise Fault("cache_corrupt", "原已完成仿真文件哈希不一致")
                    integrity = "verified_against_execution_hashes"
                elif record["status"] == "failed" and record.get("fault", {}).get("kind") == "parser":
                    # Old versions did not hash failed RAW. They can supply new
                    # diagnostic evidence, but cannot silently become verified
                    # cache entries or formal acceptance from a past execution.
                    integrity = "failed_RAW_had_no_execution_hash;diagnostic_replay_only"
                else:
                    raise Fault("execution", "来源不是已完成仿真或解析失败；不能离线改判")
                if hashes["model.lib"] != trial["model_sha256"]:
                    raise Fault("cache_corrupt", "实际仿真模型与候选哈希不一致")
                actual = (folder / "test.cir").read_text(encoding="utf-8")
                method = re.findall(r"(?im)^\.options method=(trap|gear)\s*$", actual)
                if len(method) != 1:
                    raise Fault("input", "实际求解方法缺失或不唯一")
                p["method"] = method[0]
                validate_protocol(p, candidate, case.get("contract"))
                if render(p, candidate) != actual:
                    raise Fault("cache_corrupt", "实际电路与固定测试协议不一致")
                execution = read(folder / "execution.json")
                argv = [v.replace("{circuit}", str(folder / "test.cir")) for v in task["runner"]["argv"]]
                if execution.get("returncode") != 0 or execution.get("argv") != argv:
                    raise Fault("execution", "启动记录未证明指定电路正常结束")
                if not (folder / "test.log").stat().st_size:
                    raise Fault("execution", "仿真日志为空")
                data = raw_data(folder / "test.raw")
                if not data["complete"]:
                    raise Fault("parser", "RAW仍不完整")
                reference = None
                if case.get("reference"):
                    ref = case["reference"]
                    if digest(ref["path"]) != ref["sha256"]:
                        raise Fault("cache_corrupt", "数字化参考曲线哈希不一致")
                    reference = load_reference(ref["path"], case["expectation"]["unit"], ref.get("condition"))
                result = measure(p, data, reference)
                old_acceptance = acceptance(result, case["expectation"])
                if project_standard:
                    if "metrics" in result:
                        case["expectation"]["thresholds"] = {
                            "MAE_over_reference_span_percent": project_standard["curve_mae_percent"],
                            "max_error_over_reference_span_percent": project_standard["curve_max_error_percent"]}
                    elif case["expectation"].get("typical") is not None:
                        case["expectation"].pop("typical_absolute_tolerance", None)
                        case["expectation"]["typical_relative_tolerance_percent"] = project_standard["typical_tolerance_percent"]
                result.update(test=case["id"], execution="completed", acceptance=acceptance(result, case["expectation"]),
                    original_execution=old["execution"], original_expectation_acceptance=old_acceptance,
                    expectation=case["expectation"], source_folder=str(folder), source_hashes=hashes,
                    source_integrity=integrity, model_sha256=trial["model_sha256"],
                    raw_normalization={k: data[k] for k in ("raw_points", "parsed_points", "normalizations")})
                if case["expectation"]["unit"] == "F" and result.get("value", 1) < 0:
                    result.update(acceptance="fail", model_conflict="negative capacitance")
                counts["completed"] += 1
                counts["recovered_parser_cases"] += old["execution"] != "completed"
            except (Fault, OSError, KeyError, ValueError) as e:
                f = e if isinstance(e, Fault) else Fault("missing_evidence", str(e))
                result = {"test": case["id"], "execution": "failed", "acceptance": "not_evaluated", "fault": f.record()}
                counts["failed"] += 1
            results.append(result)
        scores = [r["metrics"]["MAE_over_reference_span_percent"] for r in results if r.get("metrics", {}).get("MAE_over_reference_span_percent") is not None]
        reviewed.append({"candidate_id": trial["candidate_id"], "model_sha256": trial["model_sha256"],
            "eligible_starting_point": len(results) == len(cases) and all(r["execution"] == "completed" and not r.get("model_conflict") for r in results),
            "mean_curve_MAE_percent": sum(scores) / len(scores) if scores else None,
            "acceptance_counts": {s: sum(r["acceptance"] == s for r in results) for s in ("pass", "fail", "pending", "not_evaluated")},
            "results": results})
    report = {"status": "offline_rechecked", "source_runtime": str(runtime), "api_calls": 0, "simulations": 0,
        "project_standard": project_standard, "counts": counts, "trials": reviewed,
        "formal_delivery": False, "coverage_completed": False,
        "limitations": ["本命令复读历史电路及RAW，不调用API或仿真，不修改旧状态，不发布缓存或保留模型。",
                        "旧解析失败文件没有运行时哈希；复读结果用于诊断，不能当作已验证历史缓存。",
                        "拟合起点可有电气误差；最终验收和手册覆盖仍需独立检查。"]}
    output.mkdir(parents=True)
    save(output / "summary.json", report)
    save(output / "source_snapshot_hashes.json", {n: digest(runtime / n) for n in
        ("input_snapshot.json", "state.json", "template_selection/trial_results.json")})
    lines = ["# 历史选模离线复核", "", "API调用：0；新仿真：0；正式交付：否", "",
        "| 候选 | 可执行拟合起点 | 曲线平均归一化MAE (%) | 通过 | 不通过 | 待定 | 执行失败 |",
        "|---|---|---:|---:|---:|---:|---:|"]
    for t in reviewed:
        c = t["acceptance_counts"]
        lines.append("| %s | %s | %s | %d | %d | %d | %d |" % (t["candidate_id"], t["eligible_starting_point"],
            t["mean_curve_MAE_percent"], c["pass"], c["fail"], c["pending"], c["not_evaluated"]))
    lines += ["", "恢复解析失败记录：%d；完整读取：%d；仍失败：%d" % (counts["recovered_parser_cases"], counts["completed"], counts["failed"])]
    lines += ["", *report["limitations"]]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for t in reviewed:
        print("%s：读取完成%d/%d；验收%s" % (t["candidate_id"],
              sum(r["execution"] == "completed" for r in t["results"]), len(t["results"]), t["acceptance_counts"]), flush=True)
    print("离线复核报告：", output / "summary.json", flush=True)
    return report
