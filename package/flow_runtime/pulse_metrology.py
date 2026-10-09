"""Device-independent pulse timing, event bias and bounded-span metrology.

Only complete, strictly increasing recorded axes are accepted. No expected
DUT values enter extraction; analytic calibration fixtures are defined below.
"""
import math
from .state import Fault, finite

MODES = {'transient_frequency', 'transient_duty', 'transient_edge_time',
         'transient_delay', 'transient_event_sample', 'dc_event_sample', 'transient_span', 'dc_span'}

def extra_signals(m):
    timing = m.get('timing', {})
    return [timing[k] for k in ('trigger_signal', 'event_signal') if k in timing]

def validate(a, m, signal):
    mode = m['mode']
    if a['kind'] != ('dc' if mode.startswith('dc_') else 'tran'):
        raise Fault('proposal', 'Pulse metrology analysis type mismatch')
    w = m.get('window', {})
    if set(w) != {'start', 'end'}:
        raise Fault('proposal', 'Explicit measurement window start/end required')
    lo, hi = (a['start'], a['stop']) if mode.startswith('dc_') else (0, a['stop_s'])
    if not lo <= finite(w['start']) < finite(w['end']) <= hi:
        raise Fault('proposal', 'Measurement window outside declared analysis')
    t = m.get('timing', {})
    fields = {
        'transient_frequency': {'level', 'min_cycles', 'max_jitter_percent'},
        'transient_duty': {'level', 'min_cycles', 'max_jitter_percent', 'allow_zero'},
        'transient_edge_time': {'direction', 'low_fraction', 'high_fraction', 'event_index', 'min_swing'},
        'transient_delay': {'trigger_signal', 'trigger_level', 'trigger_direction', 'response_level', 'response_direction', 'event_index', 'max_delay_s'},
        'transient_event_sample': {'event_signal', 'event_level', 'direction', 'event_index'},
        'dc_event_sample': {'event_signal', 'event_level', 'direction', 'event_index'},
        'transient_span': set(), 'dc_span': set(),
    }[mode]
    required = fields - ({'allow_zero'} if mode == 'transient_duty' else set())
    if set(t)-fields or required-set(t):
        raise Fault('proposal', 'Unknown/missing pulse timing fields', {'mode': mode, 'required': sorted(required)})
    for k, v in t.items():
        if k.endswith('_signal'): signal(v)
        elif k in ('direction', 'trigger_direction', 'response_direction'):
            if v not in ('rising', 'falling'): raise Fault('proposal', 'Crossing direction required')
        elif k in ('event_index', 'min_cycles'):
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10000: raise Fault('proposal', 'Invalid positive event/cycle count')
        elif k == 'allow_zero':
            if not isinstance(v, bool): raise Fault('proposal', 'allow_zero must be boolean')
        else: finite(v, k)
    if mode in ('transient_frequency', 'transient_duty'):
        if t['min_cycles'] < 3 or not 0 <= t['max_jitter_percent'] <= 100: raise Fault('proposal', 'At least 3 full periods and bounded jitter required')
    if mode == 'transient_edge_time':
        if not 0 < t['low_fraction'] < t['high_fraction'] < 1 or t['min_swing'] <= 0: raise Fault('proposal', 'Invalid edge fractions/swing')
    if mode == 'transient_delay' and t['max_delay_s'] <= 0: raise Fault('proposal', 'Positive bounded delay required')
    if m.get('sign', 1) != 1 or m.get('absolute', False):
        raise Fault('proposal', 'Timing inputs must preserve recorded polarity')

def _crossings(xs, ys, level, direction):
    sign = 1 if direction == 'rising' else -1
    out = []
    for x0,x1,y0,y1 in zip(xs,xs[1:],ys,ys[1:]):
        if sign*(y0-level)<0 <= sign*(y1-level):
            out.append(x0+(x1-x0)*(level-y0)/(y1-y0))
    return out

def _event(events, index):
    if len(events) < index: raise Fault('fixture', 'Required crossing not reached', {'required': index, 'available': len(events)})
    return events[index-1]

