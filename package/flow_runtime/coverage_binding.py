"""Evidence-backed coverage overlays. No API or simulation during preflight.
Source inventory stays immutable; method, electrical acceptance and constraint scope
are independent. Reference-only qualification cannot complete device coverage.
"""
import copy
import math
from pathlib import Path
from .state import Fault, Store, read, save, digest, fingerprint, artifacts_valid, file_lock
from .agents import Agents
from .spice import (raw_data, render, measure, acceptance, load_reference,
                    validate_protocol, validate_model, model_text, path_get)
from .contracts import bound_unit, UNITS
from .input_batch import validate_batch

TERMINAL = {'authentication', 'credentials', 'api_configuration', 'budget',
            'cache_corrupt', 'api_recovery_exhausted'}
REQUIRED = {'model.lib', 'test.cir', 'test.raw', 'test.log', 'execution.json'}
CHECKS = ('conditions', 'ports', 'measurement', 'reference', 'scope')


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if root != path and root not in path.parents:
        raise Fault('cache_corrupt', '证据路径超出所属任务目录')
    return path


def source_identity(root, packet):
    return fingerprint({'packet_sha256': digest(root/'device_input.json'),
                        'inventory_sha256': digest(root/packet['inventory']),
                        'assets': packet['assets']})


def counts(inventory):
    items = inventory['items']; tests = [i for i in items if i['kind'] == 'test']
    constraints = [i for i in items if i['kind'] == 'constraint']
    info = [i for i in items if i['kind'] == 'informational']
    confirmed = [i for i in tests if i.get('binding_complete') and i.get('coverage_receipt')]
    methods = [i for i in tests if i.get('method_binding_complete') and i.get('coverage_receipt')]
    return {'records': len(items), 'test_records': len(tests),
            'configured_binding_records': sum(bool(i.get('bindings')) for i in tests),
            'configured_binding_pending': sum(bool(i.get('bindings')) and not i.get('binding_complete') for i in tests),
            'tests_without_method': sum(not i.get('bindings') and not i.get('method_binding_complete') for i in tests),
            'device_bindings_confirmed': len(confirmed),
            'reference_methods_qualified': sum(i.get('coverage_status') == 'reference_method_qualified' for i in tests),
            'electrical_pass': sum(i.get('electrical_acceptance') == 'pass' for i in confirmed),
            'electrical_fail': sum(i.get('electrical_acceptance') == 'fail' for i in confirmed),
            'electrical_pending': sum(i.get('electrical_acceptance') not in ('pass','fail') for i in confirmed),
            'constraints': len(constraints),
            'constraints_setup_audited': sum(i.get('constraint_audit_status') == 'declared_test_setup_checked' for i in constraints),
            'constraints_pending': sum(i.get('constraint_audit_status') != 'declared_test_setup_checked' for i in constraints),
            'informational': len(info), 'informational_review_pending': sum(not i.get('binding_complete') for i in info)}


def clean_inventory(source):
    inventory = copy.deepcopy(source)
    for item in inventory['items']:
        if item['kind'] == 'test':
            item['binding_complete'] = False
            item['method_binding_complete'] = False
            item['coverage_status'] = 'configured_binding_pending' if item.get('bindings') else 'test_missing'
            for name in ('coverage_receipt', 'electrical_acceptance'):
                item.pop(name, None)
    return inventory


