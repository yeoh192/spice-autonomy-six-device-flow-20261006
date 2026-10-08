import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flow_runtime.state import save,digest,Fault
from flow_runtime.registered_integration import augment_candidate
from six_batch import run

class IntegrationTests(unittest.TestCase):
 def task(self):
  return {'device':'D','model':{'sha256':'a','entry':'X','ports':['A']},'cases':[{'id':'old','protocol':{},'expectation':{}}], 'inventory':{'items':[{'id':'r','bindings':[]}]},'budgets':{'simulations':8},'acceptance_standard':{}}
 def test_exact_model_merges_and_full_regression_budget_grows(self):
  original=self.task();registered=copy.deepcopy(original);registered['cases'] += [{'id':'new','protocol':{},'expectation':{},'reference_ids':['r']},{'id':'diagnostic','coverage_registration_allowed':False}]
  with patch('flow_runtime.registered_integration.load_task',return_value=(registered,{})),patch('flow_runtime.registered_integration.digest',return_value='h'):
   task,_,receipt=augment_candidate(original,[{'device':'D','task':'a','task_sha256':'h'}])
  self.assertEqual(len(task['cases']),2);self.assertEqual(task['budgets']['simulations'],16);self.assertEqual(task['inventory']['items'][0]['bindings'],['new']);self.assertEqual(receipt['excluded'][0]['reason'],'diagnostic_only');self.assertEqual(len(original['cases']),1)
 def test_different_model_is_not_silently_rebound(self):
  task=self.task();registered=copy.deepcopy(task);registered['model']['sha256']='different'
  with patch('flow_runtime.registered_integration.load_task',return_value=(registered,{})),patch('flow_runtime.registered_integration.digest',return_value='h'):
   _,_,receipt=augment_candidate(task,[{'device':'D','task':'a','task_sha256':'h'}])
  self.assertEqual(receipt['excluded'][0]['reason'],'different_model_or_interface')
 def test_same_id_changed_acceptance_rejected(self):
  task=self.task();registered=copy.deepcopy(task);registered['cases'][0]['expectation']={'max':999}
  with patch('flow_runtime.registered_integration.load_task',return_value=(registered,{})),patch('flow_runtime.registered_integration.digest',return_value='h'),self.assertRaises(Fault):
   augment_candidate(task,[{'device':'D','task':'a','task_sha256':'h'}])
 def fixture(self,p):
  save(p/'packet.json',{'device':'D'});save(p/'batch.json',{'devices':[{'device':'D','input':'packet.json','folder':'D','configured_tests':0,'draft_tests':0}]});save(p/'cal.json',{});save(p/'registered.json',{})
  return [{'device':'D','tests':2,'task':str(p/'model/task.json'),'task_sha256':'h','proof_identity':'p'}]
 def test_library_dispatched_to_regression_coverage_and_iteration(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);rows=self.fixture(p);calls=[]
   def execute(cmd):calls.append(cmd);return SimpleNamespace(returncode=0)
   with patch('six_batch.validate_batch'),patch('flow_runtime.registered_integration.load_registered',return_value=rows):
    result=run(p/'batch.json',p/'out',p/'cal.json',p/'runner',p/'prior',check_only=True,execute=execute,registered_tasks=p/'registered.json',gpt_library=p/'library')
   self.assertEqual(calls[0][2],'preflight');self.assertIn('--gpt-library',calls[1]);self.assertIn('--registered-tasks',calls[-1]);self.assertEqual(result['registered_regression']['tests'],2)
 def test_library_execution_failure_stops_before_paid_stages(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);rows=self.fixture(p);calls=[]
   def execute(cmd):calls.append(cmd);return SimpleNamespace(returncode=1)
   with patch('six_batch.validate_batch'),patch('flow_runtime.registered_integration.load_registered',return_value=rows):
    result=run(p/'batch.json',p/'out',p/'cal.json',p/'runner',p/'prior',execute=execute,registered_tasks=p/'registered.json',gpt_library=p/'library')
   self.assertEqual(len(calls),1);self.assertEqual(result['status'],'stopped_with_evidence')
 def test_changed_library_proof_cannot_resume(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);rows=self.fixture(p)
   with patch('six_batch.validate_batch'),patch('flow_runtime.registered_integration.load_registered',return_value=rows):
    run(p/'batch.json',p/'out',p/'cal.json',p/'runner',p/'prior',check_only=True,execute=lambda cmd:SimpleNamespace(returncode=0),registered_tasks=p/'registered.json',gpt_library=p/'library')
    rows[0]['proof_identity']='tampered'
    with self.assertRaises(ValueError):run(p/'batch.json',p/'out',p/'cal.json',p/'runner',p/'prior',check_only=True,resume=True,execute=lambda cmd:self.fail('must stop before execution'),registered_tasks=p/'registered.json',gpt_library=p/'library')
