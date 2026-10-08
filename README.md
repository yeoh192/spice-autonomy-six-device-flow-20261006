# SPICE Autonomy — Six Device Flow

六器件SPICE自动化框架与本地安装包。当前开发版本包含Qwen设计、GLM独立审查、受限夹具修订、独立校准、真实仿真、反馈修订及缺口报告。

配置回归有BUK23项和1N4148六项；电容与变压器有14项草案资格/修复任务。新增清单开发默认每器件本轮最多6项，剩余项目保留队列。新增五器件入口支持受控依赖展开、逐记录参考接口选择及电流采样校准；部分测量接口仍有缺口。参考模型用于接口验证，不代表模型交付；配置回归不会重新索引选模或完成全覆盖拟合。

## 运行

这是针对原用户macOS工作目录的本地快照，依赖LTspice以及已有校准、资格目录；不是跨机器开箱即用的发行版。需要的路径在启动脚本中列明。原输入和历史实验不改动。

```bash
bash package/RUN_SIX_DEVICES.sh --check-only
bash package/RUN_SIX_DEVICES.sh --run
```

默认使用官方Qwen `qwen3.8-max-0902` 与智谱 `glm-5.3`。两个API Key在终端隐藏输入，不写入文件。也可由调用环境提供 `DASHSCOPE_API_KEY` 与 `GLM_API_KEY`；不要提交真实密钥或.env文件。

`--run`会请求付费API并启动LTspice。新增清单开发每器件最多108次API调用、72项仿真、20分钟；14项草案修复预算另计。每个阶段实际使用量由状态记录保存。不能将进程结束或 `finished_with_gaps` 解释成全覆盖验收通过。

脚本创建隔离工作目录，汇总报告为 `runs/six_batch/summary.json`。如需续跑，在生成的工作目录执行 `bash RESUME_SIX_DEVICES.sh`。

## 包内容与验证

- `package/`：经哈希校验的完整源码、索引模板库、测试库及六器件输入快照。
- v0.2.8 Release附件是上一版ZIP与SHA256文件；本次0.2.9更新以仓库源码为准。
- 当前v0.3.4源码271项离线测试通过；前四器件99项当前活动方法经真实统一回归复核，隔离安装与证据回写检查通过。
- 真实API＋LTspice全批次验收尚未执行；模拟后端结果不能作为真实测试资格。

现有手册最小/最大规格保持原值；典型值误差≤10%，曲线归一化MAE≤5%、最大误差≤10%。模型不能自行放宽标准。

详情参见[开发范围](package/CAPABILITY_DEVELOPMENT.md)与[验证说明](package/VALIDATION_CAPABILITY_DEVELOPMENT.md)。第三方手册、模型和模板保留原版权与来源，本仓库不另行授予这些材料的许可。

0.2.9把证据核对、绑定确认和覆盖报告回写接入批次。新增方法资格在实测证据验证后登记，参考模型方法与器件达标分别统计。参见[覆盖确认说明](package/COVERAGE_BINDING.md)，包括新增API预算及独立复核命令。

## v0.2.9：批次模型迭代

测试开发和草案修复之后，新增Qwen模型补丁→GLM审查实际差异→完整活动回归→保留或回退→再次迭代。普通优化最多3轮，诊断修复最多3轮，预算共享。

当前BUK23项与1N4148六项可进入模型迭代；其他四器件缺正式候选或有资格的活动测试，明确阻塞。参考模型不直接转成交付候选。完整活动回归不等于整个手册全覆盖。

已有批次可单独启动新阶段，默认原用户工作目录：

```bash
bash package/RUN_MODEL_ITERATION.sh /Users/192y/电气/D2S-FLOW-six-capabilities_20261006-160903_74908 --run
```

其他目录请替换第一个参数。旧模型、实际电路、运行器、完整RAW、依赖哈希和原签名全部匹配时复用历史仿真。新模型仍须真实回归。历史29条真实基线曾只读核对；本次合并覆盖功能后依赖签名发生变化，不匹配的缓存将重新仿真。未执行真实模型迭代API或新LTspice仿真。合并后242项离线检查通过。

