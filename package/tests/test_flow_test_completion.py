import copy,json,tempfile,unittest
from pathlib import Path
from flow_runtime import capability_development as cd
from flow_runtime.development_interfaces import closed_block,select_draft,PROTOCOL_GUIDE
from flow_runtime.state import Fault,Store,read,save,digest
from flow_runtime.spice import validate_model,validate_protocol,measure

class TestCompletionTests(unittest.TestCase):
 def test_dependency_expansion_preserves_body_and_records_hash(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'opamp.sub').write_text('.subckt opamp P N O\nR1 O 0 1000\n.ends\n')
   block,receipts=closed_block('.subckt DEV P N\nX1 P N N opamp\n.lib "opamp.sub"\n.ends DEV\n','DEV',r)
   self.assertNotIn('.lib',block);self.assertIn('X1 P N N opamp',block);self.assertEqual(receipts[0]['sha256'],digest(r/'opamp.sub'))
 def test_dependency_path_escape_rejected(self):
  with self.assertRaises(Fault):closed_block('.subckt DEV P N\n.include "../opamp.sub"\n.ends\n','DEV')
 def test_temperature_interface_selection_not_first_file(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);save(r/'drafts.json',{'cases':[{'reference_ids':['warm'],'protocol':{'temperature_C':20},'model':{'sha256':'warm'}},{'reference_ids':['cold'],'protocol':{'temperature_C':-40},'model':{'sha256':'cold'}}]})
   model=select_draft(r,{'fixture_drafts':'drafts.json'},{'id':'other','reference_evidence':{'conditions':'TA=-40°C; 120 Hz'}})
   self.assertEqual(model['sha256'],'cold')
   with self.assertRaises(Fault):select_draft(r,{'fixture_drafts':'drafts.json'},{'id':'other','reference_evidence':{'conditions':'Z(-40°C)/Z(20°C)'}})
 def test_record_checkpoint_isolation_with_shared_budget(self):
  with tempfile.TemporaryDirectory() as t:
   store=Store(t,'test',{'api_calls':2,'simulations':2,'repairs':2,'seconds':60})
   a,b=cd.RecordStore(store,'a'),cd.RecordStore(store,'b');a.put('checkpoints','workflow',{'active':'a'})
   self.assertIsNone(b.get('checkpoints','workflow'));a.reserve('api_calls');self.assertEqual(b.data['usage']['api_calls'],1)
 def test_current_voltage_probe_oracle_sign_and_scale(self):
  p={'analysis':{'kind':'dc','source':'VSCAN','start':0,'stop':1,'step':.1},'measurement':{'mode':'sample','signal':'i(vprobe)','at':.5,'scale':1e9,'sign':-1}}
  for q,e in cd.sample_oracles(p):
   validate_protocol(q,{'ports':['A','K']});target=next(c['value']['dc'] for c in q['components'] if c['kind']=='I')
   result=measure(q,{'complete':True,'axis':[-.5,1.5],'signals':{'i(vprobe)':[complex(target)]*2}})
   self.assertAlmostEqual(result['value'],e)
 def test_guide_has_only_real_protocol_fields(self):
  self.assertEqual(set(PROTOCOL_GUIDE['top_level_keys']),{'temperature_C','device_nodes','components','analysis','measurement','checks','models','method'})
 def test_actual_five_interfaces_preserve_reference_role(self):
  root=Path(__file__).resolve().parents[1]/'six_inputs'
  if not root.exists():self.skipTest('package-only source proof')
  with tempfile.TemporaryDirectory() as t:
   for name in ['1N4148','EMHK350ARA470MF80G','750311423','ACS723','ADA4528']:
    r=root/name;packet=read(r/'device_input.json');m=cd.reference_model(r,packet,Path(t)/name);validate_model(Path(m['path']).read_text(),m)
    if name not in ('1N4148',):self.assertEqual(m['provenance']['role'],'reference_interface_benchmark_only')
    if name=='ACS723':
     text=Path(m['path']).read_text();self.assertIn('params: Sensitivity=0.10000000000000001 Polarity=2',text);self.assertNotIn('.lib ',text);self.assertEqual(len(m['provenance']['dependency_receipts']),2)
 def test_preflight_all_eligible_and_filters_buk_without_api(self):
  from unittest.mock import patch
  root=Path(__file__).resolve().parents[1];batch=root/'six_inputs/batch.json'
  if not batch.exists():self.skipTest('package-only source proof')
  runner=Path('/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice')
  if not runner.exists():self.skipTest('local runner hash')
  with tempfile.TemporaryDirectory() as t,patch.object(cd,'Agents',side_effect=AssertionError('API in preflight')):
   devices=['1N4148','EMHK350ARA470MF80G','750311423','ACS723KMATR-20AB-T','ADA4528-1_MSOP']
   result=cd.run(batch,runner,Path(t)/'out',check_only=True,max_items=250,seconds=7200,devices=devices)
   self.assertEqual(len(result['devices']),5);self.assertEqual(sum(d['scheduled_tests'] for d in result['devices']),120)
   self.assertFalse(result['full_batch_delivery'])
