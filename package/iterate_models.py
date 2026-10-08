#!/usr/bin/env python3
import argparse
from pathlib import Path
from flow_runtime.batch_model_iteration import run
ap=argparse.ArgumentParser()
for n in ('batch','source-runtime','output'):ap.add_argument('--'+n,type=Path,required=True)
ap.add_argument('--registered-tasks',type=Path);ap.add_argument('--gpt-library',type=Path)
ap.add_argument('--check-only',action='store_true');ap.add_argument('--resume',action='store_true')
a=ap.parse_args();r=run(a.batch,a.source_runtime,a.output,a.check_only,a.resume,registered_tasks=a.registered_tasks,gpt_library=a.gpt_library)
print('批次模型迭代：',r['status'],'；报告：',a.output.resolve()/'summary.json')
