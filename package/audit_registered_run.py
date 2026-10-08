#!/usr/bin/env python3
"""Reconcile a real unified run with today's registered exact-condition library."""
import argparse
from pathlib import Path
from collections import Counter
from flow_runtime.state import read,save,Fault,fingerprint,artifacts_valid,digest
from flow_runtime.registered_library import verified_records
from flow_runtime.spice import raw_data,measure,render,load_reference,acceptance
ROOT=Path(__file__).resolve().parent

def audit(runtime,output,devices):
 library=list(verified_records(ROOT/'gpt_test_library'));batch=read(runtime/'summary.json');lookup={};allrows=[]
 for group in batch['results']:
  for row in read(group['summary'])['results']:
   key=(group['device'],row['model_sha256'],row['test']);lookup[key]=row;allrows.append(key)
 verified=[]
 for record,proof in library:
  c=record['case']
  if devices and c['device'] not in devices:continue
  key=(c['device'],c['model']['sha256'],c['id']);row=lookup.get(key)
  if not row or row.get('execution')!='completed' or row['protocol_sha256']!=fingerprint(c['protocol']):raise Fault('coverage_execution','Current method not completed in real unified run',{'case':key})
  folder=Path(row['artifacts'])
  ex=read(folder/'execution.json')
  if not artifacts_valid(folder,row['artifact_hashes']) or ex.get('test_backend') or ex.get('returncode')!=0 or digest(folder/'model.lib')!=c['model']['sha256']:raise Fault('cache_corrupt','Invalid unified real evidence',{'case':key})
  if (folder/'test.cir').read_text()!=render(c['protocol'],c['model']):raise Fault('coverage_protocol','Actual unified circuit differs')
  ref=load_reference(c['reference']['path'],c['expectation']['unit'],c['reference'].get('condition')) if c.get('reference') else None
  data=raw_data(folder/'test.raw')
  if not data['complete']:raise Fault('coverage_execution','Incomplete unified RAW')
  measured=measure(c['protocol'],data,ref)
  if any(row.get(k)!=v for k,v in measured.items()):raise Fault('coverage_result','Unified values differ from replayed waveform')
  verified.append({'device':c['device'],'id':c['id'],'protocol_sha256':row['protocol_sha256'],'model_sha256':row['model_sha256'],'artifacts':str(folder),'artifact_hashes':row['artifact_hashes'],'acceptance':acceptance(measured,c['expectation']),'handbook_registration_allowed':c.get('coverage_registration_allowed',True)})
 keys={(r['device'],r['model_sha256'],r['id']) for r in verified}
 excluded=[{'device':d,'model_sha256':sha,'id':id,'reason':'Historical test not in current registered library'} for d,sha,id in allrows if (not devices or d in devices) and (d,sha,id) not in keys]
 result={'status':'current_registered_methods_verified_in_unified_run','current_registered_tests':len(verified),'historical_tests':len(allrows),'excluded_historical_tests':excluded,'results':verified,'device_tests':dict(Counter(r['device'] for r in verified)),'api_calls':0,'new_simulations':0,'full_manual_coverage':False,'device_delivery_claim':False}
 save(output,result);print(result['status'],len(verified),'current methods;',len(excluded),'historical exclusions')
 return result
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--devices',nargs='+');a=ap.parse_args();audit(a.runtime,a.output,a.devices)