def load_overlay(root, packet, source, coverage):
    """Revalidate every confirmed receipt, including review and RAW hashes, on reuse."""
    if coverage is None:
        return clean_inventory(source)
    report = read(Path(coverage)/'summary.json')
    rows = [d for d in report['devices'] if d['device'] == packet['device']]
    if len(rows) != 1 or rows[0]['source_identity'] != source_identity(root, packet):
        raise Fault('cache_corrupt', '覆盖确认来源与当前输入不一致')
    folder = inside(rows[0]['folder'], coverage)
    if not artifacts_valid(folder, rows[0]['hashes']):
        raise Fault('cache_corrupt', '覆盖清单或确认收据被修改')
    inventory = read(folder/'manual_inventory.json')
    receipts = read(folder/'binding_receipts.json')
    expected = {i['id']: i for i in clean_inventory(source)['items']}
    if {i['id'] for i in inventory['items']} != set(expected):
        raise Fault('cache_corrupt', '覆盖清单项目集合改变')
    for item in inventory['items']:
        original = expected[item['id']]
        for key in original:
            if key in ('binding_complete','method_binding_complete','coverage_status'):continue
            if item.get(key) != original.get(key):
                raise Fault('cache_corrupt', '覆盖回写改变了原始条件、参考或既有绑定')
        if item.get('coverage_receipt'):
            receipt = receipts.get(item['id'])
            if not receipt or fingerprint(receipt) != item['coverage_receipt']:
                raise Fault('cache_corrupt', '覆盖收据身份不一致')
            if receipt.get('source_identity') != rows[0]['source_identity']:
                raise Fault('cache_corrupt', '覆盖收据输入身份不一致')
            verify_proof(receipt)
            replay=copy.deepcopy(original);confirm_receipt(replay,receipt)
            for field in ('binding_complete','method_binding_complete','coverage_status','electrical_acceptance','constraint_audit_status','method_bindings'):
                if item.get(field)!=replay.get(field):raise Fault('cache_corrupt','覆盖状态与收据不一致')
        elif (item.get('binding_complete') or item.get('method_binding_complete')) and item['kind'] == 'test':
            raise Fault('cache_corrupt', '无确认收据的测试不能声明绑定完成')
    return inventory


def verify_proof(receipt):
    if receipt.get('test_backend') or receipt.get('status') not in ('binding_confirmed','reference_method_qualified','constraint_setup_checked'):
        raise Fault('cache_corrupt', '离线或未获批准的收据不能写入正式覆盖')
    hashes = receipt.get('proof_files', {})
    if not hashes or any(not Path(p).is_file() or digest(p) != h for p,h in hashes.items()):
        raise Fault('cache_corrupt', '实测或审查证据已变化')


def validate_trace(case, result, model, runtime, require_live=True):
    if result.get('execution') != 'completed' or result.get('test') != case['id']:
        raise Fault('coverage_execution', '未完成同一测试，不能确认绑定')
    folder = inside(result.get('artifacts',''), runtime)
    hashes = result.get('artifact_hashes', {})
    if not REQUIRED.issubset(hashes) or not artifacts_valid(folder, hashes):
        raise Fault('cache_corrupt', '实测文件缺少完整哈希或已改变')
    execution = read(folder/'execution.json')
    if execution.get('returncode') != 0 or (require_live and execution.get('test_backend')):
        raise Fault('coverage_execution', '离线模拟或失败执行不能确认真实绑定')
    if digest(folder/'model.lib') != model['sha256'] or result.get('model_sha256') != model['sha256']:
        raise Fault('cache_corrupt', '实测模型与冻结模型不一致')
    validate_model(model_text(folder/'model.lib'), model)
    validate_protocol(case['protocol'], model, case.get('contract'))
    if result.get('protocol_sha256') != fingerprint(case['protocol']) or (folder/'test.cir').read_text() != render(case['protocol'], model):
        raise Fault('coverage_protocol', '实际电路与待确认协议不一致；需要针对实际协议重新审查')
    reference = None
    if case.get('reference'):
        ref = case['reference']
        if digest(ref['path']) != ref['sha256']:
            raise Fault('cache_corrupt', '参考曲线改变')
        reference = load_reference(ref['path'], case['expectation']['unit'], ref.get('condition'))
    data = raw_data(folder/'test.raw')
    if not data['complete']:
        raise Fault('coverage_execution', '不完整RAW不能确认绑定')
    measured = measure(case['protocol'], data, reference)
    for key, value in measured.items():
        if result.get(key) != value:
            raise Fault('coverage_result', '结果与重新解析的真实波形不一致：'+key)
    expected = acceptance(measured, case['expectation'])
    if case['expectation']['unit'] == 'F' and measured.get('value',1) < 0:
        expected = 'fail'
    if result.get('acceptance') != expected:
        raise Fault('coverage_result', '保存的验收状态与冻结标准不一致')
    proof = {str(folder/name): h for name,h in hashes.items()}
    if case.get('reference'): proof[case['reference']['path']] = case['reference']['sha256']
    return {'test': case['id'], 'measured': measured, 'acceptance': expected,
            'model_sha256': model['sha256'], 'protocol_sha256': fingerprint(case['protocol']),
            'proof_files': proof, 'scope': 'exact supplied conditions only'}


