#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:---check-only}"
python3 extend_first_four_bindings.py
python3 use_gpt_test_library.py --families --output runs/first_four_registered_tasks
if [ "$mode" = "--run" ]; then
  output="runs/first_four_unified_$(date +%Y%m%d-%H%M%S)_$$"
  python3 run_gpt_registered_batch.py --families --devices BUK7K52-60E 1N4148 EMHK350ARA470MF80G 750311423 --output "$output"
  python3 audit_registered_run.py --runtime "$output" --output "$output/current_registered_audit.json" --devices BUK7K52-60E 1N4148 EMHK350ARA470MF80G 750311423
elif [ "$mode" = "--check-only" ]; then
  output="runs/first_four_existing_evidence"
else
  echo 'Usage: RUN_FIRST_FOUR_BINDINGS.sh [--check-only|--run]' >&2
  exit 1
fi
coverage="runs/first_four_coverage_$(date +%Y%m%d-%H%M%S)_$$"
python3 coverage_audit.py --batch six_inputs/batch.json --runtime "$output" --output "$coverage" --gpt-library gpt_test_library --import-only
printf '覆盖报告：%s/%s/summary.json\n' "$PWD" "$coverage"
