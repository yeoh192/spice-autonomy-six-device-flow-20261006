import tempfile,unittest
from flow_runtime.state import Store
from flow_runtime.response_recovery import settings,truncated,focus
class RecoveryTests(unittest.TestCase):
 def test_persistent_budget_and_route_isolation(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(d,{},dict(api_calls=20,simulations=0,repairs=0,seconds=60));r={'provider':'qwen','model':'a','base_url':'https://example.com/v1'}
   self.assertEqual(settings(s,r)['max_tokens'],4096)
   for expected in (8192,16384,32768,32768):self.assertEqual(truncated(s,r)['max_tokens'],expected)
   self.assertEqual(settings(s,r)['max_tokens'],32768)
   self.assertEqual(settings(s,{**r,'model':'b'})['max_tokens'],4096)
 def test_single_focus_preserves_targets(self):
  rows=[{'test':str(i),'expectation':{'max':i},'actual_test_circuit':'circuit','result':{'acceptance':'fail'}} for i in range(3)]
  c={'shared_evidence':{'tests':rows}}
  a=focus(c,{'truncations':1});b=focus(c,{'truncations':2})
  self.assertNotEqual(a['focus_test_ids'],b['focus_test_ids'])
  self.assertEqual([r['expectation'] for r in a['shared_evidence']['tests']],[r['expectation'] for r in rows])
  self.assertEqual(sum('actual_test_circuit' in r for r in a['shared_evidence']['tests']),1)
  self.assertEqual(sum('actual_test_circuit' in r for r in rows),3)
