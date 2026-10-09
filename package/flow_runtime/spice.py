"""Declarative circuits and measurements, independent of device part numbers."""
import bisect
import csv
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from . import ac_measurements, pulse_metrology
from .state import (Fault, artifact_hashes, artifacts_valid, canonical, digest,
                    file_lock, finite, fingerprint, read, save)

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
NODE = re.compile(r"^(?:0|[A-Za-z_][A-Za-z0-9_.-]*)$")
SIGNAL = re.compile(r"^(?:v\([A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)?\)|i\([A-Za-z0-9_.-]+\))$", re.I)


def model_text(path):
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin1")


def validate_model(text, model):
    if len(text) > 2_000_000 or re.search(r"(?im)^\s*\.(?:include|inc|lib|control|exec|shell|file|load|savebias)\b", text):
        raise Fault("input", "模型必须自包含，不能有外部文件或执行指令")
    if re.search(r"(?i)\b(?:file|wavefile|read)\s*[(=]", text):
        raise Fault("input", "模型不能引用外部波形或数据文件")
    directives = re.findall(r"(?im)^\s*\.(\w+)\b", text)
    if set(d.lower() for d in directives) - {"subckt", "ends", "model", "param", "func", "global"}:
        raise Fault("proposal", "模型文件不能覆写测试分析、输出或求解设置")
    entry = model["entry"]
    if not NAME.fullmatch(entry):
        raise Fault("input", "子电路名非法")
    matches = re.findall(r"(?im)^\s*\.subckt\s+" + re.escape(entry) + r"\s+([^\r\n]+)", text)
    if len(matches) != 1:
        raise Fault("input", "必须有唯一模型入口")
    pins = re.split(r"(?i)\s+params:", matches[0])[0].split()
    if pins != model["declared_ports"] or len(set(pins)) != len(pins):
        raise Fault("input", "模型端口与任务包不一致")
    if len(model["ports"]) != len(pins) or len(set(model["ports"])) != len(pins):
        raise Fault("input", "语义端口与声明端口不是一一对应")
    if any(not NAME.fullmatch(p) for p in model["ports"] + pins):
        raise Fault("input", "模型端口名非法")


def _signal(value):
    if not isinstance(value, str) or not SIGNAL.fullmatch(value):
        raise Fault("proposal", "测量变量必须是v(node)或i(element)")
    return value.lower()


def signal_components(name):
    """LTspice saves two node traces for .save V(a,b), not a named difference."""
    match = re.fullmatch(r"v\(([^,]+),([^,]+)\)", name.lower())
    return {"v("+node+")" for node in match.groups() if node != "0"} if match else {name.lower()}


def measurement_signals(data, protocol):
    signals = dict(data["signals"])
    m = protocol["measurement"]
    wanted = {m["signal"].lower()}
    if "denominator" in m: wanted.add(m["denominator"].lower())
    if "target" in m: wanted.add(m["target"]["signal"].lower())
    wanted.update(s.lower() for s in pulse_metrology.extra_signals(m))
    wanted.update(c["signal"].lower() for c in protocol.get("checks", []))
    for name in wanted:
        match = re.fullmatch(r"v\(([^,]+),([^,]+)\)", name)
        if match and name not in signals:
            vectors = []
            for node in match.groups():
                if node == "0": vectors.append([0j]*len(data["axis"]))
                elif "v("+node+")" in signals: vectors.append(signals["v("+node+")"])
                else: raise Fault("parser", "差分测量缺少节点："+node)
            if any(len(v) != len(data["axis"]) for v in vectors):
                raise Fault("parser", "差分测量样本数不完整")
            signals[name] = [a-b for a,b in zip(*vectors)]
    return signals


def path_get(value, path):
    for part in path.split("/"):
        if "@" in part:
            field, name = part.split("@", 1)
            matches = [c for c in value[field] if c["name"] == name]
            if len(matches) != 1:
                raise KeyError("需要唯一命名元件：" + name)
            value = matches[0]
        else:
            value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def protocol_shapes(protocol):
    """Reject untrusted JSON container types before accessing their members."""
    def expect(value, cls, path):
        if not isinstance(value, cls):
            raise Fault("proposal", path + "需要" + cls.__name__ + "，实际为" + type(value).__name__)
        return value
    expect(protocol, dict, "protocol")
    for field in ("device_nodes", "analysis", "measurement"):
        expect(protocol.get(field), dict, "protocol." + field)
    for port, node in protocol["device_nodes"].items():
        expect(port, str, "device_nodes.key")
        expect(node, str, "device_nodes." + port)
    components = expect(protocol.get("components"), list, "protocol.components")
    for index, component in enumerate(components):
        path = "components[" + str(index) + "]"
        expect(component, dict, path)
        for field in ("name", "kind"):
            expect(component.get(field), str, path + "." + field)
        for node in expect(component.get("nodes"), list, path + ".nodes"):
            expect(node, str, path + ".nodes[]")
        if component["kind"] in ("D", "S"):
            expect(component.get("model"), str, path + ".model")
        value = component.get("value")
        if isinstance(value, dict) and "pwl" in value:
            for pair in expect(value["pwl"], list, path + ".value.pwl"):
                expect(pair, list, path + ".value.pwl[]")
                if len(pair) != 2:
                    raise Fault("proposal", path + ".value.pwl[]需要两个数值")
    for name, parameters in expect(protocol.get("models", {}), dict, "protocol.models").items():
        expect(name, str, "models.key")
        expect(parameters, dict, "models." + name)
    analysis = protocol["analysis"]
    expect(analysis.get("kind"), str, "analysis.kind")
    if "source" in analysis:
        expect(analysis["source"], str, "analysis.source")
    measurement = protocol["measurement"]
    expect(measurement.get("mode"), str, "measurement.mode")
    if "target" in measurement:
        expect(measurement["target"], dict, "measurement.target")
    for check in expect(protocol.get("checks", []), list, "protocol.checks"):
        expect(check, dict, "checks[]")
    return protocol


