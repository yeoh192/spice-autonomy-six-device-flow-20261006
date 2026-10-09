"""Contract fault injection uses actual Agents dispatch and persisted budgets."""
import copy,difflib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_flow_runtime import Harness
from flow_runtime.agents import Agents
from flow_runtime.evidence import bundle
from flow_runtime.model_diagnostics import capabilities
from flow_runtime.interface_contracts import prepare_request,validate_response,output_schema,schema_errors
from flow_runtime.state import Fault,read,digest

class InterfaceContractTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.h=Harness(Path(self.tmp.name),resistance=1400);self.w=self.h.build()
  self.w.results=self.w.evaluate_all(self.w.model_path,'baseline')
  self.shared=bundle(self.w)
  self.ctx={'phase':'repair_design','shared_evidence':self.shared,'capabilities':capabilities(self.w.model_path.read_text(),self.h.model),
            'triggers':['forward'],'allowed_test_ids':['forward']}
  source=self.w.model_path.read_text();candidate=source.replace('RCORE A K 1400','RCORE A K 1050')
  self.actual_diff=''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True)))
  self.good={'action':'patch','reason':'reduce measured voltage error','evidence_tests':['forward'],
             'adapter':'parameter','edits':[{'old':'RCORE A K 1400','new':'RCORE A K 1050'}]}
 def prepared(self):return prepare_request('model_repair_designer',self.ctx)
 def test_valid_contract_matches_exported_schema(self):
  ctx=self.prepared();self.assertFalse(schema_errors(self.good,output_schema('repair_design')))
  validate_response('model_repair_designer',ctx,self.good)
  self.assertEqual(ctx['interface_contract']['stage'],'repair_design')
 def test_empty_edit_extra_field_wrong_type_and_wrong_action_rejected(self):
  variants=[{**self.good,'edits':[]},{**self.good,'spice_code':'arbitrary'},
            {**self.good,'edits':{}},{**self.good,'action':'diagnose'}]
  for value in variants:
   with self.subTest(value=value),self.assertRaises(Fault) as error:validate_response('model_repair_designer',self.prepared(),value)
   self.assertEqual(error.exception.kind,'interface_contract');self.assertTrue(error.exception.evidence['issues'])
 def test_unknown_evidence_and_old_text_and_adapter_rejected(self):
  variants=[{**self.good,'evidence_tests':['invented']},
            {**self.good,'edits':[{'old':'Kp=200','new':'Kp=210'}]},
            {**self.good,'adapter':'behavioral_voltage'}]
  for value in variants:
   with self.subTest(value=value),self.assertRaises(Fault):validate_response('model_repair_designer',self.prepared(),value)
 def test_invalid_input_never_calls_provider_or_spends_budget(self):
  self.ctx['capabilities']=[]
  with patch.object(self.h.agents,'transport',side_effect=AssertionError('API called')),self.assertRaises(Fault):self.h.agents.ask('model_repair_designer',self.ctx)
  self.assertEqual(self.h.store.data['usage']['api_calls'],0)
 def test_empty_edits_are_corrected_with_field_feedback_and_cached(self):
  calls=[]
  def transport(role,route,ctx,tokens):
   calls.append(copy.deepcopy(ctx))
   if len(calls)==1:return {**self.good,'edits':[]},{}
   issues=ctx['interface_feedback']['error']['evidence']['issues']
   self.assertTrue(any(i['field']=='$.edits' for i in issues))
   self.assertEqual(ctx['interface_contract'],calls[0]['interface_contract'])
   return self.good,{}
  self.h.agents.transport=transport
  self.assertEqual(self.h.agents.ask('model_repair_designer',self.ctx),self.good)
  self.assertEqual(self.h.agents.ask('model_repair_designer',self.ctx),self.good)
  self.assertEqual(len(calls),2);self.assertEqual(self.h.store.data['usage']['api_calls'],2)
 def test_invalid_output_is_not_cached_as_completed_and_retry_is_bounded(self):
  self.h.agents.transport=lambda *args:({**self.good,'edits':[]},{})
  with self.assertRaises(Fault) as error:self.h.agents.ask('model_repair_designer',self.ctx)
  self.assertEqual(error.exception.kind,'interface_contract');self.assertEqual(self.h.store.data['usage']['api_calls'],2)
  self.assertTrue(all(r['status']=='failed' for r in self.h.store.data['requests'].values()))
  with self.assertRaises(Fault):self.h.agents.ask('model_repair_designer',self.ctx)
  self.assertEqual(self.h.store.data['usage']['api_calls'],2)
 def test_interrupted_schema_retry_resumes_feedback_without_resetting_counter(self):
  original=self.h.store.reserve
  def reserve(resource,*args):
   if resource=='api_calls' and self.h.store.data['usage']['api_calls']==1:raise KeyboardInterrupt()
   return original(resource,*args)
  self.h.agents.transport=lambda *args:({**self.good,'edits':[]},{})
  with patch.object(self.h.store,'reserve',side_effect=reserve),self.assertRaises(KeyboardInterrupt):self.h.agents.ask('model_repair_designer',self.ctx)
  self.h.build(resume=True)
  def transport(role,route,ctx,tokens):
   self.assertIn('interface_feedback',ctx);return self.good,{}
  self.h.agents.transport=transport
  self.assertEqual(self.h.agents.ask('model_repair_designer',self.ctx),self.good)
  self.assertEqual(self.h.store.data['usage']['api_calls'],2)
 def test_pre_review_cannot_approve_post_execution_or_claim_candidate_results(self):
  ctx={'phase':'pre_execution','shared_evidence':self.shared,'candidate_executed':False,'planned_test_ids':['forward'],
       'proposal':self.good,'actual_diff':self.actual_diff}
  prepared=prepare_request('patch_reviewer',ctx)
  bad={'phase':'post_execution','decision':'approve','issues':[],'evidence_ids':['forward'],'candidate_sha256':digest(self.h.model_path)}
  with self.assertRaises(Fault):validate_response('patch_reviewer',prepared,bad)
  ctx['candidate_evidence']=self.shared
  with self.assertRaises(Fault):prepare_request('patch_reviewer',ctx)
 def test_post_review_requires_actual_hash_and_phase(self):
  sha=digest(self.h.model_path)
  ctx={'phase':'post_execution','candidate_executed':True,'candidate_sha256':sha,'candidate_evidence':self.shared,'proposal':self.good}
  p=prepare_request('patch_reviewer',ctx)
  good={'phase':'post_execution','decision':'approve','issues':[],'evidence_ids':['forward'],'candidate_sha256':sha}
  validate_response('patch_reviewer',p,good)
  with self.assertRaises(Fault):validate_response('patch_reviewer',p,{**good,'candidate_sha256':'0'*64})
  bad=copy.deepcopy(ctx);bad['candidate_evidence']['tests'][0]['result']['model_sha256']='0'*64
  with self.assertRaises(Fault):prepare_request('patch_reviewer',bad)
 def test_revocation_must_contain_structured_issue_and_real_citation(self):
  ctx={'phase':'pre_execution','candidate_executed':False,'shared_evidence':self.shared,'actual_diff':self.actual_diff,'planned_test_ids':['forward'],'proposal':self.good}
  p=prepare_request('patch_reviewer',ctx)
  good={'phase':'pre_execution','decision':'revise','evidence_ids':['forward'],
        'issues':[{'code':'INVALID_EDIT','field':'edits','message':'bounded edit concern','evidence_ids':['forward']}]}
  validate_response('patch_reviewer',p,good)
  with self.assertRaises(Fault):validate_response('patch_reviewer',p,{**good,'issues':[]})
  with self.assertRaises(Fault):validate_response('patch_reviewer',p,{**good,'issues':[{'code':'OTHER','message':'fiction','evidence_ids':['invented']}]})
 def test_wrong_input_phase_fails_before_network(self):
  with self.assertRaises(Fault):prepare_request('model_repair_designer',{**self.ctx,'phase':'diagnostic_planning'})

 def test_pre_review_must_receive_diff_matching_actual_declared_edits(self):
  ctx={'phase':'pre_execution','candidate_executed':False,'shared_evidence':self.shared,'actual_diff':self.actual_diff,
       'planned_test_ids':['forward'],'proposal':self.good}
  prepare_request('patch_reviewer',ctx)
  ctx['actual_diff']=ctx['actual_diff'].replace('1050','1200')
  with self.assertRaises(Fault) as error:prepare_request('patch_reviewer',ctx)
  self.assertTrue(any(i['code']=='DIFF_PROPOSAL_MISMATCH' for i in error.exception.evidence['issues']))
 def test_optimizer_schema_fault_flows_back_to_next_design_round(self):
  calls=[]
  def transport(role,route,ctx,tokens):
   if role=='model_optimizer':
    calls.append(ctx)
    if len(calls)<=2:return {'decision':'patch','edits':[]},{}
    if len(calls)==3:
     self.assertEqual(ctx['feedback']['kind'],'interface_contract')
     return {'decision':'patch','kind':'parameter','reason':'bounded repair','evidence_ids':['forward'],'edits':self.good['edits']},{}
    return {'decision':'defer','reason':'no further repair needed'},{}
   return self.h.normal_transport(role,route,ctx,tokens)
  self.h.agents.transport=transport;self.w.optimize()
  self.assertIn('1050',self.w.model_path.read_text())
  self.assertEqual(len(calls),4)

 def test_exported_schemas_match_the_runtime_validator_source(self):
  import json
  from flow_runtime.interface_contracts import input_schema
  root=Path(__file__).resolve().parents[1]/'interface_contracts'
  for stage in ('diagnostic_planning','repair_design','model_optimization','pre_execution','post_execution'):
   for kind,fn in (('input',input_schema),('output',output_schema)):
    value=json.loads((root/(stage+'.'+kind+'.schema.json')).read_text())
    self.assertEqual({k:v for k,v in value.items() if k not in ('$schema','$id')},fn(stage))