def manual_evidence(root, packet, item):
    materials = packet['materials']; pages = read(root/materials['manual_page_evidence'])
    selected = [p for p in pages if p.get('page') in item.get('pdf_pages',[])]
    if not selected:
        raise Fault('coverage_evidence', '缺少对应手册页证据，不能自动确认')
    return {'manual_pages': selected, 'ports': read(root/materials['ports']),
            'manual_sha256': digest(root/materials['manual'])}


def review_gate(response, identity):
    if response.get('decision') != 'approve' or response.get('evidence_sha256') != identity:
        raise Fault('coverage_review', '审查要求修订或批准了不同证据', response)
    if any(response.get('checks',{}).get(k) is not True for k in CHECKS) or response.get('unresolved') != []:
        raise Fault('coverage_review', '审查未确认全部条件、端口、方法、参考和范围', response)


def confirm_receipt(item, receipt):
    verify_proof(receipt)
    item['coverage_receipt'] = fingerprint(receipt)
    if receipt['status'] == 'constraint_setup_checked':
        item['constraint_audit_status'] = 'declared_test_setup_checked'
        item['constraint_audit_scope'] = 'only supplied test setups; not device rating validation'
    elif receipt['status'] == 'reference_method_qualified':
        item['method_binding_complete'] = receipt.get('all_declared_methods_qualified',False)
        item['method_bindings'] = receipt['test_ids']
        item['binding_complete'] = False
        item['coverage_status'] = 'reference_method_qualified'
        item['benchmark_acceptance'] = receipt['electrical_acceptance']
    else:
        item['binding_complete'] = True
        item['method_binding_complete'] = True
        item['coverage_status'] = 'binding_confirmed'
        item['electrical_acceptance'] = receipt['electrical_acceptance']


def electrical_status(traces):
    states = [t['acceptance'] for t in traces]
    return 'fail' if 'fail' in states else 'pass' if states and all(s == 'pass' for s in states) else 'pending'


def validate_constraints(item, cases, proposal):
    assertions = proposal.get('assertions')
    if proposal.get('decision') != 'propose' or not isinstance(assertions,list) or not assertions:
        raise Fault('constraint_scope', '约束审计缺少可核验的测试设置断言')
    reference = item.get('reference_evidence',{}); unit = reference.get('unit')
    if unit not in UNITS:
        raise Fault('constraint_scope', '该约束单位尚无设置审计适配器')
    dim,scale = UNITS[unit]; by_id = {c['id']:c for c in cases}; checked=[]
    for assertion in assertions:
        case = by_id.get(assertion.get('test_id')); bound = assertion.get('bound')
        limit = reference.get('values',{}).get(bound)
        if case is None or bound not in ('min','max') or isinstance(limit,bool) or not isinstance(limit,(int,float)) or not math.isfinite(limit):
            raise Fault('constraint_scope', '约束断言必须引用已配置测试和手册数值边界')
        path = assertion.get('protocol_path',''); protocol = case['protocol']
        actual = path_get(protocol,path)
        dimension=bound_unit(protocol,path)
        if path in ('analysis/start','analysis/stop') and protocol['analysis']['kind']=='dc':
            dimension={'V':'V','I':'A'}.get(path_get(protocol,'components@'+protocol['analysis']['source'])['kind'])
        if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or dimension != dim:
            raise Fault('constraint_scope', '约束断言路径不是同维度的实际电路数值')
        limit *= scale
        if (bound == 'min' and actual < limit) or (bound == 'max' and actual > limit):
            raise Fault('constraint_violation','测试设置越过手册边界',{'assertion':assertion,'actual':actual,'limit':limit})
        checked.append({**assertion,'actual':actual,'limit_SI':limit})
    return checked


