# SPICE Autonomy — Six Device Flow

六器件SPICE自动化框架与本地安装包。当前开发版本包含Qwen设计、GLM独立审查、受限夹具修订、独立校准、真实仿真、反馈修订及缺口报告。

** 配置回归有BUK23项和1N4148六项；电容与变压器有14项草案资格/修复任务。新增清单开发默认每器件本轮最多6项，剩余项目保留队列。ACS723外部库依赖、电容参考接口选择及部分测量接口仍有阻塞。参考模型用于接口验证，不代表模型交付；配置回归不会重新索引选模或完成全覆盖拟合。

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
- Release附件：相同安装包ZIP与SHA256校验文件。
- 离线测试214项：213项通过、1项跳过；隔离安装和六器件离线预检通过。
- 真实API＋LTspice全批次验收尚未执行；模拟后端结果不能作为真实测试资格。

现有手册最小/最大规格保持原值；典型值误差≤10%，曲线归一化MAE≤5%、最大误差≤10%。模型不能自行放宽标准。

详情参见[开发范围](package/CAPABILITY_DEVELOPMENT.md)与[验证说明](package/VALIDATION_CAPABILITY_DEVELOPMENT.md)。第三方手册、模型和模板保留原版权与来源，本仓库不另行授予这些材料的许可。
