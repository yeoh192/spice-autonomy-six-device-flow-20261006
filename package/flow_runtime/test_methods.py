"""GPT-authored declarative recipes. Evidence and live qualification decide reuse."""
import copy
import math
import re
from .state import Fault, finite
from .task import component


UNITS = {"V": 1, "A": 1, "mA": 1e-3, "uA": 1e-6, "µA": 1e-6,
         "μA": 1e-6, "nA": 1e-9, "Hz": 1, "kHz": 1e3, "MHz": 1e6,
         "°C": 1, "degC": 1}
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"


def condition(text, name, dimension):
    # Explicit assignments only; never infer an omitted test condition.
    aliases = {"temperature": r"T(?:j)?", "vds": r"VDS", "vgs": r"VGS",
               "current": r"I(?:D|S)", "frequency": r"f"}
    units = {"temperature": ("°C", "degC"), "voltage": ("V",),
             "current": ("A", "mA", "uA", "µA", "μA", "nA"),
             "frequency": ("MHz", "kHz", "Hz")}[dimension]
    pattern = aliases[name] + r"\s*=\s*(" + NUMBER + r")\s*(" + "|".join(map(re.escape, units)) + r")(?!\w)"
    found = list(re.finditer(pattern, text, re.I))
    if len(found) != 1:
        raise Fault("missing_data", "需要唯一手册条件：" + name)
    m = found[0]
    return float(m[1]) * UNITS[m[2]], m[0]


def base(model, temperature):
    if set(model["ports"]) != {"D", "G", "S"}:
        raise Fault("capability", "本组MOSFET方法需要明确D/G/S端口，不能推测其他接口")
    return {"temperature_C": temperature, "device_nodes": {"D": "D", "G": "G", "S": "0"},
            "components": [], "analysis": {}, "measurement": {}, "checks": []}


def defaults():
    return {"release_s": 2e-6, "bus_ramp_s": 0.5e-6, "load_ramp_s": 1e-6,
            "gate_current_A": 1e-3, "max_step_s": 1e-8, "stop_s": 100e-6,
            "clamp_n": 1.0, "clamp_is": 1e-6, "clamp_rs": 0.01,
            "hold_ron": 0.01, "hold_roff": 1e12}


TUNABLE = {"release_s": (1e-6, 100e-6), "bus_ramp_s": (1e-9, 50e-6),
           "load_ramp_s": (1e-9, 50e-6), "gate_current_A": (1e-5, 1e-2),
           "max_step_s": (1e-10, 1e-7), "stop_s": (20e-6, 1e-3),
           "clamp_n": (0.5, 2), "clamp_is": (1e-12, 1e-3),
           "clamp_rs": (1e-4, 0.1), "hold_ron": (1e-4, 0.1),
           "hold_roff": (1e10, 1e14)}


def adjusted(tuning, changes):
    if not isinstance(changes, dict) or set(changes) - set(TUNABLE):
        raise Fault("proposal", "只能修改已声明的夹具数值，不能修改手册条件、端口或阈值")
    new = {**tuning, **changes}
    for key, value in new.items():
        low, high = TUNABLE[key]
        if isinstance(value, bool) or not low <= finite(value, key) <= high:
            raise Fault("proposal", "夹具调整越界：" + key)
    if max(new["bus_ramp_s"], new["load_ramp_s"]) >= new["release_s"] * 0.7:
        raise Fault("proposal", "电源与负载必须在栅极释放前稳定")
    if new["stop_s"] <= new["release_s"] * 2:
        raise Fault("proposal", "测量窗口不足")
    return new


