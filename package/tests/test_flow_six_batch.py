import unittest
from six_batch import aggregate
class SixBatchTests(unittest.TestCase):
 def test_empty_is_not_delivery(self):
  self.assertFalse(aggregate([])['full_batch_delivery'])
 def test_completed_stage_not_full_coverage(self):
  r=aggregate([{'device':'x','stages':[{'returncode':0}]}])
  self.assertEqual(r['status'],'finished_with_gaps');self.assertFalse(r['full_batch_delivery'])
 def test_coverage_runs_before_development_and_after_qualification(self):
  import tempfile
  from pathlib import Path
  from types import SimpleNamespace
  from unittest.mock import patch
  from six_batch import run
  from flow_runtime.state import save
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);save(root/'packet.json',{'device':'DUT'});save(root/'batch.json',{'devices':[{'device':'DUT','input':'packet.json','folder':'DUT','configured_tests':0,'draft_tests':0}]});save(root/'cal.json',{})
   calls=[]
   def execute(cmd):calls.append(cmd);return SimpleNamespace(returncode=0)
   with patch('six_batch.validate_batch'):
    result=run(root/'batch.json',root/'out',root/'cal.json',root/'runner',root/'previous',check_only=True,execute=execute)
   stages=[Path(c[1]).name for c in calls]
   self.assertEqual(stages,['coverage_audit.py','develop_inventory.py','spice_flow.py','coverage_audit.py','iterate_models.py'])
   self.assertIn('--coverage',calls[1]);self.assertIn('--previous',calls[-2]);self.assertIn('--source-runtime',calls[-1]);self.assertFalse(result['full_batch_delivery'])
 def test_coverage_corruption_stops_before_paid_development(self):
  import tempfile
  from pathlib import Path
  from types import SimpleNamespace
  from unittest.mock import patch
  from six_batch import run
  from flow_runtime.state import save
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);save(root/'packet.json',{'device':'DUT'});save(root/'batch.json',{'devices':[{'device':'DUT','input':'packet.json','folder':'DUT','configured_tests':0,'draft_tests':0}]});save(root/'cal.json',{})
   calls=[]
   def execute(cmd):calls.append(cmd);return SimpleNamespace(returncode=1)
   with patch('six_batch.validate_batch'):
    result=run(root/'batch.json',root/'out',root/'cal.json',root/'runner',root/'previous',execute=execute)
   self.assertEqual(len(calls),1);self.assertEqual(result['status'],'stopped_with_evidence')