def validate_protocol(protocol, model, contract=None):
    protocol_shapes(protocol)
    if not isinstance(protocol, dict) or set(protocol) - {"components", "models", "device_nodes", "analysis", "measurement", "checks", "temperature_C", "method"}:
        raise Fault("proposal", "电路接口字段未知")
    try:
        finite(protocol["temperature_C"], "temperature_C")
        nodes = protocol["device_nodes"]
        if nodes and (set(nodes) != set(model["ports"]) or any(not NODE.fullmatch(n) for n in nodes.values())):
            raise Fault("proposal", "DUT端口绑定不完整或非法")
        components = protocol["components"]
        if not isinstance(components, list) or not 1 <= len(components) <= 80:
            raise Fault("proposal", "元件数量需在1至80之间")
        names = set()
        for c in components:
            name, kind = c["name"], c["kind"]
            if not NAME.fullmatch(name) or kind not in "RCLVIDS" or len(kind) != 1 or not name.upper().startswith(kind):
                raise Fault("proposal", "元件名或类型非法")
            if name.lower() in names or name.lower() == "xdut":
                raise Fault("proposal", "元件名重复或与DUT冲突")
            names.add(name.lower())
            if len(c["nodes"]) != (4 if kind == "S" else 2) or any(not NODE.fullmatch(n) for n in c["nodes"]):
                raise Fault("proposal", "元件节点非法")
            if set(c) - {"name", "kind", "nodes", "value", "model"}:
                raise Fault("proposal", "元件存在未支持字段")
            if kind in "RCL":
                if finite(c["value"], name) <= 0:
                    raise Fault("proposal", "无源元件值必须为正")
            elif kind in "VI":
                v = c["value"]
                if not isinstance(v, dict) or not v or set(v) - {"dc", "ac", "pwl"} or ("pwl" in v and "dc" in v):
                    raise Fault("proposal", "电源仅支持dc/ac或PWL")
                for k in ("dc", "ac"):
                    if k in v:
                        finite(v[k], name + "." + k)
                if "pwl" in v:
                    p = v["pwl"]
                    if not 2 <= len(p) <= 40:
                        raise Fault("proposal", "PWL点数非法")
                    for t, a in p:
                        if finite(t, "PWL.time") < 0:
                            raise Fault("proposal", "PWL时间为负")
                        finite(a, "PWL.value")
                    if any(b[0] <= a[0] for a, b in zip(p, p[1:])):
                        raise Fault("proposal", "PWL时间必须严格递增")
            elif kind in "DS" and not NAME.fullmatch(c["model"]):
                raise Fault("proposal", "二极管模型名称非法")
        models = protocol.get("models", {})
        for name, parameters in models.items():
            if parameters.get("type") == "SW":
                if not NAME.fullmatch(name) or set(parameters) != {"type", "ron", "roff", "vt", "vh"}:
                    raise Fault("proposal", "开关模型必须显式声明ron/roff/vt/vh")
                ron, roff = finite(parameters["ron"]), finite(parameters["roff"])
                finite(parameters["vt"])
                if not 0 < ron < roff or finite(parameters["vh"]) < 0:
                    raise Fault("proposal", "开关电阻或迟滞非法")
                continue
            if not NAME.fullmatch(name) or not parameters or set(parameters) - {"is", "n", "rs", "cjo", "tt", "bv", "ibv"}:
                raise Fault("proposal", "夹具仅支持显式数值二极管模型")
            for k, v in parameters.items():
                if finite(v, "model." + k) < 0 or (k in ("is", "n") and v == 0):
                    raise Fault("proposal", "二极管参数非法")
        if any(c["kind"] in "DS" and c["model"] not in models for c in components):
            raise Fault("proposal", "夹具二极管缺少定义")
        if any(c["kind"] == "S" and models[c["model"]].get("type") != "SW" or c["kind"] == "D" and models[c["model"]].get("type") == "SW" for c in components):
            raise Fault("proposal", "器件类型与夹具模型类型不一致")
        a = protocol["analysis"]
        if a["kind"] == "dc":
            if set(a) != {"kind", "source", "start", "stop", "step"} or a["source"].lower() not in names:
                raise Fault("proposal", "DC扫描源或字段非法")
            for k in ("start", "stop", "step"):
                finite(a[k], "dc." + k)
            if a["stop"] <= a["start"] or a["step"] <= 0 or (a["stop"] - a["start"]) / a["step"] > 200000:
                raise Fault("proposal", "DC扫描范围或点数非法")
        elif a["kind"] == "ac":
            ac_measurements.frequencies(a)
        elif a["kind"] == "tran":
            if set(a) != {"kind", "stop_s", "max_step_s"} or not 0 < finite(a["max_step_s"]) <= finite(a["stop_s"]) or a["stop_s"] / a["max_step_s"] > 2_000_000:
                raise Fault("proposal", "瞬态时间范围非法")
        else:
            raise Fault("proposal", "未实现该分析接口")
        if protocol.get("method", "trap") not in ("trap", "gear"):
            raise Fault("proposal", "积分方法非法")
        m = protocol["measurement"]
        _signal(m["signal"])
        if set(m) - {"mode", "signal", "sign", "scale", "at", "start_s", "target", "absolute", "denominator", "nominal", "window", "timing"}:
            raise Fault("proposal", "测量存在未知字段")
        if "absolute" in m and not isinstance(m["absolute"], bool):
            raise Fault("proposal", "absolute需要布尔值")
        mode = m["mode"]
        if mode not in pulse_metrology.MODES and any(k in m for k in ("window","timing")):
            raise Fault("proposal", "window/timing只允许用于已实现的新测量接口")
        if mode not in ("sample", "curve", "ratio_curve", "capacitance", "integral_to_crossing", "dc_current_max", "dc_current_difference", "dc_slope", "dc_sensitivity_error_percent", "dc_linearity_percent", "transient_peak", "transient_recovery") and mode not in ac_measurements.MODES and mode not in pulse_metrology.MODES:
            raise Fault("proposal", "测量解析接口未实现")
        if mode in pulse_metrology.MODES:
            pulse_metrology.validate(a,m,_signal)
        elif mode in ac_measurements.MODES:
            ac_measurements.validate(a, m, _signal)
        elif mode in ("transient_peak", "transient_recovery"):
            if a["kind"] != "tran" or not 0 <= finite(m["start_s"]) < finite(m["at"]) <= a["stop_s"]:raise Fault("proposal","Invalid transient window")
            if mode == "transient_recovery":
                if not m["signal"].lower().startswith("i(") or m.get("absolute") or m.get("sign",1)!=1 or m["target"]["signal"]!=m["signal"] or m["target"]["direction"]!="rising" or not finite(m["target"]["value"])<0:raise Fault("proposal","Recovery requires signed actual current and negative return threshold")
        elif mode == "ratio_curve":
            if a["kind"] != "dc" or not m["signal"].lower().startswith("v(") or not _signal(m["denominator"]).startswith("i("):
                raise Fault("proposal", "电阻曲线必须为DC端口电压除以实际支路电流")
        elif mode in ('dc_slope','dc_sensitivity_error_percent','dc_linearity_percent'):
            from .dc_transfer import validate
            validate(a,m)
        elif mode in ("dc_current_max", "dc_current_difference"):
            if a["kind"]!='dc' or not m['signal'].lower().startswith('i(') or not _signal(m['denominator']).startswith('i('):raise Fault('proposal','DC input-current metrology requires two actual current probes')
            finite(m['at'])
        elif "denominator" in m:
            raise Fault("proposal", "denominator只允许用于电阻曲线")
        finite(m.get("sign", 1)); finite(m.get("scale", 1))
        if m.get("sign", 1) not in (-1, 1) or m.get("scale", 1) <= 0:
            raise Fault("proposal", "电流/电压方向只能为±1；单位比例需为正")
        if mode == "sample":
            finite(m["at"])
        elif mode == "capacitance" and a["kind"] != "ac":
            raise Fault("proposal", "电容测量需要AC分析")
        elif mode == "integral_to_crossing":
            if a["kind"] != "tran" or not 0 <= finite(m["start_s"]) < a["stop_s"]:
                raise Fault("proposal", "积分起点非法")
            _signal(m["target"]["signal"]); finite(m["target"]["value"])
            if m["target"].get("direction") not in ("rising", "falling"):
                raise Fault("proposal", "终点方向缺失")
            if not any(c.get("signal", "").lower() == m["target"]["signal"].lower() and c.get("to", float("inf")) <= m["start_s"] for c in protocol.get("checks", [])):
                raise Fault("proposal", "积分测试必须检查释放前的目标节点偏置")
        for c in protocol.get("checks", []):
            _signal(c["signal"])
            if not finite(c["from"]) <= finite(c["to"]) or not finite(c["min"]) <= finite(c["max"]):
                raise Fault("proposal", "偏置/阶段检查区间错误")
        for path, expected in (contract or {}).get("fixed", {}).items():
            if path_get(protocol, path) != expected:
                raise Fault("proposal", "不得改变手册条件或测量定义：" + path)
    except (KeyError, TypeError, IndexError, ValueError) as e:
        if isinstance(e, Fault):
            raise
        raise Fault("proposal", "电路接口缺失或格式错误：" + str(e)) from None
    return protocol


