"""Versioned stage contracts: JSON shape, evidence identity and executable edits.

The exported schemas and the dependency-free validator use the same bounded
JSON Schema subset. No provider-specific structured-output feature is assumed.
"""
import difflib
import json
import re
from .state import Fault, fingerprint

VERSION = 'model-interface-1'
TEXT = {'type':'string','minLength':1,'maxLength':8000}
IDS = {'type':'array','items':{'type':'string','minLength':1,'maxLength':300},'minItems':1,'maxItems':200,'uniqueItems':True}
EDITS = {'type':'array','minItems':1,'maxItems':4,'items':{'type':'object','required':['old','new'],
    'additionalProperties':False,'properties':{'old':TEXT,'new':TEXT}}}
ISSUE = {'type':'object','required':['code','message','evidence_ids'],'additionalProperties':False,
    'properties':{'code':{'enum':['INVALID_EDIT','FROZEN_CONDITION','INSUFFICIENT_EVIDENCE','MEASUREMENT_MISMATCH','REGRESSION','OTHER']},
                  'message':TEXT,'field':{'type':'string','maxLength':300},'evidence_ids':IDS}}


def obj(required, properties):
    return {'type':'object','required':required,'properties':properties,'additionalProperties':False}


def output_schema(stage):
    if stage in ('diagnostic_planning','repair_design'):
        props={'action':{'enum':['experiment','patch']},'reason':TEXT,'evidence_tests':IDS,
               'adapter':{'enum':['parameter','behavioral_voltage','existing_node_rewire']},'edits':EDITS}
        edit=obj(['action','reason','evidence_tests','edits'],props)
        passive=obj(['action','reason','evidence_tests','edits'],{**props,
            'action':{'enum':['stop'] if stage=='repair_design' else ['diagnose','stop']},
            'edits':{'type':'array','maxItems':0}})
        return {'oneOf':[edit,passive]}
    if stage=='model_optimization':
        patch=obj(['decision','kind','reason','evidence_ids','edits'],{'decision':{'const':'patch'},
            'kind':{'enum':['parameter','structure']},'reason':TEXT,'evidence_ids':IDS,'edits':EDITS})
        return {'oneOf':[patch,obj(['decision','reason'],{'decision':{'const':'defer'},'reason':TEXT})]}
    if stage in ('pre_execution','post_execution'):
        props={'phase':{'const':stage},'decision':{'enum':['approve','revise']},'reason':TEXT,
            'evidence_ids':IDS,'issues':{'type':'array','items':ISSUE,'maxItems':20}}
        required=['phase','decision','evidence_ids','issues']
        if stage=='post_execution':
            props['candidate_sha256']={'type':'string','pattern':'^[a-f0-9]{64}$'}
            required.append('candidate_sha256')
        return {'oneOf':[obj(required,{**props,'decision':{'const':'approve'},'issues':{'type':'array','maxItems':0}}),
                         obj(required,{**props,'decision':{'const':'revise'},'issues':{'type':'array','minItems':1,'maxItems':20,'items':ISSUE}})]}
    raise ValueError('Unsupported contract stage: '+stage)


def schema_errors(value, schema, path='$'):
    errors=[]
    if 'oneOf' in schema:
        variants=[schema_errors(value,s,path) for s in schema['oneOf']]
        matches=sum(not e for e in variants)
        if matches==1:return []
        return min(variants,key=len) if matches==0 else [{'field':path,'code':'AMBIGUOUS_VARIANT','expected':'exactly one output variant'}]
    types={'object':lambda v:isinstance(v,dict),'array':lambda v:isinstance(v,list),
           'string':lambda v:isinstance(v,str),'boolean':lambda v:type(v) is bool}
    if schema.get('type') in types and not types[schema['type']](value):
        return [{'field':path,'code':'WRONG_TYPE','expected':schema['type'],'actual_type':type(value).__name__}]
    if 'const' in schema and value!=schema['const']:errors.append({'field':path,'code':'WRONG_STAGE_OR_ACTION','expected':schema['const']})
    if 'enum' in schema and value not in schema['enum']:errors.append({'field':path,'code':'INVALID_ENUM','expected':schema['enum']})
    if isinstance(value,dict):
        for name in schema.get('required',[]):
            if name not in value:errors.append({'field':path+'.'+name,'code':'MISSING_FIELD','expected':'required'})
        props=schema.get('properties',{})
        for name,v in value.items():
            if name in props:errors.extend(schema_errors(v,props[name],path+'.'+name))
            elif schema.get('additionalProperties') is False:errors.append({'field':path+'.'+name,'code':'EXTRA_FIELD','expected':'declared fields only'})
    if isinstance(value,list):
        if len(value)<schema.get('minItems',0):errors.append({'field':path,'code':'TOO_FEW_ITEMS','expected':schema['minItems']})
        if len(value)>schema.get('maxItems',len(value)):errors.append({'field':path,'code':'TOO_MANY_ITEMS','expected':schema['maxItems']})
        if schema.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):errors.append({'field':path,'code':'DUPLICATE_ITEMS','expected':'unique IDs'})
        if 'items' in schema:
            for i,v in enumerate(value):errors.extend(schema_errors(v,schema['items'],path+'['+str(i)+']'))
    if isinstance(value,str):
        if schema.get('minLength',0) and not value.strip():errors.append({'field':path,'code':'EMPTY_TEXT','expected':'nonempty text'})
        if len(value)>schema.get('maxLength',len(value)):errors.append({'field':path,'code':'TEXT_TOO_LONG','expected':schema['maxLength']})
        if schema.get('pattern') and not re.fullmatch(schema['pattern'],value):errors.append({'field':path,'code':'INVALID_PATTERN','expected':schema['pattern']})
    return errors


