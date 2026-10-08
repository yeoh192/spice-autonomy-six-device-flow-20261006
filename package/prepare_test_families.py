#!/usr/bin/env python3
"""Map ACS/ADA records and materialize available family generators."""
import argparse,json
from pathlib import Path
from flow_runtime.state import read,save,digest
from flow_runtime.test_families import mapping,generate
ROOT=Path(__file__).resolve().parent

def prepare(output):
 catalog=read(ROOT/'gpt_test_library/cases.json')['cases'];allcases=[];devices=[]
 for folder,device in [('ACS723','ACS723KMATR-20AB-T'),('ADA4528','ADA4528-1_MSOP')]:
  path=ROOT/'six_inputs'/folder/'inventory.json';inventory=read(path);rows=mapping(inventory,device);base=next(c for c in catalog if c['device']==device);cases=[]
  for item in inventory['items']:
   rid=item['id']
   if item['kind']!='test':continue
   if device.startswith('ACS') and rid=='parameter:20AB_zero':cases.append(generate(item,base,5))
   if device.startswith('ADA') and (rid=='parameter:icc_2.5' or (rid.startswith(('parameter:voh_','parameter:vol_')) and '_full' not in rid)):cases.append(generate(item,base))
  overlay=ROOT/'runs/registered_final_coverage'/device/'manual_inventory.json'
  if overlay.exists():
   lookup={i['id']:i for i in read(overlay)['items']}
   for row in rows:row['registered_exact_point_methods']=lookup[row['reference_id']].get('method_bindings',[])
  save(output/device/'mapping.json',{'device':device,'inventory_sha256':digest(path),'records':rows,'generated_tests':len(cases),'full_manual_coverage':False});devices.append({'device':device,'mapped_records':len(rows),'generated_tests':len(cases),'families':{f:sum(r['family']==f for r in rows) for f in sorted({r['family'] for r in rows})}});allcases+=cases
 save(ROOT/'gpt_test_library/family_cases.json',{'schema':'gpt-corrected-case-library-1','cases':allcases});save(output/'summary.json',{'status':'families_mapped_with_capability_gaps','devices':devices,'generated_cases':len(allcases),'api_calls':0,'simulations':0,'full_manual_coverage':False});print(json.dumps(devices,ensure_ascii=False,indent=2))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();prepare(a.output)
