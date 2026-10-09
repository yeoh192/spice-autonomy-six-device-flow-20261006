import unittest,copy
from flow_runtime.request_context import fit
class FitTests(unittest.TestCase):
 def test_targets_and_peak_preserved(self):
  c={'shared_evidence':{'tests':[{'test':'a','expectation':{'max':10},'actual_test_circuit':'R1 D 0 1','signed_residual':{'peak':{'signed_error':2},'signed_mean':1,'comparison_samples':['x'*100000],'intervals':['x'*100000]}}]}}
  old=copy.deepcopy(c);v=fit('model_optimizer',c)
  self.assertEqual(c,old);r=v['shared_evidence']['tests'][0]
  self.assertEqual(r['expectation'],{'max':10});self.assertEqual(r['signed_residual']['peak'],{'signed_error':2});self.assertEqual(r['actual_test_circuit'],'R1 D 0 1')
