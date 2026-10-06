#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="${1:-/Users/192y/电气/D2S-FLOW-six-inputs_20261006-145226_67415}"
mode="${2:---check-only}"
design_model="${3:-qwen3.8-max-0902}"
if [[ "$mode" != --run && "$mode" != --check-only ]]; then
  echo '第二个参数只能为--run或--check-only'; exit 2
fi
qualification_dir="/Users/192y/电气/D2S-FLOW-draft-qualification_$(date +%Y%m%d-%H%M%S)_$$"
calibration_file="$source_dir/runs/ac_calibration_20261006-145715/summary.json"
runner_path="/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice"
python3 "$pkg_dir/install.py" --repo "$qualification_dir" --isolated
python3 - "$source_dir" "$qualification_dir" "$design_model" <<'PY'
import shutil,sys,json,hashlib
from pathlib import Path
src,dst=map(Path,sys.argv[1:3])
model=sys.argv[3]
if not model or any(c.isspace() for c in model): raise SystemExit("模型名称为空或含空白")
shutil.copytree(src/'inputs/six_devices',dst/'inputs/six_devices')
root=dst/'inputs/six_devices'
batch_path=root/'batch.json'
batch=json.loads(batch_path.read_text())
for row in batch['devices']:
    packet_path=root/row['input']
    packet=json.loads(packet_path.read_text())
    packet['routes']['design']={'provider':'qwen','model':model}
    packet_path.write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
    row['sha256']=hashlib.sha256(packet_path.read_bytes()).hexdigest()
batch_path.write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n')
print('新任务设计模型：',model,'；原始输入与实验不变。')
PY
cd "$qualification_dir"
python3 -m unittest discover -s tests -p 'test_flow_*.py'
python3 spice_flow.py qualify-drafts \
  --batch inputs/six_devices/batch.json \
  --calibration "$calibration_file" \
  --runner "$runner_path" \
  --output runs/qualification_precheck \
  --rounds 2 --check-only
printf '\n统一资格审查工作目录：%s\n' "$qualification_dir"
if [[ "$mode" == --run ]]; then
  python3 spice_flow.py qualify-drafts \
    --batch inputs/six_devices/batch.json \
    --calibration "$calibration_file" \
    --runner "$runner_path" \
    --output runs/qualification \
    --rounds 2
else
  echo '离线预检完成；未调用API或启动LTspice。使用--run安装到新目录后真实运行。'
fi
