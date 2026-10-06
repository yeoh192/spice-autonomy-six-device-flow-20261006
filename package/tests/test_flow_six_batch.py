import unittest
from six_batch import aggregate
class SixBatchTests(unittest.TestCase):
 def test_empty_is_not_delivery(self):
  self.assertFalse(aggregate([])['full_batch_delivery'])
 def test_completed_stage_not_full_coverage(self):
  r=aggregate([{'device':'x','stages':[{'returncode':0}]}])
  self.assertEqual(r['status'],'finished_with_gaps');self.assertFalse(r['full_batch_delivery'])
