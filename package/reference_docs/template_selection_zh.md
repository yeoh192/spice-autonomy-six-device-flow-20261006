# 从索引自动选择模型

`run_spice.py` 已增加可选 `template_selection` 阶段。旧输入仅提供 `model` 时保持原路径；新输入提供索引配置时可以省略 `model`，由选择阶段生成。参考曲线、测试条件、验收阈值、手册清单和现有迭代规则仍须由格式化输入明确提供。

```json
"template_selection": {
  "index": "/path/to/classified/templates.sqlite",
  "library_root": "/path/to/original_library",
  "max_candidates": 20,
  "max_trials": 3,
  "max_api_calls": 2
}
```

路径相对输入JSON目录解析。当前仅接入 `category=level3_nmos_subcircuit`；端口声明必须可确认DGS顺序、自包含、单顶层入口、唯一MINT NMOS Level3以及现有RD参数结构。外部依赖、多入口提取、其他优化器结构暂不自动转换，检索报告明确记录排除理由。厂家、自建、派生来源均不排除。

流程：有限SQLite检索（目标型号匹配优先）→结构与哈希预检→Qwen返回最多max_trials个候选ID→对每个候选运行同一组真实LTspice基线与曲线比较→排除仿真/规格失败项→按全部参考曲线平均归一化MAE选择起点→原有自主搜索、保留/回退、回归和交付报告。这里的最佳仅指给定检索窗口及Qwen选择的有限候选，不能声明全库最优；基线最佳也不保证所有候选充分调参后的最佳。

选择阶段有单独的预算：max_candidates≤100、max_trials≤5、max_api_calls≤3。默认最多2次选择API和3套基线；例如14项测试最多42个候选测试项。该阶段预算额外于原自主迭代预算；之后主流程还会对选定模型运行基线，当前尚未跨目录复用该套基线。原有接受阈值不放宽。排序只用有限指标，不代替全部手册覆盖验收。

## 本地BUK输入

已生成本地 `/Users/192y/电气/SPICE输入包/BUK7K52-60E/spice_input_indexed.json`，使用本地分类索引并将参考路径指向既有曲线；不把厂家模型、曲线或数据库打包到Git。

```bash
python3 run_spice.py --input /path/to/spice_input_indexed.json --output runs/indexed_task --check-only
python3 run_spice.py --input /path/to/spice_input_indexed.json --output runs/indexed_task --resume
```

check-only只检索和核对兼容性，无API或仿真。第二条会调用官方Qwen（沿用qwen.region/model），隐藏输入密钥或使用DASHSCOPE_API_KEY。密钥只保留进程环境供后续子进程使用，不写入任务文件。模型/手册内容只作为证据，不执行大模型提供的代码。

`template_selection/retrieval.json`记录有限候选与排除理由，`request_context.json`记录选择上下文，`state.json`记录选择预算/反馈和输入指纹，`trial_results.json`记录真实测试结果，`selected.json`记录选中候选、指标和范围。数据或索引变化会拒绝续跑；已完成候选报告不重复运行，选择提案非法会在独立API预算内反馈给Qwen。

目前程序测试采用模拟API和模拟仿真验证闭环；本地BUK完成真实索引预检。此版尚未用官方API和真实LTspice联合执行候选选择阶段，不能据程序检查宣称实际模型效果已经改善。
