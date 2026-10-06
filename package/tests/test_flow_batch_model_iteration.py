import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from flow_runtime import batch_model_iteration as mi
from flow_runtime.state import Fault,save,digest,fingerprint
class ModelIterationTests(unittest.TestCase):
 def task(self):return {'device':'sample','model':{'path':'model.lib','provenance':{'kind':'legacy_unverified'}},'policy':{},'budgets':{},'cases':[{'id':'first'},{'id':'second'}],'limitations':[],'input_integration':{'execution_scope':'configured_regression_only'}}
 def test_only_new_task_enables_fitting(self):
  old=self.task();before=copy.deepcopy(old)
  with patch.object(mi,'load_task',return_value=(copy.deepcopy(old),{})):
   task,_=mi.candidate_task(Path('/tmp'),{'configured_task':'task.json'})
  self.assertNotIn('input_integration',task);self.assertEqual(task['policy']['optimization_attempts'],3);self.assertEqual(old,before);self.assertEqual(task['cases'],before['cases'])
 def test_reference_model_not_promoted(self):
  task=self.task();task['model']['provenance']={'role':'reference_interface_benchmark_only'}
  with patch.object(mi,'load_task',return_value=(task,{})),self.assertRaises(Fault):mi.candidate_task(Path('/tmp'),{'configured_task':'task.json'})
 def test_no_candidate_explicit_gap(self):self.assertIsNone(mi.candidate_task(Path('/tmp'),{}))
 def test_full_baseline_then_optimizer_then_diagnosis(self):
  wf=MagicMock();wf.policy={'model_diagnosis_enabled':True};wf.model_path=Path('model.lib');wf.gaps=[]
  calls=[];wf.evaluate_all.side_effect=lambda *a:calls.append('full_baseline') or [{'test':'first'},{'test':'second'}];wf.optimize.side_effect=lambda:calls.append('optimizer')
  with patch('flow_runtime.model_diagnostics.repair',side_effect=lambda w:calls.append('diagnostic_iteration')):mi.iterate(wf)
  self.assertEqual(calls,['full_baseline','optimizer','diagnostic_iteration']);self.assertEqual(len(wf.results),2);wf.audit.assert_called_once_with('finished')
 def test_authentication_stops_with_evidence(self):
  wf=MagicMock();wf.policy={'model_diagnosis_enabled':True};wf.gaps=[];wf.optimize.side_effect=Fault('authentication','blocked')
  with patch('flow_runtime.model_diagnostics.repair') as repair:mi.iterate(wf)
  repair.assert_not_called();wf.audit.assert_called_once_with('stopped_with_evidence');self.assertEqual(wf.gaps[0]['stage'],'model_iteration')
 def test_reuse_cannot_accept_tampered_artifacts(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);save(p/'state.json',{'simulations':{'k':{'status':'completed','hashes':{'model.lib':'bad'}}}})
   with self.assertRaises(Fault):mi.seed_configured(MagicMock(),p,self.task())
 def test_missing_historical_results_does_not_fake_baseline(self):
  with tempfile.TemporaryDirectory() as t:self.assertEqual(mi.seed_configured(MagicMock(),Path(t),self.task()),0)
 def test_all_six_devices_remain_in_preflight(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);save(p/'batch.json',{'devices':[{'device':str(i),'input':str(i)+'/device_input.json','folder':str(i)} for i in range(6)]})
   for i in range(6):save(p/str(i)/'device_input.json',{'device':str(i)})
   with patch.object(mi,'validate_batch'),patch.object(mi,'Agents',side_effect=AssertionError('API in preflight')):
    report=mi.run(p/'batch.json',p/'old',p/'out',check_only=True)
   self.assertEqual(len(report['devices']),6);self.assertFalse(report['full_batch_delivery']);self.assertTrue(all(x['status']=='blocked_no_candidate_and_qualified_tests' for x in report['devices']))

 def test_new_dispatch_module_rekeys_verified_old_raw(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);old=p/'old';current=p/'new';model=p/'input.lib';model.write_text('model')
   prior={'code':{'spice.py':'same'},'protocol':'frozen','runner':'frozen','actual_circuit':'circuit'};key=fingerprint(prior)
   snapshot={'task':{},'assets':{},'code':{'flow_runtime/spice.py':'same'}};save(old/'input_snapshot.json',snapshot)
   folder=old/'simulations'/key;folder.mkdir(parents=True)
   for n in mi.FILES:(folder/n).write_text('model' if n=='model.lib' else 'circuit' if n=='test.cir' else 'fixture')
   save(folder/'execution.json',{'returncode':0})
   save(old/'state.json',{'identity':fingerprint(snapshot),'simulations':{key:{'status':'completed','hashes':{n:digest(folder/n) for n in mi.FILES}}}})
   store=MagicMock();store.folder=current;store.get.return_value=None
   task={'model':{'path':str(model)},'runner':{},'cases':[{'protocol':{}}]}
   new_signature={**prior,'code':{'spice.py':'same','batch_model_iteration.py':'new'}}
   with patch.object(mi,'raw_data',return_value={'complete':True}),patch.object(mi,'render',return_value='circuit'),patch.object(mi,'signature',return_value=('newkey',new_signature)):
    self.assertEqual(mi.seed_configured(store,old,task),1)
   self.assertEqual((current/'simulations/newkey/test.raw').read_text(),'fixture')
   store.put.assert_called_once()
   new_signature['code']['spice.py']='changed_parser'
   with patch.object(mi,'raw_data',return_value={'complete':True}),patch.object(mi,'render',return_value='circuit'),patch.object(mi,'signature',return_value=('otherkey',new_signature)):
    self.assertEqual(mi.seed_configured(store,old,task),0)
