"""Bounded peak and recovery measurements from completed actual waveforms."""
from .state import Fault

def value(m,xs,ys,interpolate):
 start,end=m['start_s'],m['at']
 if not xs[0]<=start<end<=xs[-1]:raise Fault('fixture','Transient measurement window outside actual waveform')
 points=[(start,interpolate(xs,ys,start))]+[(x,y) for x,y in zip(xs,ys) if start<x<end]+[(end,interpolate(xs,ys,end))]
 if m['mode']=='transient_peak':return {'value':max(y for x,y in points),'measurement_window':{'start':start,'end':end}}
 level=m['target']['value'];zero=None;finish=None;previous=points[0]
 for current in points[1:]:
  x0,y0=previous;x1,y1=current
  if zero is None and y0>0>=y1:zero=x0+(x1-x0)*y0/(y0-y1)
  if zero is not None and y0<level<=y1:
   finish=x0+(x1-x0)*(level-y0)/(y1-y0);break
  previous=current
 if zero is None or finish is None:raise Fault('fixture','Recovery zero/return threshold not reached; no invented duration')
 return {'value':(finish-zero)*m.get('scale',1),'zero_crossing_s':zero,'return_crossing_s':finish,'measurement_window':{'start':start,'end':end}}

def oracles(case):
 import copy
 m=case['protocol']['measurement'];out=[]
 for factor in (1,2):
  q={'temperature_C':25,'device_nodes':{},'checks':[],'analysis':{'kind':'tran','stop_s':20e-9,'max_step_s':.01e-9}}
  if m['mode']=='transient_peak':
   signal='v(p)';q['components']=[{'kind':'V','name':'VORACLE','nodes':['P','0'],'value':{'pwl':[[0,0],[2e-9,0],[4e-9,factor],[8e-9,0],[20e-9,0]]}}];e=factor*m.get('scale',1)
  else:
   signal='i(ioracle)';q['components']=[{'kind':'I','name':'IORACLE','nodes':['P','0'],'value':{'pwl':[[0,.01],[4e-9,.01],[6e-9,-.01],[8e-9,-.01],[float(12+4*(factor-1))*1e-9,0],[20e-9,0]]}},{'kind':'R','name':'RCAL','nodes':['P','0'],'value':1000}]
   # Zero at 5ns; return to -1mA at 11.6ns; second oracle varies return threshold.
   e=((8+.9*(4+4*(factor-1)))*1e-9-5e-9)*m.get('scale',1)
  qm=copy.deepcopy(m);qm.update(signal=signal,start_s=0,at=20e-9)
  if m['mode']=='transient_recovery':qm.update(sign=1,scale=m.get('scale',1),target={'signal':signal,'value':-.001,'direction':'rising'})
  q['measurement']=qm;out.append((q,e,None))
 return out
