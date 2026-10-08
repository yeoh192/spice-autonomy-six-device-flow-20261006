#!/usr/bin/env python3
"""Build source-bound methods for the first four examples, without promoting delivery."""
import copy,csv,re
from pathlib import Path
from flow_runtime.state import read,save,digest
from flow_runtime.task import load_task
from flow_runtime.spice import interpolate,validate_protocol,validate_measurement_unit
ROOT=Path(__file__).resolve().parent

def element(kind,name,a,b,v):return {'kind':kind,'name':name,'nodes':[a,b],'value':v}
def inventories(device):return read(ROOT/'six_inputs'/device/'inventory.json')['items']
def case(base,item,id,protocol,expectation=None):
 c=copy.deepcopy(base)
 for k in ('reference','contract','actual_circuit','protocol_sha256'):c.pop(k,None)
 c.update(id=id,reference_ids=[item['id']],reference_evidence=[copy.deepcopy(item)],protocol=protocol,expectation=expectation or {'unit':item['reference_evidence']['unit'],'limits':{}},authorship={'designer':'GPT/Codex','review':'Local source, topology and measurement review; no claim of GLM approval'},scope='Exact-condition method only; manual ranges, dual-instance and complete curve coverage remain open',qualification_status='awaiting_real_calibration_and_trial')
 return c

