# 正式模型接口合同：model-interface-1

统一覆盖诊断、修复设计、普通优化、执行前审查和执行后审查。每次请求附带版本、合同ID、输入与输出JSON Schema、真实测试/证据ID清单。程序先校验输入，再调用API；输出通过结构与事实校验后才能交给执行阶段。其他测试开发/模板角色暂不采用这份模型修复合同，其原有校验保持有效。

| 阶段 | 输出动作 | 强制约束 |
| --- | --- | --- |
| diagnostic_planning | diagnose / experiment / patch / stop | reason、evidence_tests、edits；引用真实残差项；纯诊断和停止的edits为空 |
| repair_design | experiment / patch / stop | 修改必须1至4项，old/new为非空字符串；不得返回纯诊断或未声明的defer |
| model_optimization | patch / defer | patch需kind、reason、evidence_ids、edits；defer需reason |
| pre_execution | approve / revise | phase、decision、evidence_ids、issues；输入必须candidate_executed=false，实际差异与提案修改一致 |
| post_execution | approve / revise | 上述字段及candidate_sha256；输入必须为该哈希的已完成实测结果 |

approve的issues为空；revise必须给出至少一个结构化问题，包含code、message、evidence_ids，可补充field。问题码为INVALID_EDIT、FROZEN_CONDITION、INSUFFICIENT_EVIDENCE、MEASUREMENT_MISMATCH、REGRESSION、OTHER。输出不得带未声明字段。引用的测试与证据必须来自程序提供的清单；old文本须准确匹配活动模型，所选适配器必须存在。全部活动测试仍受原保留/回退规则保护。

结构错误不进入仿真。API客户端最多进行一次自动格式修订，第二次输入携带interface_feedback中的错误码、字段和约束，合同与证据保持不变。两次调用都计入总API预算，计数跨中断保留；不合格输出不会标为成功缓存。修订耗尽后把interface_contract故障返回工作流，使下一轮设计能读取原因。有效的revise是审查结果，交回设计修订，不当作格式错误重试。

schemas位于interface_contracts目录，由flow_runtime/interface_contracts.py导出。运行时使用同源的有限JSON Schema校验子集，覆盖本包实际使用的type/required/properties/additionalProperties/enum/const/oneOf/items/minItems/maxItems/uniqueItems/minLength/maxLength/pattern。没有添加第三方依赖，也没有假设供应商支持原生JSON Schema强制输出；保证的是程序会拦截无效响应，不是模型每次一定生成合格输出。

完整模型文本在请求中可用时，执行前会重新生成提案差异并比对；大型模型的完整源码核验仍由工作流的实际文件补丁校验执行。合同不能证明物理因果解释正确，真实校准、仿真和完整回归继续决定保留。没有放宽10%典型值、5%曲线MAE、10%最大误差或手册规格，也未补齐当前待判定参考依据。

真实Qwen/GLM本版本运行尚待验证。Windows兼容移植不在本次改造范围内。