def saved_review_proof(store):
    proof={}
    for key,entry in store.data['requests'].items():
        if entry.get('status') == 'completed':
            for name,h in entry['hashes'].items():
                path = store.folder/'requests'/key/name
                if not path.is_file() or digest(path) != h:
                    raise Fault('cache_corrupt','审查响应文件改变')
                proof[str(path)] = h
    return proof


def write_device(output, root, packet, inventory, receipts, diagnostics):
    folder=output/packet['device'];folder.mkdir(parents=True,exist_ok=True)
    save(folder/'manual_inventory.json',inventory);save(folder/'binding_receipts.json',receipts)
    row={'device':packet['device'],'source_identity':source_identity(root,packet),'folder':str(folder.resolve()),
         'counts':counts(inventory),'diagnostics':diagnostics,
         'hashes':{n:digest(folder/n) for n in ('manual_inventory.json','binding_receipts.json')}}
    lines=['# '+packet['device']+' 覆盖确认','', '仅更新证据支持的绑定；模型验收、参考方法及约束设置审计分别统计。',
           '原手册、原清单、测试条件与验收标准保持不变。','', '| 项目 | 数量 |','|---|---:|']
    lines += ['| '+k+' | '+str(v)+' |' for k,v in row['counts'].items()]
    lines += ['', '## 待处理记录','']
    for i in inventory['items']:
        if i['kind']=='test' and not i.get('binding_complete'):
            lines.append('- '+i['id']+'：'+i.get('coverage_status','pending'))
        elif i['kind']=='constraint':
            lines.append('- '+i['id']+'：'+i.get('constraint_audit_status','constraint_audit_pending'))
    (folder/'manual_coverage_report.md').write_text('\n'.join(lines)+'\n')
    return row


