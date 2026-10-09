"""Generalize the linked diagnostic experiments, topology checks and audited stopping."""
import copy
import difflib
import re
from pathlib import Path
from .state import Fault, BudgetEnd, digest, fingerprint, read, save
from .spice import model_text, raw_data, render


def terminals(line):
    parts = line.split()
    if not parts or line.lstrip().startswith(("*", ".", "+")):
        return []
    counts = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "B": 2,
              "D": 2, "M": 4, "Q": 3, "J": 3, "E": 2, "G": 2, "F": 2, "H": 2}
    n = counts.get(parts[0][0].upper(), 0)
    return parts[1:1+n]


def capabilities(text, model):
    nodes, connected, sources = set(model["declared_ports"]), set(), []
    for line in text.splitlines():
        nodes.update(terminals(line))
        connected.update(n.casefold() for n in terminals(line))
        match = re.fullmatch(r"\s*(B[A-Za-z0-9_.-]+)\s+(\S+)\s+(\S+)\s+V\s*=\s*(.+)", line, re.I)
        if match:
            sources.append({"name": match[1], "old": line, "terminals": [match[2], match[3]],
                            "expression": match[4],
                            "voltage_controls": re.findall(r"V\(\s*([\w.-]+)\s*(?:,\s*([\w.-]+)\s*)?\)", match[4], re.I)})
    from .repair_adapters import catalog
    adapters = catalog(text)
    adapters["behavioral_voltage"] = sources
    return {"edit_adapters": adapters, "available_adapters": [k for k, v in adapters.items() if v],
            "editable_sources": sources, "existing_nodes": sorted(nodes),
            "sensed_only_ports": [p for p in model["declared_ports"] if p.casefold() not in connected],
            "allowed_actions": ["diagnose", "experiment", "patch", "stop"],
            "allowed_edits": "Choose a listed adapter: bounded numeric parameters, existing B voltage expressions, or same-subcircuit existing-component terminal rewiring. No new elements, test conditions or thresholds.",
            "existing_parameters": [p for line in re.findall(r"(?im)^\s*\.param\s+([^\r\n]+)", text)
                                    for p in re.findall(r"([A-Za-z_][\w.]*)\s*=", line)],
            "note": "Voltage sensing V(node) is not a terminal current path. Static checks are hints; real probes and full regression decide retention."}


def triggers_for(cases, results):
    from .workflow import result_cost
    by_id = {r["test"]: r for r in results}
    required = [c["id"] for c in cases if by_id.get(c["id"], {}).get("acceptance") == "fail" or
                (c["expectation"]["unit"] == "F" and c["expectation"].get("typical", 0) and by_id.get(c["id"], {}).get("value", 1) <= 0)]
    residuals = sorted([(result_cost(c, by_id[c["id"]]), c["id"]) for c in cases if c["id"] in by_id], reverse=True)
    # All explicit failures, plus up to two largest measurable residuals.
    for cost, case_id in residuals[:2]:
        if cost > 1e-6 and case_id not in required:
            required.append(case_id)
    return required


def evidence(cases, results, model):
    records = []
    rows = {r["test"]: r for r in results}
    for case in cases:
        r = rows.get(case["id"], {})
        entry = {"test": case["id"], "result": {k: v for k, v in r.items() if k != "comparison"},
                 "actual_test_circuit": render(case["protocol"], model)}
        folder = r.get("artifacts") or r.get("fault", {}).get("evidence", {}).get("folder")
        if folder:
            folder = Path(folder)
            if (folder / "test.cir").exists():
                entry["declared_test_circuit"] = entry["actual_test_circuit"]
                entry["actual_test_circuit"] = (folder / "test.cir").read_text(encoding="utf-8")
            log = folder / "test.log"
            if log.exists():
                raw = log.read_bytes()
                encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
                entry["log_tail"] = raw.decode(encoding, errors="replace")[-2500:]
            if (folder / "execution.json").exists():
                entry["execution_diagnostics"] = read(folder / "execution.json")
            if case["protocol"]["measurement"]["mode"] == "capacitance" and (folder / "test.raw").exists():
                try:
                    data = raw_data(folder / "test.raw")
                    signal = case["protocol"]["measurement"]["signal"].lower()
                    current = data["signals"][signal][0]
                    entry["measured_port_current"] = {"signal": signal,
                        "real_A": current.real, "imag_A": current.imag, "frequency_Hz": data["axis"][0]}
                except (Fault, KeyError, OSError, IndexError):
                    entry["port_current_status"] = "没有完整可核验的端口交流电流"
        records.append(entry)
    return records


