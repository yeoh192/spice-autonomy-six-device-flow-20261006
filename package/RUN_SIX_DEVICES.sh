#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
mode="${1:---run}"
if [[ "$mode" != --run && "$mode" != --check-only ]]; then echo '参数为--run或--check-only';exit 2;fi
work_dir="/Users/192y/电气/D2S-FLOW-six-capabilities_$(date +%Y%m%d-%H%M%S)_$$"
python3 "$pkg_dir/install.py" --repo "$work_dir" --isolated
cp "$pkg_dir/six_batch.py" "$work_dir/six_batch.py"
cp -R "$pkg_dir/six_inputs" "$work_dir/inputs"
python3 - "$work_dir/inputs" <<'INNER'
import json,hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]);p=root/'batch.json';b=json.loads(p.read_text())
for r in b['devices']:
 f=root/r['input'];v=json.loads(f.read_text());v['routes']['design']['model']='qwen3.8-max-0902'
 if v.get('configured_task'):
  t=f.parent/v['configured_task'];c=json.loads(t.read_text());c['routes']['design']['model']='qwen3.8-max-0902';t.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n');v['assets'][v['configured_task']]=hashlib.sha256(t.read_bytes()).hexdigest()
 f.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');r['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
p.write_text(json.dumps(b,ensure_ascii=False,indent=2)+'\n')
INNER
cd "$work_dir"
python3 -m unittest discover -s tests -p 'test_flow_*.py'
calibration='/Users/192y/电气/D2S-FLOW-six-inputs_20261006-145226_67415/runs/ac_calibration_20261006-145715/summary.json'
runner='/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice'
if [[ "$mode" == --run ]]; then
  calibration="$work_dir/runs/ac_calibration/summary.json"
  python3 spice_flow.py calibrate-ac --runner "$runner" --output "$work_dir/runs/ac_calibration"
fi
python3 use_gpt_test_library.py --families --output runs/api_registered_tasks
args=(--registered-tasks runs/api_registered_tasks/batch.json --gpt-library gpt_test_library --batch inputs/batch.json --calibration "$calibration" --runner "$runner")
python3 - "$work_dir" "$calibration" "$runner" <<'RESUME'
import shlex,sys
from pathlib import Path
w,cal,runner=sys.argv[1:]
command=['python3','six_batch.py','--registered-tasks','runs/api_registered_tasks/batch.json','--gpt-library','gpt_test_library','--batch','inputs/batch.json','--calibration',cal,'--runner',runner,'--output','runs/six_batch','--resume']
(Path(w)/'RESUME_SIX_DEVICES.sh').write_text('#!/bin/bash\nset -euo pipefail\ncd '+shlex.quote(w)+'\n'+shlex.join(command)+'\n')
RESUME
python3 six_batch.py "${args[@]}" --output runs/precheck --check-only
printf '\n六器件批次目录：%s\n' "$work_dir"
if [[ "$mode" == --run ]]; then
 echo '覆盖确认另有预算：每记录最多8次物理API请求、每器件20分钟；BUK43条最多344次。缺少本地实测证据时不请求。'
 echo '新增清单开发每器件最多6项；每器件API上限108次、仿真72项、20分钟；14项草案修复另计。'
 python3 six_batch.py "${args[@]}" --output runs/six_batch
else
 echo '离线检查结束；未调用API或LTspice。'
fi