def run(batch, runtime, output, check_only=False, resume=False, previous=None, rounds=2,
        transport=None, development=None, qualification=None, import_only=False):
    batch, runtime, output = Path(batch).resolve(), Path(runtime).resolve(), Path(output).resolve()
    validate_batch(batch)
    if rounds not in (1,2):raise Fault('input','覆盖审查最多两轮')
    if output.exists() and not resume:raise Fault('input','覆盖输出目录已存在；请续跑或使用新目录')
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':'coverage-binding-1','status':'prepared' if check_only else 'coverage_updated_with_gaps',
            'devices':[],'full_manual_coverage':False,'test_backend':bool(transport),'simulations':0}
    manifest=read(batch)
    code={str(p.name):digest(p) for p in Path(__file__).parent.glob('*.py')}
    with file_lock(output/'.coverage.lock'):
        for entry in manifest['devices']:
            root=(batch.parent/entry['input']).parent;packet=read(root/'device_input.json')
            inventory=load_overlay(root,packet,read(root/packet['inventory']),previous)
            receipts = read(Path(previous)/packet['device']/'binding_receipts.json') if previous else {}
            diagnostics=[];cases=[];results={};runtime_folder=runtime/entry['folder']/'configured'
            if packet.get('configured_task'):
                task=read(root/packet['configured_task']);model=copy.deepcopy(task['model'])
                model['path']=str((root/model['path']).resolve())
                cases=copy.deepcopy(task['cases'])
                for case in cases:
                    if case.get('reference'):case['reference']['path']=str((root/case['reference']['path']).resolve())
                if (runtime_folder/'summary.json').exists():
                    summary=read(runtime_folder/'summary.json')
                    if summary.get('device') != packet['device']:raise Fault('cache_corrupt','结果属于不同器件')
                    saved_results=summary.get('results',[])
                    results=saved_results if isinstance(saved_results,dict) else {r['test']:r for r in saved_results}
            jobs=[] if import_only else [i for i in inventory['items'] if (i['kind']=='test' and i.get('bindings') and not i.get('binding_complete')) or (i['kind']=='constraint' and i.get('constraint_audit_status')!='declared_test_setup_checked')]
            folder=output/packet['device']
            limits={'api_calls':max(1,len(jobs)*rounds*4),'simulations':0,'repairs':max(1,len(jobs)*rounds),'seconds':1200}
            store=None;agents=None
            if not check_only:
                identity=fingerprint({'source':source_identity(root,packet),'runtime':str(runtime),'code':code,
                                      'previous':str(previous),'import_only':import_only,'rounds':rounds,'test_backend':bool(transport)})
                store=Store(folder,identity,limits,resume and (folder/'state.json').exists())
                agents=Agents(store,packet['routes'],transport)
            for item in jobs:
                if check_only:
                    diagnostics.append({'reference_id':item['id'],'status':'requires_evidence_review','kind':item['kind']});continue
                feedback=None
                for attempt in range(rounds):
                    try:
                        bound_cases=[c for c in cases if c['id'] in item.get('bindings',[])]
                        if item['kind']=='test' and {c['id'] for c in bound_cases} != set(item['bindings']):
                            raise Fault('coverage_binding','绑定引用缺少实际测试')
                        traces=[];proof={}
                        if item['kind']=='test':
                            for case in bound_cases:
                                trace=validate_trace(case,results.get(case['id'],{}),model,runtime_folder)
                                traces.append(trace);proof.update(trace['proof_files'])
                        elif not cases:
                            raise Fault('constraint_scope','缺少已配置电路，保留约束待审计')
                        evidence={'record':item,'manual':manual_evidence(root,packet,item),
                                  'cases':bound_cases if item['kind']=='test' else cases,
                                  'model_interface':model if cases else None,
                                  'model_text':model_text(model['path'])[:60000] if cases else '',
                                  'actual_circuits':[render(c['protocol'],model) for c in (bound_cases if item['kind']=='test' else cases)],
                                  'traces':traces,'acceptance_standard':packet['acceptance_standard'],
                                  'scope':'exact declared device conditions only; reference qualification and constraint setup checks are not full device delivery'}
                        ev=fingerprint(evidence)
                        context={'task':'Verify the actual circuit/method and handbook binding. Return decision approve|revise|defer, evidence_sha256 EXACT supplied hash, checks conditions/ports/measurement/reference/scope:true only if established, unresolved:[]. Multi-channel FET and physical package conditions must not be assumed. A model specification failure does not invalidate an otherwise correct test binding. Pending administrative classification alone is not physical uncertainty.',
                                 'evidence':evidence,'evidence_sha256':ev,'feedback':feedback}
                        if item['kind']=='constraint':
                            context['task']='Propose a narrowly scoped numeric test-setup audit, not proof of device absolute ratings. Return decision propose|defer, assertions:[{test_id,protocol_path,bound:min|max}]. Use original manual min/max and same SI dimension. Use slash-separated protocol_path, e.g. components@VD/value/dc or analysis/stop. Cover all relevant setups; do not infer reliability or power/thermal capability from a static stimulus.'
                            proposed=agents.ask('constraint_auditor',context)
                            assertions=validate_constraints(item,cases,proposed)
                            context.update(proposal=proposed,program_checked_assertions=assertions)
                        else:
                            proposed=agents.ask('binding_verifier',context);review_gate(proposed,ev)
                            context['proposed_verification']=proposed
                        context['task']='Independently review the supplied evidence and program checks. Return decision approve|revise|defer, evidence_sha256 exact supplied hash, checks conditions/ports/measurement/reference/scope, unresolved:[]. Reject missing physical/multi-channel scope. Constraint approval covers only declared test setups, not device rating verification.'
                        reviewed=agents.ask('test_reviewer',context);review_gate(reviewed,ev)
                        proof.update(saved_review_proof(store))
                        for rel,h in packet['assets'].items():proof[str(root/rel)]=h
                        if packet.get('configured_task'):proof[str(root/packet['configured_task'])]=digest(root/packet['configured_task'])
                        receipt={'status':'constraint_setup_checked' if item['kind']=='constraint' else 'binding_confirmed',
                                 'source_identity':source_identity(root,packet),'evidence_sha256':ev,
                                 'test_ids':[c['id'] for c in bound_cases], 'electrical_acceptance':electrical_status(traces),
                                 'proof_files':proof,'test_backend':bool(transport),
                                 'proposed':proposed,'review':reviewed}
                        if transport:
                            diagnostics.append({'reference_id':item['id'],'status':'offline_review_verified_not_confirmed'});break
                        confirm_receipt(item,receipt);receipts[item['id']]=receipt
                        diagnostics.append({'reference_id':item['id'],'status':receipt['status']});break
                    except (Fault,OSError,KeyError,TypeError,ValueError) as error:
                        e=error if isinstance(error,Fault) else Fault('coverage_local_evidence',str(error))
                        feedback=e.record()
                        if e.kind in TERMINAL:report['status']='stopped_with_evidence';break
                        # Missing or invalid local evidence is not repaired by paying for repeated reviews.
                        if e.kind != 'coverage_review' and e.kind != 'constraint_scope':break
                        if attempt+1 < rounds:store.reserve('repairs')
                else:
                    pass
                if feedback and item['id'] not in receipts:
                    diagnostics.append({'reference_id':item['id'],'status':'pending','fault':feedback})
                if report['status']=='stopped_with_evidence':break
            if store:store.finish('coverage_review_with_gaps')
            if not check_only:
                import_methods(root,packet,inventory,receipts,diagnostics,development,qualification)
            row=write_device(output,root,packet,inventory,receipts,diagnostics)
            if store:row['usage']=store.data['usage'].copy();row['budgets']=limits
            report['devices'].append(row);save(output/'summary.json',report)
            c=row['counts']
            print(packet['device']+'：已有绑定待确认 '+str(c['configured_binding_pending'])+'；缺测试方法 '+str(c['tests_without_method'])+'；约束待审计 '+str(c['constraints_pending'])+'；模型验收通过 '+str(c['electrical_pass']),flush=True)
            if report['status']=='stopped_with_evidence':break
        save(output/'summary.json',report)
    return report


