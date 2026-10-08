#!/usr/bin/env python3
"""Verify registered real evidence and materialize tasks for the unified entrypoint."""
import argparse,copy,json
from pathlib import Path
from flow_runtime.state import digest,save,fingerprint,Fault,artifacts_valid
from flow_runtime.spice import acceptance
from flow_runtime.task import load_task
from flow_runtime.project_standard import expectation_with_standard
ROOT=Path(__file__).resolve().parent

def records(include_families=False):
 current=json.loads((ROOT/'gpt_test_library/cases.json').read_text())['cases']
 if include_families:
  for name in ('family_cases.json','extended_family_cases.json','sensor_family_cases.json'):current+=json.loads((ROOT/'gpt_test_library'/name).read_text())['cases']
 lookup={(c['device'],c['id']):c for c in current};out={}
 folders=[ROOT/'runs/gpt_correction/qualified_library',ROOT/'runs/acs_parameter_scope/qualified_library']
 if include_families:
  for name in ('family_qualification_corrected','static_family_qualification','sensor_family_qualification'):folders.append(ROOT/'runs'/name/'qualified_library')
 for folder in folders:
  for path in folder.glob('*.json'):
   if digest(path)!=path.with_suffix('.sha256').read_text().strip():raise Fault('cache_corrupt','Library receipt changed')
   record=json.loads(path.read_text());old=record['case'];key=(old['device'],old['id']);case=lookup[key]
   if fingerprint(old['protocol'])!=fingerprint(case['protocol']) or old['model']['sha256']!=case['model']['sha256']:raise Fault('cache_corrupt','Registered fixture or model changed')
   receipt=record['receipt']
   if len(receipt['calibrations'])!=2:raise Fault('calibration','Two independent calibrations required')
   for cal in receipt['calibrations']:
    if not artifacts_valid(Path(cal['folder']),cal['artifacts']) or cal['error']>cal['tolerance']:raise Fault('cache_corrupt','Calibration evidence invalid')
   trial=receipt['trial']
   if not artifacts_valid(Path(trial['folder']),trial['artifacts']):raise Fault('cache_corrupt','Real trial evidence invalid')
   if digest(case['model']['path'])!=case['model']['sha256']:raise Fault('cache_corrupt','Model changed')
   revised=copy.deepcopy(record);revised['case']=case
   revised['receipt']['trial']['acceptance']=acceptance(trial['result'],case['expectation'])
   if old['expectation']!=case['expectation']:revised['acceptance_revision']={'reason':'Apply frozen project 10% tolerance and explicit measured quantity; original simulation unchanged','old':old['expectation'],'new':case['expectation'],'new_simulations':0}
   out[key]=revised
 return list(out.values())

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--families',action='store_true');a=ap.parse_args()
 verified=records(a.families);ledger=ROOT/'gpt_test_library/qualified';ledger.mkdir(exist_ok=True)
 for record in verified:
  c=record['case'];p=ledger/(fingerprint({'device':c['device'],'id':c['id']})+'.json');save(p,record);p.with_suffix('.sha256').write_text(digest(p)+'\n')
 grouped={}
 for record in verified:
  c=record['case'];grouped.setdefault((c['device'],c['model']['sha256']),[]).append(c)
 tasks=[]
 for (device,model_sha),cases in grouped.items():
  packet_root=ROOT/'six_inputs'/cases[0]['input_folder'];packet=json.loads((packet_root/'device_input.json').read_text());inventory=json.loads((packet_root/'inventory.json').read_text());std=packet['acceptance_standard']
  for source in inventory.get('sources',[]):source['path']=str((packet_root/source['path']).resolve())
  for i in inventory['items']:
   i['bindings']=[c['id'] for c in cases if i['id'] in c['reference_ids']];i['binding_complete']=False
   if i['bindings']:i['method_registration']='GPT-corrected, real calibrated, exact-condition trial; device coverage still pending'
  active=[]
  for c in cases:
   c=copy.deepcopy(c);c['expectation']=expectation_with_standard(c['expectation'],std,bool(c.get('reference')));active.append(c)
  task={'schema_version':'flow-1','device':device,'model':copy.deepcopy(cases[0]['model']),'cases':active,'inventory':inventory,'runner':{'argv':['/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice','-b','-ascii','{circuit}'],'timeout_seconds':120},'routes':packet['routes'],'budgets':{'api_calls':0,'simulations':len(cases)*2,'repairs':0,'seconds':1800},'policy':{'workers':1},'acceptance_standard':std,'input_integration':{'schema':'unified-device-input-1','execution_scope':'configured_regression_only','model_fitting_ready':False},'limitations':['Only registered exact conditions; remaining inventory and thermal/statistical applicability remain open.','Reference interfaces are method benchmarks, not automatically candidate model delivery.'],'gpt_library_receipts':[str(ledger/(fingerprint({'device':c['device'],'id':c['id']})+'.json')) for c in cases]}
  path=a.output/device/model_sha[:12]/'task.json';save(path,task);load_task(path);tasks.append({'device':device,'tests':len(cases),'task':str(path.resolve())})
 save(a.output/'batch.json',{'schema':'gpt-registered-regression-batch-1','tasks':tasks,'qualified_methods':len(verified),'full_manual_coverage':False})
 print(json.dumps({'qualified_methods':len(verified),'tasks':tasks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
