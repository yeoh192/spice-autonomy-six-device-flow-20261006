# 初始模板库基础索引

```bash
python3 tools/index_templates.py --root /path/to/template_library --output /path/to/new_index_directory
```

标准库实现，不调用GPT/Qwen，不执行模型，不修改原库；输出目录须在原库之外且为空。此版本全量建基础索引，支持SQLite候选检索，尚未接入增量更新或自动模板选择/仿真。

输出：templates.sqlite（files、entries、dependencies、source_evidence、symbols、parse_issues、metadata表及检索索引）；files.csv；entries.csv；symbols.csv；source_review.csv（全部入口）；source_pending.csv（来源待核验）；missing_dependencies.csv（依赖待核验）；parse_issues.csv；summary.json；report.md。

入口按文件、行号、作用域区分；子电路内部.MODEL记录不视作独立候选。续行合并但起始行保留。符号记录PinName/SpiceOrder及模型文件引用，Value2优先作为请求入口；Value与Value2不同单独标记，不自动判为错误。符号入口映射尚未证明引脚语义正确。

文件引用在原库内按路径/大小写和唯一文件名匹配，记录引用原值和解析结果；同名歧义不静默选一个。X实例只在本文件或可达依赖文件中寻找子电路。参数化调用（如{die}）需运行时展开，单独标记。missing_dependencies.csv不等于真实缺失文件清单，要查看kind与status。

默认 `--source-policy all`：厂家、自建、派生和未知来源均可参与检索。来源标记和审计证据仍保留，不按来源推断模型精度。顶层入口在解析和完整声明依赖链满足静态检查时标为 `searchable`，存在结构/依赖疑问时标为 `needs_review`，内部定义标为 `internal`。`searchable` 不是电气验收，仍需核对端口、手册测试条件并进行真实仿真和拟合回归。`--source-policy strict` 仅用于复现历史独立来源筛选，不是当前项目默认要求。

SQLite可本地检索，例如：

```sql
SELECT path,line,name,model_type,selection_status
FROM entries WHERE top_level=1 AND model_type='VDMOS';
```

这是静态启发式解析器，不是完整SPICE编译器：没有展开全部参数/库节，不验证所有元素引脚、物理能力或数值收敛，也不证明来源独立性。基础索引之后应处理来源与结构疑问，再做类别/能力筛选和最小仿真检查。

## 来源与结构复核

```bash
python3 tools/review_templates.py --index /path/to/templates.sqlite --root /path/to/template_library --provenance-root /path/to/local_official_source_collection --output /path/to/new_review_directory
```

复核器核对原库哈希，与本地manufacturer_model_index.csv/template_library.csv中的实际文件内容做精确哈希匹配，保留记录中的来源ID、文件路径与下载地址；本次不访问网站，因此不把本地下载清单说成新完成的外部认证。生成reviewed.sqlite、provenance_review.csv、structural_review.csv、pending_entries.csv和candidate_pool.json。默认来源中立审计保留来源证据并导出静态候选池；不要求先证明来源独立。候选池不代表仿真或手册拟合验收。

作用域解析优先采用最内层同名子电路，避免把局部和全局定义混为歧义。Wolfspeed/Cree/ADI等正文线索已补充到保守筛选。动态子电路目标如{die+level}需参数展开，记录赋值行而不随意填入具体分支。发现.ENDSS等结构疑问仅报告，不修改原模板或批准其使用。此阶段未进行任何模型仿真或精度验收。

## 有界候选检索

```bash
python3 tools/search_templates.py --index /path/to/templates.sqlite --category mosfet --ports 3 --limit 10
python3 tools/search_templates.py --index /path/to/templates.sqlite --name BUK7K52 --limit 5
```

每次仅查询SQLite，不重新扫描模型文件，不调用大模型；默认最多20项，上限100项，便于将小规模候选摘要提供给Qwen。`--output /path/to/new.json` 保存结果；`--include-review` 可显式查阅待核验入口。同内容哈希、入口名和作用域去重，并保留重复文件位置。

`.MODEL` 类型提供类别；子电路内部模型仅提供启发式类别线索，混合放大器可能含MOSFET，因此线索不能代替器件分类验证。未知类别照实标记，不依据型号猜类别。检索没有拟合评分，也不会自动把最先返回的候选当成最佳模型。现阶段仍需由后续配置/仿真流程消费检索结果，不宣称任意格式化文档已自动选模。

## 功能分层分类（当前版本）

```bash
python3 tools/classify_templates.py --index /path/to/templates.sqlite --root /path/to/original_library --output /path/to/new_classified_index
python3 tools/search_templates.py --index /path/to/new_classified_index/templates.sqlite --category mosfet --subcategory n_channel --capability static_iv_core --limit 10
python3 tools/search_templates.py --index /path/to/new_classified_index/templates.sqlite --category opamp --limit 10
```

分层记录为 `category → subcategory → tags/specifications → capabilities`，每项保留证据、置信度和电气验收状态。类别支持二极管、MOSFET、BJT、JFET、运放、电流传感器、驱动器、模拟开关、变压器、电容、电感、电阻、热网络、稳压器、光耦以及未知。仅声明了分类规则的类别不保证本库已有识别结果。

原生MODEL类型提供declared证据；入口前的注释及唯一绑定符号Description提供documented_hint；D/G/S外部端口与内部核心共同提供topology_hint；电流传感器名称与测量端口共同提供identity_and_ports_hint。这些级别是证据来源，不是统计置信概率。不再只依据子电路内部MOS/BJT将整块IC归类。冲突或不足保留unknown，输出unknown_queue.json供后续补证据。

规格标签分开保存raw_value、可解析SI值、单位和meaning。BV/VDS/RON/VTO等只是模型参数；VTO不等于指定测试电流下的手册阈值，BV也不自动等于器件保证耐压。标题中Ratings等仅记为未核验的来源声明，不作为验收条件。能力标签仅说明静态结构或参数存在；如电容元件存在不代表电容曲线准确，TT存在不代表开关/反向恢复已经验证。

查询新增 `--subcategory`、`--tag`、`--capability`、`--confidence`。参数值暂不用于物理规格硬过滤，避免混淆内部参数与目标器件额定规格。旧索引可查询，但没有注释/符号证据时仅用原生类型或外部DGS端口，不再沿用内部元件决定IC功能的规则。

输出独立templates.sqlite（taxonomy/taxonomy_tags/taxonomy_capabilities及索引）、classification.csv/json、unknown_queue.json、summary.json及report.md。原始文件全部核对SHA256，不修改模型、不执行模型文本、不调用API。首次分类仍存在未识别入口，不能声称全库功能已经可靠分类，也不代表自动选模流程已接入这些标签。
