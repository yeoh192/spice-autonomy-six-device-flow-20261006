#!/usr/bin/env python3
"""Run registered exact-condition cases through the standard unified entrypoint."""
import argparse,json,subprocess,sys
from pathlib import Path
from flow_runtime.state import save
ROOT=Path(__file__).resolve().parent
ORDER=['1N4148','EMHK350ARA470MF80G','750311423','ACS723KMATR-20AB-T','ADA4528-1_MSOP']
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--resume',action='store_true');a=ap.parse_args()
 paths=ROOT/'runs/registered_tasks'
 subprocess.run([sys.executable,str(ROOT/'use_gpt_test_library.py'),'--output',str(paths)],check=True)
 tasks=json.loads((paths/'batch.json').read_text())['tasks'];tasks.sort(key=lambda r:ORDER.index(r['device']))
 results=[]
 for row in tasks:
  destination=a.output/row['device']/Path(row['task']).parent.name
  cmd=[sys.executable,str(ROOT/'spice_flow.py'),'run','--task',row['task'],'--output',str(destination)]
  if destination.exists():
   if not a.resume:raise ValueError('Existing output; use --resume')
   cmd+=['--resume']
  print('Registered regression:',row['device'],row['tests'],'tests',flush=True)
  rc=subprocess.run(cmd).returncode
  report_path=destination/'summary.json'
  report=json.loads(report_path.read_text()) if report_path.exists() else {}
  expected_gaps=rc==2 and report.get('workflow_status')=='finished_with_gaps'
  if rc==2 and report.get('status')=='finished_with_gaps':expected_gaps=True
  results.append({**row,'returncode':rc,'summary':str(report_path),'expected_incomplete_scope':expected_gaps})
  save(a.output/'summary.json',{'status':'registered_regression_finished_with_gaps' if not rc or expected_gaps else 'stopped_with_evidence','results':results,'api_calls':0,'full_manual_coverage':False})
  if rc and not expected_gaps:raise SystemExit(rc)
if __name__=='__main__':main()
