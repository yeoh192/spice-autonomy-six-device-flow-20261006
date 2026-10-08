#!/bin/bash
set -euo pipefail
pkg_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$pkg_dir"
mode="${1:---check-only}"
if [[ "$mode" == --check-only ]]; then
 python3 gpt_corrected_tests.py --output runs/library_precheck --check-only
 python3 use_gpt_test_library.py --output runs/registered_tasks
elif [[ "$mode" == --run ]]; then
 run_dir="runs/gpt_registered_$(date +%Y%m%d-%H%M%S)_$$"
 python3 run_gpt_registered_batch.py --output "$run_dir"
else
 echo 'Use --check-only or --run'; exit 2
fi
