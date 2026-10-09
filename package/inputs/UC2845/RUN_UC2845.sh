#!/bin/bash
set -euo pipefail
input_dir="$(cd "$(dirname "$0")" && pwd)"
engine="${SPICE_FLOW_PACKAGE:-$(cd "$input_dir/../.." && pwd)}"
mode="${1:---run}"
[[ "$mode" == --run || "$mode" == --check-only || "$mode" == --resume ]] || { echo '使用 --run、--check-only 或 --resume 输出目录'; exit 2; }
[[ -f "$engine/spice_flow.py" ]] || { echo '请设置SPICE_FLOW_PACKAGE为软件package目录'; exit 2; }
python3 "$input_dir/verify_input.py"
cd "$engine"
if [[ "$mode" == --resume ]]; then
  [[ $# == 2 ]] || { echo '--resume必须提供原runtime目录'; exit 2; }
  exec python3 spice_flow.py run --task "$input_dir/task.json" --output "$2" --resume
fi
out="$input_dir/runs/$(date +%Y%m%d-%H%M%S)_$$"
python3 spice_flow.py preflight --task "$input_dir/task.json" --output "$out/preflight"
printf '\n本次目录：%s\n' "$out"
if [[ "$mode" == --run ]]; then
  echo '启动UC2845：候选选择、31项测试、缺口报告、Qwen/GLM模型迭代与回归。'
  echo '第三方共享Key按终端提示隐藏输入一次；从原始候选重新开始，不复用BUK数据。'
  exec python3 spice_flow.py run --task "$input_dir/task.json" --output "$out/runtime"
fi
