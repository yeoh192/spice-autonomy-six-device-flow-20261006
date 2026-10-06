"""Evidence-to-contract normalization. Physical interpretation still needs review."""
import math
import re
from .spice import path_get
from .state import Fault, finite

UNITS = {"V": ("V", 1), "mV": ("V", 1e-3), "A": ("A", 1), "mA": ("A", 1e-3),
         "uA": ("A", 1e-6), "µA": ("A", 1e-6), "μA": ("A", 1e-6), "nA": ("A", 1e-9),
         "s": ("s", 1), "ms": ("s", 1e-3), "us": ("s", 1e-6), "µs": ("s", 1e-6), "ns": ("s", 1e-9),
         "°C": ("temperature_C", 1), "degC": ("temperature_C", 1),
         "ohm": ("ohm", 1), "Ω": ("ohm", 1), "kΩ": ("ohm", 1000), "kOhm": ("ohm", 1000), "Hz": ("Hz", 1), "kHz": ("Hz", 1000), "MHz": ("Hz", 1e6)}
NUM = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
PATTERN = re.compile("(" + NUM + r")\s*(" + "|".join(re.escape(s) for s in sorted(UNITS, key=len, reverse=True)) + r")(?!\w)")


def can_extract(item):
    reference = item.get("reference_evidence", {})
    return (bool(reference.get("unit")) and bool(reference.get("values"))
            and isinstance(reference.get("conditions"), str) and bool(PATTERN.search(reference["conditions"])))


def bound_unit(protocol, path):
    if path == "analysis/frequency_Hz":
        return "Hz"
    if path == "temperature_C":
        return "temperature_C"
    if path.startswith("components@"):
        head, rest = path.split("/", 1)
        c = path_get(protocol, head)
        if rest in ("value/dc", "value/ac") or re.fullmatch(r"value/pwl/\d+/1", rest):
            return {"V": "V", "I": "A"}.get(c["kind"])
        if re.fullmatch(r"value/pwl/\d+/0", rest):
            return "s"
        if rest == "value" and c["kind"] == "R":
            return "ohm"
    if path == "measurement/target/value":
        return "V" if protocol["measurement"]["target"]["signal"].lower().startswith("v(") else "A"
    if path == "measurement/at" and protocol["analysis"]["kind"] == "dc":
        name = protocol["analysis"]["source"]
        return {"V": "V", "I": "A"}.get(path_get(protocol, "components@" + name)["kind"])
    return None


def extracted_contract(item, proposal):
    reference, p = item["reference_evidence"], proposal["protocol"]
    if proposal.get("unresolved_conditions"):
        raise Fault("missing_data", "有无法绑定的手册条件", {"conditions": proposal["unresolved_conditions"]})
    bindings = proposal.get("condition_bindings", [])
    if not bindings:
        raise Fault("proposal", "缺少条件证据到实际电路的绑定")
    fixed = {"device_nodes": p["device_nodes"], "temperature_C": p["temperature_C"]}
    conditions = reference["conditions"]
    matched = set()
    all_numbers = {(m.start(), m.end()) for m in PATTERN.finditer(conditions)}
    for b in bindings:
        quote, path = b["quote"], b["protocol_path"]
        if not quote or quote not in conditions:
            raise Fault("proposal", "条件引文不在原始手册记录中")
        actual = finite(path_get(p, path), path)
        native = bound_unit(p, path)
        if native is None:
            raise Fault("proposal", "不支持该条件绑定路径：" + path)
        possibilities = [m for m in PATTERN.finditer(quote) if UNITS[m[2]][0] == native
            and math.isclose(actual, float(m[1]) * UNITS[m[2]][1], rel_tol=1e-10, abs_tol=1e-15)]
        if not possibilities:
            raise Fault("proposal", "实际电路的数值/单位与引文不一致：" + path)
        fixed[path] = actual
        # Every numeric condition with a supported unit must be represented, not just one.
        for found in re.finditer(re.escape(quote), conditions):
            for m in possibilities:
                matched.add((found.start() + m.start(), found.start() + m.end()))
    if all_numbers - matched:
        raise Fault("proposal", "尚有未绑定的手册数值条件", {"unbound_ranges": sorted(all_numbers - matched)})
    values = reference["values"]
    expectation = {"unit": reference["unit"], "limits": {k: v for k, v in values.items() if k in ("min", "max")},
                   "typical": values.get("typ"), "thresholds": {}}
    return {"fixed": fixed, "measurement": p["measurement"], "expectation": expectation,
            "condition_bindings": bindings, "source": "agent_extracted_and_independently_reviewed"}