def render(protocol, model):
    validate_protocol(protocol, model)
    lines = ["Declarative SPICE workflow circuit", ".options plotwinsize=0", ".temp %.17g" % protocol["temperature_C"],
             ".options method=" + protocol.get("method", "trap")]
    if protocol["device_nodes"]:
        lines.extend(['.include "model.lib"', "XDUT " + " ".join(protocol["device_nodes"][p] for p in model["ports"]) + " " + model["entry"]])
    for c in protocol["components"]:
        if c["kind"] in "RCL":
            value = "%.17g" % c["value"]
        elif c["kind"] in "DS":
            value = c["model"]
        else:
            v = c["value"]
            value = "PWL(" + " ".join("%.17g %.17g" % tuple(p) for p in v["pwl"]) + ")" if "pwl" in v else "%.17g" % v.get("dc", 0)
            if "ac" in v:
                value += " AC %.17g" % v["ac"]
        lines.append(c["name"] + " " + " ".join(c["nodes"]) + " " + value)
    for name, params in protocol.get("models", {}).items():
        kind = "SW" if params.get("type") == "SW" else "D"
        lines.append(".model " + name + " " + kind + "(" + " ".join(k + "=%.17g" % v for k, v in params.items() if k != "type") + ")")
    a = protocol["analysis"]
    if a["kind"] == "dc":
        lines.append(".dc %s %.17g %.17g %.17g" % (a["source"], a["start"], a["stop"], a["step"]))
    elif a["kind"] == "ac":
        fs = ac_measurements.frequencies(a)
        lines.append(".ac lin %d %.17g %.17g" % (len(fs), fs[0], fs[-1]))
    else:
        lines.append(".tran 0 %.17g 0 %.17g" % (a["stop_s"], a["max_step_s"]))
    m = protocol["measurement"]
    signals = {m["signal"].lower()}
    if "denominator" in m:
        signals.add(m["denominator"].lower())
    if "target" in m:
        signals.add(m["target"]["signal"].lower())
    signals.update(s.lower() for s in pulse_metrology.extra_signals(m))
    signals.update(c["signal"].lower() for c in protocol.get("checks", []))
    signals = set().union(*(signal_components(s) for s in signals))
    lines.append(".save " + " ".join(sorted(signals)))
    lines.append(".end")
    return "\n".join(lines) + "\n"