def build(method, item, model, tuning=None):
    """Return an immutable measurement draft plus exact condition citations."""
    reference = item.get("reference_evidence", {})
    text = reference.get("conditions", "")
    temperature, qt = condition(text, "temperature", "temperature")
    p = base(model, temperature)
    bindings = [{"quote": qt, "protocol_path": "temperature_C"}]
    def bind(name, dimension, path):
        value, quote = condition(text, name, dimension)
        bindings.append({"quote": quote, "protocol_path": path})
        return value
    if method == "gate_charge_total":
        t = adjusted(defaults(), tuning or {})
        release = t["release_s"]
        edge = min(1e-9, t["max_step_s"] / 10)
        start = release + edge
        vds = bind("vds", "voltage", "components@VBUS/value/pwl/1/1")
        load = bind("current", "current", "components@ILOAD/value/pwl/1/1")
        target = bind("vgs", "voltage", "measurement/target/value")
        if vds <= 0 or load <= 0 or not 0 < target <= 12:
            raise Fault("missing_data", "总栅电荷方法需要正向偏置与正目标栅压")
        p["components"] = [
            component("V", "VBUS", "BUS", "0", {"pwl": [[0, 0], [t["bus_ramp_s"], vds], [t["stop_s"], vds]]}),
            component("I", "ILOAD", "0", "D", {"pwl": [[0, 0], [t["load_ramp_s"], load], [t["stop_s"], load]]}),
            component("I", "IGATE", "0", "G", {"pwl": [[0, 0], [start, 0], [start + edge, t["gate_current_A"]], [t["stop_s"], t["gate_current_A"]]]}),
            component("V", "VHOLD", "HOLD", "0", {"pwl": [[0, 1], [release, 1], [start, 0], [t["stop_s"], 0]]}),
            {"kind": "S", "name": "SHOLD", "nodes": ["G", "0", "HOLD", "0"], "model": "HOLD_SW"},
            {"kind": "D", "name": "DCLAMP", "nodes": ["D", "BUS"], "model": "CLAMP_D"},
            {"kind": "D", "name": "DGATE_LIMIT", "nodes": ["0", "G"], "model": "GATE_LIMIT"}]
        p["models"] = {"HOLD_SW": {"type": "SW", "ron": t["hold_ron"], "roff": t["hold_roff"], "vt": 0.5, "vh": 0},
                       "CLAMP_D": {"is": t["clamp_is"], "n": t["clamp_n"], "rs": t["clamp_rs"]},
                       "GATE_LIMIT": {"is": 1e-14, "n": 1, "rs": .01, "cjo": 0, "bv": target * 1.2, "ibv": .001}}
        p["analysis"] = {"kind": "tran", "stop_s": t["stop_s"], "max_step_s": t["max_step_s"]}
        p["measurement"] = {"mode": "integral_to_crossing", "signal": "i(igate)", "sign": 1,
                            "scale": 1e9, "start_s": start,
                            "target": {"signal": "v(g)", "value": target, "direction": "rising"}}
        # Numerical fixture tolerance is explicit and is NOT electrical acceptance.
        p["checks"] = [{"signal": "v(g)", "from": release * .75, "to": release, "min": -.01, "max": .01},
                       {"signal": "v(d)", "from": release * .75, "to": release, "min": vds * .99, "max": vds * 1.01},
                       {"signal": "i(igate)", "from": release * .75, "to": release, "min": -1e-12, "max": 1e-12},
                       {"signal": "i(iload)", "from": release * .75, "to": release, "min": load * .999, "max": load * 1.001},
                       {"signal": "v(g)", "from": start, "to": t["stop_s"], "min": -.01, "max": target * 1.3},
                       {"signal": "i(dclamp)", "from": start, "to": t["stop_s"], "min": -1e-4, "max": load * 1.02}]
        return {"protocol": p, "condition_bindings": bindings, "unresolved_conditions": [],
                "measurement_rationale": "Integrate real gate-source current from verified release to first target VGS crossing; single nominal channel only.",
                "fixture_tuning": t, "coverage_scope": "single_nominal_channel",
                "scope_complete": False,
                "scope_pending": "单通道草案及数值夹具容差需与器件/封装任务范围核对；资格通过不自动完成手册绑定"}
    unit = reference.get("unit") or item.get("test_contract", {}).get("expectation", {}).get("unit")
    scales = {"V": 1, "A": 1, "μA": 1e6, "µA": 1e6, "uA": 1e6,
              "nA": 1e9, "F": 1, "pF": 1e12, "nF": 1e9, "ohm": 1, "Ω": 1, "mΩ": 1000}
    if unit not in scales:
        raise Fault("capability", "尚未支持该测量单位：" + str(unit))
    scale = scales[unit]
    if method in ("drain_leakage", "gate_leakage", "capacitance_point"):
        vd = bind("vds", "voltage", "components@VD/value/dc")
        vg = bind("vgs", "voltage", "components@VG/value/dc")
        p["components"] = [component("V", "VD", "D", "0", {"dc": vd}), component("V", "VG", "G", "0", {"dc": vg})]
        if method == "capacitance_point":
            f = bind("frequency", "frequency", "analysis/frequency_Hz")
            quantity = reference.get("key", item.get("label"))
            if quantity == "input_capacitance":
                p["components"][1]["value"]["ac"] = 1
                signal, sign = "i(vg)", -1
            elif quantity == "output_capacitance":
                p["components"][0]["value"]["ac"] = 1
                signal, sign = "i(vd)", -1
            elif quantity == "reverse_transfer_capacitance":
                p["components"][0]["value"]["ac"] = 1
                signal, sign = "i(vg)", 1
            else:
                raise Fault("missing_data", "缺少Ciss/Coss/Crss测量定义")
            p["analysis"] = {"kind": "ac", "frequency_Hz": f}
            p["measurement"] = {"mode": "capacitance", "signal": signal, "sign": sign, "scale": scale}
        else:
            p["components"].append(component("V", "VDUMMY", "UNUSED", "0", {"dc": 0}))
            p["analysis"] = {"kind": "dc", "source": "VDUMMY", "start": 0, "stop": 1, "step": 1}
            signal = "i(vd)" if method == "drain_leakage" else "i(vg)"
            p["measurement"] = {"mode": "sample", "signal": signal, "at": 0, "sign": -1, "scale": scale, "absolute": True}
    elif method in ("threshold_point", "breakdown_point", "resistance_point", "diode_forward_point"):
        current = bind("current", "current", "measurement/at")
        if current <= 0:
            raise Fault("missing_data", "测试电流必须为正")
        orientation = ("D", "0") if method == "diode_forward_point" else ("0", "D")
        p["components"] = [component("I", "IFORCE", *orientation, {"dc": 0})]
        if method == "threshold_point":
            if "VDS=VGS" not in text.replace(" ", ""):
                raise Fault("missing_data", "阈值测试必须明确VDS=VGS")
            p["device_nodes"]["G"] = "D"
        else:
            gate = bind("vgs", "voltage", "components@VG/value/dc")
            p["components"].append(component("V", "VG", "G", "0", {"dc": gate}))
        p["analysis"] = {"kind": "dc", "source": "IFORCE", "start": 0, "stop": current, "step": current / 10}
        p["measurement"] = {"mode": "sample", "signal": "v(d)", "at": current,
                            "sign": -1 if method == "diode_forward_point" else 1,
                            "scale": scale / current if method == "resistance_point" else scale}
    elif method in ("subthreshold_curve", "diode_forward_curve", "resistance_gate_curve", "resistance_current_curve"):
        contract = item.get("test_contract", {})
        ref = contract.get("reference")
        if not ref:
            raise Fault("missing_data", "曲线方法需要独立数字化CSV、单位、系列条件和哈希")
        from .spice import load_reference
        points = load_reference(ref["path"], unit, ref.get("condition"))
        first, last = points[0][0], points[-1][0]
        if first == last:
            raise Fault("missing_data", "曲线横坐标范围不足")
        if method == "subthreshold_curve":
            corner = item.get("series", {}).get("corner", "typ")
            if corner != "typ" and model.get("process_corner") != corner:
                raise Fault("capability", "最小/最大系列需要对应工艺角模型，不能用典型模型冒充")
            vd = bind("vds", "voltage", "components@VD/value/dc")
            p["components"] = [component("V", "VD", "D", "0", {"dc": vd}), component("V", "VG", "G", "0", {"dc": 0})]
            source, signal, sign = "VG", "i(vd)", -1
        elif method == "diode_forward_curve":
            vg = bind("vgs", "voltage", "components@VG/value/dc")
            p["components"] = [component("V", "VSD", "0", "D", {"dc": 0}), component("V", "VG", "G", "0", {"dc": vg})]
            source, signal, sign = "VSD", "i(vsd)", -1
        elif method == "resistance_gate_curve":
            current = bind("current", "current", "components@IFORCE/value/dc")
            p["components"] = [component("I", "IFORCE", "0", "D", {"dc": current}), component("V", "VG", "G", "0", {"dc": 0})]
            source, signal, sign = "VG", "v(d)", 1
        else:
            vg = bind("vgs", "voltage", "components@VG/value/dc")
            p["components"] = [component("I", "IFORCE", "0", "D", {"dc": 0}), component("V", "VG", "G", "0", {"dc": vg})]
            source, signal, sign = "IFORCE", "v(d)", 1
        p["analysis"] = {"kind": "dc", "source": source, "start": first, "stop": last, "step": (last - first) / 1000}
        p["measurement"] = {"mode": "ratio_curve" if method.startswith("resistance_") else "curve", "signal": signal, "sign": sign, "scale": scale}
        if method.startswith("resistance_"):
            p["measurement"]["denominator"] = "i(iforce)"
    else:
        raise Fault("capability", "该缺项方法尚未有完整测量适配器：" + method)
    return {"protocol": p, "condition_bindings": bindings, "unresolved_conditions": [],
            "measurement_rationale": "Fixed handbooks conditions; measure actual terminal voltage/current, never a model parameter value.",
            "fixture_tuning": {}, "coverage_scope": "single_nominal_channel",
            "scope_complete": "FET1 and FET2" not in text}


