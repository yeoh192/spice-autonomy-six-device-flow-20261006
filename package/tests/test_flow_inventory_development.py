import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flow_runtime import capability_development as cd
from flow_runtime.spice import measure,validate_protocol
from flow_runtime.state import Fault,save,digest
from flow_runtime import autonomous_qualification as aq

class InventoryDevelopmentTests(unittest.TestCase):
 def item(self,**extra):
  r={'id':'r','kind':'test','applicable':True,'reference_evidence':{'values':{'typ':.4},'unit':'V','conditions':'TA=25°C'},'evidence':[]};r.update(extra);return r
 def test_no_skipping_constraints(self):
  q=cd.queue({}, {'items':[self.item(kind='constraint')]});self.assertEqual(q[0]['status'],'constraint_or_classification_review_required')
 def test_unknown_reference_not_invented(self):
  i=self.item();i['reference_evidence']['values']['typ']='VCC/2';self.assertEqual(cd.queue({}, {'items':[i]})[0]['status'],'reference_or_interface_gap')
 def test_existing_bindings_skipped(self):
  self.assertEqual(cd.queue({}, {'items':[self.item(binding_complete=True)]}),[])
 def test_not_applicable_is_not_tested(self):
  self.assertEqual(cd.queue({}, {'items':[self.item(applicable=False)]}),[])
 def test_complete_record_eligible(self):
  self.assertEqual(cd.queue({}, {'items':[self.item()]})[0]['status'],'development_eligible')
 def protocol(self):
  return {'analysis':{'kind':'dc','source':'VD','start':0,'stop':5,'step':1},'measurement':{'mode':'sample','signal':'v(P,N)','at':2.5,'scale':1e6,'sign':-1,'absolute':True}}
 def test_voltage_sampling_nonzero_common_mode_and_scale(self):
  model={'ports':['P','N']}
  for p,expected in cd.sample_oracles(self.protocol()):
   validate_protocol(p,model)
   source=p['components'][0]['value']['dc'];axis=[p['analysis']['start'],p['analysis']['stop']]
   data={'axis':axis,'signals':{'v(p)':[complex(.3+source)]*2,'v(n)':[complex(.3)]*2},'complete':True}
   self.assertAlmostEqual(measure(p,data)['value'],expected)
 def test_current_sample_has_independent_oracles(self):
  p=self.protocol();p['measurement']['signal']='i(VSUP)'
  self.assertEqual(len(cd.sample_oracles(p)),2)
 def test_same_nodes_block(self):
  p=self.protocol();p['measurement']['signal']='v(P,P)'
  with self.assertRaises(Fault):cd.sample_oracles(p)
 def test_external_model_dependency_not_silently_removed(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'m.lib').write_text('.subckt DEMO P N\n.lib other.sub\n.ends\n')
   packet={'device':'DEMO_variant','reference_assets':[{'role':'reference_interface_benchmark_only','path':'m.lib'}]}
   with self.assertRaises(Fault) as e:cd.reference_model(r,packet,r/'out')
   self.assertEqual(e.exception.kind,'model_dependency_gap')
 def test_numeric_model_ports_wrapped_without_internal_edit(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);original='.subckt DEMO 1 2\nR1 1 2 1000\n.ends DEMO\n';(r/'m.lib').write_text(original)
   model=cd.reference_model(r,{'device':'DEMO_MSOP','reference_assets':[{'role':'reference_interface_benchmark_only','path':'m.lib'}]},r/'out')
   self.assertIn('R1 1 2 1000',Path(model['path']).read_text());self.assertEqual(model['ports'],['P1','P2']);self.assertFalse(model['provenance']['physical_binding_verified'])
 def test_no_live_work_in_preflight(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);(r/'runner').write_text('offline');save(r/'inventory.json',{'items':[self.item()]})
   save(r/'device_input.json',{'device':'DEMO','inventory':'inventory.json'});save(r/'batch.json',{'devices':[{'input':'device_input.json','folder':'DEMO'}]})
   with patch.object(cd,'validate_batch'),patch.object(cd,'Agents',side_effect=AssertionError('API attempted')):
    result=cd.run(r/'batch.json',r/'runner',r/'out',check_only=True)
   self.assertEqual(result['status'],'prepared');self.assertFalse(result['full_batch_delivery'])
 def test_real_packet_ada_model_preparation(self):
  root=Path(__file__).resolve().parents[1]/'six_inputs/ADA4528'
  if not root.exists():self.skipTest('package-only fixture')
  import json
  with tempfile.TemporaryDirectory() as t:
   model=cd.reference_model(root,json.loads((root/'device_input.json').read_text()),Path(t))
   self.assertEqual(model['provenance']['original_entry'],'ADA4528')

 def test_real_workflow_with_mock_transport_generates_and_qualifies(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);runner=root/'runner';runner.write_text('offline runner')
   model={'path':'m.lib','entry':'DEMO','ports':['P','N'],'declared_ports':['P','N']}
   (root/'m.lib').write_text('.subckt DEMO P N\nR1 P N 1000\n.ends\n');model['sha256']=digest(root/'m.lib')
   save(root/'task.json',{'model':model})
   item=self.item(pdf_pages=[1]);item['reference_evidence']['values']['typ']=.5
   save(root/'inventory.json',{'review_status':'pending','items':[item]})
   save(root/'ports.json',{'pins':['P','N']});save(root/'pages.json',[{'page':1,'text':'TA=25°C'}])
   packet={'device':'DEMO','inventory':'inventory.json','configured_task':'task.json','materials':{'ports':'ports.json','manual_page_evidence':'pages.json'},'routes':{'design':{'provider':'qwen','model':'offline'},'review':{'provider':'glm','model':'offline'}},'acceptance_standard':{'typical_tolerance_percent':10,'curve_mae_percent':5,'curve_max_error_percent':10}}
   save(root/'device_input.json',packet);save(root/'batch.json',{'devices':[{'input':'device_input.json','folder':'DEMO'}]})
   protocol={'temperature_C':25,'device_nodes':{'P':'P','N':'N'},'components':[{'kind':'V','name':'VDRIVE','nodes':['P','N'],'value':{'dc':0}},{'kind':'V','name':'VCM','nodes':['N','0'],'value':{'dc':.3}}],'analysis':{'kind':'dc','source':'VDRIVE','start':0,'stop':1,'step':.1},'measurement':{'mode':'sample','signal':'v(P,N)','at':.5},'checks':[]}
   calls=[]
   def transport(role,route,context,tokens):
    calls.append(role)
    if role=='contract_normalizer':return {'decision':'propose','protocol':copy.deepcopy(protocol),'condition_bindings':[{'quote':'25°C','protocol_path':'temperature_C'}],'unresolved_conditions':[]},{}
    if role=='test_designer':return {'decision':'propose','protocol':copy.deepcopy(protocol)},{}
    if context.get('stage')=='post_trial_review':return {'decision':'approve','conditions_complete':True,'measurement_correct':True,'approved_protocol_sha256':context['protocol_sha256']},{}
    return {'decision':'approve','conditions_complete':True},{}
   def backend(p,m,folder):
    a=p['analysis'];xs=[a['start']+i*a['step'] for i in range(round((a['stop']-a['start'])/a['step'])+1)]
    c=next((x for x in p['components'] if x['name']=='VORACLE'),None)
    ys=[c['value']['dc'] if c else x for x in xs]
    rows=['Title: mock voltage oracle','Plotname: DC transfer characteristic','Flags: real','No. Variables: 3','No. Points: '+str(len(xs)),'Variables:','0 voltage voltage','1 v(p) voltage','2 v(n) voltage','Values:']
    for i,(x,y) in enumerate(zip(xs,ys)):rows.extend([str(i)+' '+str(x),str(.3+y),'.3'])
    (folder/'test.raw').write_text('\n'.join(rows)+'\n');(folder/'test.log').write_text('offline analytic voltage')
   with patch.object(cd,'validate_batch'):
    report=cd.run(root/'batch.json',runner,root/'out',transport=transport,simulation_transport=backend)
   self.assertEqual(len(report['devices'][0]['qualified']),1,report)
   self.assertIn('contract_normalizer',calls);self.assertIn('test_designer',calls);self.assertEqual(calls.count('test_reviewer'),3)
   self.assertTrue(report['test_backend']);self.assertFalse(report['full_batch_delivery'])

 def test_existing_unverified_binding_not_regenerated(self):
  q=cd.queue({}, {'items':[self.item(bindings=['existing'])]})
  self.assertEqual(q[0]['status'],'existing_binding_requires_verification')

 def test_prepared_reference_interface_reused_without_qualification(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'m.lib').write_text('.subckt DEMO P N\nR1 P N 1000\n.ends\n')
   model={'path':'m.lib','entry':'DEMO','ports':['P','N'],'declared_ports':['P','N'],'sha256':digest(root/'m.lib'),'provenance':{'kind':'vendor_reference_only'}}
   save(root/'drafts.json',{'cases':[{'model':model}]})
   actual=cd.reference_model(root,{'device':'DEMO','fixture_drafts':'drafts.json'},root/'out')
   self.assertFalse(actual['provenance']['physical_binding_verified'])
   self.assertEqual(actual['provenance']['role'],'reference_interface_benchmark_only')

 def test_family_entry_prefix_still_requires_dependency_closure(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'m.lib').write_text('.subckt SENSOR P N\n.lib missing.sub\n.ends SENSOR\n')
   with self.assertRaises(Fault) as e:cd.reference_model(root,{'device':'SENSORKMATR-20AB-T','reference_assets':[{'role':'reference_interface_benchmark_only','path':'m.lib'}]},root/'out')
   self.assertEqual(e.exception.kind,'model_dependency_gap')

 def test_malformed_json_feedback_then_valid_qualifies_without_manual_edit(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);runner=root/'runner';runner.write_text('offline runner')
   model={'path':'m.lib','entry':'DEMO','ports':['P','N'],'declared_ports':['P','N']}
   (root/'m.lib').write_text('.subckt DEMO P N\nR1 P N 1000\n.ends\n');model['sha256']=digest(root/'m.lib')
   save(root/'task.json',{'model':model})
   item=self.item(pdf_pages=[1]);item['reference_evidence']['values']['typ']=.5
   save(root/'inventory.json',{'review_status':'pending','items':[item]})
   save(root/'ports.json',{'pins':['P','N']});save(root/'pages.json',[{'page':1,'text':'TA=25°C'}])
   packet={'device':'DEMO','inventory':'inventory.json','configured_task':'task.json','materials':{'ports':'ports.json','manual_page_evidence':'pages.json'},'routes':{'design':{'provider':'qwen','model':'offline'},'review':{'provider':'glm','model':'offline'}},'acceptance_standard':{'typical_tolerance_percent':10,'curve_mae_percent':5,'curve_max_error_percent':10}}
   save(root/'device_input.json',packet);save(root/'batch.json',{'devices':[{'input':'device_input.json','folder':'DEMO'}]})
   protocol={'temperature_C':25,'device_nodes':{'P':'P','N':'N'},'components':[{'kind':'V','name':'VDRIVE','nodes':['P','N'],'value':{'dc':0}},{'kind':'V','name':'VCM','nodes':['N','0'],'value':{'dc':.3}}],'analysis':{'kind':'dc','source':'VDRIVE','start':0,'stop':1,'step':.1},'measurement':{'mode':'sample','signal':'v(P,N)','at':.5},'checks':[]}
   calls=[]
   def transport(role,route,context,tokens):
    calls.append(role)
    if role=='contract_normalizer':
     candidate=copy.deepcopy(protocol)
     count=calls.count('contract_normalizer')
     if count==1:candidate['models']=[]
     if count==2:
      self.assertIn('protocol.models',context['feedback']['message'])
      candidate['analysis']=[candidate['analysis']]
     if count==3:self.assertIn('protocol.analysis',context['feedback']['message'])
     return {'decision':'propose','protocol':candidate,'condition_bindings':[{'quote':'25°C','protocol_path':'temperature_C'}],'unresolved_conditions':[]},{}
    if role=='test_designer':return {'decision':'propose','protocol':copy.deepcopy(protocol)},{}
    if context.get('stage')=='post_trial_review':return {'decision':'approve','conditions_complete':True,'measurement_correct':True,'approved_protocol_sha256':context['protocol_sha256']},{}
    return {'decision':'approve','conditions_complete':True},{}
   def backend(p,m,folder):
    a=p['analysis'];xs=[a['start']+i*a['step'] for i in range(round((a['stop']-a['start'])/a['step'])+1)]
    c=next((x for x in p['components'] if x['name']=='VORACLE'),None)
    ys=[c['value']['dc'] if c else x for x in xs]
    rows=['Title: mock voltage oracle','Plotname: DC transfer characteristic','Flags: real','No. Variables: 3','No. Points: '+str(len(xs)),'Variables:','0 voltage voltage','1 v(p) voltage','2 v(n) voltage','Values:']
    for i,(x,y) in enumerate(zip(xs,ys)):rows.extend([str(i)+' '+str(x),str(.3+y),'.3'])
    (folder/'test.raw').write_text('\n'.join(rows)+'\n');(folder/'test.log').write_text('offline analytic voltage')
   with patch.object(cd,'validate_batch'):
    report=cd.run(root/'batch.json',runner,root/'out',transport=transport,simulation_transport=backend)
   self.assertEqual(len(report['devices'][0]['qualified']),1,report)
   self.assertIn('contract_normalizer',calls);self.assertIn('test_designer',calls);self.assertEqual(calls.count('test_reviewer'),3)
   self.assertTrue(report['test_backend']);self.assertFalse(report['full_batch_delivery'])
