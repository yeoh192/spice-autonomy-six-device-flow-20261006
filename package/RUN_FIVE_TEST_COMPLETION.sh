#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
mode="${1:---check-only}"
if [[ "$mode" != --run && "$mode" != --check-only ]]; then echo '参数：--check-only 或 --run';exit 2;fi
work_dir="/Users/192y/电气/D2S-FLOW-five-tests_$(date +%Y%m%d-%H%M%S)_$$"
python3 "$pkg_dir/install.py" --repo "$work_dir" --isolated
cp -R "$pkg_dir/six_inputs" "$work_dir/inputs"
cd "$work_dir"
python3 -m unittest discover -s tests -p 'test_flow_*.py'
cal='/Users/192y/电气/D2S-FLOW-six-inputs_20261006-145226_67415/runs/ac_calibration_20261006-145715/summary.json'
prev='/Users/192y/电气/D2S-FLOW-six-capabilities_20261006-164630_81928/runs/six_batch/draft_repair'
source='/Users/192y/电气/D2S-FLOW-six-capabilities_20261006-164630_81928/runs/six_batch'
runner='/Applications/LTspice.app/Contents/SharedSupport/ltspice/LTspice/run_ltspice'
args=(--batch inputs/batch.json --runner "$runner" --calibration "$cal" --previous "$prev" --source-runtime "$source" --seconds 7200)
python3 complete_five_tests.py "${args[@]}" --output runs/precheck --check-only
python3 - "$work_dir" "$cal" "$prev" "$source" "$runner" <<'PY'
import shlex,sys
from pathlib import Path
w,cal,prev,source,runner=sys.argv[1:]
cmd=['python3','complete_five_tests.py','--batch','inputs/batch.json','--runner',runner,'--calibration',cal,'--previous',prev,'--source-runtime',source,'--seconds','7200','--output','runs/test_completion','--resume']
(Path(w)/'RESUME_FIVE_TESTS.sh').write_text('#!/bin/bash\nset -euo pipefail\ncd '+shlex.quote(w)+'\n'+shlex.join(cmd)+'\n')
PY
printf '\n五器件测试工作目录：%s\n' "$work_dir"
echo '条件整理全队列最多120项；每器件时限2小时。按实际队列最多18次API/项，合计最多2160次；14项草案修复另计。'
echo '这是测试能力开发与资格验证，不会修改参考模型拟合交付，也不把剩余缺口宣布通过。'
if [[ "$mode" == --run ]]; then python3 complete_five_tests.py "${args[@]}" --output runs/test_completion;fi
