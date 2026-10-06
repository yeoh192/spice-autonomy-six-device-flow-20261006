"""Snapshot material packets into a single, hash-checked input interface.

No part numbers, credential requests or simulation dispatch belong here.
Unqualified fixture drafts and reference models never become active tests/models.
"""
import copy
import re
from collections import Counter
from pathlib import Path

from .state import Fault, digest, fingerprint, read, save
from .spice import validate_model, model_text, validate_protocol, validate_measurement_unit, render
from .task import load_task
from .project_standard import validate_standard, apply_standard

SCHEMA = "unified-device-input-1"
BATCH_SCHEMA = "unified-input-batch-1"
KINDS = {
    "test_development_required": "test",
    "existing_test_requires_scope_audit": "test",
    "scope_or_corner_review": "constraint",
    "constraint_audit_required": "constraint",
    "series_group": "informational",
    "not_applicable": "informational",
    "informational": "informational",
}


def resolve(base, value):
    p = Path(value)
    return p.resolve() if p.is_absolute() else (base / p).resolve()


def standard_from_packet(value):
    if value.get("threshold_status") != "confirmed_project_engineering_standard":
        raise Fault("input", "输入包缺少已确认的项目验收标准")
    return validate_standard({
        "typical_tolerance_percent": value["typical_tolerance_percent"],
        "curve_mae_percent": value["curve_thresholds"]["MAE_over_reference_span_percent"],
        "curve_max_error_percent": value["curve_thresholds"]["max_error_over_reference_span_percent"],
    })


def inventory_from_plan(plan, case_ids):
    """Retain every series, corner and excluded record, and its original meaning."""
    items = []
    ids = [i["id"] for i in plan["items"]]
    if len(ids) != len(set(ids)):
        raise Fault("input", "前置清单存在重复ID")
    if dict(Counter(i["plan_status"] for i in plan["items"])) != plan["counts"]:
        raise Fault("input", "前置清单统计与记录不一致")
    for original in plan["items"]:
        status = original["plan_status"]
        if status not in KINDS:
            raise Fault("input", "未知清单状态，需要显式适配：" + status)
        item = copy.deepcopy(original)
        item["source_record"] = copy.deepcopy(original)
        item["source_kind"] = original["kind"]
        item["kind"] = KINDS[status]
        item["label"] = original.get("label") or original.get("reference_evidence", {}).get("key") or original["id"]
        item["evidence"] = original.get("evidence") or original.get("reference_evidence", {}).get("evidence_refs") or ["pdf_page:" + str(n) for n in original.get("pdf_pages", [])]
        if not item["evidence"]:
            raise Fault("input", "清单项目缺少来源证据：" + original["id"])
        bindings = [b["test_id"] if isinstance(b, dict) else b for b in original.get("bindings", [])]
        if set(bindings) - set(case_ids):
            raise Fault("input", "清单绑定没有对应已配置测试：" + original["id"])
        item["bindings"] = bindings
        # Existing measurements have not audited expanded temperature/series/package scope.
        item["binding_complete"] = bool(original.get("binding_complete") and bindings and status != "existing_test_requires_scope_audit")
        item["classification_review_pending"] = original["kind"] == "test_or_constraint_pending_classification"
        if item["kind"] == "informational":
            item["reason"] = original.get("reason") or ("原计划状态为" + status + "；保留原记录及依据，不代表该组的子曲线已验证")
        if status == "scope_or_corner_review":
            item["reason"] = "范围、统计或角落需专门审计；单一典型SPICE模型不能自动证明统计包络"
            item.pop("review", None)
        items.append(item)
    return {"review_status": plan["review_status"], "items": items,
            "full_manual_coverage": False, "open_contract_issues": copy.deepcopy(plan.get("open_contract_issues", []))}


