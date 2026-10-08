#!/usr/bin/env python3
from pathlib import Path
import copy
from flow_runtime.state import read,save
from flow_runtime.spice import validate_protocol
ROOT=Path(__file__).resolve().parent

def prepare():
 base=next(c for c in read(ROOT/'gpt_test_library/cases.json')['cases'] if c['device'].startswith('ACS'));cases=[]
 for item in read(ROOT/'six_inputs/ACS723/inventory.json')['items']:
  rid=item['id']
  if rid not in ('parameter:20AB_sensitivity','parameter:20AB_sens_error_warm','parameter:20AB_sens_error_cold','parameter:nonlinear','parameter:20AB_offset_warm','parameter:20AB_offset_cold'):continue
  for temp in ((-40,0,25) if 'cold' in rid else (25,75,125) if 'warm' in rid or rid.endswith('sensitivity') else (25,)):
   c=copy.deepcopy(base);p=c['protocol'];p['temperature_C']=temp
   p['analysis']={'kind':'dc','source':'IFORCE','start':-20,'stop':20,'step':1};p['checks']=[]
   values=item['reference_evidence']['values'];limits={k:v for k,v in values.items() if k in ('min','max') and isinstance(v,(int,float))}
   c['expectation']={'unit':item['reference_evidence']['unit'],'limits':limits}
   if rid=='parameter:nonlinear':c['coverage_registration_allowed']=False;c['definition_gap']='Endpoint-line diagnostic is calibrated; handbook nonlinearity definition requires independent confirmation'
   if 'offset' in rid:
    p['analysis']={'kind':'dc','source':'VDUMMY','start':0,'stop':1,'step':1};p['components'].append({'kind':'V','name':'VZERO','nodes':['ZERO','0'],'value':{'dc':2.5}})
    p['measurement']={'mode':'sample','signal':'v(OUT,ZERO)','at':1,'scale':1000};c['offset_nominal_supply_V']=5
   else:
    mode='dc_slope' if rid.endswith('sensitivity') else 'dc_linearity_percent' if rid=='parameter:nonlinear' else 'dc_sensitivity_error_percent'
    p['measurement']={'mode':mode,'signal':'v(OUT)','scale':1000 if mode=='dc_slope' else 1}
    if mode=='dc_sensitivity_error_percent':p['measurement']['nominal']=.1
   if isinstance(values.get('typ'),(int,float)):c['expectation'].update(typical=values['typ'],typical_relative_tolerance_percent=10)
   c['id']='sensor_'+rid.split(':')[-1]+'_T'+str(temp).replace('-','m');c['reference_ids']=[rid];c['reference_evidence']=[copy.deepcopy(item)];c['family']='sensor_transfer';c['scope']='Discrete temperature test; statistical distribution and continuous-range validation pending';c['family_conditions']={'supply_V':5,'temperature_C':temp,'handbook_conditions':item['reference_evidence']['conditions']};validate_protocol(p,c['model']);cases.append(c)
 save(ROOT/'gpt_test_library/sensor_family_cases.json',{'schema':'gpt-corrected-case-library-1','cases':cases});print('Sensor cases:',len(cases))
if __name__=='__main__':prepare()