def measure(m, xs, signals, interpolate):
    if len(xs)<3 or any(not math.isfinite(x) for x in xs) or any(b<=a for a,b in zip(xs,xs[1:])):
        raise Fault('parser', 'Pulse axis must be finite and strictly increasing')
    w, t, mode = m['window'], m.get('timing', {}), m['mode']
    start,end = w['start'],w['end']
    if start<xs[0] or end>xs[-1]+max(1e-15,abs(end)*1e-9): raise Fault('execution', 'Requested pulse window not completed')
    end = min(end,xs[-1])
    knots=[start]+[x for x in xs if start<x<end]+[end]
    def trace(name):
        vals=signals[name.lower()]
        if len(vals)!=len(xs) or any(not math.isfinite(v.real) for v in vals):raise Fault('parser','Invalid pulse signal length/value')
        real=[v.real for v in vals]
        return [interpolate(xs,real,x) for x in knots]
    ys=trace(m['signal']);scale=m.get('scale',1)
    proof={'measurement_window':{'start':start,'end':end},'method':mode}
    if mode in ('transient_span','dc_span'):
        return {'value':(max(ys)-min(ys))*scale,'minimum':min(ys),'maximum':max(ys),**proof}
    if mode in ('transient_event_sample','dc_event_sample'):
        event=_event(_crossings(knots,trace(t['event_signal']),t['event_level'],t['direction']),t['event_index'])
        return {'value':interpolate(knots,ys,event)*scale,'event_axis':event,**({'event_time_s':event} if mode=='transient_event_sample' else {}),**proof}
    if mode == 'transient_delay':
        trigger=_event(_crossings(knots,trace(t['trigger_signal']),t['trigger_level'],t['trigger_direction']),t['event_index'])
        at=interpolate(knots,ys,trigger);direction=t['response_direction'];level=t['response_level']
        if (at>=level if direction=='rising' else at<=level):raise Fault('fixture','Response already crossed at trigger; not a valid delay measurement')
        response=next((x for x in _crossings(knots,ys,level,direction) if x>=trigger),None)
        if response is None or response-trigger>t['max_delay_s']:raise Fault('fixture','Response edge not reached within allowed delay')
        return {'value':(response-trigger)*scale,'trigger_time_s':trigger,'response_time_s':response,**proof}
    if mode == 'transient_edge_time':
        low,high=min(ys),max(ys)
        if high-low<t['min_swing']:raise Fault('fixture','Insufficient actual edge amplitude')
        level0=low+(high-low)*t['low_fraction'];level1=low+(high-low)*t['high_fraction']
        if t['direction']=='falling':level0,level1=level1,level0
        first=_event(_crossings(knots,ys,level0,t['direction']),t['event_index'])
        last=next((x for x in _crossings(knots,ys,level1,t['direction']) if x>first),None)
        opposite='falling' if t['direction']=='rising' else 'rising'
        reset=next((x for x in _crossings(knots,ys,level0,opposite) if x>first),None)
        if last is None or (reset is not None and reset<last):raise Fault('fixture','Edge incomplete; cannot join crossings from different pulses')
        return {'value':(last-first)*scale,'first_crossing_s':first,'last_crossing_s':last,'levels':{'first':level0,'last':level1},'actual_swing':high-low,**proof}
    rises=_crossings(knots,ys,t['level'],'rising')
    if mode=='transient_duty' and t.get('allow_zero') and max(ys)<t['level']:
        return {'value':0,'periods':0,'zero_output_observed':True,**proof}
    if len(rises)<t['min_cycles']+1:raise Fault('fixture','Insufficient complete periods',{'rising_edges':len(rises),'required_periods':t['min_cycles']})
    periods=[b-a for a,b in zip(rises,rises[1:])];average=sum(periods)/len(periods)
    jitter=max(abs(p-average) for p in periods)/average*100
    if jitter>t['max_jitter_percent']:raise Fault('fixture','Nonstationary periods exceed requested jitter bound',{'jitter_percent':jitter})
    if any(sum(a<=x<=b for x in knots)<8 for a,b in zip(rises,rises[1:])):raise Fault('fixture','Too few recorded samples per period; aliasing risk')
    proof.update(periods=len(periods),jitter_percent=jitter,first_rising_s=rises[0],last_rising_s=rises[-1])
    if mode=='transient_frequency':return {'value':scale/average,**proof}
    falls=_crossings(knots,ys,t['level'],'falling');duties=[]
    for a,b in zip(rises,rises[1:]):
        inside=[x for x in falls if a<x<b]
        if len(inside)!=1:raise Fault('fixture','Each duty period must have exactly one falling edge')
        duties.append((inside[0]-a)/(b-a)*100)
    return {'value':sum(duties)/len(duties)*scale,'minimum_duty_percent':min(duties),'maximum_duty_percent':max(duties),**proof}

