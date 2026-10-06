#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="${1:-/Users/192y/电气/D2S-FLOW-six-capabilities_20261006-160903_74908}"
mode="${2:---run}"
if [[ "$mode" != --run && "$mode" != --check-only ]]; then exit 2;fi
iteration_dir="/Users/192y/电气/D2S-FLOW-model-iteration_$(date +%Y%m%d-%H%M%S)_$$"
python3 "$pkg_dir/install.py" --repo "$iteration_dir" --isolated
cd "$iteration_dir"
python3 -m unittest discover -s tests -p 'test_flow_*.py'
args=(--batch "$source_dir/inputs/batch.json" --source-runtime "$source_dir/runs/six_batch")
python3 iterate_models.py "${args[@]}" --output runs/precheck --check-only
python3 - "$iteration_dir" "$source_dir" <<'RESUME'
import sys,shlex
from pathlib import Path
w,source=sys.argv[1:]
cmd=['python3','iterate_models.py','--batch',source+'/inputs/batch.json','--source-runtime',source+'/runs/six_batch','--output','runs/model_iteration','--resume']
(Path(w)/'RESUME_MODEL_ITERATION.sh').write_text('#!/bin/bash\nset -euo pipefail\ncd '+shlex.quote(w)+'\n'+shlex.join(cmd)+'\n')
RESUME
printf '\n模型迭代工作目录：%s\n' "$iteration_dir"
if [[ "$mode" == --run ]]; then python3 iterate_models.py "${args[@]}" --output runs/model_iteration;fi
