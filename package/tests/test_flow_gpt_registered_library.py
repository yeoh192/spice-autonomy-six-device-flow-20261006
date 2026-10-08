import unittest
from pathlib import Path
from unittest.mock import patch
import use_gpt_test_library as lib
from flow_runtime.state import Fault
from flow_runtime.task import load_task

class GPTRegisteredLibraryTests(unittest.TestCase):
 def test_real_registration_includes_failures_without_falsifying_pass(self):
  rows=lib.records()
  self.assertEqual(len(rows),22)
  self.assertEqual(sum(r['receipt']['trial']['acceptance']=='fail' for r in rows),2)
  self.assertTrue(all(r['qualification_backend']=='real_LTspice' and not r['delivery_claim'] for r in rows))
 def test_damaged_calibration_cannot_be_registered(self):
  with patch.object(lib,'artifacts_valid',return_value=False):
   with self.assertRaises(Fault):lib.records()
 def test_symbol_port_order_is_not_package_number_order(self):
  row=next(r for r in lib.records() if r['case']['device']=='ADA4528-1_MSOP')
  self.assertEqual(row['case']['protocol']['device_nodes'],{'P1':'IN','P2':'OUT','P3':'VCC','P4':'0','P5':'OUT'})
  self.assertEqual(row['case']['port_binding_evidence']['map']['P1']['package_pin'],3)
 def test_registered_tasks_are_valid_standard_entrypoint_inputs(self):
  import json
  batch=json.loads((lib.ROOT/'runs/registered_tasks/batch.json').read_text())
  self.assertEqual(sum(r['tests'] for r in batch['tasks']),22)
  for row in batch['tasks']:load_task(Path(row['task']))
