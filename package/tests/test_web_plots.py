import tempfile,unittest
from pathlib import Path
from web_ui.plots import generate
class PlotTests(unittest.TestCase):
 def test_recorded_points_only(self):
  with tempfile.TemporaryDirectory() as d:
   rows=[{'x':1,'reference_y':2,'simulated_y':2.1},{'x':2,'reference_y':3,'simulated_y':2.9}]
   out=Path(d);p=generate({'results':[{'test':'curve','unit':'A','acceptance':'fail','comparison':rows},{'test':'pending','value':1}]},out)
   self.assertEqual(len(p),1);self.assertEqual(p[0]['acceptance'],'fail');self.assertTrue((out/p[0]['png']).is_file());self.assertIn('2.1',(out/p[0]['csv']).read_text())
