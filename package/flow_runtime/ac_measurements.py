"""Complex V/I measurements. No assumption that AC excitation equals one volt."""
import cmath
import math
from .state import Fault, finite

MODES = {
    "ac_resistance": "ohm", "ac_impedance_magnitude": "ohm",
    "ac_series_capacitance": "F", "ac_parallel_capacitance": "F",
    "ac_inductance": "H", "ac_dissipation_factor": "1",
    "ac_gain_magnitude": "1", "ac_gain_db": "dB", "ac_phase": "degree",
    "ac_current_ratio": "1", "ac_reactance_ratio": "1",
}


def frequencies(a):
    if set(a) == {"kind", "frequency_Hz"}:
        f = finite(a["frequency_Hz"])
        if f <= 0:
            raise Fault("proposal", "AC频率必须为正")
        return [f]
    if set(a) != {"kind", "sweep", "points", "start_Hz", "stop_Hz"}:
        raise Fault("proposal", "AC扫描字段不完整")
    n, start, stop = a["points"], finite(a["start_Hz"]), finite(a["stop_Hz"])
    if isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 10001 or not 0 < start < stop:
        raise Fault("proposal", "AC扫描范围或点数非法")
    if a["sweep"] != "lin":
        raise Fault("capability", "本阶段只支持明确总点数的线性AC扫描")
    return [start + (stop-start)*i/(n-1) for i in range(n)]


def validate(a, m, signal_validator):
    if a["kind"] != "ac":
        raise Fault("proposal", "复数测量需要AC分析")
    signal_validator(m["signal"]); signal_validator(m["denominator"])
    sig, den = m["signal"].lower(), m["denominator"].lower()
    if m["mode"]=="ac_current_ratio":
        if not (sig.startswith("i(") and den.startswith("i(")):raise Fault("proposal","Current ratio requires two actual current probes")
    elif m["mode"]=="ac_reactance_ratio":
        if not (sig.startswith("v(") and den.startswith("v(")):raise Fault("proposal","Reactance ratio requires two actual voltage probes")
    elif m["mode"].startswith("ac_gain") or m["mode"] == "ac_phase":
        if not (sig.startswith("v(") and den.startswith("v(")):
            raise Fault("proposal", "增益与相位必须为实际输出电压/输入电压")
    elif not (sig.startswith("v(") and den.startswith("i(")):
        raise Fault("proposal", "阻抗及衍生量必须为实际端口电压/实际支路电流")
    if "at" in m:
        f = finite(m["at"]); fs = frequencies(a)
        if not fs[0] <= f <= fs[-1]:
            raise Fault("proposal", "采样频率越界")
    if m.get("absolute"):
        raise Fault("proposal", "交流量已有明确数学定义，不能额外取绝对值隐藏方向或相位")


def measure(a, m, data, reference, interpolate):
    xs, signals = data["axis"], data["signals"]
    wanted = frequencies(a)
    if len(xs) != len(wanted) or any(not math.isclose(x,y,rel_tol=1e-8,abs_tol=1e-10) for x,y in zip(xs,wanted)):
        raise Fault("execution", "AC扫描未完整覆盖所声明频率")
    try:
        numerator, denominator = signals[m["signal"].lower()], signals[m["denominator"].lower()]
    except KeyError as e:
        raise Fault("parser", "AC缺少变量："+str(e)) from None
    if len(numerator) != len(xs) or len(denominator) != len(xs):
        raise Fault("parser", "AC变量样本数不一致")
    sign, scale = m.get("sign",1), m.get("scale",1)
    ys=[]
    for f, v, i in zip(xs,numerator,denominator):
        if abs(i) < 1e-24:
            raise Fault("fixture", "交流分母为零，激励或测量绑定不成立")
        z=sign*v/i; mode=m["mode"]; w=2*math.pi*f
        if mode == "ac_reactance_ratio":
            if v.imag <= 0 or i.imag <= 0:raise Fault("fixture","Reactance comparison requires two inductive biased ports")
            val=sign*v.imag/i.imag
        elif mode == "ac_current_ratio": val=abs(z)
        elif mode == "ac_resistance": val=z.real
        elif mode == "ac_impedance_magnitude" or mode == "ac_gain_magnitude": val=abs(z)
        elif mode == "ac_inductance":
            if z.imag <= 0: raise Fault("fixture", "端口未呈感性，不能报告正电感")
            val=z.imag/w
        elif mode == "ac_series_capacitance":
            if z.imag >= 0: raise Fault("fixture", "端口未呈容性，不能报告串联电容")
            val=-1/(w*z.imag)
        elif mode == "ac_parallel_capacitance":
            if abs(z) < 1e-24: raise Fault("fixture", "零阻抗不能转换为并联电容")
            val=(1/z).imag/w
            if val <= 0: raise Fault("fixture", "端口未呈容性，不能报告并联电容")
        elif mode == "ac_dissipation_factor":
            if z.imag >= 0 or z.real < 0: raise Fault("fixture", "损耗因数需要无源容性端口")
            val=z.real/-z.imag
        elif mode == "ac_gain_db":
            if abs(z) < 1e-24: raise Fault("fixture", "零增益不能报告有限dB")
            val=20*math.log10(abs(z))
        else: val=math.degrees(cmath.phase(z))
        finite(val);ys.append(finite(val*scale))
    if reference is not None:
        rows=[{"x":x,"reference_y":y,"simulated_y":interpolate(xs,ys,x)} for x,y in reference]
        errors=[r["simulated_y"]-r["reference_y"] for r in rows]
        span=max(y for x,y in reference)-min(y for x,y in reference)
        mae=sum(abs(e) for e in errors)/len(errors);maximum=max(abs(e) for e in errors)
        return {"metrics":{"MAE":mae,"RMSE":math.sqrt(sum(e*e for e in errors)/len(errors)),
                            "max_absolute_error":maximum,"MAE_over_reference_span_percent":100*mae/span if span else None,
                            "max_error_over_reference_span_percent":100*maximum/span if span else None},"comparison":rows}
    if "at" not in m and len(xs) != 1:
        return {"samples":[{"frequency_Hz":f,"value":y} for f,y in zip(xs,ys)],"acceptance":"not_evaluated"}
    return {"value":interpolate(xs,ys,m.get("at",xs[0]))}
