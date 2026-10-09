import copy,unittest
from flow_runtime import pulse_metrology as pm
from flow_runtime.spice import interpolate,validate_protocol,render,measure
from flow_runtime.state import Fault

class PulseTests(unittest.TestCase):
 def oracle_data(self,q):
  a=q['analysis'];stop=a.get('stop_s',a.get('stop'));step=a.get('max_step_s',a.get('step'))
  xs=[n*step for n in range(round(stop/step)+1)];xs[-1]=stop;signals={}
  for c in q['components']:
   if c['kind']=='V':
    points=c['value'].get('pwl')
    values=[interpolate([x for x,y in points],[y for x,y in points],t) if points else t for t in xs]
    signals['v('+c['nodes'][0].lower()+')']=[complex(v) for v in values]
  if any(c['kind']=='R' for c in q['components']):
   top=next(c['value'] for c in q['components'] if c['name']=='RTOP');signals['v(t)']=[v*1000/(1000+top) for v in signals['v(p)']]
  return {'axis':xs,'signals':signals,'complete':True}
 def test_distinct_analytic_oracles_all_modes(self):
  for mode in sorted(pm.MODES):
   for q,expected in pm.oracles({'protocol':{'measurement':{'mode':mode}}}):
    with self.subTest(mode=mode,expected=expected):
     validate_protocol(q,{'ports':[]});r=measure(q,self.oracle_data(q));self.assertAlmostEqual(r['value']/expected,1,places=6)
 def test_falling_edge_and_ns_scale(self):
  q,e=pm.oracles({'protocol':{'measurement':{'mode':'transient_edge_time','scale':1e9}}})[0]
  q['measurement']['timing']['direction']='falling';q['components'][0]['value']['pwl']=[[x,5-y] for x,y in q['components'][0]['value']['pwl']]
  self.assertAlmostEqual(measure(q,self.oracle_data(q))['value'],e,places=5)
 def test_timing_extra_probes_saved(self):
  for mode in ['transient_delay','transient_event_sample']:
   q,_=pm.oracles({'protocol':{'measurement':{'mode':mode}}})[0]
   self.assertIn('v(t)',render(q,{'ports':[]}))
 def test_duplicate_time_rejected(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_span'}}})[0];d=self.oracle_data(q);d['axis'][2]=d['axis'][1]
  with self.assertRaises(Fault):measure(q,d)
 def test_incomplete_record_rejected(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_frequency'}}})[0];d=self.oracle_data(q);d['axis']=d['axis'][:-100];d['signals']={k:v[:-100] for k,v in d['signals'].items()}
  with self.assertRaises(Fault):measure(q,d)
 def test_absent_frequency_does_not_become_zero(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_frequency'}}})[0];d=self.oracle_data(q);d['signals']['v(p)']=[0j]*len(d['axis'])
  with self.assertRaises(Fault):measure(q,d)
 def test_zero_duty_requires_explicit_option(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_duty'}}})[0];d=self.oracle_data(q);d['signals']['v(p)']=[0j]*len(d['axis'])
  with self.assertRaises(Fault):measure(q,d)
  q['measurement']['timing']['allow_zero']=True;self.assertEqual(measure(q,d)['value'],0)
 def test_delay_already_low_is_fixture_error(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_delay'}}})[0];d=self.oracle_data(q);d['signals']['v(p)']=[0j]*len(d['axis'])
  with self.assertRaisesRegex(Fault,'already crossed'):measure(q,d)
 def test_missing_event_is_not_a_value(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_event_sample'}}})[0];d=self.oracle_data(q);d['signals']['v(t)']=[0j]*len(d['axis'])
  with self.assertRaises(Fault):measure(q,d)
 def test_foreign_fields_and_unbounded_timing_rejected(self):
  q,_=pm.oracles({'protocol':{'measurement':{'mode':'transient_frequency'}}})[0];q['measurement']['timing']['answer']=52000
  with self.assertRaises(Fault):validate_protocol(q,{'ports':[]})
  q['measurement']['timing'].pop('answer');q['measurement']['timing']['min_cycles']=1
  with self.assertRaises(Fault):validate_protocol(q,{'ports':[]})

if __name__=='__main__':unittest.main()
