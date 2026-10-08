#!/usr/bin/env python3
"""Validate GPT-authored exact-condition tests and register evidence-backed recipes."""
import argparse,copy,csv,json,math,re
from pathlib import Path
from flow_runtime.state import Store,Fault,digest,fingerprint,save,file_lock
from flow_runtime.spice import Simulator,validate_protocol,validate_model,model_text,measure,acceptance,render
from flow_runtime.capability_development import sample_oracles
from flow_runtime.workflow import calibration_protocols
ROOT=Path(__file__).resolve().parent

def reference(case):
 if not case.get('reference'):return None
 p=Path(case['reference']['path'])
 if digest(p)!=case['reference']['sha256']:raise Fault('cache_corrupt','Reference hash differs')
 with p.open() as f:return [(float(r['x']),float(r['y'])) for r in csv.DictReader(f)]

def oracles(case):
 p=case['protocol'];m=p['measurement']
 if m['mode']=='sample':return [(q,e,None) for q,e in sample_oracles(p)]
 if m['mode']=='curve':
  a=p['analysis'];name=a['source'];signal=m['signal'].lower()
  if a['kind']!='dc' or signal!='i('+name.lower()+')' or not name.startswith('V'):raise Fault('capability','Curve calibration unavailable')
  out=[]
  for resistance in (1000,2000):
   q=copy.deepcopy(p);q['device_nodes']={};q['checks']=[]
   q['components']=[{'kind':'R','name':'RCAL','nodes':['P','0'],'value':resistance},{'kind':'V','name':name,'nodes':['P','0'],'value':{'dc':0}}]
   xs=[a['start'],(a['start']+a['stop'])/2,a['stop']]
   expected=[(x,-x/resistance*m.get('sign',1)*m.get('scale',1)) for x in xs]
   out.append((q,0,expected))
  return out
 result=[]
 for q,e in calibration_protocols(case):
  q=copy.deepcopy(q);scale=m.get('scale',1);q['measurement']['scale']=scale
  result.append((q,e*scale,None))
 return result

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--resume',action='store_true');ap.add_argument('--check-only',action='store_true');ap.add_argument('--device')
 a=ap.parse_args();catalog=ROOT/'gpt_test_library/cases.json';cases=json.loads(catalog.read_text())['cases'];cases=[c for c in cases if not a.device or c['device']==a.device]
 if not cases:raise ValueError('No matching device')
 runner=Path('/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice')
 for c in cases:
  if digest(c['model']['path'])!=c['model']['sha256']:raise Fault('cache_corrupt','Model hash differs')
  validate_model(model_text(c['model']['path']),c['model']);validate_protocol(c['protocol'],c['model']);reference(c)
  for p,e,ref in oracles(c):validate_protocol(p,c['model'])
 if a.check_only:
  print('Prepared',len(cases),'cases; no API or simulation');return
 limits={'api_calls':0,'simulations':len(cases)*3+10,'repairs':0,'seconds':3600}
 identity=fingerprint({'catalog':digest(catalog),'launcher':digest(Path(__file__)),'code':{p.name:digest(p) for p in (ROOT/'flow_runtime').glob('*.py')},'runner':digest(runner),'device':a.device})
 with file_lock(a.output/'.workflow.lock'):
  store=Store(a.output,identity,limits,a.resume);rows=[];registry=a.output/'qualified_library';registry.mkdir(exist_ok=True)
  for c in cases:
   key=c['device']+'/'+c['id'];row={'device':c['device'],'id':c['id'],'reference_ids':c['reference_ids'],'scope':c['scope'],'authorship':c['authorship'],'model_role':c['model'].get('provenance',{}).get('role','existing_engineering_candidate'),'model_sha256':c['model']['sha256'],'calibrations':[]}
   print('GPT corrected:',key,flush=True)
   sim=Simulator(store,{'argv':[str(runner),'-b','-ascii','{circuit}'],'timeout_seconds':120},c['model'])
   try:
    for index,(p,e,ref) in enumerate(oracles(c),1):
     data,folder=sim.run(p,Path(c['model']['path']),'cal_'+c['id']+'_'+str(index));result=measure(p,data,ref)
     error=result['metrics']['max_absolute_error'] if ref else abs(result['value']-e)
     tol=max(abs(e)*1e-4,1e-10) if not ref else 1e-9
     row['calibrations'].append({'expected':e,'result':result,'error':error,'tolerance':tol,'folder':str(folder),'artifacts':store.get('simulations',folder.name)['hashes']})
     if error>tol:raise Fault('calibration','Independent calibration failed',row['calibrations'][-1])
    data,folder=sim.run(c['protocol'],Path(c['model']['path']),c['id']);result=measure(c['protocol'],data,reference(c))
    row.update(status='method_qualified_exact_conditions',trial={'result':result,'acceptance':acceptance(result,c['expectation']),'folder':str(folder),'artifacts':store.get('simulations',folder.name)['hashes']},protocol_sha256=fingerprint(c['protocol']))
    record={'schema':'gpt-corrected-qualified-method-1','case':c,'receipt':row,'delivery_claim':False,'required_before_other_models':'repeat calibration and full active regression','qualification_backend':'real_LTspice','scope_complete':False}
    path=registry/(fingerprint({'device':c['device'],'id':c['id']})+'.json');save(path,record);path.with_suffix('.sha256').write_text(digest(path)+'\n')
   except Fault as e:row.update(status='blocked',fault=e.record())
   rows.append(row);report={'status':'gpt_correction_completed_with_gaps','results':rows,'qualified_methods':sum(r['status']=='method_qualified_exact_conditions' for r in rows),'api_calls':0,'usage':store.data['usage'],'full_manual_coverage':False,'candidate_models_modified':False}
   save(a.output/'summary.json',report);print(row['status'],row.get('trial',{}).get('acceptance',row.get('fault',{}).get('message')),flush=True)
  grouped={}
  for c,row in zip(cases,rows):
   if row['status']=='method_qualified_exact_conditions':grouped.setdefault(c['device'],[]).append(c)
  for device,active in grouped.items():save(a.output/'active_tests'/device/'cases.json',{'device':device,'cases':active,'full_manual_coverage':False,'origin':'GPT authored and real calibrated; registered for exact-condition regression'})
  store.finish(report['status']);print('Report:',a.output/'summary.json')
if __name__=='__main__':main()
