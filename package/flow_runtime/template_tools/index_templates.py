#!/usr/bin/env python3
"""Read-only SPICE library inventory. Standard library only; no model/API calls."""
import argparse
import csv
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import time
from datetime import datetime, timezone

VERSION = '1.1'
MODEL_SUFFIXES = {'.lib', '.sub', '.txt', '.cir'}
VENDOR = re.compile(r'(?i)copyright|proprietary|licensed software|manufacturer\s*:|NXP\s+Semiconductors|NXP\s+BUK|Nexperia|Analog Devices|Linear Technology|Nippon Chemi|W[uü]rth|DIODES INCORPORATED|Taiwan Semiconductor|Vishay|ROHM|Wolfspeed|\bCree\b|\bAllegro\b|Efficient Power Conversion|GaN Systems|\bADI\b|\bmfg\s*=')

def decode(blob):
    for enc in (['utf-16'] if blob.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'cp1252']):
        try: return blob.decode(enc), enc, False
        except UnicodeError: pass
    return blob.decode('latin1'), 'latin1', True

def logical_lines(text):
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('*'): continue
        if stripped.startswith('+'):
            if current: current[1] += ' ' + stripped[1:].strip()
            else: yield number, stripped
        else:
            if current: yield tuple(current)
            current = [number, stripped]
    if current: yield tuple(current)

def source_evidence(text):
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if VENDOR.search(line):
            # Copyright alone establishes a rights marker, not manufacturer authorship.
            kind = 'manufacturer_association' if re.search(r'(?i)\bmfg\s*=', line) else 'copyright_or_license' if re.search(r'(?i)copyright|proprietary|licensed', line) else 'named_organization'
            result.append({'line': number, 'kind': kind, 'text': line.strip()[:1200]})
    return result

def dependency_target(rest):
    rest = rest.strip()
    if not rest: return ''
    if rest[0] in ('"', "'"):
        return rest[1:].split(rest[0], 1)[0]
    return rest.split()[0]

