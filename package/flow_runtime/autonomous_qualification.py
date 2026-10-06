"""Generic supervised fixture repair: stage evidence -> bounded action -> real validation.
All agents use the same physical facts; metadata and reference benchmarks are not delivery.
"""
import copy
import math
import re
import shutil
from pathlib import Path
from .state import (Fault, Store, read, save, digest, fingerprint, artifacts_valid, file_lock)
from .agents import Agents
from .input_batch import validate_batch
from .draft_qualification import verified_calibration, context_for, approved
from .spice import Simulator, measure, render, raw_data, acceptance
from .workflow import calibration_protocols
from .fixture_repair_policy import (apply, policy, sensitivity_variant, sensitivity,
                                    program_facts, decision_route)
from .simulation_cache import signature, FILES

CRITICAL = ('spice.py','ac_measurements.py','ac_calibration.py','ac_calibration_runner.py',
            'simulation_cache.py','state.py')
TERMINAL = {'budget','authentication','api_configuration','credentials','cache_corrupt',
            'api_recovery_exhausted'}


def calibration_cases(case):
    if case['protocol']['analysis']['kind']!='dc':return calibration_protocols(case)
    m=case['protocol']['measurement']
    nodes=re.fullmatch(r'v\(([^,]+),([^\)]+)\)',m['signal'],re.I)
    if not nodes or m['mode']!='sample':
        raise Fault('capability_gap','缺少该直流测量表达式的独立校准')
    a,b=nodes.groups();source=case['protocol']['analysis']['source'];rows=[]
    for r in (1000,2000):
        p=copy.deepcopy(case['protocol']);p['device_nodes']={};p['checks']=[]
        p['components']=[{'kind':'R','name':'RCAL','nodes':[a,b],'value':r},
            {'kind':'R','name':'RGROUND','nodes':[b,'0'],'value':1e-6},
            {'kind':'I','name':source,'nodes':[b,a],'value':{'dc':0}}]
        rows.append((p,r*m['at']*m.get('scale',1)*m.get('sign',1)))
    return rows


def evidence_read(root, packet, requests):
    if not isinstance(requests,list) or any(not isinstance(n,str) for n in requests) or len(requests)>6 or len(set(requests))!=len(requests):
        raise Fault('proposal','补读请求必须是至多6个不同的白名单证据名')
    materials=packet['materials'];mapping={'manual_pages':'manual_page_evidence','ports':'ports',
        'electrical':'electrical','evidence_catalog':'evidence'}
    out={}
    for name in requests:
        if name not in mapping:raise Fault('proposal','不支持的补读证据：'+str(name))
        rel=materials.get(mapping[name])
        if not rel:
            out[name]={'status':'missing_from_task_packet'};continue
        path=(root/rel).resolve()
        if root not in path.parents or digest(path)!=packet['assets'].get(rel):
            raise Fault('cache_corrupt','补读资料哈希不符或越界')
        if path.stat().st_size>400000:
            out[name]={'status':'context_limit','sha256':digest(path),'size':path.stat().st_size}
        else:out[name]={'status':'read','sha256':digest(path),'data':read(path)}
    return out