def stage_for(role, context):
    if role=='model_diagnoser':return 'diagnostic_planning'
    if role=='model_repair_designer':return 'repair_design'
    if role=='model_optimizer':return 'model_optimization'
    if role=='patch_reviewer' and context.get('phase') in ('pre_execution','post_execution'):return context['phase']
    if role=='patch_reviewer':raise Fault('input','补丁审查缺少明确阶段',{'error_code':'MISSING_REVIEW_PHASE','field':'phase'})
    return None


def input_schema(stage):
    model={'type':'object','required':['text','active_sha256','complete_in_context'],
           'properties':{'text':{'type':'string','minLength':1,'maxLength':60000},
                         'active_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'complete_in_context':{'type':'boolean'}}}
    test={'type':'object','required':['test','result'], 'properties':{'test':{'type':'string','minLength':1},'result':{'type':'object'}}}
    shared={'type':'object','required':['model','tests'],'properties':{'model':model,'tests':{'type':'array','minItems':1,'items':test},
            'active_test_ids':IDS,'regression_guards':{'type':'array','items':{'type':'object','required':['test','expectation'],'properties':{'test':{'type':'string','minLength':1},'expectation':{'type':'object'}}}}}}
    props={'shared_evidence':shared,'capabilities':{'type':'object','required':['available_adapters','allowed_actions'],
            'properties':{'available_adapters':{'type':'array','minItems':1,'items':{'enum':['parameter','behavioral_voltage','existing_node_rewire']}},'allowed_actions':{'type':'array','minItems':1,'items':{'type':'string'}}}},'triggers':IDS,
           'allowed_test_ids':IDS,'actual_diff':TEXT,'planned_test_ids':IDS,'candidate_executed':{'type':'boolean'},
           'candidate_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'},'candidate_evidence':shared,'proposal':{'type':'object'},'phase':{'type':'string'}}
    required=['shared_evidence']
    if stage in ('diagnostic_planning','repair_design'):required+=['capabilities','triggers','allowed_test_ids','phase'];props['phase']={'const':stage}
    if stage=='pre_execution':required+=['actual_diff','planned_test_ids','candidate_executed','proposal','phase'];props['phase']={'const':stage};props['candidate_executed']={'const':False,'type':'boolean'}
    if stage=='post_execution':required=['candidate_sha256','candidate_executed','candidate_evidence','proposal','phase'];props['phase']={'const':stage};props['candidate_executed']={'const':True,'type':'boolean'}
    return {'type':'object','required':required,'properties':props}


def facts(context):
    shared=context.get('candidate_evidence') or context.get('shared_evidence') or {}
    tests=shared.get('tests',[])+shared.get('regression_guards',[])
    ids=set(context.get('allowed_test_ids',[])) | set(shared.get('active_test_ids',[])) | {t['test'] for t in tests}
    citations=set(ids)
    for t in tests:
        citations.update(t.get('evidence_ids',[]))
        for r in t.get('manual_records',[]):
            if r.get('id'):citations.add(r['id'])
            if isinstance(r.get('evidence'),list):citations.update(x for x in r['evidence'] if isinstance(x,str))
    return shared,ids,citations


def prepare_request(role, context):
    stage=stage_for(role,context)
    if stage is None:return context
    errors=schema_errors(context,input_schema(stage))
    if errors:raise Fault('input','接口输入未通过结构校验',{'error_code':'INVALID_CONTRACT_INPUT','stage':stage,'issues':errors[:20]})
    shared,ids,citations=facts(context)
    if not ids:errors.append({'field':'$.shared_evidence.tests','code':'NO_TEST_EVIDENCE','expected':'actual test IDs'})
    if stage=='pre_execution' and not set(context.get('planned_test_ids',[])) <= ids:errors.append({'field':'$.planned_test_ids','code':'UNKNOWN_TEST','expected':'known active IDs'})
    if stage=='pre_execution' and 'candidate_evidence' in context:errors.append({'field':'$.candidate_evidence','code':'WRONG_PHASE_EVIDENCE','expected':'baseline facts only before execution'})
    if stage=='pre_execution' and shared.get('model',{}).get('complete_in_context'):
        source=shared['model']['text'];candidate=source
        edits=context['proposal'].get('edits')
        edit_errors=schema_errors(edits,EDITS,'$.proposal.edits')
        errors.extend(edit_errors)
        if not edit_errors:
            for i,edit in enumerate(edits):
                if source.count(edit['old'])!=1 or candidate.count(edit['old'])!=1:
                    errors.append({'field':'$.proposal.edits['+str(i)+'].old','code':'OLD_TEXT_NOT_UNIQUE','expected':'exactly one match in active source'})
                else:candidate=candidate.replace(edit['old'],edit['new'],1)
            expected=''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True)))
            body=lambda d:[line for line in d.splitlines() if not line.startswith(('---','+++'))]
            if body(expected)!=body(context['actual_diff']):errors.append({'field':'$.actual_diff','code':'DIFF_PROPOSAL_MISMATCH','expected':'actual unified diff generated from the declared edits'})
    if stage=='post_execution':
        sha=context.get('candidate_sha256');model=shared.get('model',{})
        if model.get('active_sha256')!=sha:errors.append({'field':'$.candidate_evidence.model.active_sha256','code':'CANDIDATE_HASH_MISMATCH','expected':sha})
        rows=shared.get('tests',[])
        failed={t['test'] for t in rows if t.get('result',{}).get('execution')!='completed'}
        partial=context.get('partial_retention_only') is True
        allowed=set(context.get('persistent_execution_failures',[]))
        baseline=context.get('baseline_execution',{})
        valid_partial=partial and bool(failed) and failed==allowed and all(baseline.get(i)=='failed' for i in failed) and all(t.get('result',{}).get('acceptance')=='not_evaluated' for t in rows if t['test'] in failed)
        if any(t.get('result',{}).get('model_sha256')!=sha for t in rows) or (failed and not valid_partial):
            errors.append({'field':'$.candidate_evidence.tests','code':'UNVERIFIED_CANDIDATE_RESULTS','expected':'candidate-hash measurements; partial retention requires matching pre-existing failed tests, never delivery'})
    if errors:raise Fault('input','接口输入未通过阶段与事实校验',{'error_code':'INVALID_CONTRACT_INPUT','stage':stage,'issues':errors[:20]})
    schema=output_schema(stage)
    contract={'version':VERSION,'stage':stage,'input_schema':input_schema(stage),'output_schema':schema,
              'known_test_ids':sorted(ids),'known_evidence_ids':sorted(citations),
              'instruction':'Return ONLY an object matching output_schema. Never add prose outside JSON. Validation failures are returned as field-specific interface_feedback. An approve review authorizes this phase only.'}
    contract['contract_id']=fingerprint(contract)
    return {**context,'interface_contract':contract}