中断后在生成的工作目录执行 `bash RESUME_MODEL_ITERATION.sh`。新建任务的 `RUN_SIX_DEVICES.sh` 已自动包含模型迭代阶段。详情参见[模型迭代范围](package/MODEL_ITERATION.md)和[验证记录](package/VALIDATION_MODEL_ITERATION.md)。

## v0.3.1：五器件测试开发与提案格式恢复

新增五器件测试补齐入口，处理1N4148、电容、变压器、ACS723与ADA4528的可整理条件队列；BUK保持现有回归。它包括受控外部模型依赖展开、逐记录模型选择、共享预算与独立记录状态、电流采样校准以及方法资格回写。缺资料与未实现的测量接口继续报告缺口。

```bash
env -u DASHSCOPE_API_KEY -u GLM_API_KEY bash package/RUN_FIVE_TEST_COMPLETION.sh --run
```

此命令新建隔离目录并隐藏输入两个Key。最多120项条件整理，每项最多18次API调用，合计上限2160次；每器件2小时，14项草案修复另计。它用于方法开发与资格验证，不修改参考模型作拟合交付。

已修复模型返回models列表、analysis列表等错误类型导致的进程崩溃：先检查接口类型，再将字段路径反馈给Agent自动修订。252项离线测试及隔离安装预检通过，包含连续两次错误提案后自动反馈并继续验证的故障测试。真实五器件全覆盖与交付验收尚未通过。

更新后的代码哈希改变了缓存身份，新任务重新执行当前版本交流校准；旧目录、请求计数和结果保留。v0.3.1 Release提供与package源码一致的ZIP及SHA256。详见[测试开发说明](package/TEST_COMPLETION.md)和[格式恢复验证](package/VALIDATION_PROTOCOL_SHAPES.md)。

## v0.3.2：GPT校正测试库第一批

22项精确条件方法经GPT核对、独立解析校准和真实LTspice试验后登记；已通过统一spice_flow.py入口运行。1N4148 6项、电容8项、变压器6项、ACS723 1项、ADA4528 1项；20项模型结果通过、2项失败。方法可用与候选模型交付分开，五器件全覆盖仍未完成。256项离线检查通过。

运行 `bash package/RUN_GPT_CORRECTED_TESTS.sh --run`，无需API Key。本版本仍是原用户macOS绝对路径快照。真实证据、注册记录、剩余队列及范围见[GPT测试校正说明](package/GPT_TEST_CORRECTION.md)。Release附件提供完整校验包。


## v0.3.3：覆盖回写与继续补齐测试族

已接通登记库→覆盖回写，并完成ACS723/ADA4528的181条手册测试记录映射。共111个具体条件测试经过真实LTspice校准、器件试运行和统一入口回归；其中非线性端点算法只作诊断，不降低手册覆盖缺口。265项离线测试通过。ACS723缺方法37→29，ADA4528 144→112；整条条件覆盖、统计分布及最终模型验收仍独立保留。

入口：`bash package/RUN_REGISTERED_COVERAGE_FAMILIES.sh --check-only` 离线回写证据，`--run` 执行统一回归。当前Mac路径快照仍需对应绝对路径存在。说明：[覆盖回写与测试族](package/REGISTERED_COVERAGE_FAMILIES.md)。


## v0.3.4：前四器件方法绑定

前四器件已登记具体条件方法65、17、9、6；已有方法绑定的手册记录37、11、6、5；缺方法记录35→21、7→2、3→2、2→2。当前库190项真实资格记录，3项只作诊断，不计手册覆盖。模型正式验收与整条条件覆盖仍待完成。

```bash
bash package/RUN_FIRST_FOUR_BINDINGS.sh --check-only
```

核对真实证据并回写，不调用API或仿真。改为`--run`可通过统一入口重跑前四器件99项活动测试。详情：[前四器件方法绑定](package/FIRST_FOUR_BINDINGS.md)。原用户Mac路径仍须存在。

## v0.3.5: API batch with registered methods

RUN_SIX_DEVICES.sh now imports the 190-method qualified library, executes it in the batch, writes method coverage, and expands candidate iteration to 65 BUK / 17 diode tests. Qwen/GLM development and patch review remain enabled. The other four devices still need eligible candidates; method benchmarks are not fitted deliveries. See package/REGISTERED_AUTOMATION.md.
