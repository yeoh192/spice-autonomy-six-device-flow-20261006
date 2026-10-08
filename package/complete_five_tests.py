#!/usr/bin/env python3
"""Five-device test development and qualification; does not fit benchmark models."""
import argparse,getpass,json,os,subprocess,sys
from pathlib import Path
from flow_runtime.state import save,file_lock
from flow_runtime.input_batch import validate_batch

DEVICES=['1N4148','EMHK350ARA470MF80G','750311423','ACS723KMATR-20AB-T','ADA4528-1_MSOP']

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for n in ('batch','runner','calibration','previous','source-runtime','output'):ap.add_argument('--'+n,type=Path,required=True)
    ap.add_argument('--check-only',action='store_true');ap.add_argument('--resume',action='store_true')
    ap.add_argument('--seconds',type=int,default=7200)
    a=ap.parse_args();validate_batch(a.batch)
    out=a.output.resolve()
    if out.exists() and not a.resume:raise ValueError('请使用新输出目录或--resume')
    if not a.check_only:
        for env,label in [('DASHSCOPE_API_KEY','官方Qwen'),('GLM_API_KEY','智谱官方GLM')]:
            if not os.environ.get(env):
                if not sys.stdin.isatty():raise ValueError('请在交互终端隐藏输入密钥')
                key=getpass.getpass('请输入'+label+' API Key（隐藏输入，不保存）：').strip()
                if not key:raise ValueError('密钥为空')
                os.environ[env]=key
    result={'status':'prepared' if a.check_only else 'test_completion_with_gaps','devices':DEVICES,'stages':[],'full_batch_delivery':False}
    here=Path(__file__).resolve().parent
    with file_lock(out/'.completion.lock'):
        def call(script,args,destination):
            cmd=[sys.executable,str(here/script)]+args+['--output',str(destination)]
            if a.check_only:cmd+=['--check-only']
            if a.resume and destination.exists():cmd+=['--resume']
            rc=subprocess.run(cmd).returncode
            result['stages'].append({'stage':script,'returncode':rc,'report':str(destination/'summary.json')});save(out/'summary.json',result)
            if rc:result['status']='stopped_with_evidence';save(out/'summary.json',result);raise SystemExit(2)
            if (destination/'summary.json').is_file() and json.loads((destination/'summary.json').read_text()).get('status')=='stopped_with_evidence':
                result['status']='stopped_with_evidence';save(out/'summary.json',result);raise SystemExit(2)
            return True
        if not call('spice_flow.py',['calibrate-ac','--runner',str(a.runner.resolve())],out/'ac_calibration'):return
        if not call('develop_inventory.py',['--batch',str(a.batch.resolve()),'--runner',str(a.runner.resolve()),'--devices']+DEVICES+['--max-items','250','--seconds',str(a.seconds)],out/'inventory_development'):return
        if a.check_only:
            manifest=json.loads(a.batch.read_text());drafts=sum(r.get('draft_tests',0) for r in manifest['devices'])
            result['stages'].append({'stage':'draft_repair','status':'awaiting_current_real_calibration','drafts':drafts,'previous_qualifications_not_promoted':True})
        else:
            current_cal=out/'ac_calibration/summary.json'
            repair_args=['repair-drafts','--batch',str(a.batch.resolve()),'--runner',str(a.runner.resolve()),'--calibration',str(current_cal),'--rounds','3']
            old=a.previous/'summary.json'
            if old.is_file() and json.loads(old.read_text()).get('calibration',{}).get('identity')==json.loads(current_cal.read_text()).get('identity'):
                repair_args+=['--previous',str(a.previous.resolve())]
            if not call('spice_flow.py',repair_args,out/'draft_repair'):return
        call('coverage_audit.py',['--batch',str(a.batch.resolve()),'--runtime',str(a.source_runtime.resolve()),'--development',str(out/'inventory_development'),'--qualification',str(out/'draft_repair'),'--import-only'],out/'coverage_final')
        save(out/'summary.json',result)
    print('五器件测试能力：'+result['status']+'；报告：'+str(out/'summary.json'),flush=True)
if __name__=='__main__':main()
