"""Declared repair abilities derived from actual text, not a required B-source skeleton."""
import re
from .state import Fault, finite

NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?:meg|[tgmkunpf])?"
VALUE = re.compile(r"^"+NUMBER+r"$", re.I)
ASSIGN = re.compile(r"\b([A-Za-z_][\w.]*)\s*=\s*("+NUMBER+r")(?![\w.])", re.I)
SCALE = {"": 1, "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
         "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
COUNTS = {"R": 2, "C": 2, "L": 2, "D": 2, "M": 4, "Q": 3, "J": 3}


def number(token):
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(meg|[tgmkunpf])?", token, re.I)
    if not m:
        raise Fault("proposal", "诊断参数必须为独立SPICE数值")
    return finite(float(m[1])*SCALE[(m[2] or "").lower()])


def line_parameters(line, mode):
    slots = []
    fields = list(re.finditer(r"\S+", line))
    if mode == "passive" and len(fields) >= 4 and VALUE.fullmatch(fields[3][0]):
        m = fields[3]
        slots.append(("value", m.start(), m.end(), m[0]))
    if mode in ("model", "param", "passive"):
        for m in ASSIGN.finditer(line):
            if m[1].lower() not in ("level", "version"):
                slots.append((m[1], m.start(2), m.end(2), m[2]))
    return slots


def catalog(text):
    parameters, rewires = [], []
    scopes, scope, model_mode = {}, "global", False
    rows = text.splitlines()
    for line in rows:
        if line.lstrip().startswith("*"):
            continue
        fields = line.split()
        if not fields:
            continue
        directive = fields[0].lower()
        if directive == ".subckt":
            scope = fields[1]
            scopes.setdefault(scope, set()).update(fields[2:])
        elif directive == ".ends":
            scope = "global"
        count = COUNTS.get(fields[0][0].upper(), 0) if not directive.startswith((".", "+")) else 0
        scopes.setdefault(scope, set()).update(fields[1:1+count])
    scope = "global"
    for line in rows:
        fields = line.split()
        if not fields or line.lstrip().startswith("*"):
            continue
        directive = fields[0].lower()
        if directive == ".subckt":
            scope = fields[1]
        elif directive == ".ends":
            scope = "global"
        if not line.lstrip().startswith("+"):
            model_mode = directive == ".model"
        mode = "model" if model_mode else "param" if directive == ".param" else "passive" if fields[0][0].upper() in "RCL" and not directive.startswith((".", "+")) else "none"
        slots = line_parameters(line, mode)
        if slots and text.count(line) == 1:
            parameters.append({"old": line, "mode": mode, "scope": scope,
                "parameters": [{"name": n, "token": t, "value": number(t)} for n, a, b, t in slots]})
        count = COUNTS.get(fields[0][0].upper(), 0) if not directive.startswith((".", "+")) else 0
        if count and text.count(line) == 1 and len(fields) > count+1:
            rewires.append({"old": line, "element": fields[0], "terminal_count": count,
                "terminals": fields[1:1+count], "scope": scope, "allowed_nodes": sorted(scopes[scope] | {"0"})})
    return {"parameter": parameters, "existing_node_rewire": rewires}


def mask(line, slots):
    for n, start, end, token in reversed(slots):
        line = line[:start]+"#"+line[end:]
    return line


def validate_parameter(source, candidate, maximum_percent):
    rows = {p["old"]: p for p in catalog(source)["parameter"]}
    before, after = source.splitlines(), candidate.splitlines()
    if len(before) != len(after):
        raise Fault("proposal", "参数诊断不能增删模型行")
    changes = []
    for a, b in zip(before, after):
        if a == b:
            continue
        descriptor = rows.get(a)
        if not descriptor:
            raise Fault("capability", "参数诊断仅允许能力清单中的模型/无源参数行")
        old, new = line_parameters(a, descriptor["mode"]), line_parameters(b, descriptor["mode"])
        if [s[0] for s in old] != [s[0] for s in new] or mask(a, old) != mask(b, new):
            raise Fault("proposal", "参数诊断不能改变节点、元件、参数名或表达式")
        for x, y in zip(old, new):
            v, w = number(x[3]), number(y[3])
            if v == w:
                continue
            if not v or abs(w-v) > abs(v)*maximum_percent/100+abs(v)*1e-12 or (v*w <= 0):
                raise Fault("proposal", "诊断参数扰动越界；零值需明确物理范围，非零值不能改变符号")
            changes.append({"parameter": x[0], "old_value": v, "new_value": w, "scope": descriptor["scope"]})
    if not changes:
        raise Fault("proposal", "参数诊断没有有效数值改变")
    return changes


def validate_rewire(source, candidate):
    rows = {r["old"]: r for r in catalog(source)["existing_node_rewire"]}
    before, after = source.splitlines(), candidate.splitlines()
    if len(before) != len(after):
        raise Fault("proposal", "已有节点重接不允许新增或删除元件")
    changes = []
    for a, b in zip(before, after):
        if a == b:
            continue
        r = rows.get(a)
        if not r:
            raise Fault("capability", "结构诊断仅允许清单中的已有元件重接")
        old, new, count = a.split(), b.split(), r["terminal_count"]
        if len(old) != len(new) or old[0] != new[0] or old[count+1:] != new[count+1:]:
            raise Fault("proposal", "重接不能改变元件名、模型引用或数值")
        if not {n.casefold() for n in new[1:count+1]} <= {n.casefold() for n in r["allowed_nodes"]}:
            raise Fault("proposal", "重接不能引用其他子电路或新增节点")
        changes.append({"element": old[0], "before": old[1:count+1], "after": new[1:count+1], "scope": r["scope"]})
    if not changes:
        raise Fault("proposal", "结构诊断没有实际重接")
    return changes
