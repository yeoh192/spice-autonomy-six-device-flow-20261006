#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified CLI. No API/simulation during import or preflight."""
import argparse
import json
import sys
from pathlib import Path
from flow_runtime.state import Fault, digest, file_lock, fingerprint, save, Store
from flow_runtime.task import import_bundle, load_task
from flow_runtime.agents import Agents
from flow_runtime.spice import Simulator
from flow_runtime.workflow import Workflow


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    batch_import = commands.add_parser("prepare-batch", help="将六器件资料和前置计划转换为统一哈希快照；不调用API或仿真")
    batch_import.add_argument("--input-root", type=Path, required=True)
    batch_import.add_argument("--output", type=Path, required=True)
    batch_check = commands.add_parser("batch-preflight", help="离线检查统一输入、草案及配置回归接口")
    batch_check.add_argument("--batch", type=Path, required=True)
    batch_check.add_argument("--output", type=Path, required=True)
    ac_check = commands.add_parser("calibrate-ac", help="校准当前版本20项交流接口；不会调用API或测试器件")
    ac_check.add_argument("--runner", type=Path, required=True)
    ac_check.add_argument("--output", type=Path, required=True)
    ac_check.add_argument("--check-only", action="store_true")
    ac_check.add_argument("--resume", action="store_true")
    qualify = commands.add_parser("qualify-drafts", help="冻结草案自动两阶段资格审查；参考模型不计为器件交付")
    qualify.add_argument("--batch", type=Path, required=True)
    qualify.add_argument("--calibration", type=Path, required=True)
    qualify.add_argument("--runner", type=Path, required=True)
    qualify.add_argument("--output", type=Path, required=True)
    qualify.add_argument("--rounds", type=int, default=2)
    qualify.add_argument("--check-only", action="store_true")
    qualify.add_argument("--resume", action="store_true")
    repair = commands.add_parser("repair-drafts", help="通用受限夹具修复、自动分流、校准及两阶段资格审查")
    repair.add_argument("--batch", type=Path, required=True)
    repair.add_argument("--calibration", type=Path, required=True)
    repair.add_argument("--runner", type=Path, required=True)
    repair.add_argument("--output", type=Path, required=True)
    repair.add_argument("--previous", type=Path)
    repair.add_argument("--rounds", type=int, default=3)
    repair.add_argument("--check-only", action="store_true")
    repair.add_argument("--resume", action="store_true")
    migration = commands.add_parser("import-legacy", help="迁移已有测试与清单；不修改旧实验")
    migration.add_argument("--bundle", type=Path, required=True)
    migration.add_argument("--task", type=Path, required=True)
    indexed = commands.add_parser("import-indexed", help="迁移资料并启用打包索引、候选比较及诊断修复")
    indexed.add_argument("--bundle", type=Path, required=True)
    indexed.add_argument("--task", type=Path, required=True)
    indexed.add_argument("--index", type=Path)
    indexed.add_argument("--library-root", type=Path)
    indexed.add_argument("--max-trials", type=int, default=3)
    attach = commands.add_parser("attach-index", help="给已有flow-1任务创建独立索引任务")
    attach.add_argument("--source-task", type=Path, required=True)
    attach.add_argument("--task", type=Path, required=True)
    attach.add_argument("--index", type=Path)
    attach.add_argument("--library-root", type=Path)
    attach.add_argument("--max-trials", type=int, default=3)
    commands.add_parser("catalog-check", help="校验打包的分类索引和全部原始模板；不请求API或仿真")
    library_attach = commands.add_parser("library-attach", help="另建测试库任务；不修改原任务")
    library_attach.add_argument("--source-task", type=Path, required=True)
    library_attach.add_argument("--task", type=Path, required=True)
    library_attach.add_argument("--buk-row-series-review", action="store_true")
    library_attach.add_argument("--typical-tolerance-percent", type=float)
    library_attach.add_argument("--curve-mae-percent", type=float)
    library_attach.add_argument("--curve-max-error-percent", type=float)
    replay = commands.add_parser("recheck", help="离线复读历史选模RAW；不请求API或启动仿真")
    replay.add_argument("--runtime", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--typical-tolerance-percent", type=float)
    replay.add_argument("--curve-mae-percent", type=float)
    replay.add_argument("--curve-max-error-percent", type=float)
    for name in ("preflight", "run", "library-plan", "library-qualify"):
        p = commands.add_parser(name)
        p.add_argument("--task", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--resume", action="store_true")
        if name in ("run", "preflight"):
            p.add_argument("--continuous", action="store_true", help="主循环持续验收；不以固定预算结束，API错误等待重试")
    a = ap.parse_args(argv)
    if a.command == "repair-drafts":
        from flow_runtime.autonomous_qualification import run
        report = run(a.batch, a.calibration, a.runner, a.output, a.previous, a.rounds, a.resume, a.check_only)
        print("自动夹具修复：", report["status"], "；报告：", a.output.resolve() / "summary.json")
        return 0 if report["status"] in ("prepared", "supervised_qualification_completed_with_gaps") else 2
    if a.command == "qualify-drafts":
        from flow_runtime.draft_qualification import run
        report = run(a.batch, a.calibration, a.runner, a.output, a.rounds, a.resume, a.check_only)
        print("草案资格：", report["status"], "；报告：", a.output.resolve() / "summary.json")
        return 0 if report["status"] in ("prepared", "qualification_completed_with_gaps") else 2
    if a.command == "prepare-batch":
        from flow_runtime.input_batch import prepare_batch
        existed = a.output.exists()
        try:
            result = prepare_batch(a.input_root, a.output)
        except (Fault, OSError, KeyError, ValueError, TypeError) as error:
            if not existed and a.output.exists():
                fault = error if isinstance(error, Fault) else Fault("input", str(error))
                save(a.output / "preparation_error.json", fault.record())
            raise
        for d in result["devices"]:
            print("%s：清单%d；配置测试%d；未获资格草案%d" % (d["device"], d["record_count"], d["configured_tests"], d["draft_tests"]))
        print("统一批次：", a.output.resolve() / "batch.json")
        print("资料适配完成；未调用API或LTspice，未放行全覆盖拟合。")
        return 0
    if a.command == "batch-preflight":
        from flow_runtime.input_batch import validate_batch
        if a.output.exists():
            raise Fault("input", "请使用新预检报告路径")
        result = validate_batch(a.batch)
        save(a.output, result)
        print("统一输入预检通过；器件：", len(result["devices"]))
        print("当前版本交流校准待执行，草案仍待资格审查；未调用API或LTspice。")
        print("报告：", a.output.resolve())
        return 0
    if a.command == "calibrate-ac":
        from flow_runtime.ac_calibration_runner import run
        report = run(a.runner, a.output, a.check_only, a.resume)
        print("交流校准：", report["status"], "；报告：", a.output.resolve() / "summary.json")
        return 0 if report["status"] in ("prepared", "calibrated") else 2
    if a.command == "library-attach":
        from flow_runtime.test_library import attach
        from flow_runtime.project_standard import FIELDS, validate_standard
        numbers = [getattr(a, k) for k in FIELDS]
        if any(n is not None for n in numbers) and not all(n is not None for n in numbers):
            raise Fault("input", "项目验收标准需同时提供三项，不能缺省补猜")
        standard = validate_standard(dict(zip(FIELDS, numbers))) if all(n is not None for n in numbers) else None
        path = attach(a.source_task, a.task, a.buk_row_series_review, standard)
        print("新测试库任务：", path)
        if standard:
            print("已在启动前固定项目验收标准：", standard)
        print("未调用API或LTspice；原任务不变。")
        return 0
    if a.command == "recheck":
        from flow_runtime.offline_recheck import recheck
        from flow_runtime.state import finite
        values = [a.typical_tolerance_percent, a.curve_mae_percent, a.curve_max_error_percent]
        if any(v is not None for v in values) and not all(v is not None for v in values):
            raise Fault("input", "项目标准需同时提供典型值、曲线MAE和曲线最大误差三项")
        standard = None
        if all(v is not None for v in values):
            if any(finite(v) < 0 for v in values):
                raise Fault("input", "项目验收误差不能为负")
            standard = dict(zip(("typical_tolerance_percent", "curve_mae_percent", "curve_max_error_percent"), values))
        report = recheck(a.runtime, a.output, standard)
        return 0 if report["counts"]["failed"] == 0 else 2
    if a.command == "catalog-check":
        from flow_runtime.templates import verify_library
        print(json.dumps(verify_library(), ensure_ascii=False, indent=2))
        print("模板库全部哈希通过；未调用API或LTspice。")
        return 0
    if a.command in ("import-indexed", "attach-index"):
        from flow_runtime.templates import attach_index
        if a.command == "import-indexed":
            source_task = a.task.parent / "legacy_input.json"
            if a.task.exists() or source_task.exists():
                raise Fault("input", "索引任务输入目录已存在，请使用新目录")
            import_bundle(a.bundle, source_task)
        else:
            source_task = a.source_task
        path = attach_index(source_task, a.task, a.index, a.library_root, a.max_trials)
        task, assets = load_task(path)
        print("索引任务：", path)
        print("已配置测试：", len(task["cases"]), "；结构兼容候选：", len(task["candidates"]))
        print("来源策略：", task["template_selection"]["source_policy"], "；总预算：", task["budgets"])
        print("未调用API或LTspice；原任务与模型不变。")
        return 0
    if a.command == "import-legacy":
        path = import_bundle(a.bundle, a.task)
        task, assets = load_task(path)
        print("任务包：", path)
        print("已迁移测试：", len(task["cases"]), "；手册项目：", len(task["inventory"]["items"]))
        print("未调用API或LTspice；来源、缺口和待定阈值已保留。")
        return 0
    # One owner per workflow output. Import verification precedes credential prompts.
    a.output = a.output.resolve()
    with file_lock(a.output / ".workflow.lock"):
        try:
            print("【器件资料载入与校验】开始（载入标准包，不重新解析PDF）", flush=True)
            task, assets = load_task(a.task)
            print("【器件资料载入与校验】结束", flush=True)
            if getattr(a,"continuous",False):
                if task.get("input_integration"):
                    raise Fault("input","仅配置回归任务不能启用持续验收")
                task["policy"]["continuous_until_acceptance"]=True
            code = {str(p.relative_to(Path(__file__).parent)): digest(p)
                    for p in (Path(__file__).parent / "flow_runtime").rglob("*.py")}
            code["spice_flow.py"] = digest(Path(__file__))
            identity = fingerprint({"task": task, "assets": assets, "code": code})
            store = Store(a.output, identity, task["budgets"], a.resume)
            save(a.output / "input_snapshot.json", {"task": task, "assets": assets, "code": code})
            agents = Agents(store, task["routes"])
            simulator = Simulator(store, task["runner"], task["model"], cache=task.get("simulation_cache"))
            workflow = Workflow(task, store, agents, simulator)
            if a.command == "library-plan":
                from flow_runtime.test_library import plan
                report = plan(task)
                save(a.output / "test_library_plan.json", report)
                workflow.preflight()
                store.finish("prepared")
                print("测试库计划：", report["counts"])
                print("未调用API或LTspice；报告：", a.output / "test_library_plan.json")
                summary = {"workflow_status": "prepared"}
            elif a.command == "library-qualify":
                if not task.get("test_library", {}).get("enabled"):
                    raise Fault("input", "请先用library-attach建立测试库任务")
                if task.get("select_candidate") or task.get("template_selection"):
                    raise Fault("input", "资格验证需要明确冻结的模型；请从import-legacy任务开始，不能默认选索引第一项")
                summary = workflow.qualify_library()
            else:
                summary = workflow.run(check_only=a.command == "preflight")
            return 0 if summary["workflow_status"] in ("prepared", "delivered_declared_scope", "qualification_completed_with_gaps") else 2
        except (Fault, OSError, KeyError, ValueError, TypeError) as error:
            if not isinstance(error, Fault):
                error = Fault("input", str(error))
            # Preserve an existing run on identity mismatch; never overwrite its state.
            name = "resume_rejected.json" if (a.output / "state.json").exists() else "preflight_error.json"
            save(a.output / name, error.record())
            print("流程未启动：", str(error), "；诊断：", a.output / name)
            return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Fault, OSError, KeyError, ValueError, TypeError) as e:
        print("输入/安装错误：", str(e), file=sys.stderr)
        sys.exit(2)