def prepare():
 original=read(ROOT/'gpt_test_library/cases.json')['cases'];cases=[];gaps=[]
 # Existing BUK circuits receive the same two-oracle qualification as the new library.
 task=load_task(ROOT/'six_inputs/BUK7K52-60E/configured_task.json')[0];inv=inventories('BUK7K52-60E');lookup={i['id']:i for i in inv}
 for existing in task['cases']:
  bound=[i for i in inv if i['kind']=='test' and existing['id'] in i.get('bindings',[])]
  if not bound:continue
  c=copy.deepcopy(existing);c.update(device='BUK7K52-60E',input_folder='BUK7K52-60E',model=task['model'],reference_ids=[i['id'] for i in bound],reference_evidence=copy.deepcopy(bound),authorship={'designer':'Migrated engineering circuit; qualified by GPT/Codex tooling','review':'One three-terminal FET instance; FET1/FET2 physical package binding remains pending'},scope='Single FET instance, original declared conditions; dual-package full coverage pending')
  cases.append(c)
 bukbase=cases[0]
 for rid in ['figure:8:typ','figure:9:typ','figure:10:typ']+[f'figure:11:vgs_{s}' for s in ['5_5','6','6_5','7','8','10']]+['figure:15:ciss','figure:15:coss','figure:15:crss','figure:16:tj_25','figure:16:tj_175']:
  item=lookup[rid];suffix=rid.replace(':','_');protocols=[]
  if rid.startswith(('figure:8','figure:11')):
   gates=(4.5,6,10) if rid.startswith('figure:8') else (float(rid.split('vgs_')[1].replace('_','.')),)
   currents=(5,) if rid.startswith('figure:8') else (1,5,10)
   for gate in gates:
    for current in currents:
     p=copy.deepcopy(next(c['protocol'] for c in task['cases'] if c['id']=='rdson_25C'))
     p['components'][0]['value']['dc']=gate;p['analysis'].update(start=current*.99,stop=current*1.01,step=current*.001)
     p['measurement'].update(at=current,scale=1000/current)
     protocols.append((f'g{gate}_i{current}',p,{'unit':'mΩ','limits':{}}))
  elif rid=='figure:9:typ':
   for gate in (2,3,4):
    p={'temperature_C':25,'device_nodes':{'D':'D','G':'G','S':'0'},'components':[element('V','VD','D','0',{'dc':5}),element('V','VG','G','0',{'dc':gate}),element('V','VSCAN','UNUSED','0',{'dc':0})], 'analysis':{'kind':'dc','source':'VSCAN','start':0,'stop':1,'step':1},'measurement':{'mode':'sample','signal':'i(vd)','sign':-1,'at':0}}
    protocols.append((f'g{gate}',p,{'unit':'A','limits':{}}))
  elif rid=='figure:10:typ':
   for temp in (-55,25,175):
    p=copy.deepcopy(next(c['protocol'] for c in task['cases'] if c['id']=='threshold_25C'));p['temperature_C']=temp;protocols.append((f't{temp}',p,{'unit':'V','limits':{}}))
  elif rid.startswith('figure:15'):
   name='manual_'+rid.split(':')[-1]
   for bias in (5,25,48):
    p=copy.deepcopy(next(c['protocol'] for c in task['cases'] if c['id']==name))
    next(c for c in p['components'] if c['name']=='VD')['value']['dc']=bias;p['measurement']['scale']=1e12
    protocols.append((f'v{bias}',p,{'unit':'pF','limits':{}}))
  else:
   temp=25 if rid.endswith('25') else 175
   for current in (1,5,10):
    p=copy.deepcopy(next(c['protocol'] for c in task['cases'] if c['id']=='body_diode_25C'));p['temperature_C']=temp;p['analysis'].update(start=current*.99,stop=current*1.01,step=current*.001);p['measurement'].update(at=current)
    # Method samples current at voltage points only; not a reconstructed full voltage-domain reference curve.
    protocols.append((f'i{current}',p,{'unit':'V','limits':{}}))
  for label,p,e in protocols:
   c=case(bukbase,item,(suffix+'_'+label).replace('.','p').replace('-','minus'),p,e);c['scope_note']='Reference digitization missing in input for this series; measurements register only partial method availability, never curve acceptance';cases.append(c)
 # Diode temperature/bias series: sample actual extracted reference points, preserve CSV proof.
 diode=[c for c in original if c['device']=='1N4148'];base=diode[0]
 for item in inventories('1N4148'):
  if item['kind']!='test':continue
  rid=item['id']
  if rid.startswith('figure:fig5') or rid=='figure:fig6:capacitance':
   asset=next(a for a in item['reference_asset_bindings'] if a.get('path','').endswith('.csv'));refpath=ROOT/'six_inputs/1N4148'/asset['path']
   with refpath.open() as f:rows=[(float(r['x']),float(r['y'])) for r in csv.DictReader(f)]
   for index in (0,len(rows)//2,len(rows)-1):
    x,y=rows[index]
    if rid.startswith('figure:fig5'):
     p=copy.deepcopy(next(c['protocol'] for c in diode if c['id']=='reverse_20V_25C'));p['temperature_C']=x
     next(c for c in p['components'] if c['name']=='VREVERSE')['value']['dc']=75 if rid.endswith('75') else 20
     p['measurement']['scale']=1e6;unit='μA'
    else:
     p=copy.deepcopy(next(c['protocol'] for c in diode if c['id']=='capacitance_zero_bias'))
     next(c for c in p['components'] if c['kind']=='V' and c['value'].get('ac'))['value']['dc']=-x;unit='pF'
    c=case(base,item,rid.replace(':','_')+f'_point{index}',p,{'unit':unit,'typical':y,'typical_relative_tolerance_percent':10,'limits':{}})
    c['sampled_reference']={'path':str(refpath),'sha256':digest(refpath),'x':x,'y':y,'row_index':index};c['scope_note']='Three actual reference points only; full curve MAE/max acceptance pending';cases.append(c)
 # Forward recovery peak uses the declared 50mA/20ns edge, and actual terminal voltage.
 item=next(i for i in inventories('1N4148') if i['id']=='parameter:vfr')
 p={'temperature_C':25,'device_nodes':{'A':'A','K':'0'},'components':[element('I','IFORCE','0','A',{'pwl':[[0,0],[100e-9,0],[120e-9,.05],[500e-9,.05]]})],'analysis':{'kind':'tran','stop_s':500e-9,'max_step_s':.1e-9},'measurement':{'mode':'transient_peak','signal':'v(a)','start_s':100e-9,'at':500e-9},'checks':[{'signal':'v(a)','from':0,'to':99e-9,'min':-.01,'max':.01}]}
 # Port names from frozen wrapper, not guessed.
 p['device_nodes']={base['model']['ports'][0]:'A',base['model']['ports'][1]:'0'}
 cases.append(case(base,item,'forward_recovery_peak_50mA_20ns',p,{'unit':'V','limits':{'max':2.5}}))
 # Reverse recovery: RL100ohm and an independently measured10mA DC forward voltage set the switching source.
 records=[read(p) for p in (ROOT/'gpt_test_library/qualified').glob('*.json')]
 forward=next(r for r in records if r['case']['device']=='1N4148' and r['case']['id']=='forward_10mA')
 vf=forward['receipt']['trial']['result']['value']
 item=next(i for i in inventories('1N4148') if i['id']=='parameter:trr')
 p={'temperature_C':25,'device_nodes':{'A':'A','K':'0'},'components':[element('V','VSW','DRIVE','0',{'pwl':[[0,vf+1],[200e-9,vf+1],[200.1e-9,vf-6],[500e-9,vf-6]]}),element('R','RLOAD','DRIVE','PROBE',100),element('V','VSENSE','PROBE','A',{'dc':0})],'analysis':{'kind':'tran','stop_s':500e-9,'max_step_s':.01e-9},'measurement':{'mode':'transient_recovery','signal':'i(vsense)','start_s':199e-9,'at':500e-9,'scale':1e9,'target':{'signal':'i(vsense)','value':-.001,'direction':'rising'}},'checks':[{'signal':'i(vsense)','from':190e-9,'to':199e-9,'min':.00999,'max':.01001},{'signal':'i(vsense)','from':200.1e-9,'to':200.1e-9,'min':-.061,'max':-.059}]}
 dynamic=case(base,item,'reverse_recovery_10mA_60mA_100ohm',p,{'unit':'ns','limits':{'max':4}})
 dynamic['scope_note']='Actual pre-edge10mA and reverse-edge60mA bias checks mandatory; duration from zero crossing to rising-1mA return threshold'
 validate_protocol(p,base['model']);validate_measurement_unit(dynamic)
 save(ROOT/'gpt_test_library/diode_dynamic_cases.json',{'schema':'gpt-corrected-case-library-1','cases':[dynamic]})
 # Capacitor pair: keep vendor cold and room models distinct within a comparison wrapper.
 cap=next(c for c in original if c['id']=='cap_120Hz');item=next(i for i in inventories(cap['device']) if i['id']=='parameter:impedance_ratio_-40')
 text=Path(cap['model']['path']).read_text();text+='\n.subckt COMPARISON_CAP P20 N20 PCOLD NCOLD\nXBASE P20 N20 EMHK350ARA470MF80G_20deg-C\nXCOLD PCOLD NCOLD EMHK350ARA470MF80G_-40deg-C\n.ends COMPARISON_CAP\n'
 path=ROOT/'gpt_test_library/comparison_models/cap_cold_room.lib';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 b=copy.deepcopy(cap);b['model'].update(path=str(path),sha256=digest(path),entry='COMPARISON_CAP',ports=['P20','N20','PCOLD','NCOLD'],declared_ports=['P20','N20','PCOLD','NCOLD'])
 p={'temperature_C':20,'device_nodes':{'P20':'P20','N20':'0','PCOLD':'PCOLD','NCOLD':'0'},'components':[element('V','VROOM','P20','0',{'dc':0,'ac':.1}),element('V','VCOLD','PCOLD','0',{'dc':0,'ac':.1})],'analysis':{'kind':'ac','frequency_Hz':120},'measurement':{'mode':'ac_current_ratio','signal':'i(vroom)','denominator':'i(vcold)'},'checks':[]}
 c=case(b,item,'impedance_ratio_minus40_room_120Hz',p,{'unit':'1','limits':{'max':3}});c['scope_note']='Ratio of distinct vendor -40°C and20°C subcircuits under equal100mV excitation; no temperature interpolation';cases.append(c)
 gaps += [{'device':cap['device'],'reference_id':'parameter:impedance_ratio_-25','reason':'Vendor provides20/-40/125°C subcircuits; no-25°C model; interpolation not authorized'}, {'device':cap['device'],'reference_id':'parameter:leakage','reason':'Vendor RC ladder has no DC leakage path; cannot qualify rated-voltage two-minute leakage with ideal capacitor model'}]
 # Transformer leakage shorting ambiguity remains an explicitly labelled diagnostic.
 xf=next(c for c in original if c['id']=='primary_inductance');item=next(i for i in inventories(xf['device']) if i['id']=='parameter:leakage_l')
 for winding in ('secondary','secondary_and_aux'):
  p=copy.deepcopy(xf['protocol']);p['analysis']['frequency_Hz']=100000;p['components'].append(element('R','RSHORTSEC','SECP','SECN',1e-6))
  if winding=='secondary_and_aux':p['components'].append(element('R','RSHORTAUX','AUXP','AUXN',1e-6))
  c=case(xf,item,'leakage_'+winding,p,{'unit':'μH','limits':{'max':1.2}});c.update(coverage_registration_allowed=False,definition_gap='Manual shorting condition unspecified; alternatives are diagnostic only');cases.append(c)
 gaps += [{'device':xf['device'],'reference_id':'parameter:isat','reason':'Vendor inductances are linear; cannot find20% saturation reduction or saturation current without nonlinear magnetic model'}, {'device':xf['device'],'reference_id':'parameter:leakage_l','reason':'Two shorting conventions tested as diagnostics, not promoted to manual binding'}]
 for c in cases:validate_protocol(c['protocol'],c['model']);validate_measurement_unit(c)
 save(ROOT/'gpt_test_library/first_four_cases.json',{'schema':'gpt-corrected-case-library-1','cases':cases,'gaps':gaps,'excluded_case_ids':[{'device':'1N4148','id':'figure_fig5_point'+str(i),'reason':'Informational parent group cannot be used as independent reference series'} for i in (0,100,200)],'full_manual_coverage':False});print('First four methods:',len(cases),'explicit gaps:',len(gaps))
if __name__=='__main__':prepare()
