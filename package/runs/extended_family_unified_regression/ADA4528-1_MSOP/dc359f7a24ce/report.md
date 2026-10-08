# 自动化流程报告

流程状态：finished_with_gaps
电气验收：fail
手册覆盖：incomplete
来源策略：all
实际模板来源：{'role': 'reference_interface_benchmark_only', 'source_sha256': 'c65fb7db58918ebdcea8dd8e005c45d9f3635ae82ed6879c739fad2a90e07d60', 'original_entry': 'ADA4528', 'original_ports': ['1', '2', '3', '4', '5'], 'physical_binding_verified': False, 'dependency_receipts': [], 'parameter_evidence': []}
独立非厂商证据：not_established

| 测试 | 执行 | 验收 |
|---|---|---|
| supply_current_5V_25C | completed | pass |
| family_vol_2p5_2_V2p5_T25 | completed | fail |
| family_vol_2p5_10_V2p5_T25 | completed | fail |
| family_voh_2p5_2_V2p5_T25 | completed | pass |
| family_vol_5_2_V5p0_T25 | completed | pass |
| family_voh_2p5_10_V2p5_T25 | completed | pass |
| family_voh_5_2_V5p0_T25 | completed | pass |
| family_vol_5_10_V5p0_T25 | completed | fail |
| family_voh_5_10_V5p0_T25 | completed | pass |
| family_icc_2p5_V2p5_T25 | completed | pass |
| static_ib_5_1_T25_CM2p5 | completed | pass |
| family_voh_2p5_10_full_V2p5_T125 | completed | pass |
| family_vol_5_10_full_V5p0_T125 | completed | pass |
| family_voh_5_10_full_V5p0_Tm40 | completed | pass |
| family_vol_5_2_full_V5p0_T125 | completed | pass |
| static_ios_5_full_1_Tm40_CM2p5 | completed | pass |
| family_vol_2p5_10_full_V2p5_Tm40 | completed | pass |
| static_vos_5_full_T125_CM2p5 | completed | pass |
| family_icc_full_2p5_V2p5_T25 | completed | pass |
| family_vol_5_10_full_V5p0_T25 | completed | pass |
| family_voh_2p5_2_full_V2p5_T25 | completed | pass |
| family_voh_2p5_10_full_V2p5_T25 | completed | pass |
| family_icc_full_2p5_V2p5_Tm40 | completed | pass |
| family_vol_2p5_2_full_V2p5_T125 | completed | pass |
| static_ib_2p5_full_Tm40_CM1p25 | completed | pass |
| family_icc_full_2p5_V2p5_T125 | completed | pass |
| static_ib_2p5_T25_CM1p25 | completed | pass |
| static_ib_5_full_1_Tm40_CM2p5 | completed | pass |
| static_vos_2p5_full_MSOP_Tm40_CM1p25 | completed | pass |
| family_vol_2p5_10_full_V2p5_T125 | completed | pass |
| family_icc_full_5_V5p0_Tm40 | completed | pass |
| static_ios_2p5_full_T25_CM1p25 | completed | pass |
| family_voh_5_2_full_V5p0_Tm40 | completed | pass |
| family_vol_5_10_full_V5p0_Tm40 | completed | pass |
| family_icc_full_5_V5p0_T25 | completed | pass |
| family_vol_5_2_full_V5p0_T25 | completed | pass |
| static_ios_2p5_T25_CM1p25 | completed | pass |
| static_ios_2p5_full_T125_CM1p25 | completed | pass |
| static_ib_2p5_full_T125_CM1p25 | completed | pass |
| family_vol_2p5_2_full_V2p5_Tm40 | completed | pass |
| family_voh_5_2_full_V5p0_T125 | completed | pass |
| static_ios_5_1_T25_CM2p5 | completed | pass |
| family_voh_2p5_2_full_V2p5_T125 | completed | pass |
| static_vos_5_T25_CM2p5 | completed | fail |
| static_ib_5_full_1_T125_CM2p5 | completed | pass |
| family_voh_5_10_full_V5p0_T125 | completed | pass |
| family_voh_2p5_10_full_V2p5_Tm40 | completed | pass |
| family_icc_full_5_V5p0_T125 | completed | pass |
| family_voh_2p5_2_full_V2p5_Tm40 | completed | pass |
| static_ios_2p5_full_Tm40_CM1p25 | completed | pass |
| family_voh_5_2_full_V5p0_T25 | completed | pass |
| static_vos_2p5_full_MSOP_T125_CM1p25 | completed | pass |
| family_vol_2p5_2_full_V2p5_T25 | completed | pass |
| static_vos_5_full_T25_CM2p5 | completed | pass |
| static_ios_5_full_1_T25_CM2p5 | completed | pass |
| static_vos_5_full_Tm40_CM2p5 | completed | pass |
| static_ib_2p5_full_T25_CM1p25 | completed | pass |
| static_vos_2p5_T25_CM1p25 | completed | fail |
| static_ib_5_full_1_T25_CM2p5 | completed | pass |
| family_voh_5_10_full_V5p0_T25 | completed | pass |
| static_ios_5_full_1_T125_CM2p5 | completed | pass |
| family_vol_5_2_full_V5p0_Tm40 | completed | pass |
| family_vol_2p5_10_full_V2p5_T25 | completed | pass |
| static_vos_2p5_full_MSOP_T25_CM1p25 | completed | pass |

