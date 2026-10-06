"""Consume the existing classified SQLite library without executing template text."""
import copy
import json
import sqlite3
from pathlib import Path
from .state import Fault, digest, fingerprint, read, save
from .spice import model_text, validate_model
from .template_tools.search_templates import search


def bundled_assets():
    return Path(__file__).resolve().parents[1] / "template_assets"


def asset_config():
    root = bundled_assets()
    return {"index": str(root / "index/templates.sqlite"),
            "library_root": str(root / "library")}


def settings(value, base):
    allowed = {"index", "library_root", "category", "subcategory", "name", "tag",
               "capability", "confidence", "semantic_ports", "port_aliases",
               "max_candidates", "max_trials", "source_policy"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise Fault("input", "索引检索配置包含未知字段")
    s = {**asset_config(), "max_candidates": 20, "max_trials": 3,
         "source_policy": "all", **copy.deepcopy(value)}
    for key, maximum in (("max_candidates", 100), ("max_trials", 5)):
        if isinstance(s[key], bool) or not isinstance(s[key], int) or not 1 <= s[key] <= maximum:
            raise Fault("input", "索引选择预算越界：" + key)
    if s["source_policy"] not in ("all", "non_vendor"):
        raise Fault("input", "来源策略只能是all或non_vendor")
    for key in ("index", "library_root"):
        p = Path(s[key])
        s[key] = str((p if p.is_absolute() else base / p).resolve())
    if not Path(s["index"]).is_file() or not Path(s["library_root"]).is_dir():
        raise Fault("input", "分类索引或原始模板库不存在；请完整安装含资产的新包")
    ports = s.get("semantic_ports")
    if not isinstance(ports, list) or not ports or len(ports) != len(set(ports)):
        raise Fault("input", "索引选择必须给出唯一semantic_ports")
    if not all(isinstance(p, str) and p for p in ports):
        raise Fault("input", "语义端口必须为非空字符串")
    aliases = s.get("port_aliases", {})
    if not isinstance(aliases, dict) or set(aliases) - set(ports):
        raise Fault("input", "端口别名必须引用已声明语义端口")
    mapping = {}
    for port in ports:
        names = aliases.get(port, [])
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise Fault("input", "端口别名必须为字符串数组")
        for name in [port] + names:
            if name.casefold() in mapping and mapping[name.casefold()] != port:
                raise Fault("input", "声明端口别名对应多个语义端口：" + name)
            mapping[name.casefold()] = port
    return s, mapping


def retrieve(value, base, device):
    """Reuse the existing search implementation and add executable-interface checks."""
    s, mapping = settings(value, base)
    filters = {k: s.get(k) for k in ("category", "subcategory", "tag", "capability", "confidence")}
    filters.update(ports=len(s["semantic_ports"]), limit=s["max_candidates"])
    broad = search(s["index"], **filters)
    exact = search(s["index"], name=s.get("name", device), **filters)
    merged, seen = [], set()
    for item in exact["candidates"] + broad["candidates"]:
        key = (item["sha256"], item["entry"].casefold(), item["kind"])
        if key not in seen:
            seen.add(key)
            merged.append(item)
    pool, rejected = [], []
    root = Path(s["library_root"])
    db = sqlite3.connect(Path(s["index"]).as_uri() + "?mode=ro", uri=True)
    try:
        for item in merged[:s["max_candidates"]]:
            try:
                p = (root / item["path"]).resolve()
                if root not in p.parents or not p.is_file():
                    raise Fault("candidate", "模板路径越界或文件不存在")
                if digest(p) != item["sha256"]:
                    raise Fault("candidate", "索引记录与模板实际哈希不一致")
                if item["kind"] != "subckt":
                    raise Fault("candidate", "当前DUT接口要求SUBCKT；原生MODEL尚缺转换适配器")
                tops = db.execute("select count(*) from entries where path=? and top_level=1 and kind='subckt'", (item["path"],)).fetchone()[0]
                if tops != 1:
                    raise Fault("candidate", "多顶层入口文件尚缺经过验证的提取适配器")
                semantics = [mapping.get(pin.casefold()) for pin in item["ports"]]
                if None in semantics or set(semantics) != set(s["semantic_ports"]):
                    raise Fault("candidate", "无法根据输入别名确认每个声明端口的语义")
                provenance = {"kind": "indexed_source", "source_status": item["source_status"],
                              "evidence": item["source_evidence"], "index_path": item["path"],
                              "note": "来源中立索引保留证据；不据此声称独立作者或电气验收通过"}
                if s["source_policy"] == "non_vendor":
                    # The source-neutral index has no independent-authorship certificate.
                    # Unknown/custom-looking names must not be promoted to non_vendor.
                    raise Fault("candidate", "现有索引未提供独立非厂商作者证明；严格策略不放行")
                candidate = {"id": "indexed_" + fingerprint({"sha": item["sha256"], "entry": item["entry"]})[:16],
                             "path": str(p), "sha256": item["sha256"], "entry": item["entry"],
                             "ports": semantics, "declared_ports": item["ports"],
                             "provenance": provenance, "classification": item["classification"],
                             "index_record": item}
                validate_model(model_text(p), candidate)
                pool.append(candidate)
            except (Fault, ValueError, OSError) as e:
                rejected.append({"path": item["path"], "entry": item["entry"], "reason": str(e)})
    finally:
        db.close()
    receipt = {"settings": s, "index_sha256": digest(s["index"]),
               "matched_distinct_candidates": broad["matched_distinct_candidates"],
               "retrieved": len(merged[:s["max_candidates"]]), "compatible": len(pool),
               "candidates": pool, "rejected": rejected,
               "scope": "有限检索窗口及静态兼容检查；不是全库最优或仿真验收"}
    if not pool:
        raise Fault("template_candidates", "有限检索内没有可执行候选", receipt)
    return s, pool, receipt


def verify_library(root=None):
    assets = Path(root) if root else bundled_assets()
    record = read(assets / "library_manifest.json")
    if digest(assets / "index/templates.sqlite") != record["index_sha256"]:
        raise Fault("cache_corrupt", "打包的分类索引哈希不一致")
    for rel, expected in record["files"].items():
        p = (assets / "library" / rel).resolve()
        if (assets / "library").resolve() not in p.parents or digest(p) != expected:
            raise Fault("cache_corrupt", "打包的原始模板哈希不一致：" + rel)
    return {k: record[k] for k in ("file_count", "top_level_entries", "source_policy", "index_sha256")}


def attach_index(task_path, output, index=None, library_root=None, max_trials=3):
    """Build a NEW indexed task; original task/model/results stay unchanged."""
    source = Path(task_path).resolve()
    task = read(source)
    output = Path(output).resolve()
    if output.exists():
        raise Fault("input", "新索引任务已存在，拒绝覆盖")
    # Resolve input asset paths before moving the task to a new directory.
    from .task import load_task
    task, _ = load_task(source)
    model = task.pop("model")
    ports = model["ports"]
    aliases = dict(zip(ports, ([p] for p in model["declared_ports"])))
    query = {**asset_config(), "semantic_ports": ports, "port_aliases": aliases,
             "max_candidates": 20, "max_trials": max_trials, "source_policy": "all"}
    if set(ports) == {"D", "G", "S"}:
        query.update(category="mosfet", subcategory="n_channel")
        query["port_aliases"] = {"D": ["DRAIN"], "G": ["GATE"], "S": ["SOURCE"]}
    if index:
        query["index"] = str(Path(index).resolve())
    if library_root:
        query["library_root"] = str(Path(library_root).resolve())
    task.pop("candidates", None)
    task.pop("template_retrieval", None)
    task["template_selection"] = query
    task["select_candidate"] = True
    task["policy"]["require_non_vendor"] = False
    task["policy"].update(model_diagnosis_enabled=True, diagnostic_attempts=6)
    # All dispatches still use one total ledger, including candidate baselines.
    task["budgets"]["simulations"] = max(task["budgets"]["simulations"],
        len(task["cases"]) * (max_trials + task["policy"]["optimization_attempts"] + 2) + 24)
    task["budgets"]["repairs"] = max(task["budgets"]["repairs"], 16)
    task["simulation_cache"] = str(output.parent / "simulation_cache")
    task["capability_library"] = str(output.parent / "capabilities")
    save(output, task)
    return output
