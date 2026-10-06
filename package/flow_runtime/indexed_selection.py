"""Finite indexed candidates are ranked by agents, then compared by real baselines."""
import copy
import shutil
from pathlib import Path
from .state import Fault, digest, read, save
from .spice import model_text, validate_protocol


def choose(workflow):
    w = workflow
    s = w.task["template_selection"]
    pool = w.task["candidates"]
    folder = w.store.folder / "template_selection"
    save(folder / "retrieval.json", w.task["template_retrieval"])
    saved = w.store.get("plans", "indexed_ranking") or {}
    ranking = saved.get("ranking")
    feedback = saved.get("feedback")
    by_id = {c["id"]: c for c in pool}
    if not ranking:
        for n in range(saved.get("attempt", 0), w.policy["selection_attempts"]):
            if n:
                w.store.reserve("repairs", "indexed-ranking:" + str(n))
            summaries = [{k: c[k] for k in ("id", "entry", "ports", "declared_ports", "provenance", "classification")} for c in pool]
            proposal = w.agents.ask("template_selector", {
                "task": "Rank a finite shortlist. Return {ranked_ids:[candidate IDs],reason,evidence_ids}. No more than max_trials; no source-based accuracy assumptions. Real identical baselines select the starting model.",
                "device": w.task["device"], "candidates": summaries, "max_trials": s["max_trials"],
                "manual": w.inventory, "feedback": feedback})
            try:
                ids = proposal["ranked_ids"]
                if (not isinstance(ids, list) or not 1 <= len(ids) <= s["max_trials"] or
                    not all(isinstance(i, str) and i in by_id for i in ids) or len(ids) != len(set(ids))):
                    raise Fault("proposal", "ranked_ids未知、重复或超过候选试跑预算")
                review = w.agents.ask("template_reviewer", {
                    "task": "Review shortlist, declared pins, evidence and actual self-contained model text. Return decision approve|revise and issues. Approval is not electrical acceptance.",
                    "proposal": proposal, "manual": w.inventory,
                    "candidates": [{**by_id[i], "model_excerpt": model_text(by_id[i]["path"])[:12000]} for i in ids]})
                if review.get("decision") != "approve":
                    raise Fault("review", "索引候选审查要求修订", review)
                ranking = ids
                w.store.put("plans", "indexed_ranking", {"ranking": ranking, "proposal": proposal, "review": review})
                break
            except (KeyError, TypeError) as e:
                feedback = Fault("proposal", "选择提案接口错误：" + str(e)).record()
            except Fault as e:
                if e.kind not in ("proposal", "review"):
                    raise
                feedback = e.record()
            w.store.put("plans", "indexed_ranking", {"attempt": n+1, "feedback": feedback})
            w.store.event("template_selection", "revision_required", feedback)
        if not ranking:
            raise Fault("template_selection_budget", "候选选择预算内未通过", feedback)
    if not w.cases:
        raise Fault("template_selection", "候选比较需要至少一项有参考或限制的已配置测试")
    original = copy.deepcopy(w.task["model"])
    trials = []
    winner = None
    for candidate_id in ranking:
        candidate = by_id[candidate_id]
        w.task["model"] = copy.deepcopy(candidate)
        w.simulator.model = w.task["model"]
        for case in w.cases:
            validate_protocol(case["protocol"], candidate, case.get("contract"))
        # Evaluating cached RAW again verifies it; saved summary alone is not trusted.
        results = w.evaluate_all(Path(candidate["path"]), "candidate_" + candidate_id)
        # A usable starting model may have fitting/specification errors. Those
        # remain visible to optimization and final audit; they are not approval.
        eligible = all(r["execution"] == "completed" and not r.get("model_conflict") for r in results)
        values = [r["metrics"].get("MAE_over_reference_span_percent") for r in results if "metrics" in r]
        values = [v for v in values if v is not None]
        if values:
            score = sum(values) / len(values)
            metric = "mean_curve_MAE_over_reference_span_percent"
        else:
            from .workflow import result_cost
            score = sum(result_cost(c, r) for c, r in zip(w.cases, results)) / len(results)
            metric = "declared_scalar_reference_cost"
        trial = {"candidate_id": candidate_id, "model_sha256": candidate["sha256"],
                 "eligible": eligible, "eligibility_scope": "executable_starting_point_only",
                 "electrical_acceptance": "pass" if all(r["acceptance"] == "pass" for r in results) else "fail" if any(r["acceptance"] == "fail" for r in results) else "pending",
                 "score": score, "score_metric": metric, "results": results}
        trials.append(trial)
        save(folder / "trial_results.json", trials)
        if eligible and (winner is None or score < winner[0]):
            winner = (score, candidate, results)
    if winner is None:
        w.task["model"] = original
        w.simulator.model = original
        raise Fault("template_baselines", "所有有限候选均有执行失败或模型响应冲突；没有可执行拟合起点", {"trials": trials})
    _, candidate, results = winner
    w.task["model"] = copy.deepcopy(candidate)
    w.simulator.model = w.task["model"]
    w.model_path = w.store.folder / "models" / ("selected_" + candidate["sha256"] + ".lib")
    # Preserve exact bytes so non-UTF8 comments do not invalidate the winning RAW.
    shutil.copyfile(candidate["path"], w.model_path)
    w.results, w.selected = results, True
    selected = {"candidate": candidate, "score": winner[0], "results": results,
                "scope": "有限检索和有限基线的最优起点，不代表全库最优或完成拟合"}
    save(folder / "selected.json", selected)
    w.checkpoint()
    w.store.event("template_selection", "completed", {"candidate": candidate["id"], "trials": len(trials), "score": winner[0]})
