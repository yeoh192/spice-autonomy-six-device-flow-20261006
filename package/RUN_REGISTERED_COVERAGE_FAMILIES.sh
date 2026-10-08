#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:---check-only}"
python3 prepare_test_families.py --output runs/family_mapping
python3 extend_static_families.py
python3 extend_sensor_families.py
if [ "$mode" = "--run" ]; then
  if [ ! -f runs/family_qualification_corrected/summary.json ]; then
    python3 gpt_corrected_tests.py --catalog gpt_test_library/family_cases.json --output runs/family_qualification_corrected
  fi
  for kind in static sensor; do
    if [ ! -f "runs/${kind}_family_qualification/summary.json" ]; then
      catalog="gpt_test_library/${kind}_family_cases.json"
      [ "$kind" != static ] || catalog="gpt_test_library/extended_family_cases.json"
      python3 gpt_corrected_tests.py --catalog "$catalog" --output "runs/${kind}_family_qualification"
    fi
  done
  output="runs/family_unified_$(date +%Y%m%d-%H%M%S)_$$"
  python3 run_gpt_registered_batch.py --families --output "$output"
elif [ "$mode" = "--check-only" ]; then
  output="runs/family_existing_evidence"
else
  echo 'Usage: RUN_REGISTERED_COVERAGE_FAMILIES.sh [--check-only|--run]' >&2
  exit 1
fi
python3 use_gpt_test_library.py --families --output runs/family_registered_tasks
coverage="runs/family_coverage_$(date +%Y%m%d-%H%M%S)_$$"
python3 coverage_audit.py --batch six_inputs/batch.json --runtime "$output" --output "$coverage" --gpt-library gpt_test_library --import-only
printf '覆盖报告：%s/%s/summary.json\n' "$PWD" "$coverage"