def stage_context(base, case, protocol, stage, receipt, feedback, supplement, trial=None):
    """Evidence IDs enumerate only available values, never promised future results."""
    facts=program_facts(protocol,case['model'],base['model_text'],base['evidence']['ports'])
    ids=case['reference_ids']+['manual_pages','ports','actual_circuit','program_facts','calibration','model','model_text']
    ids += [name for name, value in supplement.items() if value.get('status') == 'read']
    context={'schema':'fixture-supervision-context-1','stage':stage,'device':base['device'],
        'reference_ids':case['reference_ids'],'expectation':case['expectation'],
        'acceptance_standard':base['acceptance_standard'],'manual_evidence':base['evidence'],
        'model':case['model'],'model_text':base['model_text'],'protocol':protocol,
        'actual_circuit':render(protocol,case['model']), 'program_facts':facts,
        'calibration':receipt,'repair_policy':policy(case),'feedback':feedback,
        'supplemental_evidence':supplement,'evidence_ids':ids,
        'phase_contract':{'pre_trial':'Review whether conditions/ports/measurement allow running. '
            'Benchmark results do not exist yet and are NOT required. No device pass claimed.',
            'post_trial':'Review actual calibration and benchmark outputs. Results required.',
            'pending_metadata':'Pending binding, classification and delivery flags do not by themselves '
            'block exact fixture review. Physical uncertainty still must be resolved.',
            'reference_role':'Interface benchmark only; no candidate model or entire-manual delivery claim.'},
        'response_schema':{'decision':'approve|revise|defer','reason':'concise evidence-based explanation',
            'evidence_ids':['available ID'], 'checks':{'conditions':True,'ports':True,'measurement':True,'scope':True},
            'unresolved':[], 'issues':[{'kind':'stage|syntax|process_status|missing_evidence|conditions|ports|protocol|scope|model_deviation',
                'evidence_id':'available ID','claim':'specific verifiable claim'}],
            'read_requests':['manual_pages|ports|electrical|evidence_catalog']}}
    if stage=='design':
        context['response_schema'].update(action='keep|solver_method|refine_grid|ground_reference',parameters={})
    if stage=='post_trial':
        if trial is None:raise Fault('input','后审缺少真实试验结果')
        context['trial']=trial;context['evidence_ids']=ids+['benchmark_result','exact_calibrations']
        context['response_schema']['checks']['results']=True
    return context


def gate(response, context, post=False):
    """No bypass: disputes always return to a reviewer with program facts."""
    route=decision_route(response,context['stage'])
    if route['kind']=='response_format' or response.get('read_requests'):
        raise Fault('review_revision','响应格式或待补读请求尚未闭合',{'response':response,'route':route})
    try:approved(response,context['evidence_ids'],post)
    except Fault as e:
        raise Fault('review_revision','审查尚未完成；自动返回修订',
                    {'response':response,'route':route,'validation_fault':e.record()})


