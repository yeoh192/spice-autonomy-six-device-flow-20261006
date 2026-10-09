"""Offline recovery tests: invalid plans, rollback, direction changes and resume."""
import re
import tempfile
import unittest
from pathlib import Path
from test_flow_runtime import Harness, encode_raw
import test_flow_integration_v2 as integration
from flow_runtime.model_diagnostics import repair, parameter_targets, direction_guard
from flow_runtime.state import Fault, digest, read

class RobustLoopTests(unittest.TestCase):
 def test_invalid_edit_and_pre_review_revision_do_not_spend_trial_slot(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t),resistance=1400);h.task['policy']['diagnostic_attempts']=1
   designs=[];reviews=[]
   def transport(role,route,ctx,tokens):
    if role in ('model_diagnoser','model_repair_designer'):
     designs.append(ctx)
     self.assertEqual(ctx['loop_budget']['executed_trials'],0)
     if len(designs)>1:self.assertEqual(ctx['history'][-1]['status'],'proposal_or_evaluation_error')
     return {'action':'patch','reason':'test repair','evidence_tests':['forward'],
             'edits':[] if len(designs)==1 else [{'old':'RCORE A K 1400','new':'RCORE A K 1050'}]},{}
    if role=='patch_reviewer':
     reviews.append(ctx['phase'])
     return {'decision':'revise' if len(reviews)==1 else 'approve'},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');repair(w)
   state=h.store.get('checkpoints','model_diagnostics')
   self.assertEqual(state['physical_trials'],1);self.assertEqual(len(designs),3)
   self.assertEqual(state['history'][-1]['status'],'repair_retained')
   self.assertEqual(h.backend.count,2)

 def test_rollback_then_other_parameter_is_regressed_and_retained(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t));h.model_path.write_text('.subckt DUT A K\nRCORE A K 1000\nRALT A K 400\n.ends DUT\n')
   h.model['sha256']=digest(h.model_path);h.task['policy']['diagnostic_attempts']=2
   def backend(protocol,model,folder):
    text=Path(model).read_text();r=sum(float(v) for v in re.findall(r'^R\w+ A K ([\d.]+)',text,re.M))
    a=protocol['analysis'];xs=[a['start']+i*a['step'] for i in range(21)]
    encode_raw(folder,xs,{'v(d)':[x*r for x in xs]})
   calls=[]
   def transport(role,route,ctx,tokens):
    if role in ('model_diagnoser','model_repair_designer'):
     calls.append(ctx)
     if len(calls)==1:old,new='RCORE A K 1000','RCORE A K 1250'
     else:
      self.assertEqual(ctx['history'][-1]['status'],'repair_rolled_back')
      self.assertIn('rcore',str(ctx['history'][-1]['parameter_targets']).lower())
      old,new='RALT A K 400','RALT A K 300'
     return {'action':'patch','reason':'change optimization direction','evidence_tests':['forward'],'edits':[{'old':old,'new':new}]},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.backend=backend;h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');repair(w)
   history=h.store.get('checkpoints','model_diagnostics')['history']
   self.assertEqual([r['status'] for r in history],['repair_rolled_back','repair_retained'])
   self.assertIn('RCORE A K 1000',w.model_path.read_text());self.assertIn('RALT A K 300',w.model_path.read_text())

 def test_repeated_parameter_fails_guard_but_joint_and_other_slots_allowed(self):
  source='.subckt DUT A K\n.model MINT NMOS(Kp=20 Vto=3)\nRCORE A K 1000\n.ends DUT\n'
  def targets(new):return parameter_targets(source,{'edits':[{'old':'.model MINT NMOS(Kp=20 Vto=3)','new':new}]})
  kp=targets('.model MINT NMOS(Kp=21 Vto=3)')
  history=[{'status':'rolled_back','baseline_sha256':'same','parameter_targets':kp}]*2
  with self.assertRaises(Fault):direction_guard(kp,history,'same')
  direction_guard(targets('.model MINT NMOS(Kp=20 Vto=3.1)'),history,'same')
  direction_guard(targets('.model MINT NMOS(Kp=21 Vto=3.1)'),history,'same')
  direction_guard(kp,history,'new-baseline')

 def test_repeated_analysis_is_bounded_and_not_counted_as_simulation(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t),resistance=1400);h.task['policy']['diagnostic_attempts']=1
   def transport(role,route,ctx,tokens):
    if role in ('model_diagnoser','model_repair_designer'):return {'action':'diagnose','reason':'no actual change','evidence_tests':['forward'],'edits':[]},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');repair(w)
   state=h.store.get('checkpoints','model_diagnostics')
   self.assertEqual(state['physical_trials'],0);self.assertEqual(state['analyses'],1)
   self.assertEqual(state['consecutive_revisions'],3);self.assertEqual(h.backend.count,1)
   self.assertEqual(w.gaps[-1]['kind'],'proposal_revision_budget')

 def test_optimizer_rollback_evidence_reaches_diagnoser(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t),resistance=1400)
   def transport(role,route,ctx,tokens):
    if role=='model_optimizer':
     if ctx['feedback']:return {'decision':'defer'},{}
     return {'decision':'patch','kind':'parameter','edits':[{'old':'RCORE A K 1400','new':'RCORE A K 1500'}]},{}
    if role in ('model_diagnoser','model_repair_designer'):
     self.assertEqual(ctx['optimizer_history'][-1]['status'],'rolled_back')
     self.assertTrue(ctx['optimizer_history'][-1]['parameter_targets'])
     return {'action':'patch','reason':'correct the observed direction','evidence_tests':['forward'],'edits':[{'old':'RCORE A K 1400','new':'RCORE A K 1050'}]},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');w.optimize();repair(w)
   self.assertEqual(h.store.get('checkpoints','model_diagnostics')['history'][-1]['status'],'repair_retained')

class LastTrialResumeTests(integration.DiagnosticLoopTests):
 # Inherit just the interruption fixture; execute its existing end-to-end test
 # with the smallest trial limit, ensuring an already reserved trial can finish.
 def fixture(self,*args,**kwargs):
  h=super().fixture(*args,**kwargs);h.workflow.policy['diagnostic_attempts']=1
  return h
 test_stop_rejection_probe_feedback_then_full_regression_without_editing=None
 test_probe_execution_failure_has_actual_log_and_never_changes_active_model=None
 test_small_probe_improves_but_full_regression_loss_rolls_back=None
