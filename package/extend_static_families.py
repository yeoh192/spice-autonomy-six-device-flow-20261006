#!/usr/bin/env python3
"""Generate additional declared static points; keep ranges explicitly incomplete."""
from pathlib import Path
from flow_runtime.state import read,save
from flow_runtime.static_metrology import dc_point
ROOT=Path(__file__).resolve().parent

def prepare():
 basecases=read(ROOT/'gpt_test_library/cases.json')['cases'];cases=[]
 for folder,device in [('ACS723','ACS723KMATR-20AB-T'),('ADA4528','ADA4528-1_MSOP')]:
  base=next(c for c in basecases if c['device']==device);inv=read(ROOT/'six_inputs'/folder/'inventory.json')
  for item in inv['items']:
   if item['kind']!='test':continue
   rid=item['id'];cond=item['reference_evidence'].get('conditions','')
   if device.startswith('ACS') and rid=='parameter:icc':
    for supply in (4.5,5,5.5):
     for temp in (-40,25,125):cases.append(dc_point(item,base,temp,supply=supply))
   if device.startswith('ADA'):
    if rid.startswith(('parameter:ib_','parameter:ios_')):
     if not item.get('applicable',True):continue
     if rid.endswith('_2'):continue
     for temp in ((-40,25,125) if '_full' in rid else (25,)):cases.append(dc_point(item,base,temp))
    if rid.startswith('parameter:vos') and item.get('applicable',True):
     if 'LFCSP' in rid:continue
     for temp in ((-40,25,125) if '_full' in rid else (25,)):cases.append(dc_point(item,base,temp))
    if rid.startswith(('parameter:icc_full','parameter:voh_','parameter:vol_')) and '_full' in rid:
     for temp in (-40,25,125):cases.append(dc_point(item,base,temp))
 save(ROOT/'gpt_test_library/extended_family_cases.json',{'schema':'gpt-corrected-case-library-1','cases':cases})
 print('Extended static cases:',len(cases),'unique handbook records:',len({(c['device'],c['reference_ids'][0]) for c in cases}))
if __name__=='__main__':prepare()