def import_history(previous, jobs, runner, calibration, store=None, output=None, offline=False):
    if previous is None:return {'reused':{},'simulations_imported':0,'diagnostic_history':{}}
    previous=Path(previous).resolve();summary=read(previous/'summary.json');state=read(previous/'state.json')
    if summary.get('calibration',{}).get('identity')!=calibration['identity']:
        raise Fault('input','旧资格任务使用不同的校准身份')
    old_root=previous.parent.parent/'flow_runtime'
    if any(not (old_root/n).is_file() or digest(old_root/n)!=digest(Path(__file__).parent/n) for n in CRITICAL):
        raise Fault('input','测量执行依赖已变化；不能迁移旧实测证据')
    signatures=[]
    for root,packet,case in jobs:
        signatures.append((case['protocol'],root/case['model']['path'],case['model']))
        for p,_ in calibration_cases(case):signatures.append((p,root/case['model']['path'],case['model']))
    imported=0
    # Only exact known protocols/circuits/models, complete successful real records can seed cache.
    for old_key,entry in state.get('simulations',{}).items():
        if entry.get('status')!='completed':continue
        folder=previous/'simulations'/old_key
        if not artifacts_valid(folder,entry.get('hashes')):
            raise Fault('cache_corrupt','旧仿真文件校验失败')
        exe=read(folder/'execution.json')
        if exe.get('test_backend') or exe.get('returncode')!=0:
            raise Fault('input','离线记录不能迁移为真实校准或资格')
        data=raw_data(folder/'test.raw')
        if not data['complete']:raise Fault('cache_corrupt','旧RAW不完整')
        matching=next(((p,m,model) for p,m,model in signatures if digest(m)==digest(folder/'model.lib')
                       and render(p,model)==(folder/'test.cir').read_text()),None)
        if not matching:continue
        p,m,model=matching
        key,_=signature(p,m,runner,render(p,model))
        imported+=1
        if store is not None and not offline and not store.get('simulations',key):
            dst=output/'simulations'/key;dst.mkdir(parents=True,exist_ok=True)
            for name in FILES:shutil.copy2(folder/name,dst/name)
            hashes={n:digest(dst/n) for n in FILES}
            save(dst/'migration.json',{'source':str(folder),'source_hashes':entry['hashes'],
                                     'critical_executor_hashes':{n:digest(old_root/n) for n in CRITICAL}})
            store.put('simulations',key,{'status':'completed','hashes':hashes,'migrated_verified_real':True})
    reused={};history={}
    by_id={(packet['device'],c['id']):(root,packet,c) for root,packet,c in jobs}
    for idx,row in enumerate(summary.get('results',[]),1):
        pair=(row['device'],row['id'])
        if pair not in by_id:continue
        root,packet,case=by_id[pair]
        source_input=read(previous/('case_%02d'%idx)/'input.json')
        if source_input['case']!=case:
            raise Fault('input','旧草案与当前输入不一致；不能套用资格或失败意见')
        if row['status']!='method_qualified_reference_benchmark_only':
            history[pair]={'source':str(previous),'previous_fault':row.get('fault'),
                'disposition':'Historical opinion only; re-evaluate with stage-specific facts.'}
            continue
        key=fingerprint({'device':packet['device'],'case':case});ledger=state.get('qualifications',{}).get(key)
        if not ledger or not artifacts_valid(previous,ledger['hashes']):
            raise Fault('cache_corrupt','旧方法审查记录已损坏')
        if row.get('qualification_backend')!='real' or row.get('protocol_sha256')!=fingerprint(case['protocol']):
            raise Fault('input','旧资格不是真实同一协议')
        cps=calibration_cases(case)
        if len(row.get('calibrations',[]))!=len(cps):raise Fault('calibration','旧方法校准不完整')
        for calibration_row,(p,expected) in zip(row['calibrations'],cps):
            folder=Path(calibration_row['folder']).resolve()
            if previous not in folder.parents or not artifacts_valid(folder,calibration_row['artifact_hashes']):
                raise Fault('cache_corrupt','旧方法校准文件损坏或越界')
            if (folder/'test.cir').read_text()!=render(p,case['model']):raise Fault('calibration','旧校准电路不一致')
            value=measure(p,raw_data(folder/'test.raw'))['value']
            if abs(value-expected)>max(abs(expected)*1e-4,1e-12):raise Fault('calibration','旧方法校准误差不合格')
        bench=row['benchmark'];folder=Path(bench['folder']).resolve()
        if previous not in folder.parents or not artifacts_valid(folder,bench['artifact_hashes']):
            raise Fault('cache_corrupt','旧资格实测证据损坏或越界')
        if (folder/'test.cir').read_text()!=render(case['protocol'],case['model']) or digest(folder/'model.lib')!=case['model']['sha256']:
            raise Fault('cache_corrupt','旧资格电路或模型身份不同')
        result=measure(case['protocol'],raw_data(folder/'test.raw'))
        if result!=bench['result']:raise Fault('cache_corrupt','旧资格报告与RAW不一致')
        reused[pair]={'row':row,'source':str(previous),'source_qualification_sha256':digest(previous/ledger['record'])}
    return {'reused':reused,'simulations_imported':imported,'diagnostic_history':history}