def raw_data(path, partial=False):
    path = Path(path)
    if path.stat().st_size > 256_000_000:
        raise Fault("parser", "RAW超过256MB解析上限")
    blob = path.read_bytes()
    enc = "utf-16" if blob.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le" if b"\0" in blob[:200] else "latin1"
    lines = blob.decode(enc, errors="replace" if partial else "strict").splitlines()
    try:
        nv = int(next(l.split(":", 1)[1] for l in lines if l.startswith("No. Variables:")))
        np = int(next(l.split(":", 1)[1] for l in lines if l.startswith("No. Points:")))
        plotname = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("Plotname:")), "")
        complex_values = any(l.startswith("Flags:") and "complex" in l for l in lines)
        dc_plot = plotname.lower().startswith("dc ") and not complex_values
        start = lines.index("Variables:") + 1
        names = [l.split()[1].lower() for l in lines[start:start + nv]]
        if not 1 <= nv <= 500 or np < 1 or len(names) != nv or len(set(names)) != nv:
            raise ValueError("变量表不完整")
        values = [l.strip() for l in lines[lines.index("Values:") + 1:] if l.strip()]
        rows, normalizations = [], []
        for offset in range(0, len(values), nv):
            group = values[offset:offset + nv]
            try:
                number, first = group[0].split(None, 1)
                if len(group) != nv or int(number) != len(rows):
                    raise ValueError("RAW样本不完整或序号错误")
                def number_value(s):
                    if complex_values:
                        pair = s.split(",")
                        if len(pair) != 2:
                            raise ValueError("复数格式错误")
                        return complex(*map(float, pair))
                    return float(s)
                row = [number_value(s) for s in [first] + group[1:]]
                if any(not math.isfinite(v.real) or not math.isfinite(v.imag) for v in row):
                    raise ValueError("非有限RAW数据")
                if row[0].imag != 0 or (rows and row[0].real < rows[-1][0].real):
                    raise ValueError("RAW横轴异常")
                if rows and row[0].real == rows[-1][0].real:
                    # LTspice can emit the rounded DC stop twice. Only a single
                    # final real DC sample is supported; never deduplicate time,
                    # interior points or conflicting operating points silently.
                    if not dc_plot or len(rows) != np - 1 or offset + nv != len(values) or normalizations:
                        raise ValueError("RAW横轴重复不是DC末端样本")
                    if any(not math.isclose(x, y, rel_tol=1e-5, abs_tol=1e-15)
                           for x, y in zip(rows[-1][1:], row[1:])):
                        raise ValueError("DC重复末端的测量值不一致")
                    normalizations.append({"kind": "dc_terminal_duplicate", "axis": row[0],
                        "source_indices": [np - 2, np - 1], "kept_source_index": np - 1,
                        "max_absolute_signal_difference": max((abs(x-y) for x,y in zip(rows[-1][1:], row[1:])), default=0),
                        "relative_consistency_tolerance": 1e-5, "absolute_consistency_tolerance": 1e-15})
                rows.append(row)
            except (ValueError, IndexError):
                if partial and offset + nv >= len(values):
                    break
                raise
        if not rows or (not partial and len(rows) != np):
            raise ValueError("RAW点数不完整")
        complete = len(rows) == np and len(values) == np * nv
        raw_points = len(rows)
        if normalizations:
            rows[-2:] = [rows[-1]]
        return {"axis": [r[0].real for r in rows], "signals": {n: [r[i] for r in rows] for i, n in enumerate(names)},
                "complete": complete, "raw_points": raw_points, "parsed_points": len(rows),
                "plotname": plotname, "normalizations": normalizations}
    except (ValueError, KeyError, IndexError, StopIteration) as e:
        raise Fault("parser", str(e)) from None


