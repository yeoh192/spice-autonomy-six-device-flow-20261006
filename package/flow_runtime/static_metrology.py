"""Reusable bounded DC point fixtures; no claim of continuous-range coverage."""
import copy,re
from .state import Fault
from .spice import validate_protocol
from .test_families import generate

def dc_point(item,base,temperature=25,common_mode=None,supply=None):
    rid=item['id'];e=item['reference_evidence'];conditions=e.get('conditions','')
    match=re.search(r'VSY=(2\.5|5) V',conditions);supply=float(match.group(1)) if match else supply
    if supply is None:raise Fault('contract','Supply point required')
    if base['device'].startswith('ACS') or rid.startswith(('parameter:icc','parameter:voh','parameter:vol')):
        return generate(item,base,supply,temperature)
    if not rid.startswith(('parameter:vos','parameter:ib_','parameter:ios_')):raise Fault('capability','Unsupported static metrology')
    cm=supply/2 if common_mode is None else common_mode
    if not 0<=cm<=supply or not -40<=temperature<=125:raise Fault('contract','Point outside declared range')
    if 'TA=25' in conditions and '..' not in conditions and temperature!=25:raise Fault('contract','Temperature changed')
    c=copy.deepcopy(base);p=c['protocol'];p['temperature_C']=temperature;p['analysis']={'kind':'dc','source':'VDUMMY','start':0,'stop':1,'step':1};p['checks']=[]
    parts=[{'kind':'V','name':'VSUP','nodes':['VCC','0'],'value':{'dc':supply}},{'kind':'V','name':'VCM','nodes':['CM','0'],'value':{'dc':cm}},{'kind':'V','name':'VDUMMY','nodes':['UNUSED','0'],'value':{'dc':0}}]
    if ':vos' in rid:
        p['device_nodes']={'P1':'CM','P2':'OUT','P3':'VCC','P4':'0','P5':'OUT'}
        signal='v(OUT,CM)';scale=1e6
        p['checks']=[{'signal':'v(OUT)','from':0,'to':1,'min':0,'max':supply}]
    else:
        p['device_nodes']={'P1':'IP','P2':'IM','P3':'VCC','P4':'0','P5':'OUT'}
        # Parallel voltage-source probes keep both inputs at the required VCM.
        parts.extend([{'kind':'V','name':'VIP','nodes':['CM','IP'],'value':{'dc':0}},{'kind':'V','name':'VIM','nodes':['CM','IM'],'value':{'dc':0}}]);signal='i(VIP)';scale=1e12
    p['components']=parts
    if ':ios' in rid:
        p['measurement']={'mode':'dc_current_difference','signal':'i(VIP)','denominator':'i(VIM)','at':1,'scale':1e12,'absolute':True}
    elif ':ib_' in rid:
        p['measurement']={'mode':'dc_current_max','signal':'i(VIP)','denominator':'i(VIM)','at':1,'scale':1e12,'absolute':True}
    else:p['measurement']={'mode':'sample','signal':signal,'at':1,'scale':scale,'absolute':True}
    values=e['values'];c['expectation']={'unit':e['unit'],'limits':{k:v for k,v in values.items() if k in ('min','max') and isinstance(v,(int,float))}}
    if isinstance(values.get('typ'),(int,float)):c['expectation'].update(typical=values['typ'],typical_relative_tolerance_percent=10)
    c['id']='static_'+rid.split(':')[-1].replace('.','p')+'_T'+str(temperature).replace('-','m')+'_CM'+str(cm).replace('.','p');c['reference_ids']=[rid];c['reference_evidence']=[copy.deepcopy(item)];c.pop('reference',None);c['scope']='One declared temperature/common-mode point; statistical and continuous range qualification pending';c['family']='offset_bias';c['family_conditions']={'supply_V':supply,'temperature_C':temperature,'common_mode_V':cm,'handbook_conditions':conditions}
    validate_protocol(p,c['model']);return c

def current_oracles(case):
    p=case['protocol'];m=p['measurement'];first=re.fullmatch(r'i\(([^)]+)\)',m['signal'])[1];second=re.fullmatch(r'i\(([^)]+)\)',m['denominator'])[1];out=[]
    # Analytic unequal and oppositely signed input currents expose average/difference mistakes.
    for x,y in [(4e-10,2e-10),(8e-10,-2e-10)]:
        q=copy.deepcopy(p);q['device_nodes']={};q['checks']=[];q['temperature_C']=25
        q['components']=[{'kind':'V','name':first,'nodes':['0','A'],'value':{'dc':0}},{'kind':'I','name':'ICAL1','nodes':['A','0'],'value':{'dc':x}},{'kind':'V','name':second,'nodes':['0','B'],'value':{'dc':0}},{'kind':'I','name':'ICAL2','nodes':['B','0'],'value':{'dc':y}},{'kind':'V','name':'VDUMMY','nodes':['UNUSED','0'],'value':{'dc':0}}]
        expected=(max(abs(x),abs(y)) if m['mode']=='dc_current_max' else x-y)*m.get('scale',1)
        if m.get('absolute'):expected=abs(expected)
        out.append((q,expected,None))
    return out