def trial_run(case, proposal, protocol, sim, root, store):
    calibrations=[]
    for p,expected in calibration_cases(case):
        data,folder=sim.run(p,root/case['model']['path'],'calibration_'+case['id'])
        measured=measure(p,data)
        if abs(measured['value']-expected)>max(abs(expected)*1e-4,1e-12):
            raise Fault('calibration','独立测量校准不合格',measured)
        calibrations.append({'expected':expected,'result':measured,'folder':str(folder),
                             'artifact_hashes':store.get('simulations',folder.name)['hashes']})
    data,folder=sim.run(protocol,root/case['model']['path'],'benchmark_'+case['id'])
    result=measure(protocol,data)
    benchmark={'result':result,'acceptance':acceptance(result,case['expectation']),
               'folder':str(folder),'artifact_hashes':store.get('simulations',folder.name)['hashes'],
               'model_role':'reference_interface_benchmark_only'}
    trial={'exact_calibrations':calibrations,'benchmark_result':benchmark}
    alt=sensitivity_variant(case,proposal)
    if alt:
        alt_data,alt_folder=sim.run(alt,root/case['model']['path'],'sensitivity_'+case['id'])
        second=measure(alt,alt_data)
        trial['sensitivity']={**sensitivity(result,second),'alternative_protocol':alt,
            'folder':str(alt_folder),'artifact_hashes':store.get('simulations',alt_folder.name)['hashes']}
    return trial


def proof_files(output, folder, store, trial=None):
    files={str(p.relative_to(output)):digest(p) for p in folder.glob('*.json')}
    for key,r in store.data['requests'].items():
        if r.get('status')=='completed':
            for n,h in r['hashes'].items():files['requests/'+key+'/'+n]=h
    if trial:
        rows=trial['exact_calibrations']+[trial['benchmark_result']]+([trial['sensitivity']] if trial.get('sensitivity') else [])
        for r in rows:
            f=Path(r['folder'])
            for n,h in r['artifact_hashes'].items():files[str((f/n).relative_to(output))]=h
    return files


