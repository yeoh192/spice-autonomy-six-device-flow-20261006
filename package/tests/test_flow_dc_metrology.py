import unittest
from flow_runtime.spice import measure,validate_protocol,validate_measurement_unit
from flow_runtime.dc_transfer import value
from flow_runtime.state import Fault
class DCMetrologyTests(unittest.TestCase):
 def test_signed_slope_and_error(self):
  a={'kind':'dc','source':'IIN','start':-1,'stop':1,'step':1}
  self.assertAlmostEqual(value(a,{'mode':'dc_slope','scale':1000},[-1,0,1],[2.4,2.5,2.6]),100)
  self.assertAlmostEqual(value(a,{'mode':'dc_slope','scale':1000},[-1,0,1],[2.6,2.5,2.4]),-100)
  self.assertAlmostEqual(value(a,{'mode':'dc_sensitivity_error_percent','nominal':.1},[-1,0,1],[2.4,2.5,2.6]),0)
 def test_nonlinearity_detects_bowed_response(self):
  self.assertAlmostEqual(value({}, {'mode':'dc_linearity_percent'},[-1,0,1],[0,1.1,2]),5)
 def test_two_current_signs_not_hidden_before_subtraction(self):
  p={'analysis':{'kind':'dc','source':'VD','start':0,'stop':1,'step':1},'measurement':{'mode':'dc_current_difference','signal':'i(VP)','denominator':'i(VM)','at':1,'scale':1e12,'absolute':True},'checks':[]}
  d={'axis':[0,1],'signals':{'i(vp)':[complex(220e-12)]*2,'i(vm)':[complex(-220e-12)]*2}}
  self.assertAlmostEqual(measure(p,d)['value'],440)
  p['measurement']['mode']='dc_current_max';self.assertAlmostEqual(measure(p,d)['value'],220)
 def test_percent_cannot_be_voltage_sample(self):
  with self.assertRaises(Fault):validate_measurement_unit({'protocol':{'analysis':{'kind':'dc'},'measurement':{'mode':'sample','signal':'v(out)','scale':1}},'expectation':{'unit':'%'}})