## 未完成项目

- parameter:supply_abs：constraint_review_pending
- parameter:supply_range：constraint_review_pending
- parameter:temperature：constraint_review_pending
- parameter:storage：constraint_review_pending
- parameter:thermal_ja_MSOP：uncovered
- parameter:thermal_jc_MSOP：uncovered
- parameter:vos_2.5：uncovered
- parameter:cmrr_2.5：uncovered
- parameter:cmrr_full_2.5：uncovered
- parameter:rin_diff_2.5：uncovered
- parameter:rin_cm_2.5：uncovered
- parameter:cin_diff_2.5：uncovered
- parameter:cin_cm_2.5：uncovered
- parameter:icc_2.5：uncovered
- parameter:icc_full_2.5：uncovered
- parameter:psrr_2.5：uncovered
- parameter:sr_2.5：uncovered
- parameter:gbp_2.5：uncovered
- parameter:bw_2.5：uncovered
- parameter:noise_2.5：uncovered
- parameter:density_2.5：uncovered
- parameter:current_density_2.5：uncovered
- parameter:recovery_2.5：uncovered
- parameter:vos_5：uncovered
- parameter:cmrr_5：uncovered
- parameter:cmrr_full_5：uncovered
- parameter:rin_diff_5：uncovered
- parameter:rin_cm_5：uncovered
- parameter:cin_diff_5：uncovered
- parameter:cin_cm_5：uncovered
- parameter:icc_5：uncovered
- parameter:icc_full_5：uncovered
- parameter:psrr_5：uncovered
- parameter:sr_5：uncovered
- parameter:gbp_5：uncovered
- parameter:bw_5：uncovered
- parameter:noise_5：uncovered
- parameter:density_5：uncovered
- parameter:current_density_5：uncovered
- parameter:recovery_5：uncovered
- parameter:vos_2.5_full_MSOP：uncovered
- parameter:drift_2.5_MSOP：uncovered
- parameter:vos_5_full：uncovered
- parameter:drift_5：uncovered
- parameter:ib_2.5：uncovered
- parameter:ib_2.5_full：uncovered
- parameter:ios_2.5：uncovered
- parameter:ios_2.5_full：uncovered
- parameter:ib_5_1：uncovered
- parameter:ib_5_full_1：uncovered
- parameter:ios_5_1：uncovered
- parameter:ios_5_full_1：uncovered
- parameter:vin_range_2.5：uncovered
- parameter:aol_2.5_0：uncovered
- parameter:aol_2.5_0_full：uncovered
- parameter:aol_2.5_1：uncovered
- parameter:aol_2.5_1_full：uncovered
- parameter:voh_2.5_10：uncovered
- parameter:voh_2.5_10_full：uncovered
- parameter:vol_2.5_10：uncovered
- parameter:vol_2.5_10_full：uncovered
- parameter:voh_2.5_2：uncovered
- parameter:voh_2.5_2_full：uncovered
- parameter:vol_2.5_2：uncovered
- parameter:vol_2.5_2_full：uncovered
- parameter:isc_2.5：uncovered
- parameter:zout_2.5：uncovered
- parameter:psrr_full_2.5：uncovered
- parameter:settling_2.5：uncovered
- parameter:ugc_2.5：uncovered
- parameter:phase_2.5：uncovered
- parameter:noise_density_cm_2.5：uncovered
- parameter:current_noise_2.5：uncovered
- parameter:vin_range_5：uncovered
- parameter:aol_5_0：uncovered
- parameter:aol_5_0_full：uncovered
- parameter:aol_5_1：uncovered
- parameter:aol_5_1_full：uncovered
- parameter:voh_5_10：uncovered
- parameter:voh_5_10_full：uncovered
- parameter:vol_5_10：uncovered
- parameter:vol_5_10_full：uncovered
- parameter:voh_5_2：uncovered
- parameter:voh_5_2_full：uncovered
- parameter:vol_5_2：uncovered
- parameter:vol_5_2_full：uncovered
- parameter:isc_5：uncovered
- parameter:zout_5：uncovered
- parameter:psrr_full_5：uncovered
- parameter:settling_5：uncovered
- parameter:ugc_5：uncovered
- parameter:phase_5：uncovered
- parameter:noise_density_cm_5：uncovered
- parameter:current_noise_5：uncovered
- parameter:input_abs：constraint_review_pending
- parameter:input_current_abs：constraint_review_pending
- parameter:differential_abs：constraint_review_pending
- parameter:short_duration：constraint_review_pending
- parameter:junction：constraint_review_pending
- parameter:lead_solder：constraint_review_pending
- figure:3：uncovered
- figure:8：constraint_review_pending
- figure:9：constraint_review_pending
- figure:10：uncovered
- figure:11：constraint_review_pending
- figure:12：constraint_review_pending
- figure:13：uncovered
- figure:14：uncovered
- figure:15：uncovered
- figure:16：uncovered
- figure:17：uncovered
- figure:18：uncovered
- figure:19：uncovered
- figure:20：uncovered
- figure:21：uncovered
- figure:22：uncovered
- figure:23：uncovered
- figure:24：uncovered
- figure:25：uncovered
- figure:26：uncovered
- figure:27：uncovered
- figure:28：uncovered
- figure:29：uncovered
- figure:30：uncovered
- figure:31：uncovered
- figure:32：uncovered
- figure:33：uncovered
- figure:34：uncovered
- figure:35：uncovered
- figure:36：uncovered
- figure:37：uncovered
- figure:38：uncovered
- figure:39：uncovered
- figure:40：uncovered
- figure:41：uncovered
- figure:42：uncovered
- figure:43：uncovered
- figure:44：uncovered
- figure:45：uncovered
- figure:46：uncovered
- figure:47：uncovered
- figure:48：uncovered
- figure:49：uncovered
- figure:50：uncovered
- figure:51：uncovered
- figure:52：uncovered
- figure:53：uncovered
- figure:54：uncovered
- figure:55：uncovered
- figure:56：uncovered
- figure:57：uncovered
- figure:58：uncovered
- figure:59：uncovered
- figure:60：uncovered
- figure:61：uncovered
- figure:62：constraint_review_pending
- figure:63：constraint_review_pending
- figure:64：uncovered
- figure:66：uncovered
- figure:67：uncovered
- figure:69：constraint_review_pending
- figure:72：constraint_review_pending
- manual:unlabelled_series_audit：constraint_review_pending

## 程序诊断