def interpolate(xs, ys, x):
    tol = max(1e-15, (xs[-1] - xs[0]) * 1e-8)
    if x < xs[0] - tol or x > xs[-1] + tol:
        raise Fault("parser", "测量横坐标超出有效波形；禁止外推")
    x = min(xs[-1], max(xs[0], x))
    j = bisect.bisect_left(xs, x)
    if j == 0:
        return ys[0]
    if j == len(xs):
        return ys[-1]
    return ys[j-1] + (ys[j] - ys[j-1]) * (x - xs[j-1]) / (xs[j] - xs[j-1])


def measure(protocol, data, reference=None):
    xs, signals = data["axis"], measurement_signals(data, protocol)
    a, m = protocol["analysis"], protocol["measurement"]
    if a["kind"] == "ac" and m["mode"] not in ac_measurements.MODES and (len(xs) != 1 or not math.isclose(xs[0], a.get("frequency_Hz", -1), rel_tol=1e-9)):
        raise Fault("parser", "AC实际频率与请求不一致")
    if a["kind"] == "dc" and (not math.isclose(xs[0], a["start"], rel_tol=1e-8, abs_tol=1e-12) or not math.isclose(xs[-1], a["stop"], rel_tol=1e-8, abs_tol=1e-12)):
        raise Fault("execution", "DC未完成所声明的扫描区间", {"first_axis": xs[0], "last_axis": xs[-1]})
    if a["kind"] == "tran" and xs[-1] < a["stop_s"] * (1 - 1e-6):
        raise Fault("execution", "瞬态未推进至结束时间", {"last_time": xs[-1]})
    try:
        for c in protocol.get("checks", []):
            ys = signals[c["signal"].lower()]
            values = [v.real for x, v in zip(xs, ys) if c["from"] <= x <= c["to"]]
            values += [interpolate(xs, ys, t).real for t in (c["from"], c["to"])]
            if min(values) < c["min"] or max(values) > c["max"]:
                raise Fault("fixture", "实际偏置/阶段检查未通过", {"check": c, "min": min(values), "max": max(values)})
        if m["mode"] in pulse_metrology.MODES:
            return pulse_metrology.measure(m,xs,signals,interpolate)
        if m["mode"] in ac_measurements.MODES:
            return ac_measurements.measure(a, m, {**data, "signals": signals}, reference, interpolate)
        ys = signals[m["signal"].lower()]
        sign, scale = m.get("sign", 1), m.get("scale", 1)
        if m["mode"] == "capacitance":
            value = sign * ys[0].imag / (2 * math.pi * a["frequency_Hz"]) * scale
            return {"value": value}
        if m["mode"] in ("transient_peak","transient_recovery"):
            from .transient_metrology import value
            actual=[y.real*sign*(1 if m["mode"]=="transient_recovery" else scale) for y in ys]
            return value(m,xs,actual,interpolate)
        ys = [y.real * sign * scale for y in ys]
        if m['mode'] in ('dc_slope','dc_sensitivity_error_percent','dc_linearity_percent'):
            from .dc_transfer import value
            return {'value':value(a,m,xs,[v.real for v in signals[m['signal'].lower()]])}
        if m['mode'] in ('dc_current_max','dc_current_difference'):
            first=interpolate(xs,[v.real for v in signals[m['signal'].lower()]],m['at'])
            second=interpolate(xs,[v.real for v in signals[m['denominator'].lower()]],m['at'])
            value=(max(abs(first),abs(second)) if m['mode']=='dc_current_max' else first-second)*sign*scale
            return {'value':abs(value) if m.get('absolute') else value}
        if m["mode"] == "sample":
            value = interpolate(xs, ys, m["at"])
            return {"value": abs(value) if m.get("absolute") else value}
        if m["mode"] in ("curve", "ratio_curve"):
            if m["mode"] == "ratio_curve":
                denominators = [v.real for v in signals[m["denominator"].lower()]]
                if any(abs(d) < 1e-15 for d in denominators):
                    raise Fault("fixture", "电阻曲线存在零电流，禁止无穷值或伪造导通电阻")
                ys = [v / d for v, d in zip(ys, denominators)]
            if not reference:
                raise Fault("missing_data", "缺少数字化曲线")
            rows = [{"x": x, "reference_y": y, "simulated_y": interpolate(xs, ys, x)} for x, y in reference]
            errors = [r["simulated_y"] - r["reference_y"] for r in rows]
            span = max(y for x, y in reference) - min(y for x, y in reference)
            mae = sum(abs(e) for e in errors) / len(errors)
            return {"metrics": {"MAE": mae, "RMSE": math.sqrt(sum(e*e for e in errors)/len(errors)),
                    "max_absolute_error": max(abs(e) for e in errors),
                    "MAE_over_reference_span_percent": mae / span * 100 if span else None,
                    "max_error_over_reference_span_percent": max(abs(e) for e in errors) / span * 100 if span else None}, "comparison": rows}
        target = m["target"]
        ts = [v.real for v in signals[target["signal"].lower()]]
        start, level = m["start_s"], target["value"]
        direction = 1 if target["direction"] == "rising" else -1
        if direction * (interpolate(xs, ts, start) - level) >= 0:
            raise Fault("fixture", "释放时已达终点；初始化不成立")
        end = None
        for j in range(1, len(xs)):
            if xs[j] <= start:
                continue
            left = max(start, xs[j-1]); before = interpolate(xs, ts, left)
            if direction * (before - level) < 0 <= direction * (ts[j] - level):
                end = left + (xs[j] - left) * (level - before) / (ts[j] - before)
                break
        if end is None:
            raise Fault("fixture", "有效测量区间内未达终点")
        knots = [start] + [x for x in xs if start < x < end] + [end]
        charge = sum((interpolate(xs, ys, x) + interpolate(xs, ys, y)) * (y-x) / 2 for x, y in zip(knots, knots[1:]))
        return {"value": charge, "measurement_window": {"start": start, "end": end}}
    except KeyError as e:
        raise Fault("parser", "RAW缺少保存变量：" + str(e)) from None


