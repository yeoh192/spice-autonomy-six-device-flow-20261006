"""Candidate-only model iteration with full active regression and verified RAW reuse."""
import copy,shutil,os
from pathlib import Path
from .state import Fault,Store,BudgetEnd,read,save,digest,fingerprint,file_lock,artifacts_valid
from .task import load_task
from .agents import Agents
from .spice import Simulator,raw_data,render
from .simulation_cache import signature,FILES
from .workflow import Workflow
from .input_batch import validate_batch

TERMINAL={'authentication','credentials','api_configuration','api_recovery_exhausted','cache_corrupt'}

def candidate_task(root,packet):
    if not packet.get('configured_task'):return None
    task,assets=load_task(root/packet['configured_task'])
    provenance=task['model'].get('provenance',{})
    if provenance.get('role')=='reference_interface_benchmark_only' or provenance.get('kind')=='vendor_reference_only':
        raise Fault('candidate_gap','参考接口模型不能直接转为拟合候选')
    task.pop('input_integration',None)
    task['policy']['optimization_attempts']=3
    task['policy']['model_diagnosis_enabled']=True
    task['policy']['diagnostic_attempts']=6
    task['policy']['diagnostic_revision_attempts']=3
    task['policy']['diagnostic_analysis_attempts']=2
    task['budgets']={'api_calls':24,'simulations':max(64,len(task['cases'])*8),'repairs':12,'seconds':1800}
    task['limitations'].append('Model iteration covers all configured active tests; entire manual coverage remains separately pending.')
    return task,assets

def seed_configured(store,old,task):
    old=Path(old)
    if not (old/'state.json').exists():return 0
    state=read(old/'state.json');copied=0
    snapshot_path=old/'input_snapshot.json'
    if not snapshot_path.exists():snapshot_path=old/'iteration_input.json'
    snapshot=read(snapshot_path) if snapshot_path.exists() else None
    if snapshot and fingerprint(snapshot)!=state['identity']:raise Fault('cache_corrupt','历史输入及代码快照身份不符')
    for key,entry in state.get('simulations',{}).items():
        if entry.get('status')!='completed':continue
        folder=old/'simulations'/key
        if not artifacts_valid(folder,entry.get('hashes')):raise Fault('cache_corrupt','历史仿真证据哈希变化')
        if any(not (folder/n).is_file() for n in FILES):continue
        exe=read(folder/'execution.json')
        if exe.get('test_backend') or exe.get('returncode')!=0:continue
        if not raw_data(folder/'test.raw')['complete']:continue
        if digest(folder/'model.lib')!=digest(task['model']['path']):continue
        for case in task['cases']:
            text=render(case['protocol'],task['model'])
            if (folder/'test.cir').read_text()!=text:continue
            new_key,_=signature(case['protocol'],task['model']['path'],task['runner'],text)
            if new_key!=key:
                # New orchestration module changes global cache hash. Verify every old
                # runtime dependency before reproducing the original cache signature.
                if not snapshot:continue
                old_code={Path(n).name:h for n,h in snapshot['code'].items() if (n.startswith('flow_runtime/') and len(Path(n).parts)==2) or len(Path(n).parts)==1}
                _,new_signature=signature(case['protocol'],task['model']['path'],task['runner'],text)
                orchestration_only={'evidence.py','model_diagnostics.py','workflow.py','batch_model_iteration.py','agents.py','interface_contracts.py'}
                if not old_code or any(new_signature['code'].get(n)!=h for n,h in old_code.items() if n not in orchestration_only):continue
                prior={**new_signature,'code':old_code}
                if fingerprint(prior)!=key:continue
                key=new_key
            if not store.get('simulations',key):
                target=store.folder/'simulations'/key;target.mkdir(parents=True,exist_ok=True)
                for n in FILES:shutil.copy2(folder/n,target/n)
                store.put('simulations',key,{'status':'completed','hashes':{n:digest(target/n) for n in FILES},'verified_source':str(folder)})
                copied+=1
            break
    return copied

