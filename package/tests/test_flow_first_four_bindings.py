import copy,unittest
from pathlib import Path
from flow_runtime.state import read,Fault
from flow_runtime.spice import measure,interpolate,validate_measurement_unit
from flow_runtime.ac_calibration import protocols
from flow_runtime.transient_metrology import value
ROOT=Path(__file__).resolve().parents[1]
class FirstFourBindingTests(unittest.TestCase):
 def test_equal_drive_cap_ratio_uses_room_over_cold_current(self):
  cases=read(ROOT/'gpt_test_library/first_four_cases.json')['cases']
  c=next(c for c in cases if c['id']=='impedance_ratio_minus40_room_120Hz')
  p=c['protocol'];self.assertEqual([e['value']['ac'] for e in p['components']],[.1,.1])
  r=measure(p,{'axis':[120],'signals':{'i(vroom)':[-.1j],'i(vcold)':[-.025j]}},None)
  self.assertEqual(r['value'],4);self.assertEqual(c['expectation']['limits']['max'],3)
 def test_missing_saturation_and_25_model_remain_gaps(self):
  catalog=read(ROOT/'gpt_test_library/first_four_cases.json')
  gaps={r['reference_id'] for r in catalog['gaps']}
  self.assertIn('parameter:isat',gaps);self.assertIn('parameter:impedance_ratio_-25',gaps)
  diagnostics=[c for c in catalog['cases'] if c['device']=='750311423']
  self.assertTrue(all(c.get('coverage_registration_allowed') is False for c in diagnostics))
 def test_peak_rejects_missing_actual_window(self):
  m={'mode':'transient_peak','start_s':1,'at':3}
  with self.assertRaises(Fault):value(m,[0,1,2],[0,1,2],interpolate)
 def test_recovery_requires_negative_peak_then_threshold_return(self):
  m={'mode':'transient_recovery','start_s':0,'at':6,'target':{'value':-1},'scale':1}
  r=value(m,[0,1,2,3,4,5,6],[2,2,0,-2,-2,0,0],interpolate)
  self.assertEqual(r['zero_crossing_s'],2);self.assertEqual(r['return_crossing_s'],4.5);self.assertEqual(r['value'],2.5)
  with self.assertRaises(Fault):value(m,[0,1,2,3,4,5,6],[2,2,0,-2,-2,-2,-2],interpolate)
 def test_recovery_current_cannot_claim_voltage(self):
  p={'measurement':{'mode':'transient_recovery','signal':'i(vsense)','scale':1e9},'analysis':{'kind':'tran'}}
  with self.assertRaises(Fault):validate_measurement_unit({'protocol':p,'expectation':{'unit':'V'}})
 def test_buk_series_without_csv_not_given_fake_reference(self):
  cs=read(ROOT/'gpt_test_library/first_four_cases.json')['cases']
  partial=[c for c in cs if c['id'].startswith('figure_') and c['device']=='BUK7K52-60E']
  self.assertTrue(partial)
  for c in partial:self.assertNotIn('reference',c);self.assertEqual(c['expectation']['limits'],{});self.assertNotIn('typical',c['expectation'])
if __name__=='__main__':unittest.main()
