# 自动化流程报告

流程状态：finished_with_gaps
电气验收：fail
手册覆盖：incomplete
来源策略：all
实际模板来源：{'role': 'reference_interface_benchmark_only', 'source_sha256': '8e9c9190a601795576cd3cf0de46687496995b292da7cdd73934b447c870b0aa', 'original_entry': 'ACS723', 'original_ports': ['IP+', 'IP-', 'DUT_GND', 'VCC', 'Viout', 'BW_SEL'], 'physical_binding_verified': False, 'dependency_receipts': [{'name': 'opamp.sub', 'sha256': '7bbdf4d33fab0620879996c2c61cd09a4f589a0ee9bc4a26ccf8ecec9c24dc87'}, {'name': 'UniversalOPAmps2.sub', 'sha256': '4c7cd040212fc64700ec5fc6402c7fc11cdb7d9f84544dbae9914e4a5221f2d1'}], 'parameter_evidence': [{'reference_id': 'parameter:20AB_sensitivity', 'unit': 'mV/A', 'value': 100, 'symbol_sha256': '9eb9f2559a2a4bbcd364402ba5fe3a69f68088e30c651e992e3e598c75ef676a', 'role': 'reference_benchmark_configuration'}], 'parameter_scope_correction': 'Pass symbol-derived parameters on XREFERENCE instance; vendor subcircuit unchanged'}
独立非厂商证据：not_established

| 测试 | 执行 | 验收 |
|---|---|---|
| conductor_resistance_25C | completed | fail |
| family_20AB_zero_V5_T25 | completed | pass |

## 未完成项目

- parameter:20AB_range：uncovered
- parameter:20AB_sensitivity：uncovered
- parameter:20AB_zero：uncovered
- parameter:20AB_error_warm：uncovered
- parameter:20AB_error_cold：uncovered
- parameter:vcc_abs：constraint_review_pending
- parameter:vout_abs：constraint_review_pending
- parameter:ip_abs：constraint_review_pending
- parameter:tj_abs：constraint_review_pending
- parameter:ta：constraint_review_pending
- parameter:tstg：constraint_review_pending
- parameter:vcc：uncovered
- parameter:icc：uncovered
- parameter:cl：uncovered
- parameter:rl：uncovered
- parameter:rip：uncovered
- parameter:bw80：uncovered
- parameter:rise_time80：uncovered
- parameter:propagation_delay80：uncovered
- parameter:response_time80：uncovered
- parameter:noise80：uncovered
- parameter:bw20：uncovered
- parameter:rise_time20：uncovered
- parameter:propagation_delay20：uncovered
- parameter:response_time20：uncovered
- parameter:noise20：uncovered
- parameter:poweron：uncovered
- parameter:vout_hi：uncovered
- parameter:vout_lo：uncovered
- parameter:isotest：constraint_review_pending
- parameter:working_basic：constraint_review_pending
- parameter:working_reinforced：constraint_review_pending
- parameter:20AB_sens_error_warm：uncovered
- parameter:20AB_sens_error_cold：uncovered
- parameter:20AB_offset_warm：uncovered
- parameter:20AB_offset_cold：uncovered
- parameter:20AB_sensitivity_error_lifetime_drift：constraint_review_pending
- parameter:20AB_total_output_error_lifetime_drift：constraint_review_pending
- parameter:noise_density：uncovered
- parameter:nonlinear：uncovered
- parameter:reverse_supply：constraint_review_pending
- parameter:reverse_output：constraint_review_pending
- parameter:coupling：uncovered
- parameter:clearance：constraint_review_pending
- parameter:creepage：constraint_review_pending
- figure:20AB:zero_output:average：uncovered
- figure:20AB:zero_output:plus3sigma：constraint_review_pending
- figure:20AB:zero_output:minus3sigma：constraint_review_pending
- figure:20AB:offset:average：uncovered
- figure:20AB:offset:plus3sigma：constraint_review_pending
- figure:20AB:offset:minus3sigma：constraint_review_pending
- figure:20AB:sensitivity:average：uncovered
- figure:20AB:sensitivity:plus3sigma：constraint_review_pending
- figure:20AB:sensitivity:minus3sigma：constraint_review_pending
- figure:20AB:sensitivity_error:average：uncovered
- figure:20AB:sensitivity_error:plus3sigma：constraint_review_pending
- figure:20AB:sensitivity_error:minus3sigma：constraint_review_pending
- figure:20AB:nonlinearity:average：uncovered
- figure:20AB:nonlinearity:plus3sigma：constraint_review_pending
- figure:20AB:nonlinearity:minus3sigma：constraint_review_pending
- figure:20AB:total_error:average：uncovered
- figure:20AB:total_error:plus3sigma：constraint_review_pending
- figure:20AB:total_error:minus3sigma：constraint_review_pending
- figure:3：constraint_review_pending
- figure:4：constraint_review_pending
- figure:5：constraint_review_pending
- figure:10：constraint_review_pending
- figure:11：constraint_review_pending
- table:external_field：uncovered

## 程序诊断

