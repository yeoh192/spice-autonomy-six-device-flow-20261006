import copy
import math
import unittest
from flow_runtime import ac_measurements, ac_calibration
from flow_runtime.spice import validate_protocol,validate_measurement_unit,measure,render
from flow_runtime.state import Fault

MODEL={"ports":["P","N"],"declared_ports":["P","N"],"entry":"Oracle"}


class ACInterfaces(unittest.TestCase):
    def setUp(self):
        self.p=ac_calibration.protocols("ac_series_capacitance")[0][0]

    def data(self,z,f=1000):
        return {"axis":[f],"signals":{"v(p)":[complex(.3)],"i(vcal)":[-.3/z]},"complete":True}

    def test_nonunity_source_and_correct_series_definition(self):
        c=1e-6;z=1000-1j/(2*math.pi*1000*c)
        self.assertAlmostEqual(measure(self.p,self.data(z))["value"],c)
        self.p["measurement"]["mode"]="ac_parallel_capacitance"
        self.assertLess(measure(self.p,self.data(z))["value"],c/10)

    def test_sign_and_zero_drive_rejected(self):
        self.p["measurement"]["sign"]=1
        with self.assertRaises(Fault):measure(self.p,self.data(1-1j))
        self.p["measurement"]["sign"]=-1;d=self.data(1-1j);d["signals"]["i(vcal)"]=[0j]
        with self.assertRaises(Fault):measure(self.p,d)

    def test_inductive_port_cannot_be_capacitance(self):
        with self.assertRaises(Fault):measure(self.p,self.data(1+1j))

    def test_missing_signal_is_parser_fault(self):
        d=self.data(1-1j);del d["signals"]["i(vcal)"]
        with self.assertRaises(Fault) as r:measure(self.p,d)
        self.assertEqual(r.exception.kind,"parser")

    def test_voltage_not_current_denominator(self):
        self.p["measurement"]["denominator"]="v(p)"
        with self.assertRaises(Fault):validate_protocol(self.p,MODEL)

    def test_real_frequency_coverage_required(self):
        with self.assertRaises(Fault):measure(self.p,self.data(1-1j,2000))
        self.p["analysis"]={"kind":"ac","sweep":"lin","points":3,"start_Hz":1000,"stop_Hz":3000}
        self.assertIn('.ac lin 3 1000 3000',render(self.p,MODEL))
        with self.assertRaises(Fault):measure(self.p,self.data(1-1j))

    def test_known_curve_max_error(self):
        self.p["measurement"]["mode"]="ac_resistance"
        self.p["analysis"]={"kind":"ac","sweep":"lin","points":3,"start_Hz":1000,"stop_Hz":3000}
        d={"axis":[1000,2000,3000],"signals":{"v(p)":[.3]*3,"i(vcal)":[-.3/10,-.3/20,-.3/50]}}
        r=measure(self.p,d,[(1000,10),(2000,20),(3000,30)])
        self.assertAlmostEqual(r["metrics"]["max_error_over_reference_span_percent"],100)

    def test_unit_does_not_allow_wrong_dimension(self):
        self.p["measurement"]["scale"]=1e6
        validate_measurement_unit({"protocol":self.p,"expectation":{"unit":"μF"}})
        with self.assertRaises(Fault):validate_measurement_unit({"protocol":self.p,"expectation":{"unit":"μH"}})

    def test_all_oracles_validate(self):
        for mode in ac_measurements.MODES:
            for p,expected in ac_calibration.protocols(mode):
                validate_protocol(p,MODEL)
                self.assertTrue(math.isfinite(expected))

    def test_sweep_point_budget(self):
        self.p['analysis']={"kind":"ac","sweep":"lin","points":20000,"start_Hz":1,"stop_Hz":1000}
        with self.assertRaises(Fault):validate_protocol(self.p,MODEL)

    def test_differential_trace_actual_nodes(self):
        self.p['measurement']['signal']='v(p,n)'
        self.p['measurement']['mode']='ac_resistance'
        d={'axis':[1000],'signals':{'v(p)':[4+2j],'v(n)':[1+2j],'i(vcal)':[-.003]}}
        self.assertEqual(measure(self.p,d)['value'],1000)
        self.assertIn('.save i(vcal) v(n) v(p)',render(self.p,MODEL))
        del d['signals']['v(n)']
        with self.assertRaises(Fault):measure(self.p,d)

if __name__=='__main__':unittest.main()
