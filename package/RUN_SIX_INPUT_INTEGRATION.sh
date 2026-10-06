#!/bin/bash
set -euo pipefail
package_dir="$(cd "$(dirname "$0")" && pwd)"
input_root="${1:-/Users/192y/电气/六器件标准输入包_20261006}"
target_dir="${2:-/Users/192y/电气/D2S-FLOW-six-inputs_$(date +%Y%m%d-%H%M%S)_$$}"
python3 "$package_dir/install.py" --repo "$target_dir" --isolated
cd "$target_dir"
mkdir -p runs/input_integration
printf '运行合并版本离线检查（不调用API或LTspice）……\n'
if ! PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_flow_*.py' > runs/input_integration/framework_tests.log 2>&1; then
  cat runs/input_integration/framework_tests.log
  exit 1
fi
tail -n 5 runs/input_integration/framework_tests.log
python3 spice_flow.py catalog-check > runs/input_integration/catalog_check.json.txt
python3 spice_flow.py prepare-batch --input-root "$input_root" --output inputs/six_devices
python3 spice_flow.py batch-preflight --batch inputs/six_devices/batch.json --output runs/input_integration/preflight.json
printf '\n统一版本工作目录：%s\n统一批次：%s\n' "$target_dir" "$target_dir/inputs/six_devices/batch.json"
printf '本步仅安装与统一输入；未调用API或LTspice，未进行模型拟合。\n'
printf '当前版本交流校准、14项草案资格审查及手册覆盖闭合仍待执行。\n'
