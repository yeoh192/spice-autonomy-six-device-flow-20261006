"""Reusable source-preserving family classification and bounded DC generators."""
import copy,re
from .state import Fault
from .spice import validate_protocol
FAMILIES=[('thermal',('thermal','junction','package')),('noise',('noise','distortion','thd')),('rejection',('rejection','cmrr','psrr')),('offset_bias',('offset','bias current','bias_current','offset_current','drift')),('supply',('supply current','supply_current')),('output_swing',('output_voltage','output voltage','saturation','short_circuit','short circuit','rail-to-rail')),('input_impedance',('input resistance','input_resistance','input capacitance','input_capacitance')),('output_impedance',('output impedance','output_impedance')),('frequency',('bandwidth','gain','phase','crossover')),('timing',('time','delay','slew','transient','overshoot','recovery','settling')),('sensor_transfer',('sensitivity','zero','linearity','nonlinear','total_error','total_output_error','current_range')),('magnetic',('field','coupling')),('conductor_resistance',('conductor_resistance',)),('setup',('load','operating_voltage','supply_voltage','vcc','input_voltage_range'))]
def family(item):
 text=(item.get('label','')+' '+item.get('reference_evidence',{}).get('key','')).lower()
 return next((name for name,terms in FAMILIES if any(t in text for t in terms)),'unclassified')
def mapping(inventory,device):
 rows=[]
 for item in inventory['items']:
  if item['kind']!='test':continue
  f=family(item);e=item.get('reference_evidence',{});conditions=e.get('conditions','');gaps=[]
  if not conditions:gaps.append('manual_conditions_missing')
  if item['id'].startswith('figure:') and not item.get('reference_asset_bindings'):gaps.append('numeric_reference_curve_missing')
  if '..' in conditions:gaps.append('continuous_range_not_proved_by_endpoint_samples')
  if item.get('typical_interpretation'):gaps.append('statistical_band_not_nominal_target')
  supported=item['id'].startswith(('parameter:vos','parameter:ib_','parameter:ios_','parameter:20AB_sensitivity','parameter:20AB_sens_error','parameter:20AB_offset','parameter:nonlinear')) or f=='supply' or (device.startswith('ADA') and item['id'].startswith(('parameter:voh_','parameter:vol_'))) or item['id']=='parameter:20AB_zero'
  rows.append({'reference_id':item['id'],'family':f,'generator':f if supported else None,'conditions_verbatim':conditions,'reference_evidence':copy.deepcopy(e),'pdf_pages':item.get('pdf_pages',[]),'reference_asset_bindings':item.get('reference_asset_bindings',[]),'gaps':gaps,'status':'generator_available_exact_points' if supported else 'capability_or_evidence_pending','full_record_coverage':False})
 return rows

def generate(item,base,supply=None,temperature=25):
 c=copy.deepcopy(base);e=item['reference_evidence'];conditions=e.get('conditions','');device=c['device'];rid=item['id'];f=family(item)
 match=re.search(r'VSY=(2\.5|5) V',conditions);supply=float(match.group(1)) if match else supply
 if supply is None:raise Fault('contract','Explicit supply point required')
 if not conditions:raise Fault('contract','Handbook conditions missing')
 if match and supply!=float(match.group(1)):raise Fault('contract','Supply differs from handbook')
 if 'VCC=5 V' in conditions and supply!=5:raise Fault('contract','Supply differs from handbook')
 if 'TA=25' in conditions and '..' not in conditions and temperature!=25:raise Fault('contract','Temperature differs from handbook')
 if 'TA=-40..125' in conditions and not -40<=temperature<=125:raise Fault('contract','Temperature outside handbook range')
 if f not in ('supply','output_swing','sensor_transfer'):raise Fault('capability','Family generator not implemented')
 p=c['protocol'];p['temperature_C']=temperature;p['analysis']={'kind':'dc','source':'VDUMMY','start':0,'stop':1,'step':1};p['checks']=[]
 components=[{'kind':'V','name':'VSUP','nodes':['VCC','0'],'value':{'dc':supply}},{'kind':'V','name':'VDUMMY','nodes':['UNUSED','0'],'value':{'dc':0}}]
 if device.startswith('ADA'):
  components.append({'kind':'V','name':'VCM','nodes':['IN','0'],'value':{'dc':supply/2}});p['device_nodes']={'P1':'IN','P2':'OUT','P3':'VCC','P4':'0','P5':'OUT'}
  if f=='output_swing':
   if not rid.startswith(('parameter:voh_','parameter:vol_')):raise Fault('capability','Not a voltage swing record')
   load=re.search(r'RL=(10|2) kΩ',conditions)
   if not load:raise Fault('contract','Swing load missing')
   components.extend([{'kind':'R','name':'RLOAD','nodes':['OUT','CMLOAD'],'value':float(load.group(1))*1000},{'kind':'V','name':'VLOAD','nodes':['CMLOAD','0'],'value':{'dc':supply/2}}]);components[2]['value']['dc']=supply/2+(0.01 if ':voh_' in rid else -0.01);components.append({'kind':'V','name':'VNEG','nodes':['NIN','0'],'value':{'dc':supply/2+(-0.01 if ':voh_' in rid else 0.01)}});p['device_nodes']['P2']='NIN'
 else:
  p['device_nodes']={'P1':'IP','P2':'0','P3':'0','P4':'VCC','P5':'OUT','P6':'0'};components.append({'kind':'I','name':'IFORCE','nodes':['0','IP'],'value':{'dc':0}})
  if f!='supply' and rid!='parameter:20AB_zero':raise Fault('capability','Only zero-current transfer point implemented')
 p['components']=components;scale=1000 if e['unit'] in ('mA','mV') else 1;p['measurement']={'mode':'sample','signal':'i(VSUP)' if f=='supply' else 'v(OUT)','at':1,'scale':scale,'sign':-1 if f=='supply' else 1}
 values=e.get('values',{});limits={k:v for k,v in values.items() if k in ('min','max') and isinstance(v,(float,int))};typ=supply*.5 if rid=='parameter:20AB_zero' else values.get('typ')
 if not limits and not isinstance(typ,(int,float)):raise Fault('contract','Numeric expectation unavailable')
 c['expectation']={'unit':e['unit'],'limits':limits}
 if isinstance(typ,(int,float)):c['expectation'].update(typical=typ,typical_relative_tolerance_percent=10)
 c['id']='family_'+rid.split(':')[-1].replace('.','p')+'_V'+str(supply).replace('.','p')+'_T'+str(temperature).replace('-','m');c['reference_ids']=[rid];c['reference_evidence']=[copy.deepcopy(item)];c.pop('reference',None);c['scope']='Exact supply/temperature/load point only; continuous range not certified';c['family']=f;c['family_conditions']={'supply_V':supply,'temperature_C':temperature,'handbook_conditions':conditions}
 validate_protocol(p,c['model']);return c
