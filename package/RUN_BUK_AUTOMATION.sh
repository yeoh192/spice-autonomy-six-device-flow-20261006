#!/bin/bash
set -euo pipefail
pkg="$(cd "$(dirname "$0")" && pwd)"
mode="${1:---run}"
[[ "$mode" == --run || "$mode" == --check-only ]] || exit 2
cd "$pkg"
if [[ -f "$pkg/local_api_routes.json" ]]; then
  export SPICE_ROUTES_FILE="${SPICE_ROUTES_FILE:-$pkg/local_api_routes.json}"
fi
out="/Users/192y/电气/BUK_automation_$(date +%Y%m%d-%H%M%S)_$$"
mkdir -p "$out"
if [[ "${BUK_TEST_SCOPE:-extended}" != core ]]; then
  python3 use_gpt_test_library.py --families --output "$out/registered" > "$out/registration.log"
fi
python3 - "$out" "$pkg" <<'PY'
import sys,json,shutil
from pathlib import Path
from flow_runtime.state import save
out,pkg=map(Path,sys.argv[1:]);batch=json.loads((pkg/'six_inputs/batch.json').read_text())
batch['devices']=[r for r in batch['devices'] if r['device']=='BUK7K52-60E']
assert len(batch['devices'])==1
for r in batch['devices']:
 source=pkg/'six_inputs'/r['input']
 shutil.copytree(source.parent,(out/r['input']).parent)
save(out/'batch.json',batch)
PY
args=(--batch "$out/batch.json" --source-runtime "${BUK_BASELINE_RUNTIME:-$out/baseline}")
if [[ "${BUK_TEST_SCOPE:-extended}" != core ]]; then
  args+=(--registered-tasks "$out/registered/batch.json" --gpt-library "$pkg/gpt_test_library")
fi
python3 iterate_models.py "${args[@]}" --output "$out/preflight" --check-only
printf '\nBUK新任务目录：%s\n' "$out"
if [[ "$mode" == --run ]]; then
  echo "开始真实BUK测试、Qwen/GLM模型迭代与回归；测试范围：${BUK_TEST_SCOPE:-extended}，数量见预检报告；密钥按提示隐藏输入。"
  python3 iterate_models.py "${args[@]}" --output "$out/runtime"
fi