def trace_result(case, row):
    return {**row['result'], 'test':case['id'], 'execution':'completed',
            'acceptance':row.get('acceptance', acceptance(row['result'],case['expectation'])),
            'model_sha256':case['model']['sha256'], 'protocol_sha256':fingerprint(case['protocol']),
            'artifacts':row['folder'], 'artifact_hashes':row['artifact_hashes']}


def qualification_sources(root, packet, development=None, qualification=None):
    """Yield exact qualified methods with receipts, never reuse a success flag alone."""
    items={i['id']:i for i in read(root/packet['inventory'])['items']}
    if development and (Path(development)/'summary.json').exists():
        runtime=Path(development).resolve();report=read(runtime/'summary.json')
        if report.get('test_backend'):raise Fault('coverage_execution','离线开发报告不能回写正式方法')
        for dev in report.get('devices',[]):
            if dev['device']!=packet['device']:continue
            folder=inside(runtime/root.name,runtime)
            state=read(folder/'state.json')
            for row in dev.get('qualified',[]):
                record_key=row.get('workflow_key')
                checkpoint_key=record_key+':workflow' if record_key else 'workflow'
                checkpoint=state['checkpoints'][checkpoint_key];model=checkpoint['active_model']
                descriptor_folder=inside(folder/'records'/record_key,folder) if record_key else folder
                case=row['case'];rid=row['reference_id']
                if rid not in items:raise Fault('cache_corrupt','新方法引用未知手册记录')
                descriptors=[p for p in (descriptor_folder/'capabilities').glob('*.json') if read(p).get('case')==case]
                if len(descriptors)!=1:raise Fault('cache_corrupt','缺少唯一方法实测资格收据')
                descriptor=descriptors[0];saved=read(descriptor)
                ledger=state.get('plans',{}).get(record_key+':'+descriptor.stem if record_key else descriptor.stem)
                if not ledger or ledger.get('status')!='registered' or ledger.get('case')!=case:
                    raise Fault('cache_corrupt','方法收据与注册账本不同')
                post=saved.get('post_trial_review',{})
                if saved.get('review',{}).get('decision')!='approve' or post.get('decision')!='approve' or post.get('conditions_complete') is not True or post.get('measurement_correct') is not True or post.get('approved_protocol_sha256')!=fingerprint(case['protocol']):
                    raise Fault('coverage_review','新方法缺少针对同一协议的前后审查')
                proof={str(descriptor):digest(descriptor)};matching_reference=False;pre_ok=False;post_ok=False
                for key,entry in state.get('requests',{}).items():
                    if entry.get('status')!='completed':continue
                    request_folder=folder/'requests'/key
                    if not artifacts_valid(request_folder,entry['hashes']):raise Fault('cache_corrupt','方法审查缓存变化')
                    request=read(request_folder/'request.json');reference=request['context'].get('reference',{})
                    if reference.get('id')==rid and reference.get('reference_evidence')==items[rid].get('reference_evidence'):
                        matching_reference=True
                        response=read(request_folder/'response.json')
                        if request.get('role')=='test_reviewer' and request['context'].get('protocol')==case['protocol']:
                            if request['context'].get('stage')=='design_review' and response==saved['review']:pre_ok=True
                            if request['context'].get('stage')=='post_trial_review' and response==post:post_ok=True
                        proof.update({str(request_folder/n):h for n,h in entry['hashes'].items()})
                if not (matching_reference and pre_ok and post_ok):raise Fault('coverage_evidence','新方法未引用同一手册参考条件')
                from .capability_development import sample_oracles
                from .workflow import calibration_protocols
                oracles=sample_oracles(case['protocol']) if case['protocol']['measurement']['mode']=='sample' else calibration_protocols(case)
                calibrations=saved.get('calibration',[])
                if len(oracles)!=len(calibrations):raise Fault('calibration','方法校准数量不完整')
                for (p,expected),cal in zip(oracles,calibrations):
                    tol=max(abs(expected)*1e-4,1e-15)
                    oracle={'id':'oracle_'+fingerprint(p)[:12],'protocol':p,'expectation':{'unit':'oracle_native','limits':{'min':expected-tol,'max':expected+tol}}}
                    trace=validate_trace(oracle,cal['result'],model,folder)
                    if trace['acceptance']!='pass' or cal['expected']!=expected:raise Fault('calibration','独立校准未通过')
                    proof.update(trace['proof_files'])
                trace=validate_trace(case,saved['trial'],model,folder);proof.update(trace['proof_files'])
                yield rid,case,trace,proof
    if qualification and (Path(qualification)/'summary.json').exists():
        runtime=Path(qualification).resolve();report=read(runtime/'summary.json');state=read(runtime/'state.json')
        cases={c['id']:c for c in read(root/packet['fixture_drafts'])['cases']}
        for row in report.get('results',[]):
            if row.get('device')!=packet['device'] or row.get('status')!='method_qualified_reference_benchmark_only':continue
            if row.get('qualification_backend')!='real':raise Fault('coverage_execution','离线资格不能回写正式方法绑定')
            original=cases.get(row['id'])
            if original is None or set(row.get('reference_ids',[]))!=set(original['reference_ids']):raise Fault('cache_corrupt','资格报告引用不同草案或参考')
            key=fingerprint({'device':packet['device'],'case':original});entry=state.get('supervised_methods',{}).get(key)
            if not entry or not artifacts_valid(runtime,entry['hashes']):raise Fault('cache_corrupt','资格账本证据不完整')
            proof={str(inside(runtime/n,runtime)):h for n,h in entry['hashes'].items()}
            anchor=runtime
            if row.get('reused_qualification'):
                reused=row['reused_qualification'];anchor=Path(reused['source']).resolve();old=read(anchor/'state.json')
                old_entry=old.get('qualifications',{}).get(key)
                if not old_entry or not artifacts_valid(anchor,old_entry['hashes']):raise Fault('cache_corrupt','复用的旧资格缺少完整证据')
                if digest(anchor/old_entry['record'])!=reused['source_qualification_sha256']:raise Fault('cache_corrupt','复用资格身份改变')
                old_input=read((anchor/old_entry['record']).parent/'input.json')
                if old_input['case']!=original:raise Fault('cache_corrupt','复用资格的原始条件不同')
                proof.update({str(inside(anchor/n,anchor)):h for n,h in old_entry['hashes'].items()})
            else:
                # GLM must have approved this exact measured candidate in the post-trial phase.
                post_ok=False
                for request_key,request_entry in state.get('requests',{}).items():
                    if request_entry.get('status')!='completed':continue
                    f=runtime/'requests'/request_key
                    if not artifacts_valid(f,request_entry['hashes']):raise Fault('cache_corrupt','资格审查响应改变')
                    request=read(f/'request.json');ctx=request['context'];response=read(f/'response.json')
                    if request['role']=='test_reviewer' and ctx.get('stage')=='post_trial' and ctx.get('device')==packet['device'] and ctx.get('protocol')==row['protocol'] and ctx.get('reference_ids')==row['reference_ids']:
                        post_ok=response.get('decision')=='approve' and response.get('unresolved')==[] and all(response.get('checks',{}).get(k) is True for k in ('conditions','ports','measurement','scope','results'))
                        if post_ok:break
                if not post_ok:raise Fault('coverage_review','缺少同一实际候选的独立实测后审')
            case=copy.deepcopy(original);case['protocol']=row.get('protocol',original['protocol'])
            if row.get('protocol_sha256')!=fingerprint(case['protocol']) or row.get('model_sha256')!=original['model']['sha256']:
                raise Fault('cache_corrupt','资格协议或模型身份不一致')
            from .autonomous_qualification import calibration_cases
            oracles=calibration_cases(case)
            if len(oracles)!=len(row.get('calibrations',[])):raise Fault('calibration','草案独立校准不完整')
            for (p,expected),cal in zip(oracles,row['calibrations']):
                tol=max(abs(expected)*1e-4,1e-15)
                oracle={'id':'oracle','model':case['model'],'protocol':p,'expectation':{'unit':'oracle_native','limits':{'min':expected-tol,'max':expected+tol}}}
                result=trace_result(oracle,cal);trace=validate_trace(oracle,result,case['model'],anchor)
                if trace['acceptance']!='pass' or cal['expected']!=expected:raise Fault('calibration','草案校准未通过')
                proof.update(trace['proof_files'])
            trace=validate_trace(case,trace_result(case,row['benchmark']),case['model'],anchor);proof.update(trace['proof_files'])
            for rid in original['reference_ids']:yield rid,case,trace,proof


