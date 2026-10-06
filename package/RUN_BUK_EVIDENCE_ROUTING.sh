#!/bin/bash
set -euo pipefail
package_dir="$(cd "$(dirname "$0")" && pwd)"
target_dir="/Users/192y/电气/D2S-FLOW-evidence-routing_$(date +%Y%m%d-%H%M%S)_$$"
bundle_dir="/Users/192y/电气/D2S-FLOW-reproduction/runs/autonomy_integration_20261005-221849-036241"
history_dir="/Users/192y/电气/D2S-FLOW-reproduction/runs/flow_indexed_v2_20261006-103644/runtime"
mode="${1:-}"
if [[ "$mode" != "" && "$mode" != "--run" ]]; then
  echo "用法：bash RUN_BUK_EVIDENCE_ROUTING.sh [--run]" >&2
  exit 2
fi
if [[ ! -d "$bundle_dir" ]]; then
  echo "找不到已有BUK资料包：$bundle_dir" >&2
  exit 1
fi
python3 "$package_dir/install.py" --repo "$target_dir" --isolated
cd "$target_dir"
mkdir -p runs/buk_evidence_routing
echo "运行离线模拟测试（不连接API或LTspice）……"
if ! python3 -m unittest discover -s tests -p 'test_flow_*.py' > runs/buk_evidence_routing/framework_tests.log 2>&1; then
  cat runs/buk_evidence_routing/framework_tests.log
  exit 1
fi
tail -n 5 runs/buk_evidence_routing/framework_tests.log
python3 spice_flow.py import-indexed --bundle "$bundle_dir" --task runs/buk_evidence_routing/indexed/task.json --max-trials 3
python3 spice_flow.py library-attach \
  --source-task runs/buk_evidence_routing/indexed/task.json \
  --task runs/buk_evidence_routing/input/task.json \
  --buk-row-series-review \
  --typical-tolerance-percent 10 --curve-mae-percent 5 --curve-max-error-percent 10
python3 spice_flow.py library-plan --task runs/buk_evidence_routing/input/task.json --output runs/buk_evidence_routing/plan
if [[ -f "$history_dir/template_selection/trial_results.json" ]]; then
  python3 spice_flow.py recheck --runtime "$history_dir" --output runs/buk_evidence_routing/historical_recheck \
    --typical-tolerance-percent 10 --curve-mae-percent 5 --curve-max-error-percent 10
fi
printf '\n共享证据与修复分流版本工作目录：%s\n' "$target_dir"
if [[ "$mode" == "--run" ]]; then
  echo "进入真实Qwen/GLM与LTspice统一流程；两个Key由终端隐藏输入。"
  python3 spice_flow.py run --task runs/buk_evidence_routing/input/task.json --output runs/buk_evidence_routing/runtime
else
  printf '离线预检完成；未调用API或仿真。继续同一任务的真实运行命令：\ncd %q\npython3 spice_flow.py run --task runs/buk_evidence_routing/input/task.json --output runs/buk_evidence_routing/runtime\n' "$target_dir"
fi
