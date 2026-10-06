# Evidence-backed coverage confirmation (0.2.9)

The serial batch now runs:

1. Existing configured regression.
2. `coverage_audit.py`: reparse complete RAW outputs, verify model, protocol,
   reference CSV and artifact hashes; Qwen checks the handbook binding and GLM
   independently reviews the same actual circuit and scope. Revisions return
   automatically to Qwen, at most two rounds.
3. Inventory development consumes the verified coverage overlay. Existing
   unverified bindings are never sent to the circuit developer as missing methods.
4. Supervised draft repair and qualification.
5. Final coverage writeback imports qualified methods using their request ledgers,
   calibration artifacts and benchmark RAW. This final import adds no API calls
   or simulations. The aggregate batch report includes coverage counts per device.

Original input inventories, manuals, reference data, conditions and acceptance
standards are immutable. Overlays and evidence receipts live under
`runs/six_batch/coverage_configured` and `coverage_final`. Reuse checks every
receipt hash and source identity. Edited or missing evidence is rejected.

Counts distinguish configured bindings pending confirmation, genuinely missing
methods, reference-only qualified methods, electrical acceptance and constraint
setup audits. A valid method can expose a failing model. Reference benchmarks
never establish device delivery. Partial draft sets remain in the gap queue.

BUK's initial categories are 23 configured bindings pending verification,
35 records without methods and 20 constraints requiring audit. These are not
automatically set to zero: missing multi-FET scope, manual conditions, data or
unsupported measurement primitives remain explicit gaps. Constraint checks cover
only the supplied numeric test setups, not reliability or absolute-rating proof.

## Run against existing results

From an installed framework directory containing the supplied input batch:

```bash
python3 coverage_audit.py --batch inputs/batch.json \
  --runtime runs/six_batch --output runs/coverage_review --check-only
```

Remove `--check-only` for real Qwen/GLM review. Run from an interactive terminal;
keys are hidden or supplied via `DASHSCOPE_API_KEY` / `GLM_API_KEY` and never saved.
No new simulations are performed by this command. Add `--resume` with the same
output to reuse verified saved responses. It requires the real configured results
under `<runtime>/<device-folder>/configured/summary.json`.

Coverage review has its own explicit per-device budget: up to two review rounds,
two logical requests per record, each with at most two physical request attempts,
and 1200 seconds. BUK's 43 reviewable records therefore have a maximum of 344
physical requests; missing local evidence stops that record before requesting an
API. This is additional to existing development and draft-repair budgets.
The batch runner prompts once per provider and shares credentials only through
child-process environments. Preflight invokes neither provider nor LTspice.

This change closes coverage bookkeeping and confirmation dispatch. It does not
claim that all missing tests have been developed or that six-device electrical
acceptance has passed.

For a new batch, `six_batch.py --coverage <previous-coverage_final>` revalidates
previous receipts and source hashes before reuse; still-pending configured records
are reviewed again. `develop_inventory.py --coverage <coverage_final>` can also
consume this overlay directly. `coverage_audit.py --import-only` is the final
writeback stage and never repeats the configured-record API review.
