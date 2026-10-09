import tempfile,unittest
from unittest.mock import patch
from flow_runtime.agents import Agents
from flow_runtime.state import Store,Fault
class RetryTests(unittest.TestCase):
 def run_sequence(self,seq,budget=20):
  d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
  s=Store(d.name,{},dict(api_calls=budget,simulations=0,repairs=0,seconds=60));calls=[]
  def transport(*args):
   calls.append(args[-1]);v=seq.pop(0)
   if v:raise Fault(v,'test',{'finish_reason':'length'} if v=='response_incomplete' else {})
   return {'ok':True},{}
  a=Agents(s,{'design':{'provider':'qwen','model':'fake'}},transport)
  return s,a,calls
 def test_mixed_then_success(self):
  s,a,c=self.run_sequence(['response_incomplete','transport','response_incomplete','transport',None])
  with patch.object(a,'credentials'):self.assertEqual(a.ask('test_designer',{}),{'ok':True})
  self.assertEqual(len(c),5);self.assertGreaterEqual(c[-1],16384)
 def test_exhaustion_does_not_reset(self):
  s,a,c=self.run_sequence(['transport']*3)
  with patch.object(a,'credentials'):
   for _ in range(2):
    with self.assertRaises(Fault):a.ask('test_designer',{})
  self.assertEqual(len(c),3)
 def test_global_budget_still_wins(self):
  s,a,c=self.run_sequence(['transport']*3,budget=1)
  with patch.object(a,'credentials'),self.assertRaises(Fault):a.ask('test_designer',{})
  self.assertEqual(len(c),1)
