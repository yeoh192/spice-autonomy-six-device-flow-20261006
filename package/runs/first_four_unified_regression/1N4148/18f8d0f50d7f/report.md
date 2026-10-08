# 自动化流程报告

流程状态：finished_with_gaps
电气验收：fail
手册覆盖：incomplete
来源策略：all
实际模板来源：{'origin': 'historical_LTspice_OnSemi_candidate_fitted_to_NXP_reference', 'not_new_index_selection': True}
独立非厂商证据：not_established

| 测试 | 执行 | 验收 |
|---|---|---|
| reverse_20V_25C | completed | pass |
| capacitance_zero_bias | completed | pass |
| reverse_20V_150C | completed | pass |
| forward_10mA | completed | pass |
| forward_175C | completed | fail |
| forward_25C | completed | pass |
| figure_fig5_vr20_point100 | completed | fail |
| figure_fig6_capacitance_point0 | completed | pass |
| figure_fig5_point0 | completed | fail |
| forward_recovery_peak_50mA_20ns | completed | pass |
| figure_fig5_point100 | completed | fail |
| figure_fig5_vr75_point100 | completed | fail |
| figure_fig5_vr20_point0 | completed | fail |
| figure_fig6_capacitance_point101 | completed | pass |
| figure_fig5_vr20_point200 | completed | fail |
| figure_fig5_vr75_point200 | completed | fail |
| figure_fig5_vr75_point0 | completed | fail |
| figure_fig6_capacitance_point202 | completed | pass |
| figure_fig5_point200 | completed | fail |
| reverse_recovery_10mA_60mA_100ohm | completed | pass |

## 未完成项目

- parameter:vrrm：constraint_review_pending
- parameter:vr：constraint_review_pending
- parameter:if：constraint_review_pending
- parameter:ifrm：constraint_review_pending
- parameter:p：constraint_review_pending
- parameter:tj：constraint_review_pending
- parameter:ifsm_1 μs：constraint_review_pending
- parameter:ifsm_1 ms：constraint_review_pending
- parameter:ifsm_1 s：constraint_review_pending
- parameter:tstg：constraint_review_pending
- parameter:vf10：uncovered
- parameter:ir25：uncovered
- parameter:ir150：uncovered
- parameter:cd：uncovered
- parameter:trr：uncovered
- parameter:vfr：uncovered
- parameter:rthtp：uncovered
- parameter:rtha：uncovered
- figure:fig2:max_if：constraint_review_pending
- figure:fig3:typ_175C：uncovered
- figure:fig3:typ_25C：uncovered
- figure:fig3:max_25C：constraint_review_pending
- figure:fig4:surge：constraint_review_pending
- figure:fig5:vr75：uncovered
- figure:fig5:vr20：uncovered
- figure:fig6:capacitance：uncovered

## 程序诊断

