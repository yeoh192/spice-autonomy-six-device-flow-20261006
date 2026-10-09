import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock
from test_flow_runtime import Harness
from flow_runtime.model_diagnostics import review_results
from flow_runtime.state import Fault,digest
from flow_runtime.evidence import bundle,compact_history

class ReviewPhaseTests(unittest.TestCase):
 def test_pre_review_then_execution_then_post_review_and_rejection_rolls_back(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t),resistance=1400);phases=[]
   def transport(role,route,ctx,tokens):
    if role=='model_optimizer':
     if phases:return {'decision':'defer'},{}
     return {'decision':'patch','kind':'parameter','edits':[{'old':'RCORE A K 1400','new':'RCORE A K 1050'}]},{}
    if role=='patch_reviewer':
     phases.append(ctx['phase'])
     if ctx['phase']=='pre_execution':
      self.assertFalse(ctx['candidate_executed']);return {'decision':'approve'},{}
     self.assertTrue(ctx['candidate_executed'])
     for row in ctx['candidate_evidence']['tests']:self.assertEqual(row['result']['model_sha256'],ctx['candidate_sha256'])
     self.assertNotEqual(ctx['candidate_sha256'],digest(h.model_path))
     return {'decision':'revise','reason':'post-trial concern'},{}
    return h.normal_transport(role,route,ctx,tokens)
   h.transport=transport;w=h.build();w.results=w.evaluate_all(w.model_path,'baseline');before=digest(w.model_path);w.optimize()
   self.assertEqual(phases,['pre_execution','post_execution']);self.assertEqual(digest(w.model_path),before)
 def test_baseline_hash_cannot_be_claimed_as_candidate(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'candidate.lib';p.write_text('candidate');w=MagicMock()
   with self.assertRaises(Fault):review_results(w,p,[{'id':'x'}],[{'test':'x','execution':'completed','model_sha256':'baseline'}],{})
   w.agents.ask.assert_not_called()
 def test_missing_guard_test_rejected_before_review(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'candidate.lib';p.write_text('candidate')
   with self.assertRaises(Fault):review_results(MagicMock(),p,[{'id':'x'},{'id':'guard'}],[{'test':'x','execution':'completed','model_sha256':digest(p)}],{})
 def test_compact_bundle_keeps_full_file_and_drops_nested_snapshot(self):
  with tempfile.TemporaryDirectory() as t:
   h=Harness(Path(t));w=h.build();w.inventory['items'][0]['source_record']={'large':'x'*100000}
   w.results=w.evaluate_all(w.model_path,'baseline');b=bundle(w)
   full=json.loads(Path(b['full_evidence_file']).read_text())
   self.assertLess(len(json.dumps(b)),len(json.dumps(full))/2)
   self.assertIn('source_record',full['tests'][0]['manual_records'][0]);self.assertIn('signed_residual',b['tests'][0])
 def test_history_keeps_failure_log_without_duplicate_capability_catalog(self):
  r=compact_history([{'status':'experiment_failed','allowed_scope':{'large':'x'*10000},'candidate_diagnostics':[{'test':'a','log_tail':'failed','actual_test_circuit':'huge'}]}],[])[0]
  self.assertNotIn('allowed_scope',r);self.assertEqual(r['candidate_diagnostics'],[{'test':'a','log_tail':'failed'}])