def topology_precheck(original, candidate, model, cases, results):
    before, after = capabilities(original, model), capabilities(candidate, model)
    old = {s["name"].casefold(): s for s in before["editable_sources"]}
    changed = [s for s in after["editable_sources"] if s["name"].casefold() in old and old[s["name"].casefold()]["old"] != s["old"]]
    connected = {n.casefold() for line in candidate.splitlines() for n in terminals(line)}
    rows = {r["test"]: r for r in results}
    for case in cases:
        protocol = case["protocol"]
        mapping = {decl.casefold(): protocol["device_nodes"][sem].casefold()
                   for sem, decl in zip(model["ports"], model["declared_ports"])}
        driven = {frozenset(n.casefold() for n in c["nodes"])
                  for c in protocol["components"] if c["kind"] == "V"}
        for source in changed:
            mapped = [mapping.get(n.casefold(), "0" if n == "0" else None) for n in source["terminals"]]
            if all(n is not None for n in mapped) and frozenset(mapped) in driven:
                raise Fault("topology", source["name"] + ":修改后的电压源与实际测试电压源并联")
        result = rows.get(case["id"], {})
        if (protocol["measurement"]["mode"] != "capacitance" or result.get("value", 1) > 0):
            continue
        measured = re.fullmatch(r"i\(([^)]+)\)", protocol["measurement"]["signal"], re.I)
        if not measured:
            continue
        drive = next((c for c in protocol["components"] if c["name"].casefold() == measured[1].casefold()), None)
        if not drive:
            continue
        for declared in before["sensed_only_ports"]:
            if mapping[declared.casefold()] in {n.casefold() for n in drive["nodes"]} and declared.casefold() not in connected:
                raise Fault("topology", declared + ":电容冲突的端口仍只有电压感测，没有恢复实际元件端口连接")


