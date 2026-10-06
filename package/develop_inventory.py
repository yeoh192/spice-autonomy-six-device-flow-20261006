#!/usr/bin/env python3
import argparse
from pathlib import Path
from flow_runtime.capability_development import run
ap=argparse.ArgumentParser()
for n in ('batch','runner','output'):ap.add_argument('--'+n,type=Path,required=True)
ap.add_argument('--check-only',action='store_true');ap.add_argument('--resume',action='store_true');ap.add_argument('--max-items',type=int,default=6)
a=ap.parse_args();r=run(a.batch,a.runner,a.output,a.check_only,a.resume,a.max_items)
print('清单自动开发：',r['status'],'；报告：',a.output.resolve()/'summary.json')