def bind_reference_assets(inventory, references):
    """Bind explicit curve paths to preserved snapshot aliases, never by filename alone."""
    def locators(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("csv", "path", "reference_csv") and isinstance(item, str) and item.lower().endswith(".csv"):
                    yield item
                else:
                    yield from locators(item)
        elif isinstance(value, list):
            for item in value:
                yield from locators(item)
    for item in inventory["items"]:
        bindings = []
        for name in sorted(set(locators(item.get("reference_evidence", {})))):
            normalized = name.replace("\\", "/")
            suffix = "references/curves/" + normalized.split("/curves/", 1)[1] if "/curves/" in normalized else normalized
            matches = [r for r in references if r["source_relative"] == suffix or (
                r["source_relative"].startswith("references/curves/") and
                normalized.endswith("/" + r["source_relative"][len("references/curves/"):]))]
            if len(matches) == 1:
                bindings.append({"source_locator": name, "status": "snapshot_bound", **matches[0]})
            else:
                bindings.append({"source_locator": name, "status": "unresolved_reference_locator"})
        item["reference_asset_bindings"] = bindings


class Snapshot:
    def __init__(self, folder):
        self.folder = folder
        self.assets = {}
        self.sources = {}

    def file(self, source, expected=None):
        source = Path(source).resolve()
        if not source.is_file():
            raise Fault("input", "资料文件不存在：" + str(source))
        data = source.read_bytes()
        import hashlib
        actual = hashlib.sha256(data).hexdigest()
        if expected is not None and actual != expected:
            raise Fault("input", "资料哈希与来源声明不一致：" + str(source))
        if str(source) in self.sources and self.sources[str(source)] != actual:
            raise Fault("input", "导入期间来源发生变化：" + str(source))
        suffix = source.suffix.lower() if re.fullmatch(r"\.[a-zA-Z0-9]+", source.suffix) else ".bin"
        name = "assets/" + actual + suffix
        target = self.folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
        self.assets[name] = actual
        self.sources[str(source)] = actual
        return name

    def object(self, name, value):
        save(self.folder / name, value)
        self.assets[name] = digest(self.folder / name)
        return name

    def stable(self):
        for source, expected in self.sources.items():
            if digest(source) != expected:
                raise Fault("input", "输入仍在被其他任务修改，请稍后重新导入：" + source)


def relocate_task(task, base, snap):
    task = copy.deepcopy(task)
    for model in [task["model"]] + task.get("candidates", []):
        model["path"] = snap.file(resolve(base, model["path"]), model["sha256"])
    for case in task["cases"]:
        if case.get("reference"):
            r = case["reference"]
            r["path"] = snap.file(resolve(base, r["path"]), r["sha256"])
    for item in task["inventory"]["items"]:
        contract = item.get("test_contract", {})
        if contract.get("reference"):
            r = contract["reference"]
            r["path"] = snap.file(resolve(base, r["path"]), r["sha256"])
    for src in task["inventory"].get("sources", []):
        src["path"] = snap.file(resolve(base, src["path"]), src["sha256"])
    # This is migration of an explicit regression model, not indexed selection.
    if task.get("select_candidate") or task.get("template_selection"):
        raise Fault("input", "资料包中的配置回归任务必须有明确冻结模型；索引任务须单独创建")
    task.pop("simulation_cache", None)
    task["capability_library"] = "runtime_capabilities"
    return task


def prepare_device(packet_path, output):
    packet_path, output = Path(packet_path).resolve(), Path(output).resolve()
    packet = read(packet_path)
    if packet.get("schema_version") != "device-input-packet-1":
        raise Fault("input", "不支持的器件资料包格式")
    if output.exists():
        raise Fault("input", "请使用新输入目录，拒绝覆盖资料")
    output.mkdir(parents=True)
    snap = Snapshot(output)
    snap.file(packet_path)
    base = packet_path.parent
    materials = {}
    for key in ("manual", "electrical", "evidence", "ports", "inventory", "test_plan", "acceptance"):
        materials[key] = snap.file(resolve(base, packet[key]))
    reference_assets = []
    # Preserve digitized series that have not yet acquired an executable test.
    # The alias table makes original material paths resolvable after relocation.
    for category in ("references", "reference_models"):
        for source in sorted((base / category).rglob("*")):
            if not source.is_file():
                continue
            if source.suffix.lower() not in (".csv", ".json", ".lib", ".txt", ".md"):
                continue
            name = snap.file(source)
            reference_assets.append({"source_relative": str(source.relative_to(base)), "path": name,
                "sha256": snap.assets[name], "role": "reference_interface_benchmark_only" if category == "reference_models" else "reference_data"})
    standard = standard_from_packet(read(output / materials["acceptance"]))
    stage = packet.get("upfront_coverage_stage", {})
    if not stage.get("plan"):
        raise Fault("input", "需要前置覆盖清单，不能默认原清单已完整")
    plan_path = resolve(base, stage["plan"])
    plan = read(plan_path)
    if plan["device"] != packet["device"]:
        raise Fault("input", "器件与前置清单型号不一致")
    if plan["source_inventory_sha256"] != digest(output / materials["inventory"]):
        raise Fault("input", "前置清单对应的原清单已变化")
    snap.file(resolve(plan_path.parent, plan["manual"]["path"]), plan["manual"]["sha256"])
    if plan["manual"]["sha256"] != snap.assets[materials["manual"]]:
        raise Fault("input", "资料包和前置计划不是同一份PDF")
    snap.object("coverage_plan.json", plan)
    pages = plan_path.parents[2] / "manual_review" / plan_path.parent.name / "pages.json"
    if pages.is_file():
        materials["manual_page_evidence"] = snap.file(pages)
    task = None
    if packet.get("execution_task"):
        path = resolve(base, packet["execution_task"])
        snap.file(path)
        source_task, _ = load_task(path)
        if source_task["device"] != packet["device"]:
            raise Fault("input", "配置回归任务型号不一致")
        task = relocate_task(source_task, path.parent, snap)
    inventory = inventory_from_plan(plan, [c["id"] for c in task["cases"]] if task else [])
    bind_reference_assets(inventory, reference_assets)
    configured = {c["id"]: c for c in task["cases"]} if task else {}
    for item in inventory["items"]:
        item["configured_reference_bindings"] = [{"test_id": k, **configured[k]["reference"]}
            for k in item["bindings"] if configured[k].get("reference")]
    inventory["sources"] = [{"path": materials["manual"], "sha256": snap.assets[materials["manual"]]}]
    drafts = []
    draft_path = plan_path.parent / "executable_tests.json"
    if draft_path.is_file():
        snap.file(draft_path)
        for original in read(draft_path)["cases"]:
            c = copy.deepcopy(original)
            if set(c["reference_ids"]) - {i["id"] for i in inventory["items"]}:
                raise Fault("input", "电路草案引用未知清单项目")
            if c.get("protocol_sha256") != fingerprint(c["protocol"]):
                raise Fault("input", "草案电路与协议哈希不一致")
            m = c["model"]
            m["path"] = snap.file(resolve(draft_path.parent, m["path"]), m["sha256"])
            validate_model(model_text(output / m["path"]), m)
            validate_protocol(c["protocol"], m, c["contract"])
            validate_measurement_unit(c)
            c.update(delivery_eligible=False, qualification_status="draft_requires_current_calibration_and_two_stage_review")
            c["model_role"] = "reference_interface_benchmark_only"
            actual = output / "fixture_circuits" / (c["id"] + ".cir")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", c["id"]):
                raise Fault("input", "草案ID非法")
            actual.parent.mkdir(exist_ok=True)
            actual.write_text(render(c["protocol"], m), encoding="utf-8")
            name = str(actual.relative_to(output))
            snap.assets[name] = digest(actual)
            c["actual_circuit"] = {"path": name, "sha256": snap.assets[name]}
            drafts.append(c)
    snap.object("fixture_drafts.json", {"cases": drafts, "active_test_count": 0, "qualified": False})
    snap.object("inventory.json", inventory)
    if task:
        task["inventory"] = copy.deepcopy(inventory)
        task = apply_standard(task, standard)
        task["input_integration"] = {"schema": SCHEMA, "execution_scope": "configured_regression_only",
            "model_fitting_ready": False, "coverage_review_status": inventory["review_status"],
            "qualification_status": "expanded_inventory_and_fixtures_pending"}
        task["inventory"]["sources"] += [{"path": "coverage_plan.json", "sha256": snap.assets["coverage_plan.json"]},
                                           {"path": materials["ports"], "sha256": snap.assets[materials["ports"]]}]
        snap.object("configured_task.json", task)
        load_task(output / "configured_task.json")
    queue = [{"reference_id": i["id"], "source_kind": i["source_kind"],
              "plan_status": i["plan_status"], "method_family": i.get("method_family"),
              "capability_status": i.get("capability_status"), "pdf_pages": i.get("pdf_pages", []),
              "bindings": i["bindings"], "binding_complete": i["binding_complete"]} for i in inventory["items"]]
    for queued, item in zip(queue, inventory["items"]):
        queued["reference_asset_bindings"] = item["reference_asset_bindings"]
    snap.object("coverage_queue.json", {"items": queue, "review_status": inventory["review_status"]})
    value = {"schema_version": SCHEMA, "device": packet["device"],
        "category": packet["category"], "selected_variant": packet["selected_variant"],
        "materials": materials, "coverage_plan": "coverage_plan.json", "inventory": "inventory.json",
        "reference_assets": reference_assets,
        "coverage_queue": "coverage_queue.json", "fixture_drafts": "fixture_drafts.json",
        "configured_task": "configured_task.json" if task else None,
        "configured_tests": len(task["cases"]) if task else 0, "draft_tests": len(drafts),
        "acceptance_standard": standard, "record_count": len(inventory["items"]),
        "review_status": inventory["review_status"], "model_fitting_ready": False,
        "full_manual_coverage": False, "delivery_eligible": False,
        "template_selection": copy.deepcopy(packet.get("template_selection", {})),
        "routes": {"design": {"provider": "qwen", "model": "qwen3.8-max"},
                   "review": {"provider": "glm", "model": "glm-5.3"}},
        "assets": snap.assets, "source_hashes": snap.sources,
        "status": "configured_scope_available_with_gaps" if task else "materials_and_drafts_with_execution_gaps"}
    snap.stable()
    save(output / "device_input.json", value)
    return value


def prepare_batch(input_root, output):
    root, output = Path(input_root).resolve(), Path(output).resolve()
    if root == output or root in output.parents or output in root.parents:
        raise Fault("input", "输出目录必须与来源资料目录分开")
    original = read(root / "batch_manifest.json")
    original_hash = digest(root / "batch_manifest.json")
    if original.get("schema_version") != "six-device-input-batch-1":
        raise Fault("input", "批次来源格式不支持")
    if output.exists():
        raise Fault("input", "请使用新批次目录")
    devices = original["devices"]
    if len({d["device"] for d in devices}) != len(devices) or len({d["folder"] for d in devices}) != len(devices):
        raise Fault("input", "批次器件或目录重复")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", d["folder"]) or d["folder"] in (".", "..") for d in devices):
        raise Fault("input", "批次子目录非法")
    output.mkdir(parents=True)
    rows = []
    for d in devices:
        value = prepare_device(resolve(root, d["input"]), output / d["folder"])
        if value["device"] != d["device"]:
            raise Fault("input", "批次声明与输入型号不符")
        rows.append({"device": value["device"], "folder": d["folder"],
            "input": d["folder"] + "/device_input.json",
            "sha256": digest(output / d["folder"] / "device_input.json"),
            "configured_tests": value["configured_tests"], "draft_tests": value["draft_tests"],
            "record_count": value["record_count"], "status": value["status"]})
    if digest(root / "batch_manifest.json") != original_hash:
        raise Fault("input", "导入期间批次来源已变化")
    from . import VERSION
    from .ac_measurements import MODES
    report = {"schema_version": BATCH_SCHEMA, "framework_version": VERSION,
        "source_manifest_sha256": original_hash, "devices": rows,
        "execution_order": [d["device"] for d in rows],
        "measurement_primitives": [{"mode": k, "native_unit": v,
            "status": "implemented_current_version_calibration_required",
            "qualified_device_fixture": False} for k, v in MODES.items()],
        "api_calls": 0, "simulations": 0, "model_fitting_ready": False,
        "full_batch_delivery": False, "historical_calibrations_are_current_receipts": False}
    save(output / "batch.json", report)
    return report


def validate_batch(batch_path):
    path = Path(batch_path).resolve()
    batch = read(path)
    if batch.get("schema_version") != BATCH_SCHEMA:
        raise Fault("input", "批次接口版本不支持")
    rows = []
    for row in batch["devices"]:
        p = resolve(path.parent, row["input"])
        if path.parent not in p.parents or digest(p) != row["sha256"]:
            raise Fault("cache_corrupt", "统一器件输入已变化或越界")
        value = read(p)
        if value.get("schema_version") != SCHEMA or value["device"] != row["device"]:
            raise Fault("input", "统一输入型号/版本不符")
        for name, expected in value["assets"].items():
            asset = resolve(p.parent, name)
            if p.parent not in asset.parents or digest(asset) != expected:
                raise Fault("cache_corrupt", "输入资料快照已变化或越界：" + name)
        plan = read(p.parent / value["coverage_plan"])
        inventory = read(p.parent / value["inventory"])
        if [i["id"] for i in plan["items"]] != [i["id"] for i in inventory["items"]]:
            raise Fault("input", "前置清单记录在适配时丢失")
        cases = []
        if value["configured_task"]:
            task, _ = load_task(p.parent / value["configured_task"])
            if task["input_integration"]["execution_scope"] != "configured_regression_only":
                raise Fault("input", "未完成覆盖的迁移任务不能自动变成全流程拟合任务")
            cases = task["cases"]
        for draft in read(p.parent / value["fixture_drafts"])["cases"]:
            if draft.get("delivery_eligible") or draft.get("model_role") != "reference_interface_benchmark_only":
                raise Fault("input", "草案或参考模型不得升级为交付证据")
            validate_model(model_text(p.parent / draft["model"]["path"]), draft["model"])
            validate_protocol(draft["protocol"], draft["model"], draft["contract"])
            validate_measurement_unit(draft)
        rows.append({"device": value["device"], "configured_tests": len(cases),
            "record_count": len(inventory["items"]), "draft_tests": value["draft_tests"],
            "coverage_status_counts": dict(Counter(i["plan_status"] for i in inventory["items"])),
            "review_status": value["review_status"], "model_fitting_ready": False,
            "full_manual_coverage": False})
    return {"status": "input_integration_preflight_passed_with_gaps", "devices": rows,
            "api_calls": 0, "simulations": 0, "full_batch_delivery": False,
            "current_ac_calibration": "required_after_version_merge"}