def import_methods(root,packet,inventory,receipts,diagnostics,development=None,qualification=None):
    by_id={i['id']:i for i in inventory['items']}
    try:
        for rid,case,trace,proof in qualification_sources(root,packet,development,qualification):
            item=by_id[rid]
            # Keep original physical conditions and references; only add verified method evidence.
            for rel,h in packet['assets'].items():proof[str(root/rel)]=h
            receipt={'status':'reference_method_qualified','source_identity':source_identity(root,packet),
                     'test_ids':[case['id']],'electrical_acceptance':trace['acceptance'],
                     'protocol_sha256':fingerprint(case['protocol']),'proof_files':proof,'test_backend':False,
                     'all_declared_methods_qualified':False}
            if item.get('binding_complete'):continue
            old=receipts.get(rid,{})
            if old.get('status')=='reference_method_qualified':
                verify_proof(old);receipt['proof_files']={**old['proof_files'],**proof}
                receipt['test_ids']=sorted(set(old['test_ids']+receipt['test_ids']))
            # Only the complete set of declared drafts may suppress repeated development.
            drafts=read(root/packet['fixture_drafts'])['cases'] if packet.get('fixture_drafts') else []
            required={c['id'] for c in drafts if rid in c.get('reference_ids',[])}
            receipt['all_declared_methods_qualified']=required.issubset(receipt['test_ids']) if required else case.get('origin')=='agent_developed'
            confirm_receipt(item,receipt);receipts[rid]=receipt
            diagnostics.append({'reference_id':rid,'status':'reference_method_qualified'})
    except (Fault,OSError,KeyError,TypeError,ValueError) as e:
        diagnostics.append({'status':'method_import_rejected','fault':e.record() if isinstance(e,Fault) else {'kind':'method_receipt','message':str(e)}})