def validate_action(workflow, source, proposal, triggers):
    known = {c["id"] for c in workflow.cases}
    if not isinstance(proposal, dict) or proposal.get("action") not in ("diagnose", "experiment", "patch", "stop"):
        raise Fault("proposal", "诊断提案必须指定action=diagnose|experiment|patch|stop")
    refs = proposal.get("evidence_tests")
    if (not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs) or
            not set(refs) <= known or not set(refs).intersection(triggers)):
        raise Fault("proposal", "所有证据须为真实测试ID，且至少包含一个实际冲突/残差项")
    if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
        raise Fault("proposal", "诊断动作缺少理由和证据解释")
    if proposal["action"] in ("diagnose", "stop"):
        if proposal.get("edits") != []:
            raise Fault("proposal", "diagnose/stop的edits必须为空")
        return None
    caps = capabilities(source, workflow.task["model"])
    adapter = proposal.get("adapter")
    if adapter is None:
        edits = proposal.get("edits", [])
        adapter = "behavioral_voltage" if edits and all(e.get("old") in {s["old"] for s in caps["editable_sources"]} for e in edits) else "parameter"
    if adapter not in caps["available_adapters"]:
        raise Fault("capability", "所选诊断修改接口不可用：" + str(adapter))
    if adapter in ("parameter", "existing_node_rewire"):
        from .repair_adapters import validate_parameter, validate_rewire
        kind = "parameter" if adapter == "parameter" else "structure"
        candidate = workflow.apply_patch(source, {"kind": kind, "edits": proposal.get("edits")})
        if kind == "parameter":
            validate_parameter(source, candidate, workflow.policy.get("max_diagnostic_parameter_step_percent", 25))
        else:
            if not workflow.policy["allow_structure_edit"]:
                raise Fault("capability", "任务未开启结构修改")
            validate_rewire(source, candidate)
        topology_precheck(source, candidate, workflow.task["model"], workflow.cases, workflow.results)
        return candidate
    allowed = {s["old"]: s for s in caps["editable_sources"]}
    for edit in proposal.get("edits", []):
        if edit.get("old") not in allowed or "\n" in edit.get("new", "") or "\r" in edit.get("new", ""):
            raise Fault("proposal", "诊断试验只能修改列出的完整B电压源行")
        old = allowed[edit["old"]]
        temporary = capabilities(edit["new"], workflow.task["model"])["editable_sources"]
        if len(temporary) != 1 or temporary[0]["name"].casefold() != old["name"].casefold():
            raise Fault("proposal", "B源名称或V=表达式形式改变")
        new = temporary[0]
        if not {n.casefold() for n in new["terminals"]} <= {n.casefold() for n in caps["existing_nodes"]}:
            raise Fault("proposal", "诊断试验不允许引入新节点")
        expression = new["expression"]
        stripped = re.sub(r"V\(\s*([\w.-]+)\s*(?:,\s*([\w.-]+)\s*)?\)", "0", expression, flags=re.I)
        for pair in new["voltage_controls"]:
            if not {n.casefold() for n in pair if n} <= {n.casefold() for n in caps["existing_nodes"]}:
                raise Fault("proposal", "表达式引用了不存在的节点")
        stripped = re.sub(r"(?<![\w.])[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?", "0", stripped)
        tokens = re.findall(r"[A-Za-z_][\w.]*", stripped)
        if not {t.casefold() for t in tokens} <= {p.casefold() for p in caps["existing_parameters"]} | {"temp"}:
            raise Fault("proposal", "表达式仅允许已有参数、temp及基本算术")
        cleaned = re.sub(r"[A-Za-z_][\w.]*", "0", stripped)
        if re.search(r"[^0-9+*/().\-\s]", cleaned):
            raise Fault("proposal", "诊断表达式含未允许语法")
    normalized = {"kind": "structure", "edits": proposal.get("edits")}
    candidate = workflow.apply_patch(source, normalized)
    topology_precheck(source, candidate, workflow.task["model"], workflow.cases, workflow.results)
    return candidate


def audit_stop(history, baseline_sha):
    good = [h for h in history if h.get("status") == "experiment_completed" and h.get("baseline_sha256") == baseline_sha
            and h.get("result_review", {}).get("decision", "approve") == "approve" and h.get("results") and all(r["execution"] == "completed" for r in h["results"])]
    if any(h.get("improved") for h in good):
        return "已有试验改善冲突项；须提交patch进行完整回归，不能直接停止"
    if len({h["model_sha256"] for h in good}) < 2:
        return "停止依据不足：尚未完成两种不同候选的有效对照；失败试验不计数"
    return None