def acceptance(result, expectation):
    if "metrics" in result:
        thresholds = expectation.get("thresholds", {})
        if not thresholds:
            return "pending"
        for k, limit in thresholds.items():
            finite(limit, "threshold")
            if k not in result["metrics"] or result["metrics"][k] is None:
                return "pending"
            if result["metrics"][k] > limit:
                return "fail"
        return "pass"
    if "value" not in result:
        return "pending"
    value = finite(result["value"], "result.value")
    limits = expectation.get("limits", {})
    if ("min" in limits and value < limits["min"]) or ("max" in limits and value > limits["max"]):
        return "fail"
    typical = expectation.get("typical")
    if typical is not None:
        absolute = expectation.get("typical_absolute_tolerance")
        relative = expectation.get("typical_relative_tolerance_percent")
        if absolute is None and relative is None:
            return "pending"
        tolerance = absolute if absolute is not None else abs(typical) * relative / 100
        if abs(value - typical) > tolerance:
            return "fail"
    return "pass" if limits or typical is not None else "pending"


def validate_measurement_unit(case):
    p, m, unit = case["protocol"], case["protocol"]["measurement"], case["expectation"]["unit"]
    mode, scale = m["mode"], m.get("scale", 1)
    units = {"V": ("V", 1), "mV": ("V", 1e-3), "A": ("A", 1), "mA": ("A", 1e-3),
             "uA": ("A", 1e-6), "μA": ("A", 1e-6), "µA": ("A", 1e-6), "nA": ("A", 1e-9), "F": ("F", 1), "pF": ("F", 1e-12), "nF": ("F", 1e-9),
             "C": ("C", 1), "nC": ("C", 1e-9), "uC": ("C", 1e-6), "ohm": ("ohm", 1), "Ω": ("ohm", 1), "mΩ": ("ohm", 1e-3)}
    units.update({"uV":("V",1e-6),"μV":("V",1e-6),"pA":("A",1e-12),"uF":("F",1e-6),"μF":("F",1e-6),"H":("H",1),"uH":("H",1e-6),"μH":("H",1e-6),"s":("s",1),"ns":("s",1e-9),"us":("s",1e-6),"Hz":("Hz",1),"kHz":("Hz",1e3),"1":("1",1),"dB":("dB",1),"degree":("degree",1),"mV/A":("V/A",1e-3),"%":("%",1)})
    if mode in ac_measurements.MODES and "at" not in m and "frequency_Hz" not in p["analysis"] and not case.get("reference"):
        raise Fault("missing_data", "交流频扫验收需要参考曲线或明确采样频率，不能以无阈值样本作为标量")
    if unit == "oracle_native":
        return
    if unit not in units:
        raise Fault("capability", "测量单位需要新增受验证的解析适配器：" + unit)
    dimension, factor = units[unit]
    signal_dimension = "V" if m["signal"].lower().startswith("v(") else "A"
    if mode in ("capacitance", "integral_to_crossing") and signal_dimension != "A":
        raise Fault("proposal", "电容/电荷必须测量电流，不能积分电压后冒充电荷")
    native = "F" if mode == "capacitance" else "C" if mode == "integral_to_crossing" and signal_dimension == "A" else signal_dimension
    expected_scale = 1 / factor
    if mode == "transient_frequency":
        native = "Hz"
    elif mode == "transient_duty":
        native = "%"
    elif mode in ("transient_edge_time", "transient_delay"):
        native = "s"
    elif mode == "transient_recovery":
        native = "s"
    elif mode in ac_measurements.MODES:
        native = ac_measurements.MODES[mode]
    if mode=='dc_slope':native='V/A'
    if mode in ('dc_sensitivity_error_percent','dc_linearity_percent'):native='%'
    if mode == "ratio_curve":
        native = "ohm"
    if dimension == "ohm" and mode == "sample" and signal_dimension == "V":
        a = p["analysis"]
        source = path_get(p, "components@" + a["source"]) if a["kind"] == "dc" else {}
        if source.get("kind") != "I" or m.get("at", 0) == 0:
            raise Fault("proposal", "电阻测量需定义电流驱动与V/I换算")
        native, expected_scale = "ohm", 1 / (m["at"] * factor)
    if native != dimension or not math.isclose(scale, expected_scale, rel_tol=1e-10, abs_tol=1e-15):
        raise Fault("proposal", "测量维度或SI单位换算不一致")


