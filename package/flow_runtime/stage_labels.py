"""Human-readable lifecycle labels; completion is not acceptance."""
NAMES={'model_materialization':'SPICE候选模型生成','input_loading':'器件资料载入与校验','template_selector':'模型模板筛选','template_reviewer':'模板筛选审查','template_selection':'模型评估筛选','model_optimizer':'模型参数与结构修正提案','model_repair_designer':'模型修复设计','model_diagnoser':'模型故障诊断','patch_reviewer':'修正方案与结果复核','circuit_build':'测试电路构建','simulation_execution':'真实仿真执行','result_comparison':'结果对比校验','report_export':'模型与报告导出','test_designer':'测试电路设计','test_reviewer':'测试电路审查','calibration':'测试电路校准','test_library':'测试方法开发','workflow':'自动化流程','preflight':'输入预检','execution_diagnoser':'执行故障诊断','model_patch':'模型修正验证','initial_known_tests':'初始测试批次','baseline':'基线测试批次'}
STATUS={'running':'开始','requesting':'开始（请求大模型）','completed':'结束','passed':'结束（通过）','failed':'失败','request_failed':'请求失败','request_recovery':'请求恢复中','waiting_for_recovery':'等待恢复','revision_required':'待修订','retained':'结束（保留修改）','rolled_back':'结束（回退修改）','reused':'结束（复用已有回复）','simulation_reused':'结束（复用仿真）','budget_exhausted':'结束（预算耗尽）','stopped_with_evidence':'结束（保留失败证据）','interrupted':'结束（用户中断）'}
def message(stage,status,detail=None):
    name=NAMES.get(stage)
    if not name and stage.startswith('regression_'):name='修正模型完整回归'
    if not name:return None
    suffix=STATUS.get(status,status);test=(detail or {}).get('test')
    return '【'+name+'】'+suffix+(' · '+str(test) if test else '')
