#!/usr/bin/env python3
"""Serial available-stage runner. Missing capabilities never count as delivery."""
import argparse,json,os,subprocess,sys,getpass
from pathlib import Path
from flow_runtime.state import save,file_lock,digest,fingerprint,Fault
from flow_runtime.input_batch import validate_batch

def aggregate(rows):
    return {"status":"finished_with_gaps", "full_batch_delivery":False,
            "scope":"configured regression plus inventory-driven development and supervised draft qualification; not six-device full coverage",
            "devices":rows}

def run(batch,output,calibration,runner,previous,check_only=False,resume=False,execute=None,coverage=None,registered_tasks=None,gpt_library=None):
    batch=Path(batch).resolve();output=Path(output).resolve()
    validate_batch(batch)
    execute=execute or subprocess.run
    if output.exists() and not resume:raise ValueError("输出目录已存在，请使用--resume或新目录")
    output.mkdir(parents=True,exist_ok=True)
    identity={"batch":digest(batch),"calibration":digest(calibration),"runner":str(Path(runner).resolve()),"previous":str(Path(previous).resolve()) if previous else None}
    if bool(registered_tasks) != bool(gpt_library):raise ValueError("测试库与登记任务必须同时提供")
    registered=[]
    if registered_tasks:
        from flow_runtime.registered_integration import load_registered
        registered=load_registered(registered_tasks,gpt_library)
        identity['registered_tasks']=digest(registered_tasks)
        identity['registered_identity']=fingerprint(registered)
    identity_path=output/'batch_identity.json'
    if resume and identity_path.exists() and json.loads(identity_path.read_text())!=identity:raise ValueError("续跑输入改变")
    save(identity_path,identity)
    rows=[]; manifest=json.loads(batch.read_text())
    def child(args,folder):
        command=[sys.executable,str(Path(__file__).with_name('spice_flow.py'))]+args
        if resume and (folder/'state.json').exists():command+=['--resume']
        result=execute(command)
        return result.returncode
    with file_lock(output/'.batch.lock'):
        if registered_tasks:
            library_rows=[]
            for registered_row in registered:
                folder=output/'registered_regression'/registered_row['device']/Path(registered_row['task']).parent.name
                args=['preflight' if check_only else 'run','--task',registered_row['task'],'--output',str(folder)]
                print('已登记测试：'+registered_row['device']+' '+str(registered_row['tests'])+' 项',flush=True)
                code=child(args,folder)
                report=json.loads((folder/'summary.json').read_text()) if (folder/'summary.json').exists() else {}
                expected_gap=code==2 and report.get('workflow_status',report.get('status'))=='finished_with_gaps'
                library_rows.append({**registered_row,'returncode':code,'summary':str(folder/'summary.json')})
                save(output/'registered_regression/summary.json',{'results':library_rows,'full_manual_coverage':False})
                if code and not expected_gap:
                    result=aggregate(rows);result.update(status='stopped_with_evidence',registered_regression=library_rows)
                    save(output/'summary.json',result);return result
        for entry in manifest['devices']:
            packet_path=batch.parent/entry['input'];packet=json.loads(packet_path.read_text())
            row={"device":entry['device'],"configured_tests":entry['configured_tests'],"draft_tests":entry['draft_tests'],"delivery_passed":False,"stages":[]}
            print('六器件批次：'+row['device'],flush=True)
            if packet.get('configured_task'):
                folder=output/entry['folder']/'configured'
                args=['preflight' if check_only else 'run','--task',str(packet_path.parent/packet['configured_task']),'--output',str(folder)]
                code=child(args,folder)
                row['stages'].append({"stage":"configured_regression","returncode":code,"report":str(folder/'summary.json'),"scope":"existing configured tests only"})
            elif not entry['draft_tests']:
                row['stages'].append({"stage":"capability_development","status":"queued_inventory_development","reason":"交自动清单开发；无资格前不能进行模型拟合或验收"})
            else:
                row['stages'].append({"stage":"draft_qualification","status":"queued_shared_qualification"})
            row['stages'].append({'stage':'inventory_development','status':'queued_shared_development'})
            rows.append(row);save(output/'summary.json',aggregate(rows))
        prior_coverage=coverage
        coverage=output/'coverage_configured'
        def audit(destination, extra=None):
            cmd=[sys.executable,str(Path(__file__).with_name('coverage_audit.py')),
                 '--batch',str(batch),'--runtime',str(output),'--output',str(destination)]
            if gpt_library:cmd+=['--gpt-library',str(Path(gpt_library).resolve())]
            if check_only:cmd+=['--check-only']
            if resume and destination.exists():cmd+=['--resume']
            return execute(cmd+(extra or [])).returncode
        coverage_code=audit(coverage,['--previous',str(prior_coverage)] if prior_coverage else None)
        if coverage_code:
            result=aggregate(rows);result['status']='stopped_with_evidence'
            result['coverage']={'returncode':coverage_code,'report':str(coverage/'summary.json'),'fault':str(coverage/'failure.json')}
            save(output/'summary.json',result);return result
        development=output/'inventory_development'
        command=[sys.executable,str(Path(__file__).with_name('develop_inventory.py')),'--batch',str(batch),'--runner',str(runner),'--output',str(development),'--coverage',str(coverage)]
        if check_only:command+=['--check-only']
        if resume and development.exists():command+=['--resume']
        development_code=execute(command).returncode
        folder=output/'draft_repair'
        args=['repair-drafts','--batch',str(batch),'--calibration',str(Path(calibration).resolve()),'--runner',str(Path(runner).resolve()),'--output',str(folder),'--rounds','3']
        if previous:args+=['--previous',str(Path(previous).resolve())]
        if check_only:args+=['--check-only']
        calibration_pending=None
        if check_only and registered_tasks:
            from flow_runtime.draft_qualification import verified_calibration
            try:verified_calibration(calibration,runner)
            except (Fault,OSError,KeyError,ValueError) as e:
                calibration_pending=str(e)
        if calibration_pending:
            code=0
            save(folder/'summary.json',{'status':'pending_current_calibration','reason':calibration_pending,'next_action':'Launcher performs current-version AC calibration before real run','api_calls':0,'simulations':0})
            print('草案修复预检：待当前版本交流校准；真实启动时自动执行',flush=True)
        else:code=child(args,folder)
        final_coverage=output/'coverage_final'
        final_code=audit(final_coverage,['--import-only','--previous',str(coverage),'--development',str(development),'--qualification',str(folder)])
        result=aggregate(rows);result['coverage']={'returncode':final_code,'report':str(final_coverage/'summary.json')}
        if final_code==0:
            coverage_report=json.loads((final_coverage/'summary.json').read_text()) if (final_coverage/'summary.json').exists() else {}
            for row in rows:
                match=next((d for d in coverage_report.get('devices',[]) if d['device']==row['device']),None)
                if match:row['coverage']=match['counts'];row['coverage_inventory']=str(Path(match['folder'])/'manual_inventory.json')
        result['inventory_development']={'returncode':development_code,'report':str(development/'summary.json')}
        result['draft_qualification']={"returncode":code,"report":str(folder/'summary.json')}
        iteration=output/'model_iteration'
        iteration_code=None
        if final_code==0:
            iteration_command=[sys.executable,str(Path(__file__).with_name('iterate_models.py')),'--batch',str(batch),'--source-runtime',str(output),'--output',str(iteration)]
            if registered_tasks:iteration_command+=['--registered-tasks',str(Path(registered_tasks).resolve()),'--gpt-library',str(Path(gpt_library).resolve())]
            if check_only:iteration_command+=['--check-only']
            if resume and iteration.exists():iteration_command+=['--resume']
            iteration_code=execute(iteration_command).returncode
        if registered_tasks:result['registered_regression']={'tests':sum(r['tests'] for r in registered),'report':str(output/'registered_regression/summary.json')}
        result['model_iteration']={'returncode':iteration_code,'report':str(iteration/'summary.json'),'status':'blocked_coverage_failure' if final_code else 'dispatched'}
        failed_stages=[]
        for name,returncode,report_path in [('inventory_development',development_code,development/'summary.json'),('draft_qualification',code,folder/'summary.json'),('model_iteration',iteration_code,iteration/'summary.json')]:
            saved=json.loads(report_path.read_text()) if report_path.exists() else {}
            if returncode or saved.get('status')=='stopped_with_evidence':failed_stages.append(name)
        result['failed_stages']=failed_stages
        result['status']='stopped_with_evidence' if final_code or failed_stages else 'prepared_with_gaps' if check_only else 'finished_with_gaps'
        save(output/'summary.json',result)
        print('六器件批次结束；全覆盖验收未通过。报告：'+str(output/'summary.json'),flush=True)
        return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('batch','output','calibration','runner'):ap.add_argument('--'+name,type=Path,required=True)
    ap.add_argument('--previous',type=Path)
    ap.add_argument('--registered-tasks',type=Path)
    ap.add_argument('--gpt-library',type=Path)
    ap.add_argument('--coverage',type=Path,help='复用上次已验证的覆盖清单，待确认记录仍会审查')
    ap.add_argument('--check-only',action='store_true');ap.add_argument('--resume',action='store_true')
    a=ap.parse_args()
    if not a.check_only:
        # Kept only in this process and child environments, never serialized.
        for name,label in [('DASHSCOPE_API_KEY','官方Qwen'),('GLM_API_KEY','智谱官方GLM')]:
            if not os.environ.get(name):
                if not sys.stdin.isatty():raise ValueError('请在交互终端执行以隐藏输入密钥')
                key=getpass.getpass('请输入'+label+' API Key（隐藏输入，不保存）：')
                if not key.strip():raise ValueError('密钥为空')
                os.environ[name]=key.strip()
    run(a.batch,a.output,a.calibration,a.runner,a.previous,a.check_only,a.resume,coverage=a.coverage,registered_tasks=a.registered_tasks,gpt_library=a.gpt_library)
if __name__=='__main__':main()
