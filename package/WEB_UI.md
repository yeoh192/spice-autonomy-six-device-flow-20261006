# 本地器件工作台

Mac：`bash RUN_WEB_UI.sh`；Windows：双击 `RUN_WEB_UI.cmd`（需Python 3.9以上、LTspice及原流程依赖）。也可执行 `python -m web_ui.server --port 8765`。打开终端打印的完整地址，地址中的随机令牌仅用于本地会话。服务只监听127.0.0.1，不适合作为公网服务。

1. 将标准包压缩为ZIP，含唯一task.json、模型、参考和覆盖文件；保留相对路径，不包含runs。上传上限50MB，解压上限200MB。
2. 填写本机LTspice可执行文件路径，Windows例如实际安装目录中的LTspice.exe。填入API地址和Key；同一第三方Key时GLM Key可留空。使用官方接口时Base URL留空并分别填写两个Key。
3. 可以先仅预检；通过后点击启动自动化。输入包验收标准与预算沿用，强制有限模式；上传包自带的脚本不会执行。运行器由页面设置覆盖。
4. 停止按钮请求进程中断并保存；收到报告后导出结果。不保证中断时已有足够数据生成报告。已有外部终端SPICE任务请先结束，同一Web服务一次仅允许一个任务。

输出保存在package/web_runs/<任务ID>/exports/：
- measurements.csv：测试ID、执行/验收状态、实测值、单位、最小/典型/最大、典型值误差百分比和诊断。
- waveforms/*.csv：报告引用的实际波形，保持原始轴和信号名称；AC复数拆分实部/虚部。不是PDF参考曲线，不为缺失参考造值。
- candidate.lib：报告中的当前保留模型；可能没有变化或未达标。
- circuits/<测试ID>/：实际.cir与同目录模型依赖，解压后可手动在LTspice打开test.cir。
- summary.json、export_manifest.json：验收缺口、模型变化标识和波形导出失败原因。
- results.zip：上述所有文件。

Key只传给本机子进程环境，不写入任务配置；控制台已知Key脱敏。历史报告保存在磁盘，重启服务可查看既有任务；此版Web UI不提供跨版本续跑。Windows停止机制尚未在Windows实机验证。真实API执行效果依赖原引擎与服务商，Web UI不改变验收逻辑。
