"""Verified local dependency closure and record-specific reference interface selection."""
import copy,re
from pathlib import Path
from .state import Fault,read,digest,save
from .spice import model_text,validate_model


def library_root():
    return Path(__file__).resolve().parents[1]/'template_assets/library'


def closed_block(text,entry,dependencies=None):
    dependencies=dependencies or library_root()
    m=re.search(r'(?ims)^\s*\.subckt\s+'+re.escape(entry)+r'\s+.*?^\s*\.ends(?:\s+[^\r\n]+)?',text)
    if not m:raise Fault('model_interface_gap','入口不存在：'+entry)
    block=m.group(0);receipts=[];extra=[]
    pattern=r'(?im)^\s*\.(?:lib|include|inc)\s+["\']?([^"\'\r\n]+?)["\']?\s*$'
    for name in re.findall(pattern,block):
        name=name.strip()
        if name not in ('opamp.sub','UniversalOPAmps2.sub'):
            raise Fault('model_dependency_gap','依赖不在已打包白名单：'+name)
        p=dependencies/name
        if not p.is_file():raise Fault('model_dependency_gap','缺少本地依赖：'+name)
        content=model_text(p)
        if re.search(r'(?im)^\s*\.(?:lib|include|inc)\b',content):raise Fault('model_dependency_gap','嵌套依赖尚未闭合：'+name)
        extra.append(content);receipts.append({'name':name,'sha256':digest(p)})
    block=re.sub(pattern,'',block)
    block=re.sub(r'(?im)^\s*\.backanno\s*$','',block)
    return block+'\n'+'\n'.join(extra),receipts


def select_draft(root,packet,item=None):
    if not packet.get('fixture_drafts'):return None
    cases=read(root/packet['fixture_drafts']).get('cases',[])
    models={c['model']['sha256']:c['model'] for c in cases}
    if len(models)==1:return copy.deepcopy(next(iter(models.values())))
    if not models:return None
    # Prefer the exact declared case binding, never guess from model file order.
    exact=[c for c in cases if item and item['id'] in c.get('reference_ids',[])]
    options={c['model']['sha256']:c['model'] for c in exact}
    if len(options)==1:return copy.deepcopy(next(iter(options.values())))
    if item:
        temps=[float(x) for x in re.findall(r'(-?\d+(?:\.\d+)?)\s*°C',item.get('reference_evidence',{}).get('conditions',''))]
        if len(set(temps))==1:
            chosen={c['model']['sha256']:c['model'] for c in cases if c['protocol']['temperature_C']==temps[0]}
            if len(chosen)==1:return copy.deepcopy(next(iter(chosen.values())))
        raise Fault('model_interface_gap','本记录需多个温度或独立参考接口，不能用单一温度替代',{'reference_id':item['id'],'available_models':list(models)})
    # Preflight lists all variants; a default fixture is not authority for other records.
    chosen=next(iter(models.values()));return copy.deepcopy(chosen)


def model_parameters(root,packet,entry):
    if entry.upper()!='ACS723':return {},[]
    inv=read(root/packet['inventory']);device=packet['device']
    matches=[i for i in inv['items'] if i.get('applicable',True) and i.get('reference_evidence',{}).get('key')=='sensitivity' and device in i['reference_evidence'].get('applies_to',[])]
    if len(matches)!=1:raise Fault('model_parameter_gap','必须唯一绑定所选型号的灵敏度')
    r=matches[0]['reference_evidence'];unit=r['unit'];v=r['values'].get('typ')
    if unit!='mV/A' or not isinstance(v,(int,float)):raise Fault('model_parameter_gap','灵敏度单位/值未绑定')
    symbol=library_root()/'ACS723.asy'
    if not symbol.is_file() or not re.search(r'(?i)Polarity=2\b',model_text(symbol)) or '-20AB-' not in device:
        raise Fault('model_parameter_gap','双向极性尚无可核验的型号/符号证据')
    # These configure the benchmark, not a candidate or independent fitted result.
    return {'Sensitivity':v*1e-3,'Polarity':2},[{'reference_id':matches[0]['id'],'unit':unit,'value':v,'symbol_sha256':digest(symbol),'role':'reference_benchmark_configuration'}]


PROTOCOL_GUIDE={
 'field_types':{'device_nodes':'object mapping every semantic port to a string node','components':'array of component objects, never an object keyed by component names','analysis':'one object, never an array','measurement':'object','models':'object mapping model names to parameter objects; use {} when empty, never []','checks':'array of check objects'},
 'models_example':{'DCLAMP':{'is':1e-14,'n':1,'rs':.1}},
 'top_level_keys':['temperature_C','device_nodes','components','analysis','measurement','checks','models','method'],
 'source_example':{'kind':'V','name':'VDRIVE','nodes':['P','0'],'value':{'dc':0,'ac':1}},
 'current_example':{'kind':'I','name':'IFORCE','nodes':['0','P'],'value':{'pwl':[[0,0],[1e-6,.01],[2e-6,.01]]}},
 'analyses':[{'kind':'dc','source':'VDRIVE','start':0,'stop':1,'step':.01},{'kind':'ac','frequency_Hz':1000},{'kind':'tran','stop_s':2e-6,'max_step_s':1e-9}],
 'sample_example':{'mode':'sample','signal':'i(vdrive)','at':.5,'sign':-1,'scale':1e9},
 'rules':['Example values are syntax examples only; actual conditions must come from the supplied manual.','No extra top-level fields such as netlist, parameters, sources, nodes or description.','All DUT semantic ports must be present in device_nodes.','Model numerical mismatch does not invalidate a correct measurement fixture.','Future calibration/DUT outputs are not required during pre-trial review.','Supply/internal subcircuit port numbers are not physical package pin numbers.']}


def interface_evidence(workflow,item,protocol=None):
    from .fixture_repair_policy import program_facts
    model=workflow.task['model'];text=model_text(workflow.model_path)
    result={'protocol_schema':PROTOCOL_GUIDE,'model_provenance':model.get('provenance',{}),'full_model_text':text[:60000],
            'physical_ports':item.get('supplied_ports',{}),'required_device_nodes_keys':model['ports'],'stage_contract':{'pre_trial':'Verify conditions, wiring and measurement to authorize actual trials. Do not require future trial results or electrical acceptance.', 'post_trial':'Verify actual independent calibration, bias and measurement. Separate model numerical failure from method failure.'}}
    if protocol:result['program_facts']=program_facts(protocol,model,text,item.get('supplied_ports'))
    return result
