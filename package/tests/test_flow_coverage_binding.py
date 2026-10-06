"""Coverage receipts: offline faults never count as live qualification."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from flow_runtime import coverage_binding as cb
from flow_runtime.capability_development import queue
from flow_runtime.state import Fault,save,read,digest,fingerprint
from flow_runtime.spice import render,measure,raw_data,acceptance
from test_flow_runtime import encode_raw

class CoverageBindingTests(unittest.TestCase):
 def test_buk_categories_are_not_collapsed(self):
  root=Path(__file__).resolve().parents[1]/'six_inputs/BUK7K52-60E'
  if not root.exists():self.skipTest('package fixture')
  c=cb.counts(cb.clean_inventory(read(root/'inventory.json')))
  self.assertEqual((c['configured_binding_pending'],c['tests_without_method'],c['constraints_pending']),(23,35,20))
 def proof(self,root,status='binding_confirmed'):
  p=root/'evidence';p.write_text('frozen live evidence')
  return {'status':status,'test_backend':False,'proof_files':{str(p):digest(p)},'test_ids':['case'],'electrical_acceptance':'fail'}
 def test_model_failure_does_not_mean_unbound_test(self):
  with tempfile.TemporaryDirectory() as t:
   item={'kind':'test'};cb.confirm_receipt(item,self.proof(Path(t)))
   self.assertTrue(item['binding_complete']);self.assertEqual(item['electrical_acceptance'],'fail')
 def test_reference_method_is_not_device_coverage(self):
  with tempfile.TemporaryDirectory() as t:
   item={'kind':'test'};cb.confirm_receipt(item,self.proof(Path(t),'reference_method_qualified'))
   self.assertFalse(item['binding_complete']);self.assertFalse(item['method_binding_complete'])
 def test_offline_receipt_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   r=self.proof(Path(t));r['test_backend']=True
   with self.assertRaises(Fault):cb.verify_proof(r)
 def test_modified_evidence_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);r=self.proof(root);(root/'evidence').write_text('tampered')
   with self.assertRaises(Fault):cb.verify_proof(r)
 def test_review_must_confirm_scope_and_exact_evidence(self):
  r={'decision':'approve','evidence_sha256':'x','checks':dict.fromkeys(cb.CHECKS,True),'unresolved':[]}
  cb.review_gate(r,'x')
  for bad in ({**r,'evidence_sha256':'other'},{**r,'checks':{**r['checks'],'scope':False}},{**r,'unresolved':['FET2 not bound']}):
   with self.assertRaises(Fault):cb.review_gate(bad,'x')
 def test_no_path_escape(self):
  with self.assertRaises(Fault):cb.inside('/tmp/elsewhere','/tmp/task')
 def fixture(self,root):
  folder=root/'result';folder.mkdir()
  model={'entry':'DUT','ports':['P','N'],'declared_ports':['P','N'],'path':str(folder/'model.lib')}
  (folder/'model.lib').write_text('.subckt DUT P N\nR1 P N 1000\n.ends DUT\n');model['sha256']=digest(folder/'model.lib')
  p={'temperature_C':25,'device_nodes':{'P':'P','N':'0'},'components':[{'kind':'V','name':'VD','nodes':['P','0'],'value':{'dc':0}}],'analysis':{'kind':'dc','source':'VD','start':0,'stop':1,'step':.5},'measurement':{'mode':'sample','signal':'v(P)','at':.5},'checks':[]}
  case={'id':'sample','protocol':p,'expectation':{'unit':'V','limits':{'min':.4,'max':.6}}}
  (folder/'test.cir').write_text(render(p,model));encode_raw(folder,[0,.5,1],{'v(p)':[0,.5,1]});save(folder/'execution.json',{'returncode':0})
  measured=measure(p,raw_data(folder/'test.raw'))
  result={**measured,'test':'sample','execution':'completed','acceptance':acceptance(measured,case['expectation']),'model_sha256':model['sha256'],'protocol_sha256':fingerprint(p),'artifacts':str(folder),'artifact_hashes':{n:digest(folder/n) for n in cb.REQUIRED}}
  return case,result,model,folder
 def test_reparse_real_artifacts_without_simulation(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c,r,m,f=self.fixture(root)
   self.assertEqual(cb.validate_trace(c,r,m,root)['acceptance'],'pass')
 def test_trace_identity_and_acceptance_faults(self):
  for change in ({'test':'other'},{'acceptance':'fail'},{'value':.6},{'protocol_sha256':'other'},{'model_sha256':'other'}):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as t:
    root=Path(t);c,r,m,f=self.fixture(root);r.update(change)
    with self.assertRaises(Fault):cb.validate_trace(c,r,m,root)
 def test_live_trace_rejects_fake_execution_even_with_valid_hashes(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c,r,m,f=self.fixture(root);save(f/'execution.json',{'returncode':0,'test_backend':True});r['artifact_hashes']['execution.json']=digest(f/'execution.json')
   with self.assertRaises(Fault):cb.validate_trace(c,r,m,root)
 def test_truncated_raw_rejected_even_with_updated_hash(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c,r,m,f=self.fixture(root);p=f/'test.raw';p.write_text(p.read_text().replace('No. Points: 3','No. Points: 4'));r['artifact_hashes']['test.raw']=digest(p)
   with self.assertRaises(Fault):cb.validate_trace(c,r,m,root)
 def test_constraint_is_setup_audit_only(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c,r,m,f=self.fixture(root)
   item={'reference_evidence':{'unit':'V','values':{'max':2}}}
   proposal={'decision':'propose','assertions':[{'test_id':'sample','protocol_path':'analysis/stop','bound':'max'}]}
   self.assertEqual(cb.validate_constraints(item,[c],proposal)[0]['actual'],1)
   item['reference_evidence']['values']['max']=.1
   with self.assertRaises(Fault):cb.validate_constraints(item,[c],proposal)
 def test_receipt_overlay_revalidates_evidence_and_never_changes_source(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);packet={'device':'DUT','inventory':'inventory.json','assets':{}}
   source={'items':[{'id':'r','kind':'test','bindings':['case'],'reference_evidence':{'conditions':'fixed'},'binding_complete':False}]}
   save(root/'inventory.json',source);save(root/'device_input.json',packet)
   inv=cb.clean_inventory(source);receipt=self.proof(root);receipt['source_identity']=cb.source_identity(root,packet);cb.confirm_receipt(inv['items'][0],receipt)
   output=root/'coverage';row=cb.write_device(output,root,packet,inv,{'r':receipt},[]);save(output/'summary.json',{'devices':[row]})
   self.assertTrue(cb.load_overlay(root,packet,source,output)['items'][0]['binding_complete']);self.assertFalse(source['items'][0]['binding_complete'])
   (root/'evidence').write_text('changed')
   with self.assertRaises(Fault):cb.load_overlay(root,packet,source,output)
 def test_confirmed_method_not_developed_again(self):
  item={'id':'r','kind':'test','method_binding_complete':True,'binding_complete':False}
  self.assertEqual(queue({}, {'items':[item]}),[])
 def test_check_only_never_calls_agents_or_simulator(self):
  root=Path(__file__).resolve().parents[1]/'six_inputs'
  if not root.exists():self.skipTest('package fixture')
  with tempfile.TemporaryDirectory() as t,patch.object(cb,'Agents',side_effect=AssertionError('API')):
   r=cb.run(root/'batch.json',Path(t)/'no_runtime',Path(t)/'out',check_only=True)
   self.assertEqual(len(r['devices']),6);self.assertEqual(r['simulations'],0);self.assertFalse(r['full_manual_coverage'])
 def test_revision_is_automatically_returned_to_verifier(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c,result,model,folder=self.fixture(root)
   item={'id':'r','kind':'test','bindings':['sample'],'binding_complete':False,'pdf_pages':[1],'reference_evidence':{'conditions':'25 C'}}
   save(root/'inventory.json',{'items':[item]});save(root/'pages.json',[{'page':1,'text':'25 C'}]);save(root/'ports.json',{'P':'positive','N':'negative'});(root/'manual.pdf').write_bytes(b'offline evidence')
   save(root/'task.json',{'model':model,'cases':[c]})
   packet={'device':'DUT','inventory':'inventory.json','assets':{},'configured_task':'task.json','materials':{'manual_page_evidence':'pages.json','ports':'ports.json','manual':'manual.pdf'},'acceptance_standard':{},'routes':{'design':{'provider':'qwen','model':'offline'},'review':{'provider':'glm','model':'offline'}}}
   save(root/'device_input.json',packet);save(root/'batch.json',{'devices':[{'input':'device_input.json','folder':'DUT'}]})
   runtime=root/'runtime';summary=runtime/'DUT/configured/summary.json';save(summary,{'device':'DUT','results':{'sample':result}})
   calls=[]
   def transport(role,route,context,tokens):
    calls.append((role,context.get('feedback')))
    if len(calls)==1:return {'decision':'revise','issues':['check scope']},{}
    return {'decision':'approve','checks':dict.fromkeys(cb.CHECKS,True),'unresolved':[],'evidence_sha256':context['evidence_sha256']},{}
   with patch.object(cb,'validate_batch'),patch.object(cb,'validate_trace',return_value={'test':'sample','acceptance':'pass','proof_files':{}}):
    report=cb.run(root/'batch.json',runtime,root/'coverage',transport=transport)
   self.assertEqual([r for r,f in calls],['binding_verifier','binding_verifier','test_reviewer']);self.assertIsNotNone(calls[1][1])
   self.assertEqual(report['devices'][0]['counts']['device_bindings_confirmed'],0)
 def test_partial_reference_method_is_kept_in_gap_queue(self):
  item={'id':'r','kind':'test','method_binding_complete':False,'binding_complete':False,'reference_evidence':{'unit':'V','values':{'typ':1},'conditions':'TA=25°C'},'evidence':[]}
  self.assertEqual(len(queue({}, {'items':[item]})),1)