def oracles(case):
    """Two distinct analytic PWL/resistor fixtures per measurement mode."""
    from copy import deepcopy
    mode=case['protocol']['measurement']['mode'];scale=case['protocol']['measurement'].get('scale',1);out=[]
    for factor in (1,2):
        q={'temperature_C':25,'device_nodes':{},'checks':[],'components':[]}
        def voltage(name,node,points):q['components'].append({'kind':'V','name':name,'nodes':[node,'0'],'value':{'pwl':points}})
        m={'mode':mode,'signal':'v(p)','window':{'start':0,'end':100e-6},'scale':scale}
        q['analysis']={'kind':'tran','stop_s':100e-6,'max_step_s':10e-9}
        if mode in ('transient_frequency','transient_duty'):
            period=(10+factor*5)*1e-6;duty=.2*factor;edge=.1e-6;points=[[0,0]]
            for n in range(6):
                start=(1+n*period/1e-6)*1e-6
                points.extend([[start,0],[start+edge,5],[start+period*duty,5],[start+period*duty+edge,0]])
            points.append([100e-6,0]);voltage('VORACLE','P',points)
            # Factor 2 needs longer window to include at least five whole periods.
            stop=130e-6 if factor==2 else 100e-6
            q['components'][0]['value']['pwl'][-1]=[stop,0];q['analysis']['stop_s']=stop;m['window']['end']=stop
            m['timing']={'level':2.5,'min_cycles':3,'max_jitter_percent':.1}
            expected=(1/period if mode=='transient_frequency' else duty*100)*scale
        elif mode=='transient_edge_time':
            duration=factor*2e-6;voltage('VORACLE','P',[[0,0],[10e-6,0],[10e-6+duration,5],[100e-6,5]])
            m['timing']={'direction':'rising','low_fraction':.1,'high_fraction':.9,'event_index':1,'min_swing':1};expected=.8*duration*scale
        elif mode=='transient_delay':
            duration=factor*1e-6
            voltage('VTRIGGER','T',[[0,0],[10e-6,0],[10.1e-6,2],[100e-6,2]])
            voltage('VORACLE','P',[[0,5],[10e-6+duration,5],[10.1e-6+duration,0],[100e-6,0]])
            m['timing']={'trigger_signal':'v(t)','trigger_level':1,'trigger_direction':'rising','response_level':2.5,'response_direction':'falling','event_index':1,'max_delay_s':10e-6};expected=duration*scale
        elif mode=='transient_event_sample':
            voltage('VTRIGGER','T',[[0,0],[100e-6,5]])
            voltage('VORACLE','P',[[0,0],[100e-6,10*factor]])
            m['timing']={'event_signal':'v(t)','event_level':2.5,'direction':'rising','event_index':1};expected=5*factor*scale
        elif mode=='transient_span':
            voltage('VORACLE','P',[[0,1],[100e-6,1+factor]])
            expected=factor*scale
        elif mode=='dc_event_sample':
            q['analysis']={'kind':'dc','source':'VORACLE','start':0,'stop':6*factor,'step':.01}
            q['components']=[{'kind':'V','name':'VORACLE','nodes':['P','0'],'value':{'dc':0}},
                             {'kind':'R','name':'RTOP','nodes':['P','T'],'value':1000*(2*factor-1)},
                             {'kind':'R','name':'RBOTTOM','nodes':['T','0'],'value':1000}]
            m['window']={'start':0,'end':6*factor};m['timing']={'event_signal':'v(t)','event_level':1.25,'direction':'rising','event_index':1};expected=2.5*factor*scale
        else:
            q['analysis']={'kind':'dc','source':'VORACLE','start':0,'stop':factor,'step':factor/20}
            q['components']=[{'kind':'V','name':'VORACLE','nodes':['P','0'],'value':{'dc':0}}]
            m['window']={'start':0,'end':factor};expected=factor*scale
        q['measurement']=m;out.append((q,expected))
    return out
