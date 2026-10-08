import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flow_runtime.state import Fault,read
from flow_runtime.registered_library import verified_records
from flow_runtime.coverage_binding import run,load_overlay
from flow_runtime.test_families import mapping,generate
ROOT=Path(__file__).resolve().parents[1]
class RegisteredCoverageFamilyTests(unittest.TestCase):
 def test_library_import_reduces_missing_without_promoting_model(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'coverage'
   result=run(ROOT/'six_inputs/batch.json',ROOT/'runs/unified_registered_regression',out,import_only=True,gpt_library=ROOT/'gpt_test_library')
   d=next(r for r in result['devices'] if r['device']=='ACS723KMATR-20AB-T')
   self.assertLessEqual(d['counts']['tests_without_method'],36);self.assertEqual(d['counts']['electrical_pass'],0)
   root=ROOT/'six_inputs/ACS723';packet=read(root/'device_input.json')
   overlay=load_overlay(root,packet,read(root/'inventory.json'),out)
   self.assertFalse(overlay['items'][0].get('method_binding_complete',False))
 def test_failed_or_mocked_trace_rejected(self):
  from flow_runtime import registered_library as reg
  original=reg.read
  def altered(p):
   x=original(p)
   if Path(p).name=='execution.json':x['test_backend']=True
   return x
  with patch.object(reg,'read',side_effect=altered):
   with self.assertRaises(Fault):list(verified_records(ROOT/'gpt_test_library'))
 def test_all_handbook_test_records_mapped_no_conditions_invented(self):
  for folder,device in [('ACS723','ACS723KMATR-20AB-T'),('ADA4528','ADA4528-1_MSOP')]:
   inv=read(ROOT/'six_inputs'/folder/'inventory.json');rows=mapping(inv,device)
   tests=[r for r in inv['items'] if r['kind']=='test'];self.assertEqual(len(rows),len(tests))
   for row,item in zip(rows,tests):self.assertEqual(row['conditions_verbatim'],item['reference_evidence'].get('conditions',''));self.assertFalse(row['full_record_coverage'])
 def test_swing_preserves_common_mode_and_load(self):
  inv=read(ROOT/'six_inputs/ADA4528/inventory.json');item=next(i for i in inv['items'] if i['id']=='parameter:voh_5_2')
  base=next(c for c in read(ROOT/'gpt_test_library/cases.json')['cases'] if c['device'].startswith('ADA'))
  c=generate(item,base);parts={c['name']:c for c in c['protocol']['components']}
  self.assertAlmostEqual((parts['VCM']['value']['dc']+parts['VNEG']['value']['dc'])/2,2.5)
  self.assertEqual(parts['RLOAD']['value'],2000);self.assertEqual(c['expectation']['limits']['min'],4.96)
 def test_unsupported_noise_does_not_generate_fake_test(self):
  inv=read(ROOT/'six_inputs/ADA4528/inventory.json');item=next(i for i in inv['items'] if i['id']=='parameter:noise_5')
  base=next(c for c in read(ROOT/'gpt_test_library/cases.json')['cases'] if c['device'].startswith('ADA'))
  with self.assertRaises(Fault):generate(item,base)