def repair(workflow):
    w = workflow
    if not w.cases or any(r["execution"] != "completed" for r in w.results):
        w.store.event("model_diagnosis", "deferred_execution_failure")
        return
    triggers = triggers_for(w.cases, w.results)
    if not triggers:
        w.store.event("model_diagnosis", "no_model_conflict")
        return
    state = w.store.get("checkpoints", "model_diagnostics") or {"index": 0, "history": []}
    if state.get("terminal"):
        return
    history = state["history"]
    for n in range(state["index"], w.policy["diagnostic_attempts"]):
        source = model_text(w.model_path)
        baseline_sha = digest(w.model_path)
        triggers = triggers_for(w.cases, w.results)
        if not triggers:
            break
        caps = capabilities(source, w.task["model"])
        if not caps["available_adapters"]:
            w.gaps.append({"stage": "model_diagnosis", "kind": "unsupported_edit_adapter",
                           "reason": "实际模型没有已验证的参数、B电压源或已有节点重接接口；未支持任意新增代码"})
            w.store.event("model_diagnosis", "capability_gap")
            w.store.put("checkpoints", "model_diagnostics", {"index": n, "history": history, "terminal": True})
            break
        key = "diagnostic-round:" + str(n)
        saved = w.store.get("plans", key) or {}
        related = set(triggers) | set(saved.get("proposal", {}).get("evidence_tests", []))
        probe_cases = [c for c in w.cases if c["id"] in related]
        baseline_evidence = evidence(probe_cases, w.results, w.task["model"])
        from .evidence import bundle, compact_history
        shared = bundle(w)
        context = saved.get("context") or {
            "task": "For diagnose/stop edits MUST be []; to change a parameter choose experiment or patch. Diagnose recorded model deviations; choose diagnose|experiment|patch|stop. experiment runs only evidence tests and never changes the active model. patch requires full regression before retention. Cite actual test IDs. No source replacement or changed acceptance.",
            "schema": {"action": "diagnose|experiment|patch|stop", "reason": "hypothesis, facts and proposed check",
                       "evidence_tests": "real tests; must include at least one trigger", "adapter": "parameter|behavioral_voltage|existing_node_rewire",
                       "edits": [{"old": "unique text from capabilities", "new": "small validated replacement"}]},
            "shared_evidence": shared, "capabilities": caps, "triggers": triggers,
            "parameter_step_limit_percent": w.policy.get("max_diagnostic_parameter_step_percent", 25),
            "adapter_rules": "Choose only an available adapter: parameter changes listed numeric values; behavioral_voltage changes existing B expressions; existing_node_rewire changes only existing component terminals within the same subcircuit. No new arbitrary elements or code.",
            "allowed_test_ids": [c["id"] for c in w.cases],
            "history": compact_history(history[-6:], w.cases), "remaining_budget": {k: w.store.data["limits"][k]-w.store.data["usage"][k] for k in ("api_calls", "simulations", "repairs")}}
        w.store.put("plans", key, {**saved, "context": context})
        w.store.reserve("repairs", key + ":" + baseline_sha)
        record = {"round": n+1, "baseline_sha256": baseline_sha}
        try:
            proposal = saved.get("proposal") or w.agents.ask("model_diagnoser", context)
            w.store.put("plans", key, {**saved, "context": context, "proposal": proposal})
            record["proposal"] = proposal
            candidate = validate_action(w, source, proposal, triggers)
            action = proposal["action"]
            if action == "diagnose":
                record.update(status="diagnostic_completed", diagnostic_evidence=baseline_evidence)
            elif action == "stop":
                reason = audit_stop(history, baseline_sha)
                if reason:
                    record.update(status="stop_rejected", reason=reason)
                else:
                    review = w.agents.ask("diagnosis_reviewer", {"task": "Review stopping against actual distinct completed experiments and residuals. approve|revise; failed experiments are not no-improvement evidence.",
                        "proposal": proposal, "history": compact_history(history, w.cases), "baseline_evidence": baseline_evidence, "shared_evidence": shared})
                    record.update(status="stop_audited" if review.get("decision") == "approve" else "stop_rejected", review=review)
                    if record["status"] == "stop_audited":
                        history.append(record)
                        w.gaps.append({"stage": "model_diagnosis", "kind": "audited_no_improvement", "record": record})
                        w.store.put("checkpoints", "model_diagnostics", {"index": n+1, "history": history, "terminal": True})
                        save(w.store.folder / "model_diagnostics/history.json", history)
                        w.store.event("model_diagnosis", "stop_audited")
                        break
            else:
                sha = fingerprint(candidate)
                record["candidate_key"] = sha
                if any(h.get("candidate_key") == sha and h.get("proposal", {}).get("action") == action for h in history):
                    raise Fault("proposal", "同动作重复候选；experiment可以提升为patch，但不能重复同一试验")
                diff = "".join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile="active.lib", tofile="diagnostic.lib"))
                review = w.agents.ask("patch_reviewer", {"task": "Authorize a bounded experiment BEFORE execution: check actual diff, adapter, frozen conditions and hypothesis. All supplied measurements are BASELINE, not candidate results. Do not demand post-change waveforms or guaranteed improvement at this phase. approve means permission to simulate only; revise for a concrete invalid edit or test plan.",
                    "planned_test_ids": [c["id"] for c in w.cases if action=="patch" or c["id"] in set(proposal["evidence_tests"])|set(triggers)], "proposal": proposal, "actual_diff": diff, "phase": "pre_execution", "candidate_executed": False, "baseline_evidence": baseline_evidence, "capabilities": caps, "shared_evidence": shared})
                if review.get("decision") != "approve":
                    raise Fault("review", "诊断补丁审查要求修订", review)
                folder = w.store.folder / "model_diagnostics" / ("round_%02d" % (n+1))
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / "candidate.lib"
                path.write_text(candidate, encoding="utf-8")
                record["model_sha256"] = digest(path)
                (folder / "changes.diff").write_text(diff, encoding="utf-8")
                selected = [c for c in w.cases if c["id"] in set(proposal["evidence_tests"]) | set(triggers)]
                if action == "experiment":
                    after = [w.evaluate_one(c, path, allow_recovery=False) for c in selected]
                    record["results"] = after
                    record["candidate_diagnostics"] = evidence(selected, after, w.task["model"])
                    if any(r["execution"] != "completed" for r in after):
                        record["status"] = "experiment_failed"
                    else:
                        from .workflow import retention
                        before = [r for r in w.results if r["test"] in {c["id"] for c in selected}]
                        improved, reasons, gain = retention(selected, before, after, w.policy)
                        result_review = review_results(w, path, selected, after, proposal)
                        record['result_review'] = result_review
                        record.update(status="experiment_completed", improved=improved and result_review.get('decision')=='approve', reasons=reasons, gain=gain,
                                      instruction="试验候选不交付；有改善须提交同补丁patch完整回归")
                else:
                    after = w.evaluate_all(path, "diagnostic_regression_%02d" % (n+1))
                    from .workflow import retention
                    keep, reasons, gain = retention(w.cases, w.results, after, w.policy)
                    result_review = review_results(w, path, w.cases, after, proposal)
                    record['result_review'] = result_review
                    keep = keep and result_review.get('decision')=='approve'
                    record.update(status="repair_retained" if keep else "regression_failed" if any(r["execution"] != "completed" for r in after) else "repair_rolled_back",
                                  results=after, reasons=reasons, gain=gain,
                                  candidate_diagnostics=evidence(w.cases, after, w.task["model"]))
                    if keep:
                        w.model_path, w.results = path, after
                        w.checkpoint()
                save(folder / "feedback.json", record)
        except BudgetEnd:
            raise
        except Fault as e:
            if e.kind in ("authentication", "credentials", "api_configuration", "cache_corrupt"):
                raise
            record.update(status="proposal_or_evaluation_error", fault=e.record(), allowed_scope=caps)
        except (KeyError, ValueError, TypeError) as e:
            record.update(status="proposal_or_evaluation_error", fault=Fault("proposal", str(e)).record(), allowed_scope=caps)
        history.append(record)
        w.store.put("checkpoints", "model_diagnostics", {"index": n+1, "history": history})
        save(w.store.folder / "model_diagnostics/history.json", history)
        w.store.event("model_diagnosis", record["status"], {"round": n+1})
        w.checkpoint()
    save(w.store.folder / "model_diagnostics/summary.json", {"history": history, "active_model_sha256": digest(w.model_path),
         "remaining_triggers": triggers_for(w.cases, w.results), "scope": "按实际模板能力选择受限参数、B表达式或已有节点重接；保留必须经过完整回归"})
    w.checkpoint()


def review_results(w, path, cases, results, proposal):
    """Only candidate-hash measurements can support a post-execution review."""
    if any(r.get('execution')!='completed' for r in results):
        return {'decision':'revise','reason':'candidate_execution_incomplete'}
    if {r['test'] for r in results}!={c['id'] for c in cases}:
        raise Fault('cache_corrupt','Candidate result test set differs from executed plan')
    if any(r.get('model_sha256')!=digest(path) for r in results):
        raise Fault('cache_corrupt','Candidate results carry a different model hash')
    from .evidence import bundle, residual
    baseline={r["test"]:r for r in w.results}
    return w.agents.ask('patch_reviewer',{'phase':'post_execution','candidate_executed':True,
        'task':'Review actual candidate measurements against frozen targets and baseline. approve|revise. Program retention additionally requires full regression; experiment approval is not delivery.',
        'proposal':proposal,'candidate_sha256':digest(path),
        'baseline_summary':[{'test':c['id'],'residual':residual(baseline.get(c['id'],{}),c['expectation'])} for c in cases],
        'candidate_evidence':bundle(w,cases,results,path)})
