# 通用清单自动开发阶段
所有六器件的适用且未绑定记录自动进入队列。条件和数值完整的测试交Qwen整理合同、生成实际声明式电路，GLM审查，独立已知电路校准，真实参考模型试运行，实测后再审查。校验/审查失败自动反馈修订，每项目3轮；方法登记与模型验收分开。默认每器件本轮最多6条记录，其余记录保存为预算队列。本轮不是全部清单完成。
新增电压/差分电压采样校准，含非零共模验证；不再用电阻校准套所有采样接口。无对应校准的测量明确阻塞。
参考模型自动选择唯一家族入口、提取子电路并生成端口包装器，保持内部参数不变。含外部依赖或入口不明确自动报告模型接口缺口。ACS723参考模型包含外部lib依赖，当前仍会受阻，尚未自动开发任意解析器/模型依赖封装。ADA4528参考接口可以提取；物理引脚对应必须经过Agent审查，不默认通过。
修正审查证据ID漏登记model/model_text的框架问题；补读证据只在已读时可引用。原实测核心解析文件不改动。
真实API和LTspice由用户运行。本包不是六器件全覆盖完成版。输出包含qualified方法、剩余预算队列、参考资料缺口和错误。模型不合格与方法不合格分开，参考模型不进行自动拟合。

## 启动
运行bash RUN_SIX_DEVICES.sh --run。在当前Mac隔离安装、离线检查，通过后隐藏输入两家密钥，执行六器件现有配置测试、清单自动开发和14项草案自动修复。新开发每器件最多6项，剩余队列及依赖缺口保存在summary，不代表全部手册完成。
原始输入与历史结果不修改。用户授权真实API和仿真后自行执行，本次仅离线验证。

Coverage writeback is now part of the batch: evidence-backed binding confirmation
runs before inventory development, and final method qualification receipts are
imported after draft repair. See [COVERAGE_BINDING.md](COVERAGE_BINDING.md) for
separate counts, review budgets and standalone use. Source inputs remain unchanged;
full coverage and electrical acceptance are still reported independently.

## 模型迭代（v0.2.9）
保留覆盖确认与资格回写，随后接入Qwen模型补丁→GLM审查→全部活动测试回归→保留或回退→再次迭代。目前BUK23项、1N4148六项可进入模型迭代；其余四器件缺正式候选或正式活动测试时明确阻塞。参考接口模型不会自动变成交付候选。详见MODEL_ITERATION.md。

## 五器件测试能力补齐（0.3.0开发版）
新增完整可整理队列的测试开发与资格回写入口，详见TEST_COMPLETION.md。默认离线预检，真实执行使用RUN_FIVE_TEST_COMPLETION.sh --run。正式模型交付状态与测试方法资格分别报告。

## GPT校正测试库第一批

22项方法已通过真实校准/试运行并登记；256项离线检查通过，统一入口完成22项回归。20项模型结果通过、2项失败；五器件全手册覆盖未完成。运行与证据见[GPT_TEST_CORRECTION.md](GPT_TEST_CORRECTION.md)。


## v0.3.3 覆盖回写与测试族

新库通过 `coverage_audit.py --gpt-library gpt_test_library --import-only` 回写；所有实际校准、模型和波形重新核验。新增ACS/ADA测试族映射及10项真实验证方法，合计32项。运行 `bash RUN_REGISTERED_COVERAGE_FAMILIES.sh --check-only` 可离线生成覆盖报告；`--run` 执行统一回归。完整说明见 [REGISTERED_COVERAGE_FAMILIES.md](REGISTERED_COVERAGE_FAMILIES.md)。其余生成器与全手册交付仍有缺口。


## v0.3.4 前四器件绑定

新增方法、恢复测量与低温阻抗比较已完成真实资格和统一回归，见[FIRST_FOUR_BINDINGS.md](FIRST_FOUR_BINDINGS.md)。当前库190项真实记录，3项诊断不计覆盖；前四器件已登记方法65、17、9、6，缺方法记录21、2、2、2。使用RUN_FIRST_FOUR_BINDINGS.sh --check-only核对与回写，--run执行当前99项前四器件回归。模型正式验收仍未通过。

## v0.3.5: API batch with registered methods

RUN_SIX_DEVICES.sh now imports the 190-method qualified library, executes it in the batch, writes method coverage, and expands candidate iteration to 65 BUK / 17 diode tests. Qwen/GLM development and patch review remain enabled. The other four devices still need eligible candidates; method benchmarks are not fitted deliveries. See REGISTERED_AUTOMATION.md.

## v0.3.6 Review phases

Patch review now authorizes a trial before execution; a separate result review follows measured candidate-hash validation and cannot override regression retention. Full evidence stays on disk while API contexts exclude redundant snapshots. The BUK launcher accepts BUK_BASELINE_RUNTIME pointing to a prior iteration runtime, verifying RAW, circuit, runner, parser and model identity before reuse in a new run. Offline tests do not establish successful real model fitting.

## v0.3.7 Robust diagnostic loop

Rollback evidence now reaches the diagnostic planner, including changed numeric slots and signed residuals. Invalid proposals and review revisions do not spend physical-trial slots. Bounded analysis, consecutive-revision guards, repeated single-parameter failure detection and durable trial reservations improve recovery without changing acceptance or global budgets. Offline fault tests cover rollback to a different parameter, invalid edits, review feedback and last-trial interruption. Real API fitting quality is still pending validation.