def iterate(workflow):
    terminal='finished'
    try:
        workflow.results=workflow.evaluate_all(workflow.model_path,'model_iteration_baseline');workflow.checkpoint()
        workflow.optimize()
        if workflow.policy.get('model_diagnosis_enabled'):
            from .model_diagnostics import repair
            repair(workflow)
        workflow.checkpoint()
    except KeyboardInterrupt:
        terminal='interrupted';workflow.checkpoint()
    except Fault as e:
        terminal='budget_exhausted' if isinstance(e,BudgetEnd) else 'stopped_with_evidence'
        workflow.gaps.append({'stage':'model_iteration','fault':e.record()});workflow.checkpoint()
    return workflow.audit(terminal)

def run(batch,source_runtime,output,check_only=False,resume=False,registered_tasks=None,gpt_library=None):
    batch=Path(batch).resolve();source_runtime=Path(source_runtime).resolve();output=Path(output).resolve();validate_batch(batch)
    if output.exists() and not resume:raise Fault('input','请使用新模型迭代目录或--resume')
    rows=[];report={'status':'prepared' if check_only else 'model_iteration_completed_with_gaps','devices':rows,'full_batch_delivery':False,'original_models_modified':False}
    registered=[]
    if registered_tasks:
        from .registered_integration import load_registered
        registered=load_registered(registered_tasks,gpt_library)
    shared_keys={};code={str(f.relative_to(Path(__file__).parent)):digest(f) for f in Path(__file__).parent.rglob('*.py')}
    with file_lock(output/'.iteration.lock'):
        for row in read(batch)['devices']:
            root=(batch.parent/row['input']).parent;packet=read(root/'device_input.json');entry={'device':packet['device'],'delivery_passed':False};rows.append(entry)
            try:
                prepared=candidate_task(root,packet)
                if prepared is None:
                    entry.update(status='blocked_no_candidate_and_qualified_tests',reason='已有草案或参考接口不等于正式候选及有资格的测试；不能修改参考模型充当交付。')
                    save(output/'summary.json',report);continue
                task,assets=prepared
                if os.environ.get('SPICE_ROUTES_FILE'):
                    routes=read(Path(os.environ['SPICE_ROUTES_FILE']).resolve())
                    probe=Agents(None,routes)
                    probe.route('model_optimizer');probe.route('patch_reviewer')
                    task['routes']=routes
                    print('API路由：'+routes['design']['model']+' / '+routes['review']['model'],flush=True)
                if registered_tasks:
                    from .registered_integration import augment_candidate
                    task,extra_assets,registration=augment_candidate(task,registered)
                    assets.update(extra_assets);entry['registered_library']=registration
                entry.update(active_regression_tests=len(task['cases']),budgets=task['budgets'],scope='full configured active regression; not full manual coverage')
                if check_only:
                    entry['status']='model_iteration_ready';save(output/'summary.json',report);continue
                folder=output/row['folder'];identity=fingerprint({'task':task,'assets':assets,'code':code})
                store=Store(folder,identity,task['budgets'],resume and (folder/'state.json').exists())
                save(folder/'iteration_input.json',{'task':task,'assets':assets,'code':code})
                # Lock source workflow while examining reusable evidence; never write its state.
                baseline_folder=source_runtime/row['folder']/'configured'
                if not baseline_folder.exists() and (source_runtime/row['folder']/'iteration_input.json').exists():
                    baseline_folder=source_runtime/row['folder']
                with file_lock(baseline_folder/'.workflow.lock'):
                    entry['baseline_records_imported']=seed_configured(store,baseline_folder,task)
                for registered_row in registered:
                    if registered_row['device']==task['device']:
                        old=source_runtime/'registered_regression'/task['device']/Path(registered_row['task']).parent.name
                        with file_lock(old/'.workflow.lock'):
                            entry['baseline_records_imported']+=seed_configured(store,old,task)
                agents=Agents(store,task['routes']);agents.keys=shared_keys
                workflow=Workflow(task,store,agents,Simulator(store,task['runner'],task['model']))
                print(packet['device']+'：模型迭代，完整活动回归 '+str(len(workflow.cases))+' 项',flush=True)
                result=iterate(workflow)
                entry.update(status=result['workflow_status'],electrical_acceptance=result['electrical_acceptance'],model=result['model'],model_sha256=result['model_sha256'],report=str(folder/'summary.json'),usage=result['usage'])
                save(output/'summary.json',report)
            except Fault as e:
                entry.update(status='stopped_with_evidence',fault=e.record());save(output/'summary.json',report)
                if e.kind in TERMINAL:report['status']='stopped_with_evidence';break
        save(output/'summary.json',report)
    return report