def parse_file(path, relative, blob):
    text, encoding, fallback = decode(blob)
    evidence = source_evidence(text)
    record = {'path': relative, 'suffix': path.suffix.lower(), 'size_bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest(), 'mtime_ns': path.stat().st_mtime_ns, 'encoding': encoding, 'encoding_fallback': fallback, 'evidence': evidence}
    entries, dependencies, symbol, issues = [], [], None, []
    if path.suffix.lower() == '.asy':
        attrs, pins, pin = {}, [], None
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if line.startswith('SYMATTR '):
                parts = line.split(None, 2)
                if len(parts) == 3: attrs[parts[1]] = parts[2]
            elif line.startswith('PIN '):
                pin = {'line': number}; pins.append(pin)
            elif line.startswith('PINATTR ') and pin is not None:
                parts = line.split(None, 2)
                if len(parts) == 3: pin[parts[1]] = parts[2]
        symbol = {'path': relative, 'attributes': attrs, 'pins': pins}
        for name in ('ModelFile', 'SpiceModel'):
            target = attrs.get(name, '')
            if Path(target.replace('\\', '/')).suffix.lower() in MODEL_SUFFIXES:
                dependencies.append({'path': relative, 'scope': '', 'line': 0, 'kind': 'symbol_file', 'target': target})
    elif path.suffix.lower() in MODEL_SUFFIXES:
        stack = []
        sections = {m.group(1).lower() for m in re.finditer(r'(?im)^\s*\.endl\s+(\S+)', text)}
        for number, line in logical_lines(text):
            parts = line.split()
            command = parts[0].lower()
            scope = '/'.join(stack)
            if command == '.subckt' and len(parts) >= 2:
                ports = []
                for token in parts[2:]:
                    if token.lower().startswith('params:') or '=' in token: break
                    ports.append(token)
                entries.append({'path': relative, 'line': number, 'name': parts[1], 'kind': 'subckt', 'scope': scope, 'ports': ports, 'model_type': '', 'declaration': line, 'top_level': not stack})
                stack.append(parts[1])
            elif command == '.ends':
                if not stack: issues.append({'line': number, 'detail': 'unmatched .ENDS'})
                else:
                    if len(parts) > 1 and parts[1].lower() != stack[-1].lower(): issues.append({'line': number, 'detail': '.ENDS name mismatch'})
                    stack.pop()
            elif command == '.model' and len(parts) >= 3:
                entries.append({'path': relative, 'line': number, 'name': parts[1], 'kind': 'model', 'scope': scope, 'ports': [], 'model_type': re.split(r'[\s(]', parts[2])[0], 'declaration': line, 'top_level': not stack})
            elif command in ('.include', '.inc', '.lib'):
                target = dependency_target(line[len(parts[0]):])
                if command == '.lib' and target.lower() in sections and len(parts) == 2: continue
                dependencies.append({'path': relative, 'scope': scope, 'line': number, 'kind': 'file', 'target': target})
            elif command.startswith('x'):
                body = parts[1:]
                end = next((i for i, token in enumerate(body) if '=' in token or token.lower().startswith('params:')), len(body))
                if end:
                    dependencies.append({'path': relative, 'scope': scope, 'line': number, 'kind': 'subckt_call', 'target': body[end - 1]})
        if stack: issues.append({'line': 0, 'detail': 'unclosed .SUBCKT: ' + '/'.join(stack)})
    return record, entries, dependencies, symbol, issues

def resolve_file(root, origin, target, by_lower, by_basename):
    normalized = target.replace('\\', '/')
    # Never open dependencies outside the requested library.
    candidate = (Path(origin).parent / normalized).as_posix()
    matches = by_lower.get(candidate.lower(), [])
    if not matches: matches = by_basename.get(PureWindowsPath(target).name.lower(), [])
    if len(matches) == 1: return 'resolved', matches
    return ('ambiguous' if matches else 'missing'), matches

def build_inventory(root, source_policy="all"):
    if source_policy not in ("all", "strict"): raise ValueError("Unknown source policy")
    paths = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink())
    files, entries, deps, symbols, issues = [], [], [], [], []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        f, es, ds, symbol, problems = parse_file(path, relative, path.read_bytes())
        files.append(f); entries.extend(es); deps.extend(ds)
        if symbol: symbols.append(symbol)
        issues.extend({'path': relative, **p} for p in problems)
    lower, basenames = {}, {}
    for f in files:
        lower.setdefault(f['path'].lower(), []).append(f['path'])
        basenames.setdefault(Path(f['path']).name.lower(), []).append(f['path'])
    subcircuits = {}
    for e in entries:
        if e['kind'] == 'subckt': subcircuits.setdefault(e['name'].lower(), []).append(e)
    for d in deps:
        if d['kind'] != 'subckt_call':
            d['status'], d['resolved'] = resolve_file(root, d['path'], d['target'], lower, basenames)
        else:
            if '{' in d['target'] or '}' in d['target']:
                d['status']='parameterized_call';d['resolved']=[];continue
            matches = subcircuits.get(d['target'].lower(), [])
            local = [e for e in matches if e['path'] == d['path'] and (not e['scope'] or d['scope']==e['scope'] or d['scope'].startswith(e['scope']+'/'))]
            accessible = {d['path']}
            pending = [d['path']]
            seen = set()
            # Follow included files without attributing unrelated global entries as resolved.
            while pending:
                current = pending.pop()
                if current in seen: continue
                seen.add(current)
                for other in deps:
                    if other['path'] == current and other['kind'] != 'subckt_call':
                        status, targets = resolve_file(root, current, other['target'], lower, basenames)
                        if status == 'resolved': accessible.update(targets); pending.extend(targets)
            if local:
                depth=max(len(e['scope'].split('/')) if e['scope'] else 0 for e in local)
                local=[e for e in local if (len(e['scope'].split('/')) if e['scope'] else 0)==depth]
            candidates = local or [e for e in matches if e['path'] in accessible and e['top_level']]
            d['status'] = 'resolved' if len(candidates) == 1 else 'ambiguous' if candidates else 'unresolved_call'
            d['resolved'] = [e['path'] + ':' + str(e['line']) for e in candidates]
    for symbol in symbols:
        attrs=symbol['attributes']
        names=attrs.get('Value2',attrs.get('Value','')).split()
        requested=names[0] if names else ''
        references=[d for d in deps if d['path']==symbol['path'] and d['kind']=='symbol_file']
        accessible={p for d in references if d['status']=='resolved' for p in d['resolved']}
        candidates=[e for e in entries if e['top_level'] and e['name'].lower()==requested.lower() and (not references or e['path'] in accessible)]
        symbol['binding']={'requested_entry':requested,'status':'resolved' if len(candidates)==1 else 'ambiguous' if candidates else 'not_resolved','entries':[e['path']+':'+str(e['line']) for e in candidates],'value_value2_difference':bool(attrs.get('Value') and attrs.get('Value2') and attrs['Value']!=attrs['Value2'])}
    file_map = {f['path']: f for f in files}
    for e in entries:
        f = file_map[e['path']]
        specific = [v for v in f['evidence'] if v['line'] == e['line']]
        e['source_evidence'] = specific or f['evidence'][:20]
        e['source_status'] = 'organization_or_rights_marker' if f['evidence'] else 'unverified'
        e['selection_status'] = 'excluded' if f['evidence'] else 'pending_review'
        e['selection_reason'] = 'strict_policy: organization/rights/manufacturer marker; requires provenance distinction' if f['evidence'] else 'no verified independent provenance; absence of a marker is not proof'
        e['entry_role'] = 'candidate_entry' if e['top_level'] else 'internal_definition'
        e['dependency_status'] = 'needs_review' if any(d['path'] == e['path'] and d['status'] != 'resolved' for d in deps) else 'declared_dependencies_resolved'
        e['parser_status'] = 'needs_review' if f['encoding_fallback'] or any(i['path'] == e['path'] for i in issues) else 'parsed_not_simulated'
    # Evaluate the complete declared include closure, preserving source evidence separately.
    for e in entries:
        reachable = {e['path']}; pending = [e['path']]
        while pending:
            current = pending.pop()
            for d in deps:
                if d['path'] == current and d['kind'] == 'file' and d['status'] == 'resolved':
                    for target in d['resolved']:
                        if target not in reachable: reachable.add(target); pending.append(target)
        e['dependency_status'] = 'needs_review' if any(d['path'] in reachable and d['status'] != 'resolved' for d in deps) else 'declared_dependencies_resolved'
        e['parser_status'] = 'needs_review' if any(file_map[p]['encoding_fallback'] for p in reachable) or any(i['path'] in reachable for i in issues) else 'parsed_not_simulated'
        if source_policy == 'all':
            e['selection_status'] = 'internal' if not e['top_level'] else 'searchable' if e['parser_status'] == 'parsed_not_simulated' and e['dependency_status'] == 'declared_dependencies_resolved' else 'needs_review'
            e['selection_reason'] = 'source-neutral index; static search eligibility only, simulation and datasheet fit required'
    return {'files': files, 'entries': entries, 'dependencies': deps, 'symbols': symbols, 'parse_issues': issues, 'source_policy': source_policy}

