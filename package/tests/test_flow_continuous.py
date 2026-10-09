import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flow_runtime.state import Store,Fault
from flow_runtime.continuous import run
from flow_runtime.workflow import retention
from flow_runtime.task import DEFAULT_POLICY
from flow_runtime.agents import Agents
class ContinuousTests(unittest.TestCase):
 def make(self,f):
  s=Store(f,{},dict(api_calls=0,simulations=0,repairs=0,seconds=0));s.continuous=True
  w=SimpleNamespace(store=s,task={'model':{}},policy={**DEFAULT_POLICY,'continuous_until_acceptance':True},cases=[{'id':'x','expectation':{'unit':'V','typical':1}}],inventory={'review_status':'reviewed_complete','items':[{'id':'x','kind':'test','binding_complete':True,'bindings':['x']}]},results=[],gaps=[],model_path=Path(f)/'model.lib',optimization_index=0)
  w.model_path.write_text('initial');w.checkpoint=lambda:None;w.select_template=lambda:None;w.develop_missing=lambda:None
  w.evaluate_all=lambda *a:[{'test':'x','execution':'completed','acceptance':'fail','value':2}]
  w.audit=lambda status:{'status':status};return w
 def test_failed_measurement_and_local_defer_cannot_end_master(self):
  with tempfile.TemporaryDirectory() as f:
   w=self.make(f);w.evaluate_all=lambda *a:[{'test':'x','execution':'failed','acceptance':'not_evaluated'}];calls=[]
   def optimize():
    calls.append(w.store.get('checkpoints','master_cycle'))
    if len(calls)==3:w.results=[{'test':'x','execution':'completed','acceptance':'pass','value':1}]
   w.optimize=optimize
   with patch('flow_runtime.model_diagnostics.repair',lambda x:None):r=run(w)
   self.assertEqual(r['status'],'finished');self.assertEqual(calls,[0,1,2])
 def test_missing_reference_pause_not_success(self):
  with tempfile.TemporaryDirectory() as f:
   w=self.make(f);w.gaps=[{'kind':'missing_reference','reference_id':'curve'}];w.optimize=lambda:None
   with patch('flow_runtime.model_diagnostics.repair',lambda x:None):r=run(w)
   self.assertEqual(r['status'],'paused_missing_information');self.assertTrue((Path(f)/'missing_information.json').exists())
 def test_interrupt_resume_preserves_phase(self):
  with tempfile.TemporaryDirectory() as f:
   w=self.make(f);w.optimize=lambda:(_ for _ in ()).throw(KeyboardInterrupt())
   with patch('flow_runtime.model_diagnostics.repair',lambda x:None):r=run(w)
   self.assertEqual(r['status'],'interrupted');self.assertEqual(w.store.get('checkpoints','master')['phase'],'optimize')
   w.select_template=lambda:self.fail('resume repeated selection')
   w.optimize=lambda:setattr(w,'results',[{'test':'x','execution':'completed','acceptance':'pass','value':1}])
   with patch('flow_runtime.model_diagnostics.repair',lambda x:None):self.assertEqual(run(w)['status'],'finished')
 def test_usage_counted_without_budget_termination(self):
  with tempfile.TemporaryDirectory() as f:
   w=self.make(f)
   for resource in ('api_calls','simulations','repairs'):
    for n in range(4):w.store.reserve(resource)
    self.assertEqual(w.store.data['usage'][resource],4)
   self.assertGreater(w.store.remaining_seconds(),100)
 def test_partial_retention_full_scope_and_pass_protection(self):
  cases=[{'id':x,'expectation':{'typical':1,'unit':'V'}} for x in ('good','broken')]
  before=[{'test':'good','execution':'completed','acceptance':'fail','value':2},{'test':'broken','execution':'failed','acceptance':'not_evaluated'}]
  after=copy.deepcopy(before);after[0].update(value=1,acceptance='pass');p={**DEFAULT_POLICY,'continuous_until_acceptance':True}
  self.assertTrue(retention(cases,before,after,p)[0]);self.assertFalse(retention(cases,before,after[:1],p)[0])
  before[0].update(value=1,acceptance='pass');after[0].update(value=2,acceptance='fail');self.assertFalse(retention(cases,before,after,p)[0])
 def test_api_retry_past_two_calls_and_unique_attempt_artifacts(self):
  with tempfile.TemporaryDirectory() as f:
   w=self.make(f);calls=[]
   def transport(*args):
    calls.append(args)
    if len(calls)<4:raise Fault('authentication' if len(calls)==1 else 'transport','injected')
    return {'decision':'approve','issues':[]},{}
   a=Agents(w.store,{'design':{'provider':'qwen','model':'fake'},'review':{'provider':'glm','model':'fake'}},transport)
   with patch.object(a,'credentials',lambda roles:None),patch('flow_runtime.continuous.time.sleep',lambda t:None):r=a.ask('template_reviewer',{'candidates':[]})
   self.assertEqual(r['decision'],'approve');self.assertEqual(len(calls),4);self.assertEqual(w.store.data['usage']['api_calls'],4);self.assertEqual(len(list((Path(f)/'requests').glob('*/attempt_*'))),4)
 def test_real_optimizer_retains_partial_improvement_after_all_tests_attempted(self):
  from test_flow_runtime import Harness
  with tempfile.TemporaryDirectory() as f:
   h=Harness(Path(f),resistance=2000);h.task['policy'].update(continuous_until_acceptance=True,optimization_attempts=1,execution_attempts=1)
   broken=copy.deepcopy(h.case);broken['id']='broken';broken['protocol']['checks']=[{'signal':'v(d)','from':0,'to':.001,'min':10,'max':11}];h.task['cases'].append(broken)
   def transport(role,route,context,tokens):
    if role=='model_optimizer':return {'decision':'patch','kind':'parameter','reason':'repair measured residual while preserving failure evidence','evidence_ids':['forward'],'edits':[{'old':'RCORE A K 2000','new':'RCORE A K 1000'}]},{}
    return h.normal_transport(role,route,context,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');w.optimize()
   self.assertIn('RCORE A K 1000',w.model_path.read_text());self.assertEqual(len(w.results),2);self.assertEqual(w.results[1]['execution'],'failed')
   self.assertTrue(any(e['stage']=='model_patch' and e['status']=='retained' for e in h.store.data['events']))

if __name__=='__main__':unittest.main()