def load_reference(path, unit, condition=None):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        records = list(csv.DictReader(f))
    points = []
    for row in records:
        if row.get("y_unit") != unit or (condition is not None and row.get("condition") != condition):
            raise Fault("input", "参考曲线单位或条件不一致")
        x, y = float(row["x"]), float(row["y"])
        finite(x); finite(y); points.append((x, y))
    points.sort()
    if len(points) < 2 or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        raise Fault("input", "参考横坐标必须严格递增")
    return points


class Simulator:
    def __init__(self, store, runner, model, transport=None, cache=None):
        self.store, self.runner, self.model, self.transport = store, runner, model, transport
        self.cache = Path(cache) if cache else None
        self.mutex = threading.Lock()
        self.locks = {}

    def run(self, protocol, model_path, label, retry=0):
        from .simulation_cache import signature
        key, data = signature(protocol, model_path, self.runner, render(protocol, self.model), self.transport, retry)
        with self.mutex:
            mutex = self.locks.setdefault(key, threading.Lock())
        with mutex:
            if self.cache:
                with file_lock(self.cache / ("locks/" + key + ".lock"), timeout=min(120, self.store.remaining_seconds()), tick=print):
                    return self._run_locked(protocol, model_path, label, retry, key, data)
            return self._run_locked(protocol, model_path, label, retry, key, data)

    def _run_locked(self, protocol, model_path, label, retry=0, key=None, signature_data=None):
        circuit = render(protocol, self.model)
        if key is None:
            from .simulation_cache import signature
            key, signature_data = signature(protocol, model_path, self.runner, circuit, self.transport, retry)
        old = self.store.get("simulations", key)
        folder = self.store.folder / "simulations" / key
        if old and old["status"] == "completed":
            if not artifacts_valid(folder, old.get("hashes")):
                raise Fault("cache_corrupt", "仿真缓存文件校验失败", {"folder": str(folder)})
            self.store.event(label, "simulation_reused")
            return raw_data(folder / "test.raw"), folder
        if old and old["status"] == "failed":
            raise Fault(**old["fault"])
        if not old and self.cache:
            from .simulation_cache import restore, FILES
            if restore(self.cache, key, signature_data, folder):
                self.store.put("simulations", key, {"status": "completed", "label": label, "hashes": artifact_hashes(folder, FILES), "shared_cache": True})
                self.store.event(label, "shared_simulation_reused", {"cache_key": key})
                return raw_data(folder / "test.raw"), folder
        # Interrupted work has an unknown result. Existing complete artifacts are checked
        # before any new dispatch; otherwise route to diagnosis, rather than overwrite.
        if old:
            try:
                if (digest(folder / "model.lib") == digest(model_path)
                    and (folder / "test.cir").read_text() == circuit
                    and (folder / "test.log").stat().st_size and raw_data(folder / "test.raw")["complete"]):
                    self.store.put("simulations", key, {"status": "completed", "label": label, "hashes": artifact_hashes(folder, ["model.lib", "test.cir", "test.raw", "test.log"])})
                    return raw_data(folder / "test.raw"), folder
            except (Fault, OSError):
                pass
            raise Fault("execution_interrupted", "上次仿真中断，证据已保留", {"folder": str(folder)})
        self.store.event("circuit_build", "running", {"test": label})
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "test.cir").write_text(circuit, encoding="utf-8")
        shutil.copyfile(model_path, folder / "model.lib")
        self.store.event("circuit_build", "completed", {"test": label, "folder": str(folder)})
        self.store.reserve("simulations")
        self.store.put("simulations", key, {"status": "started", "label": label})
        try:
            self.store.event("simulation_execution", "running", {"test": label})
            if self.transport:
                self.transport(protocol, model_path, folder)
                record = {"returncode": 0, "test_backend": True}
            else:
                record = self._launch(folder)
            save(folder / "execution.json", record)
            if record["returncode"] != 0 or not (folder / "test.raw").is_file() or not (folder / "test.log").is_file() or (folder / "test.log").stat().st_size == 0:
                raise Fault("execution", "仿真未生成完整输出", {"folder": str(folder), "execution": record})
            data = raw_data(folder / "test.raw")
            self.store.event("simulation_execution", "completed", {"test": label})
            self.store.put("simulations", key, {"status": "completed", "label": label, "hashes": artifact_hashes(folder, ["model.lib", "test.cir", "test.raw", "test.log", "execution.json"])})
            if self.cache:
                from .simulation_cache import publish
                publish(self.cache, key, signature_data, folder)
            return data, folder
        except Fault as e:
            self.store.event("simulation_execution", "failed", {"test": label, "fault": e.record()})
            detail = e.evidence.copy(); detail["folder"] = str(folder)
            log = folder / "test.log"
            if log.exists():
                raw_log = log.read_bytes()
                encoding = "utf-16" if raw_log.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
                lines = raw_log.decode(encoding, errors="replace").splitlines()
                detail["log_head"], detail["log_tail"] = lines[:15], lines[-15:]
            if (folder / "test.raw").exists():
                try:
                    partial = raw_data(folder / "test.raw", partial=True)
                    detail["partial_waveform"] = {"last_axis": partial["axis"][-1], "points": len(partial["axis"]), "complete": partial["complete"]}
                except Fault as parse_error:
                    detail["raw_diagnostic"] = parse_error.record()
            fault = Fault(e.kind, str(e), detail)
            save(folder / "fault.json", fault.record())
            self.store.put("simulations", key, {"status": "failed", "label": label, "fault": fault.record()})
            raise fault
        except OSError as e:
            fault = Fault("runner", str(e), {"folder": str(folder)})
            self.store.put("simulations", key, {"status": "failed", "label": label, "fault": fault.record()})
            raise fault

    def _launch(self, folder):
        # Same lock path as the pre-existing runner, so old and new jobs cooperate.
        lock = Path(tempfile.gettempdir()) / ("d2sflow-ltspice-%d.lock" % os.getuid())
        timeout = min(self.runner.get("timeout_seconds", 120), self.store.remaining_seconds())
        argv = [a.replace("{circuit}", str(folder / "test.cir")) for a in self.runner["argv"]]
        started = time.monotonic()
        with file_lock(lock, min(self.runner.get("lock_timeout_seconds", 120), timeout), print):
            timeout = min(timeout, self.store.remaining_seconds())
            if timeout <= 0:
                from .state import BudgetEnd
                raise BudgetEnd("seconds")
            with (folder / "runner.txt").open("wb") as output:
                p = subprocess.Popen(argv, cwd=folder, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                save(folder / "process.json", {"pid": p.pid, "argv": argv, "started": time.time()})
                last_progress = time.monotonic()
                while p.poll() is None:
                    if time.monotonic() - started >= timeout or self.store.remaining_seconds() == 0:
                        # Terminate only the process group launched by this invocation.
                        try:
                            os.killpg(p.pid, signal.SIGTERM)
                            p.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            os.killpg(p.pid, signal.SIGKILL); p.wait()
                        except ProcessLookupError:
                            p.wait()
                        raise Fault("execution_timeout", "仿真进程超时", {"pid": p.pid, "seconds": timeout})
                    if time.monotonic() - last_progress >= 10:
                        print("等待仿真：%d秒" % (time.monotonic() - started), flush=True)
                        self.store.flush()
                        last_progress = time.monotonic()
                    time.sleep(.2)
                return {"returncode": p.returncode, "elapsed_seconds": time.monotonic() - started, "argv": argv}
