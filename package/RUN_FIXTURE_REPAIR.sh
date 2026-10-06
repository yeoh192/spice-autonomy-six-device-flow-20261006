#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="${1:-/Users/192y/电气/D2S-FLOW-draft-qualification_20261006-151450_70664}"
mode="${2:---check-only}"
design_model="${3:-qwen3.8-max-0902}"
if [[ "$mode" != --run && "$mode" != --check-only ]]; then echo '第二个参数只能为--run或--check-only'; exit 2; fi
repair_dir="/Users/192y/电气/D2S-FLOW-fixture-repair_$(date +%Y%m%d-%H%M%S)_$$"
previous_dir="$source_dir/runs/qualification"
calibration_file="$(python3 - "$previous_dir" <<'PY'
import json,sys
from pathlib import Path
print(json.loads((Path(sys.argv[1])/'summary.json').read_text())['calibration']['path'])
PY
)"
runner_path="/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice"
python3 "$pkg_dir/install.py" --repo "$repair_dir" --isolated
python3 - "$source_dir" "$repair_dir" "$design_model" <<'PY'
import json,hashlib,shutil,sys
from pathlib import Path
source,dst=map(Path,sys.argv[1:3]);model=sys.argv[3]
if not model or any(c.isspace() for c in model):raise SystemExit('模型名称为空或含空白')
shutil.copytree(source/'inputs/six_devices',dst/'inputs/six_devices')
root=dst/'inputs/six_devices';path=root/'batch.json';batch=json.loads(path.read_text())
for row in batch['devices']:
    p=root/row['input'];v=json.loads(p.read_text());v['routes']['design']={'provider':'qwen','model':model}
    p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');row['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
path.write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n')
print('设计模型：',model,'；原始输入和实验保持不变。')
PY
cd "$repair_dir"
python3 -m unittest discover -s tests -p 'test_flow_*.py'
python3 spice_flow.py repair-drafts \
  --batch inputs/six_devices/batch.json \
  --calibration "$calibration_file" \
  --runner "$runner_path" \
  --previous "$previous_dir" \
  --output runs/repair_precheck --rounds 3 --check-only
printf '\n自动修复工作目录：%s\n' "$repair_dir"
if [[ "$mode" == --run ]]; then
  python3 spice_flow.py repair-drafts \
    --batch inputs/six_devices/batch.json \
    --calibration "$calibration_file" \
    --runner "$runner_path" \
    --previous "$previous_dir" \
    --output runs/repair --rounds 3
else
  echo '离线预检完成；未调用API或启动LTspice。'
fi
