#!/usr/bin/env python3
"""Evidence-backed static functional taxonomy; classification is not electrical validation."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from .index_templates import decode

PRIMITIVES={'D':('diode','unspecified'),'NMOS':('mosfet','n_channel'),'PMOS':('mosfet','p_channel'),'VDMOS':('mosfet','vdmos_polarity_unspecified'),'NPN':('bjt','npn'),'PNP':('bjt','pnp'),'NJF':('jfet','n_channel'),'PJF':('jfet','p_channel'),'C':('capacitor','primitive'),'L':('inductor','primitive'),'R':('resistor','primitive')}
CATEGORIES=list(dict.fromkeys(v[0] for v in PRIMITIVES.values()))+['opamp','current_sensor','gate_driver','analog_switch','transformer','thermal_network','voltage_regulator','optocoupler','unknown']
HEADER_RULES=[('mosfet',r'\bMOSFET\b'),('diode',r'\b(?:schottky|zener|rectifier|LED)\b|二极管'),('opamp',r'\bop(?:erational)?[ -]?amp(?:lifier)?s?\b|operational amplifier|运算放大器'),('current_sensor',r'current sensor|hall[ -]?effect|电流传感器'),('gate_driver',r'gate driver|half.bridge driver|high.side.*driver|栅极驱动'),('analog_switch',r'analog switch|multiplexer|模拟开关'),('voltage_regulator',r'voltage regulator|DC.DC converter|LDO regulator'),('optocoupler',r'optocoupler|opto.isolator'),('inductor',r'\binductor\b|ferrite bead|common.mode choke|电感'),('resistor',r'\bresistor\b|电阻'),('transformer',r'\btransformer\b|变压器'),('capacitor',r'\bcapacitor\b|电容器'),('thermal_network',r'\bthermal (?:network|model)\b')]

def numeric_parameter(raw):
    m=re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmunpf]?)(?:[A-Za-z]*)',raw,re.I)
    if not m:return None
    factors={'':1,'t':1e12,'g':1e9,'meg':1e6,'k':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'p':1e-12,'f':1e-15}
    return float(m.group(1))*factors[m.group(2).lower()]

UNITS={'BV':'V','VDS':'V','RON':'ohm','VTO':'V','CJO':'F','CGS':'F','CGDMAX':'F','TT':'s','TNOM':'degC','EG':'eV','XTI':'dimensionless'}

def classify(entry, lines, internal=(), symbols=()):
    pins=json.loads(entry['ports_json']);start=entry['line'];evidence=[]
    # Keep entry-specific leading comments: file header only for the first entry.
    header=[]
    i=start-2
    while i>=0 and (not lines[i].strip() or lines[i].lstrip().startswith('*')):
        if lines[i].lstrip().startswith('*'):header.append((i+1,lines[i].strip()))
        i-=1
    header=list(reversed(header))
    body=[];depth=0
    for number in range(start-1,len(lines)):
        text=lines[number].strip();body.append((number+1,text))
        if re.match(r'(?i)^\.subckt\b',text):depth+=1
        if re.match(r'(?i)^\.ends\b',text):
            depth-=1
            if depth==0:break
        if entry['kind']=='model':
            if number+1>=len(lines) or not lines[number+1].lstrip().startswith('+'):break
    category,subtype,confidence='unknown','unspecified','unknown'
    native=entry['model_type'].upper()
    if entry['kind']=='model' and native in PRIMITIVES:
        category,subtype=PRIMITIVES[native];confidence='declared';evidence.append({'line':start,'text':entry['declaration'],'basis':'native_model_type'})
        if native=='VDMOS' and re.search(r'(?i)\bpchan\b',entry['declaration']):subtype='p_channel'
    else:
        matched={cat for cat,pattern in HEADER_RULES if any(re.search(pattern,t,re.I) for _,t in header)}
        if len(matched)==1:
            category=matched.pop();confidence='documented_hint'
            evidence += [{'line':n,'text':t,'basis':'entry_header'} for n,t in header if any(re.search(p,t,re.I) for c,p in HEADER_RULES if c==category)][:5]
        elif len(matched)>1:
            evidence.append({'line':start,'basis':'conflicting_header','categories':sorted(matched)})
        # Require external port semantics as well as MOS core; do not classify ICs by internals alone.
        elif len(pins)==3 and {p.upper() for p in pins} in ({'D','G','S'},{'DRAIN','GATE','SOURCE'}):
            types={r['model_type'].upper() for r in internal if r['scope']==entry['name'] or r['scope'].startswith(entry['name']+'/')}
            if types & {'NMOS','PMOS','VDMOS'}:
                category='mosfet';confidence='topology_hint'
                subtype='n_channel' if types & {'NMOS','PMOS'}=={'NMOS'} else 'p_channel' if types & {'NMOS','PMOS'}=={'PMOS'} else 'polarity_needs_review'
                evidence.append({'line':start,'basis':'external_DGS_ports_and_core','ports':pins,'core_types':sorted(types)})
        elif re.match(r'(?i)^(ACS\d+|A1366)',entry['name']) and any(p.upper().startswith('IP+') or 'FIELD' in p.upper() for p in pins):
            category='current_sensor';confidence='identity_and_ports_hint';evidence.append({'line':start,'basis':'sensor_name_and_measurement_ports','ports':pins})
    if category=='unknown' and not evidence:
        claims={cat for cat,pattern in HEADER_RULES if any(re.search(pattern,s['description'],re.I) for s in symbols)}
        if len(claims)==1:
            category=claims.pop();confidence='documented_hint'
            evidence += [{'path':s['path'],'text':s['description'][:1200],'basis':'uniquely_bound_symbol_description'} for s in symbols if any(re.search(p,s['description'],re.I) for c,p in HEADER_RULES if c==category)][:5]
    if category=='mosfet'  and subtype=='unspecified':
        if any(re.search(r'(?i)\bN[- ]Channel\b',t) for _,t in header):subtype='n_channel'
        elif any(re.search(r'(?i)\bP[- ]Channel\b',t) for _,t in header):subtype='p_channel'
    if category=='diode':
        for label in ('schottky','zener','rectifier','led'):
            if any(re.search(r'(?i)\b'+label+r'\b',t) for _,t in header) or any(re.search(r'(?i)\b'+label+r'\b',x['description']) for x in symbols):subtype=label;break
    if category=='current_sensor':subtype='current_sensor_unspecified'
    if category=='opamp':subtype='opamp_macro_model'
    if category=='gate_driver':subtype='gate_driver_unspecified'
    tags=[]
    for tag,pattern in [('sic',r'\bSiC\b|silicon carbide'),('gan',r'\bGaN\b|gallium nitride'),('schottky',r'\bschottky\b'),('dual',r'\bdual\b|两个MOSFET')]:
        for n,t in header:
            if re.search(pattern,t,re.I):tags.append({'tag':tag,'confidence':'documented_hint','line':n,'text':t});break
    specs=[]
    # Store model parameters as model parameters, never guaranteed datasheet ratings.
    parameters=('BV','VDS','RON','VTO','CJO','CGS','CGDMAX','TT','TNOM','EG','XTI')
    for n,t in body:
        if not t.upper().startswith(('.MODEL','+')):continue
        for param in parameters:
            m=re.search(r'(?i)\b'+param+r'\s*=\s*([^\s,)]+)',t)
            if m:specs.append({'key':param,'raw_value':m.group(1),'value_si':numeric_parameter(m.group(1)),'unit':UNITS[param],'meaning':'model_parameter_not_datasheet_guarantee','line':n})
    for n,t in header:
        if re.search(r'(?i)ratings?|rated|maximum|typical',t):specs.append({'key':'header_specification_claim','raw_value':t,'meaning':'source_header_claim_not_verified_against_datasheet','line':n})
    capabilities=[]
    checks=[('behavioral_sources',r'^[BEG]\S*\s'),('explicit_capacitor',r'^C\S*\s'),('explicit_inductor',r'^L\S*\s'),('diode_branch',r'^D\S*\s'),('explicit_temperature_expression',r'\btemp\b|\bTC(?:1|2)?\s*='),('thermal_nodes',r'junction.temperature|j-a-temp|thermal')]
    for cap,pattern in checks:
        hit=next(((n,t) for n,t in body if not t.startswith('*') and re.search(pattern,t,re.I)),None)
        if hit:capabilities.append({'capability':cap,'status':'structural_hint_not_validated','line':hit[0],'text':hit[1][:300]})
    if entry['kind']=='model' and category!='unknown':capabilities.append({'capability':'native_'+native.lower(),'status':'declared_not_validated','line':start})
    if category in ('mosfet','diode','bjt','jfet'):capabilities.append({'capability':'static_iv_core','status':'category_hint_not_validated','line':start})
    if any(s['key']=='TT' for s in specs):capabilities.append({'capability':'transit_time_parameter','status':'parameter_present_not_validated'})
    return {'category':category,'subcategory':subtype,'confidence':confidence,'evidence':evidence,'tags':tags,'specifications':specs,'capabilities':capabilities,'electrical_acceptance':'not_evaluated'}

def enrich(index,root,out):
    root=root.resolve();out=out.resolve()
    if out==root or root in out.parents:raise ValueError('输出不得在原模板库中')
    if out.exists() and any(out.iterdir()):raise ValueError('输出目录必须为空')
    out.mkdir(parents=True,exist_ok=True);shutil.copy2(index,out/'templates.sqlite')
    db=sqlite3.connect(out/'templates.sqlite');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE taxonomy(entry_id INTEGER PRIMARY KEY,category TEXT,subcategory TEXT,confidence TEXT,details_json TEXT); CREATE INDEX taxonomy_category ON taxonomy(category,subcategory,confidence); CREATE TABLE taxonomy_tags(entry_id INTEGER,tag TEXT); CREATE INDEX taxonomy_tag ON taxonomy_tags(tag); CREATE TABLE taxonomy_capabilities(entry_id INTEGER,capability TEXT); CREATE INDEX taxonomy_capability ON taxonomy_capabilities(capability);')
    texts={};internal={};records=[];symbol_map={}
    for s in db.execute('select path,attributes_json,binding_json from symbols'):
        binding=json.loads(s['binding_json']);attrs=json.loads(s['attributes_json'])
        if binding.get('status')=='resolved' and attrs.get('Description'):
            for key in binding['entries']:symbol_map.setdefault(key,[]).append({'path':s['path'],'description':attrs['Description']})
    for r in db.execute('select * from entries where top_level=0'):internal.setdefault(r['path'],[]).append(dict(r))
    for f in db.execute('select path,sha256 from files'):
        p=(root/f['path']).resolve()
        if root not in p.parents:raise ValueError('索引路径越界')
        b=p.read_bytes()
        if hashlib.sha256(b).hexdigest()!=f['sha256']:raise ValueError('原文件哈希变化：'+f['path'])
        if p.suffix.lower()!='.asy':texts[f['path']]=decode(b)[0].splitlines()
    for row in db.execute('select * from entries where top_level=1'):
        e=dict(row);details=classify(e,texts[e['path']],internal.get(e['path'],[]),symbol_map.get(e['path']+':'+str(e['line']),[]))
        db.execute('insert into taxonomy values(?,?,?,?,?)',(e['id'],details['category'],details['subcategory'],details['confidence'],json.dumps(details,ensure_ascii=False)))
        db.executemany('insert into taxonomy_tags values(?,?)',[(e['id'],t['tag']) for t in details['tags']]);db.executemany('insert into taxonomy_capabilities values(?,?)',[(e['id'],t['capability']) for t in details['capabilities']])
        records.append({'path':e['path'],'line':e['line'],'entry':e['name'],'selection_status':e['selection_status'],**details})
    db.execute("INSERT OR REPLACE INTO metadata VALUES('taxonomy_version','1.0')");db.commit()
    summary={'entries':len(records),'categories':dict(Counter(r['category'] for r in records)),'confidence':dict(Counter(r['confidence'] for r in records)),'integrity_check':db.execute('pragma integrity_check').fetchone()[0],'electrical_acceptance':'not_evaluated'};db.close()
    (out/'unknown_queue.json').write_text(json.dumps([{'path':r['path'],'line':r['line'],'entry':r['entry'],'reason':'insufficient_functional_evidence','next_step':'bounded source/port review; no automatic guess'} for r in records if r['category']=='unknown'],ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'classification.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'classification.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['path','line','entry','selection_status','category','subcategory','confidence','evidence','tags','specifications','capabilities']);w.writeheader()
        for r in records:w.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],list) else r[k] for k in w.fieldnames})
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'report.md').write_text('# 模板分层分类\n\n'+ '\n'.join('- '+k+'：'+str(v) for k,v in summary['categories'].items())+'\n\n分类证据和置信度见 classification.csv。参数标签是模型声明，不是数据手册保证值。能力标签只表示结构存在，不表示已通过该特性验证。未知分类保留；未调用API或仿真，未修改原模板。\n',encoding='utf-8')
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--index',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(enrich(a.index,a.root,a.output),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
