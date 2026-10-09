"""Explicit analysis-to-design dispatch and bounded planning evidence."""
import json,tempfile,unittest
from pathlib import Path
from test_flow_runtime import Harness
from flow_runtime.model_diagnostics import repair
from flow_runtime.evidence import compact_history,planner_history,planner_evidence,bundle

class RepairHandoffTests(unittest.TestCase):
 def test_analysis_is_handed_to_edit_authorized_designer_then_regressed(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t),resistance=1400);h.task['policy']['diagnostic_attempts']=1;roles=[]
   def transport(role,route,ctx,tokens):
    if role=='model_diagnoser':
     roles.append(role)
     return {'action':'diagnose','reason':'wrong resistance; change a different numeric direction','evidence_tests':['forward'],'edits':[]},{}
    if role=='model_repair_designer':
     roles.append(role)
     self.assertEqual(ctx['phase'],'repair_design')
     self.assertEqual(ctx['allowed_actions'],['experiment','patch','stop'])
     self.assertEqual(ctx['history'][-1]['status'],'diagnostic_completed')
     self.assertIn('IS authorized',ctx['task'])
     return {'action':'patch','reason':'execute bounded repair','adapter':'parameter','evidence_tests':['forward'],
             'edits':[{'old':'RCORE A K 1400','new':'RCORE A K 1050'}]},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');repair(w)
   self.assertEqual(roles,['model_diagnoser','model_repair_designer'])
   state=h.store.get('checkpoints','model_diagnostics')
   self.assertEqual(state['physical_trials'],1);self.assertEqual(state['history'][-1]['status'],'repair_retained')
   phases=[ctx['phase'] for role,ctx in h.calls if role=='patch_reviewer'];self.assertEqual(phases,['pre_execution','post_execution'])

 def test_recompaction_does_not_erase_curve_residuals(self):
  cases=[{'id':'curve','expectation':{'unit':'A'}}]
  raw={'test':'curve','execution':'completed','acceptance':'fail','comparison':[
      {'x':0,'reference_y':1,'simulated_y':2},{'x':1,'reference_y':3,'simulated_y':1}],
      'large_duplicate':'x'*100000}
  once=compact_history([{'status':'rolled_back','results':[raw]}],cases)
  twice=compact_history(once,cases)
  self.assertEqual(once[0]['results'][0]['signed_residual'],twice[0]['results'][0]['signed_residual'])
  self.assertNotIn('large_duplicate',twice[0]['results'][0])
  short=planner_history(twice,cases)[0]['results'][0]['signed_residual']
  self.assertEqual(short['peak']['signed_error'],-2)
  self.assertNotIn('comparison_samples',short)

 def test_selected_planning_facts_keep_all_guard_targets(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t));w=h.build();w.results=w.evaluate_all(w.model_path,'baseline')
   shared=bundle(w)
   guard=json.loads(json.dumps(shared['tests'][0]));guard['test']='protected'
   guard['actual_test_circuit']='x'*100000;shared['tests'].append(guard)
   result=planner_evidence(shared,['forward'])
   self.assertEqual([x['test'] for x in result['tests']],['forward'])
   self.assertEqual(result['active_test_ids'],['forward','protected'])
   self.assertEqual(result['regression_guards'][0]['expectation'],guard['expectation'])
   self.assertEqual(result['evidence_sha256'],shared['evidence_sha256'])
   self.assertLess(len(json.dumps(result)),len(json.dumps(shared))/10)