def write_csv(path, fields, records):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for record in records:
            writer.writerow({name: json.dumps(record.get(name), ensure_ascii=False) if isinstance(record.get(name), (dict, list)) else record.get(name) for name in fields})

def write_database(path, data):
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE files(path TEXT PRIMARY KEY,suffix TEXT,size_bytes INTEGER,sha256 TEXT,mtime_ns INTEGER,encoding TEXT,encoding_fallback INTEGER);
CREATE TABLE entries(id INTEGER PRIMARY KEY,path TEXT REFERENCES files(path),line INTEGER,name TEXT,kind TEXT,scope TEXT,model_type TEXT,ports_json TEXT,declaration TEXT,top_level INTEGER,entry_role TEXT,source_status TEXT,selection_status TEXT,selection_reason TEXT,dependency_status TEXT,parser_status TEXT);
CREATE INDEX entries_name ON entries(name COLLATE NOCASE); CREATE INDEX entries_type ON entries(model_type,kind); CREATE INDEX entries_selection ON entries(selection_status,top_level);
CREATE TABLE dependencies(id INTEGER PRIMARY KEY,path TEXT REFERENCES files(path),scope TEXT,line INTEGER,kind TEXT,target TEXT,status TEXT,resolved_json TEXT);
CREATE TABLE source_evidence(id INTEGER PRIMARY KEY,path TEXT REFERENCES files(path),line INTEGER,kind TEXT,text TEXT);
CREATE TABLE symbols(path TEXT PRIMARY KEY REFERENCES files(path),attributes_json TEXT,pins_json TEXT,binding_json TEXT);
CREATE TABLE parse_issues(path TEXT REFERENCES files(path),line INTEGER,detail TEXT);
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);''')
    for f in data['files']:
        db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?)', tuple(f[k] for k in ('path', 'suffix', 'size_bytes', 'sha256', 'mtime_ns', 'encoding', 'encoding_fallback')))
        db.executemany('INSERT INTO source_evidence(path,line,kind,text) VALUES(?,?,?,?)', [(f['path'], v['line'], v['kind'], v['text']) for v in f['evidence']])
    for e in data['entries']:
        db.execute('INSERT INTO entries(path,line,name,kind,scope,model_type,ports_json,declaration,top_level,entry_role,source_status,selection_status,selection_reason,dependency_status,parser_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (e['path'], e['line'], e['name'], e['kind'], e['scope'], e['model_type'], json.dumps(e['ports']), e['declaration'], e['top_level'], e['entry_role'], e['source_status'], e['selection_status'], e['selection_reason'], e['dependency_status'], e['parser_status']))
    for d in data['dependencies']: db.execute('INSERT INTO dependencies(path,scope,line,kind,target,status,resolved_json) VALUES(?,?,?,?,?,?,?)', (d['path'], d['scope'], d['line'], d['kind'], d['target'], d['status'], json.dumps(d['resolved'])))
    for s in data['symbols']: db.execute('INSERT INTO symbols VALUES(?,?,?,?)', (s['path'], json.dumps(s['attributes'], ensure_ascii=False), json.dumps(s['pins'], ensure_ascii=False),json.dumps(s['binding'],ensure_ascii=False)))
    for i in data['parse_issues']: db.execute('INSERT INTO parse_issues VALUES(?,?,?)', (i['path'], i['line'], i['detail']))
    db.execute('INSERT INTO metadata VALUES(?,?)', ('source_policy', data.get('source_policy', 'all'))); db.execute('INSERT INTO metadata VALUES(?,?)', ('version', VERSION)); db.commit(); db.close()

def report_text(summary):
    return f"""# 初始模板库基础索引报告

- 文件：{summary['files']}；符号：{summary['symbols']}。
- 入口及内部定义：{summary['entries']}；顶层入口：{summary['top_level_entries']}。
- 当前来源策略：{summary['source_policy']}；可检索候选：{summary['searchable_candidates']}；结构待核验：{summary['structural_review_candidates']}。
- 顶层入口：保守排除{summary['top_level_excluded']}，来源待核验{summary['top_level_pending_review']}，自动可选{summary['selectable']}。
- 依赖待核验：{summary['dependency_review']}；结构问题：{summary['parse_issues']}。
- 建库耗时：{summary['elapsed_seconds']}秒。

## 输出说明

SQLite保存文件哈希、入口及作用域、端口、符号关联、依赖和来源原文行号。
source_pending.csv包含待核验入口（包括内部定义）；entries.csv可按entry_role筛选顶层候选。
missing_dependencies.csv须按status区分参数化调用、歧义与缺失，不可将全部记录解释为缺失文件。

## 来源与范围

保守排除表示存在组织、版权、许可或厂商关联标记；不等于认定原作者是厂商或认定法律禁止使用。
没有来源标记也不证明独立来源。默认来源中立策略允许厂家及派生模型参与检索；来源保留为证据，不作为精度结论。strict 策略仅用于复现此前来源筛选。searchable 只代表静态条件满足，绝非拟合验收。
本次仅静态启发式解析；未调用大模型，未运行仿真，未修改原库，未接入模板推荐。
不证明模型完整性、引脚语义、物理适用性或拟合效果。
"""

def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('--root', type=Path, required=True); ap.add_argument('--output', type=Path, required=True); ap.add_argument("--source-policy", choices=["all", "strict"], default="all"); args = ap.parse_args()
    root, out = args.root.resolve(), args.output.resolve()
    if not root.is_dir(): ap.error('模板目录不存在')
    if out == root or root in out.parents: ap.error('索引输出不得放在模板库内')
    if out.exists() and any(out.iterdir()): ap.error('输出目录不为空，请指定新目录')
    start = time.monotonic(); data = build_inventory(root, args.source_policy); out.mkdir(parents=True, exist_ok=True)
    write_database(out / 'templates.sqlite', data)
    write_csv(out / 'entries.csv', ['path','line','name','kind','scope','entry_role','model_type','ports','selection_status','selection_reason','dependency_status','parser_status'], data['entries'])
    unresolved = [d for d in data['dependencies'] if d['status'] != 'resolved']
    write_csv(out / 'missing_dependencies.csv', ['path','scope','line','kind','target','status','resolved'], unresolved)
    write_csv(out / 'source_review.csv', ['path','line','name','entry_role','source_status','selection_status','selection_reason','source_evidence'], data['entries'])
    write_csv(out / 'source_pending.csv',['path','line','name','entry_role','selection_reason'],[e for e in data['entries'] if e['selection_status']=='pending_review'])
    write_csv(out / 'symbols.csv',['path','attributes','pins','binding'],data['symbols'])
    write_csv(out / 'parse_issues.csv', ['path','line','detail'], data['parse_issues'])
    write_csv(out / 'files.csv', ['path','suffix','size_bytes','sha256','mtime_ns','encoding','encoding_fallback'], data['files'])
    summary = {'version':VERSION,'source_policy':args.source_policy,'searchable_candidates':sum(e['selection_status']=='searchable' for e in data['entries']),'structural_review_candidates':sum(e['top_level'] and e['selection_status']=='needs_review' for e in data['entries']),'created_at':datetime.now(timezone.utc).isoformat(),'root':str(root),'elapsed_seconds':round(time.monotonic()-start,3),'files':len(data['files']),'entries':len(data['entries']),'top_level_entries':sum(e['top_level'] for e in data['entries']),'top_level_excluded':sum(e['top_level'] and e['selection_status']=='excluded' for e in data['entries']),'top_level_pending_review':sum(e['top_level'] and e['selection_status']=='pending_review' for e in data['entries']),'symbols':len(data['symbols']),'dependencies':len(data['dependencies']),'dependency_review':len(unresolved),'parse_issues':len(data['parse_issues']),'selectable':sum(e['selection_status']=='selectable' for e in data['entries']),'excluded':sum(e['selection_status']=='excluded' for e in data['entries']),'pending_review':sum(e['selection_status']=='pending_review' for e in data['entries']),'limitations':['Static heuristic parser; no simulation or provenance verification performed.','Rights and organization markers are retained; only strict policy excludes by source. They do not establish authorship or electrical accuracy.','Source policy is independent of electrical acceptance; searchable does not imply validated.','Subcircuit dependency lookup is case-insensitive; missing calls may require LTspice built-ins or manual scope resolution.']}
    pool=[{k:e[k] for k in ('path','line','name','kind','model_type','ports','source_status','dependency_status','parser_status')} for e in data['entries'] if e['selection_status']=='searchable']
    (out/'candidate_pool.json').write_text(json.dumps({'source_policy':args.source_policy,'status':'static_retrieval_pool','electrical_acceptance':'not_evaluated','entries':pool},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'report.md').write_text(report_text(summary),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2)); print('输出：', out)
if __name__ == '__main__': main()
