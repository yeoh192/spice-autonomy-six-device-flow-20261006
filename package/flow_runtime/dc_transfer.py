"""DC transfer metrology with signed slope and endpoint-line nonlinearity."""
import copy
from .state import Fault

def validate(a,m):
 if a['kind']!='dc' or not a['source'].upper().startswith('I') or not m['signal'].lower().startswith('v('):raise Fault('proposal','DC transfer requires actual current sweep and output voltage')
 if a['start']>=a['stop']:raise Fault('proposal','DC transfer sweep must ascend')
 if m['mode']=='dc_sensitivity_error_percent' and m.get('nominal',0)<=0:raise Fault('proposal','Positive frozen nominal sensitivity required')

def value(a,m,xs,ys):
 if len(xs)<2:raise Fault('execution','Insufficient DC transfer points')
 slope=(ys[-1]-ys[0])/(xs[-1]-xs[0]);sign=m.get('sign',1);scale=m.get('scale',1)
 if m['mode']=='dc_slope':return slope*sign*scale
 if m['mode']=='dc_sensitivity_error_percent':return (slope*sign/m['nominal']-1)*100
 span=abs(ys[-1]-ys[0])
 if span<1e-12:raise Fault('fixture','Zero transfer output span')
 return max(abs(y-(ys[0]+slope*(x-xs[0]))) for x,y in zip(xs,ys))/span*100

def oracles(case):
 p=case['protocol'];m=p['measurement'];source=p['analysis']['source'];out=[]
 node=m['signal'][2:-1]
 for resistance in (.1,.2):
  q=copy.deepcopy(p);q['device_nodes']={};q['checks']=[];q['components']=[{'kind':'I','name':source,'nodes':['0',node],'value':{'dc':0}},{'kind':'R','name':'RCAL','nodes':[node,'0'],'value':resistance}]
  mode=m['mode'];expected=resistance*m.get('scale',1)*m.get('sign',1) if mode=='dc_slope' else (resistance*m.get('sign',1)/m['nominal']-1)*100 if mode=='dc_sensitivity_error_percent' else 0
  out.append((q,expected,None))
 return out
