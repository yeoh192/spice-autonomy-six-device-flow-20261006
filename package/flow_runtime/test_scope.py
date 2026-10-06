"""Reviewed BUK row/series EXPANSION; never a declaration of complete coverage."""
import copy
from .state import Fault


def expand_buk(inventory):
    value = copy.deepcopy(inventory)
    items = value["items"]
    lookup = {i["id"]: i for i in items}
    if "parameter:igss" not in lookup:
        raise Fault("input", "此修订需要原合并IGSS项目；已拆分清单请勿再次应用")
    replacements = {}
    igss = lookup["parameter:igss"]
    variants = []
    for suffix, voltage, match in (("minus20", -20, "minus20"), ("plus20", 20, "plus20")):
        i = copy.deepcopy(igss)
        i["id"] = "parameter:igss_" + suffix
        i["reference_evidence"]["id"] = "igss_" + suffix
        i["reference_evidence"]["conditions"] = f"FET1 and FET2; VGS={voltage} V; VDS=0 V; Tj=25°C"
        i["bindings"] = [b for b in igss.get("bindings", []) if match in b]
        i["legacy_bindings"] = [b for b in igss.get("legacy_bindings", []) if match in b["test_id"]]
        i["binding_complete"] = bool(igss.get("binding_complete") and i["bindings"])
        i["series"] = {"table": 7, "row": "IGSS", "gate_voltage_V": voltage}
        i["scope_review"] = "row_split_only_not_live_requalified"
        variants.append(i)
    replacements[igss["id"]] = variants
    def row(parent, suffix, key, unit, conditions, series):
        i = copy.deepcopy(parent)
        i.update(id=parent["id"] + ":" + suffix, label=key, bindings=[], binding_complete=False, legacy_bindings=[],
                 series=series, reason="逐系列登记；仍需数字化数据、条件/测量核验与实际执行")
        i["reference_evidence"] = {"key": key, "values": {}, "unit": unit, "conditions": conditions}
        return i
    expansions = {
        "figure:9": [(c, "subthreshold_current", "A", "Tj=25°C; VDS=5 V", {"corner": c}) for c in ("min", "typ", "max")],
        "figure:10": [(c, "threshold_temperature_curve", "V", "ID=1 mA; VDS=VGS", {"corner": c}) for c in ("min", "typ", "max")],
        "figure:8": [("typ", "resistance_gate_curve", "mΩ", "Tj=25°C; ID=5 A", {"corner": "typ"})],
        "figure:11": [("vgs_" + str(v).replace(".", "_"), "resistance_current_curve", "mΩ", f"Tj=25°C; VGS={v} V", {"gate_voltage_V": v}) for v in (5.5, 6, 6.5, 7, 8, 10)],
        "figure:12": [("normalized", "normalized_resistance_temperature", "1", "a=RDSon(Tj)/RDSon(25°C)", {"normalization_temperature_C": 25})],
        "figure:13": [(f"vds_{v}", "gate_charge_curve", "V", f"Tj=25°C; ID=5 A; VDS={v} V", {"drain_voltage_V": v, "x_unit": "nC"}) for v in (14, 48)],
        "figure:15": [(q, "capacitance_bias_curve", "pF", "VGS=0 V; f=1 MHz", {"quantity": q, "temperature_evidence": "not_printed_in_figure_caption"}) for q in ("ciss", "coss", "crss")],
        "figure:16": [(f"tj_{t}", "diode_forward_curve", "A", f"VGS=0 V; Tj={t}°C", {"temperature_C": t}) for t in (25, 175)],
        "figure:5": [("single" if d == 0 else str(d).replace(".", "_"), "thermal_impedance_pulse", "K/W", "single shot" if d == 0 else f"duty={d}", {"duty": d}) for d in (0, .02, .05, .1, .2, .5)]}
    for identifier, series in expansions.items():
        if identifier in lookup:
            replacements[identifier] = [row(lookup[identifier], *spec) for spec in series]
    new_items = [child for i in items for child in replacements.get(i["id"], [i])]
    for identifier, label, maximum, conditions in (
            ("parameter:is", "source_current_rating", 15.4, "Tmb=25°C"),
            ("parameter:ism", "peak_source_current_rating", 71, "pulsed; tp≤10 μs; Tmb=25°C")):
        if identifier not in {i["id"] for i in new_items}:
            new_items.append({"id": identifier, "label": label, "kind": "constraint",
                "evidence": ["PDF Table.5 page.3", "test_library_assets/provenance/buk_manual_evidence.json"],
                "reference_evidence": {"key": label, "values": {"max": maximum}, "unit": "A",
                    "conditions": conditions, "rating_kind": "absolute_maximum"},
                "bindings": [], "binding_complete": False,
                "reason": "额定值属于使用范围/约束审计；普通电气仿真不能证明封装损坏边界"})
    ids = [i["id"] for i in new_items]
    if len(set(ids)) != len(ids):
        raise Fault("input", "BUK逐行/系列修订产生重复ID")
    value["items"] = new_items
    value["review_status"] = "pending_full_manual_review_after_row_and_series_expansion"
    value["test_library_scope_expansion"] = {"source": "BUK7K52-60E PDF Table.5/Table.7 Fig.5/8-13/15-16",
        "old_count": len(items), "new_count": len(new_items), "reviewed_complete": False,
        "note": "本修订登记已核对的行/系列，不取代主线程的全手册复核，不生成缺失参考曲线"}
    return value
