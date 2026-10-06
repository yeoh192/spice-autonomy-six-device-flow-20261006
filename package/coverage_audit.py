#!/usr/bin/env python3
"""Verify and write coverage receipts without modifying original inventories."""
import argparse
from pathlib import Path
from flow_runtime.coverage_binding import run
from flow_runtime.state import Fault, save

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('batch','runtime','output'):ap.add_argument('--'+name,type=Path,required=True)
    for name in ('previous','development','qualification'):ap.add_argument('--'+name,type=Path)
    ap.add_argument('--check-only',action='store_true');ap.add_argument('--resume',action='store_true')
    ap.add_argument('--import-only',action='store_true',help='仅导入已获资格的方法证据，不重做待确认记录的API审查')
    ap.add_argument('--rounds',type=int,default=2,choices=(1,2))
    a=ap.parse_args()
    try:
        report=run(a.batch,a.runtime,a.output,a.check_only,a.resume,a.previous,a.rounds,
                   development=a.development,qualification=a.qualification,import_only=a.import_only)
    except (Fault,OSError,KeyError,ValueError,TypeError) as e:
        report={'status':'stopped_with_evidence','full_manual_coverage':False,
                'fault':e.record() if isinstance(e,Fault) else {'kind':'coverage_input','message':str(e)}}
        save(a.output/'failure.json',report)
        print('覆盖确认停止；诊断：',a.output/'failure.json')
        return 1
    print('覆盖确认：',report['status'],'；报告：',a.output/'summary.json')
    return int(report['status']=='stopped_with_evidence')
if __name__=='__main__':raise SystemExit(main())