def gate_charge_oracles():
    """Release-switch circuit with known C*V; no oracle comes from an LLM."""
    answer = []
    for capacitance in (0.5e-9, 1e-9, 2e-9):
        end = 40e-6
        p = {"temperature_C": 25, "device_nodes": {},
             "components": [component("C", "CCAL", "P", "0", capacitance),
                component("I", "ICAL", "0", "P", {"pwl": [[0, 0], [2e-6, 0], [2.001e-6, .001], [end, .001]]}),
                component("V", "VHOLD", "HOLD", "0", {"pwl": [[0, 1], [1.999e-6, 1], [2e-6, 0], [end, 0]]}),
                {"kind": "S", "name": "SHOLD", "nodes": ["P", "0", "HOLD", "0"], "model": "HOLD_SW"}],
             "models": {"HOLD_SW": {"type": "SW", "ron": .01, "roff": 1e12, "vt": .5, "vh": 0}},
             "analysis": {"kind": "tran", "stop_s": end, "max_step_s": 1e-8},
             "measurement": {"mode": "integral_to_crossing", "signal": "i(ical)", "start_s": 2e-6, "sign": 1,
                             "target": {"signal": "v(p)", "value": 10, "direction": "rising"}},
             "checks": [{"signal": "v(p)", "from": 1e-6, "to": 1.999e-6, "min": -.001, "max": .001}]}
        answer.append((p, capacitance * 10))
    return answer