def validate_response(role, context, value):
    contract=context.get('interface_contract')
    if not contract:return
    stage=contract['stage'];errors=schema_errors(value,contract['output_schema'])
    if not errors:
        refs=value.get('evidence_tests',value.get('evidence_ids',[]));known=set(contract['known_test_ids'] if 'evidence_tests' in value else contract['known_evidence_ids'])
        if not set(refs)<=known:errors.append({'field':'$.evidence_tests' if 'evidence_tests' in value else '$.evidence_ids','code':'UNKNOWN_EVIDENCE','expected':'supplied evidence IDs'})
        for i,issue in enumerate(value.get('issues',[])):
            if not set(issue['evidence_ids'])<=set(contract['known_evidence_ids']):errors.append({'field':'$.issues['+str(i)+'].evidence_ids','code':'UNKNOWN_EVIDENCE','expected':'supplied evidence IDs'})
        if 'evidence_tests' in value and not set(refs).intersection(context['triggers']):errors.append({'field':'$.evidence_tests','code':'MISSING_TRIGGER','expected':'at least one actual failing/residual test'})
        if stage=='post_execution' and value.get('candidate_sha256')!=context['candidate_sha256']:errors.append({'field':'$.candidate_sha256','code':'CANDIDATE_HASH_MISMATCH','expected':context['candidate_sha256']})
        shared,_,_=facts(context);model=shared.get('model',{});source=model.get('text','')
        if value.get('edits'):
            for i,edit in enumerate(value['edits']):
                if model.get('complete_in_context') and source.count(edit['old'])!=1:errors.append({'field':'$.edits['+str(i)+'].old','code':'OLD_TEXT_NOT_UNIQUE','expected':'exactly one match in supplied active model'})
                if edit['old']==edit['new']:errors.append({'field':'$.edits['+str(i)+'].new','code':'NO_ACTUAL_CHANGE','expected':'actual bounded change'})
            adapter=value.get('adapter')
            if adapter and adapter not in context.get('capabilities',{}).get('available_adapters',[]):errors.append({'field':'$.adapter','code':'UNAVAILABLE_ADAPTER','expected':context.get('capabilities',{}).get('available_adapters',[])})
    if errors:
        raise Fault('interface_contract','模型输出未通过正式接口合同',{'error_code':'INVALID_CONTRACT_OUTPUT','stage':stage,
                    'contract_id':contract['contract_id'],'issues':errors[:20]})


def retry_context(context, fault):
    if not fault:return context
    return {**context,'interface_feedback':{'error':fault,'instruction':'Correct the failed fields against the unchanged interface_contract and evidence. Do not repeat the rejected output or relax conditions.'}}