def run(batch, calibration, runner_path, output, previous=None, rounds=3, resume=False,
        check_only=False, agent_transport=None, simulation_transport=None):
    batch,output,runner_path=Path(batch).resolve(),Path(output).resolve(),Path(runner_path).resolve()
    if not 1<=rounds<=3:raise Fault('input','rounds必须在1至3之间')
    validate_batch(batch);receipt=verified_calibration(calibration,runner_path)
    jobs=[]
    for d in read(batch)['devices']:
        root=(batch.parent/d['input']).parent;packet=read(root/'device_input.json')
        for c in read(root/packet['fixture_drafts'])['cases']:jobs.append((root,packet,c))
    runner={'argv':[str(runner_path),'-b','-ascii','{circuit}'],'timeout_seconds':120}
    offline=bool(agent_transport or simulation_transport)
    history=import_history(previous,jobs,runner,receipt,offline=offline)
    previous_hash=digest(Path(previous)/'summary.json') if previous else None
    limits={'api_calls':168,'simulations':84,'repairs':len(jobs)*max(0,rounds-1),'seconds':7200}
    identity=fingerprint({'batch':digest(batch),'jobs':[(p,c) for _,p,c in jobs],
        'code':{p.name:digest(p) for p in Path(__file__).parent.glob('*.py')},
        'calibration':receipt,'runner':runner,'limits':limits,'rounds':rounds,'previous':previous_hash,'offline':offline})
    prepared={'status':'prepared','drafts':len(jobs),'reusable_methods':len(history['reused']),
        'reusable_simulations':history['simulations_imported'],'budgets':limits,
        'calibration':receipt,'api_calls':0,'simulations':0}
    if check_only:
        if output.exists():raise Fault('input','请使用新预检目录')
        save(output/'summary.json',prepared);return prepared
    with file_lock(output/'.workflow.lock'):
        store=Store(output,identity,limits,resume);results=[];terminal=None;shared_keys={}
        imported=import_history(previous,jobs,runner,receipt,store,output,offline)
        for index,(root,packet,case) in enumerate(jobs,1):
            key=fingerprint({'device':packet['device'],'case':case});pair=(packet['device'],case['id'])
            folder=output/('case_%02d'%index);cached=store.get('supervised_methods',key)
            if cached:
                if not artifacts_valid(output,cached['hashes']):raise Fault('cache_corrupt','监督资格缓存损坏')
                results.append(read(output/cached['record']));store.event(case['id'],'supervised_result_reused');continue
            if pair in imported['reused'] and not offline:
                prior=imported['reused'][pair];row=copy.deepcopy(prior['row'])
                row['reused_qualification']=dict(source=prior['source'],source_qualification_sha256=prior['source_qualification_sha256'])
                save(folder/'qualification.json',row);save(output/'capabilities'/(key+'.json'),row)
                name=str((folder/'qualification.json').relative_to(output))
                store.put('supervised_methods',key,{'record':name,'hashes':{name:digest(output/name)}})
                results.append(row);store.event(case['id'],'previous_verified_qualification_reused');continue
            row={'device':packet['device'],'id':case['id'],'reference_ids':case['reference_ids'],
                 'status':'supervision_pending','delivery_eligible':False,'candidate_model_verified':False,
                 'full_manual_coverage':False,'repair_retained':False,'attempts':[]}
            agents=Agents(store,packet['routes'],transport=agent_transport);agents.keys=shared_keys
            sim=Simulator(store,runner,case['model'],transport=simulation_transport)
            feedback=imported['diagnostic_history'].get(pair);supplement={};trial=None;last_fault=None
            try:
                base=context_for(root,packet,case)
                for n in range(1,rounds+1):
                    attempt_key=key+':'+str(n);old=store.get('fixture_attempts',attempt_key)
                    if old:
                        if not artifacts_valid(output,old['hashes']):raise Fault('cache_corrupt','修订反馈缓存损坏')
                        rec=read(output/old['record']);row['attempts'].append(rec)
                        feedback=rec['feedback'];supplement=rec['supplement'];last_fault=rec.get('fault');continue
                    if n>1:store.reserve('repairs',attempt_key)
                    candidate=copy.deepcopy(case['protocol']);proposal=None;stage='design';trial=None
                    try:
                        ctx=stage_context(base,case,candidate,'design',receipt,feedback,supplement)
                        save(folder/('context_%02d_design.json'%n),ctx)
                        proposal=agents.ask('test_designer',ctx)
                        candidate=apply(case,proposal);gate(proposal,ctx)
                        stage='pre_trial'
                        ctx=stage_context(base,case,candidate,stage,receipt,feedback,supplement)
                        ctx['proposal']=proposal;save(folder/('context_%02d_pre.json'%n),ctx)
                        pre=agents.ask('test_reviewer',ctx);gate(pre,ctx)
                        stage='trial';trial=trial_run(case,proposal,candidate,sim,root,store)
                        save(folder/('trial_%02d.json'%n),trial)
                        stage='post_trial'
                        ctx=stage_context(base,case,candidate,stage,receipt,feedback,supplement,trial)
                        ctx['proposal']=proposal;ctx['pre_review']=pre
                        save(folder/('context_%02d_post.json'%n),ctx)
                        post=agents.ask('test_reviewer',ctx);gate(post,ctx,True)
                        row.update(status='method_qualified_reference_benchmark_only',protocol=candidate,
                            protocol_sha256=fingerprint(candidate),model_sha256=case['model']['sha256'],
                            benchmark=trial['benchmark_result'],calibrations=trial['exact_calibrations'],
                            sensitivity=trial.get('sensitivity'),repair_retained=candidate!=case['protocol'],
                            qualification_backend='offline_test' if offline else 'real')
                        store.event(case['id'],'repair_retained' if row['repair_retained'] else 'method_qualified')
                        if trial['benchmark_result']['acceptance']=='fail':
                            queue={'device':packet['device'],'test':case['id'],'reason':'model_deviation',
                                'result':trial['benchmark_result'],'expectation':case['expectation'],
                                'status':'candidate_model_required_before_model_repair',
                                'reference_model_must_not_be_modified':True}
                            save(output/'model_repair_queue'/(key+'.json'),queue)
                            row['model_repair_required']=queue
                        break
                    except Fault as e:
                        last_fault=e.record()
                        response=e.evidence.get('response',proposal or {})
                        read_requests=response.get('read_requests',[]) if isinstance(response,dict) else []
                        requested=read_requests or (['manual_pages','ports','electrical','evidence_catalog']
                            if e.kind=='review_revision' else [])
                        if requested:
                            try:supplement=evidence_read(root,packet,requested)
                            except Fault as read_fault:
                                if read_fault.kind in TERMINAL:raise
                                supplement={'read_request_error':read_fault.record()}
                        feedback={'failed_stage':stage,'fault':last_fault,'proposal':proposal,
                            'candidate_protocol':candidate,'program_facts':program_facts(candidate,case['model'],base['model_text'],base['evidence']['ports']),
                            'trial':trial,'disposition':'Return to design/review with verified stage facts; '
                                'do not relax conditions. Candidate not retained.'}
                        rec={'round':n,'stage':stage,'fault':last_fault,'feedback':feedback,'supplement':supplement,
                             'candidate_retained':False}
                        path=folder/('attempt_%02d_feedback.json'%n);save(path,rec)
                        store.event(case['id'],'candidate_rolled_back',last_fault)
                        if e.kind in TERMINAL:raise
                        row['attempts'].append(rec)
                        name=str(path.relative_to(output));store.put('fixture_attempts',attempt_key,
                            {'record':name,'hashes':{name:digest(path)}})
                else:row.update(status='repair_budget_with_gaps',fault=last_fault)
            except Fault as e:
                row.update(status=e.kind,fault=e.record())
                if e.kind in TERMINAL:terminal=e.record()
            except (OSError,KeyError,ValueError,TypeError) as e:
                row.update(status='local_fault',fault={'kind':'local_fault','message':str(e)})
            save(folder/'qualification.json',row)
            if row['status']=='method_qualified_reference_benchmark_only' and not offline:
                save(output/'capabilities'/(key+'.json'),row)
            if not terminal:
                name=str((folder/'qualification.json').relative_to(output));store.put('supervised_methods',key,
                    {'record':name,'hashes':proof_files(output,folder,store,trial)})
            results.append(row);save(output/'progress.json',results)
            if terminal:break
        report={'status':'stopped_with_evidence' if terminal else 'supervised_qualification_completed_with_gaps',
            'drafts':len(jobs),'results':results,'not_processed':len(jobs)-len(results),
            'method_qualified':sum(r['status']=='method_qualified_reference_benchmark_only' for r in results),
            'retained_repairs':sum(r.get('repair_retained',False) for r in results),
            'previous_qualifications_reused':sum('reused_qualification' in r for r in results),
            'usage':store.data['usage'],'budgets':limits,'terminal':terminal,'calibration':receipt,
            'inventory_modified':False,'models_modified':False,'full_batch_delivery':False,
            'remaining_capabilities':'No arbitrary executor edits; no candidate model selected for these reference fixtures. '
                'Unsupported action or missing physical condition remains an explicit gap.'}
        save(output/'summary.json',report)
        lines=['# 自动夹具修复与资格报告','', '状态：'+report['status'],
               '方法资格：%d/%d；保留修复：%d' % (report['method_qualified'],len(jobs),report['retained_repairs']),
               '', '手册条件、模型和输入清单未修改；参考模型测试不代表器件交付。','']
        for r in results:
            lines.append('- %s / %s：%s'%(r['device'],r['id'],r['status']))
            if r.get('fault'):lines.append('  缺口：'+r['fault']['message'])
        (output/'report.md').write_text('\n'.join(lines)+'\n')
        store.finish(report['status']);return report
