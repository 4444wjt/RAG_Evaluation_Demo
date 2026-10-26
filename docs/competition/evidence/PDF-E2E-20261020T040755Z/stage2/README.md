# PDF 样本 A：阶段 2 执行记录

**结论：预检已完成，真实评测未执行。正常路径估算约 159,929 token，超过任务规定的 100,000 token 红线，按要求停止；另发现仅修改六路径无法隔离逐题 CSV。**

本目录是本次预检的独立新归档；没有复用历史评测指标，没有修改已有阶段 1 文件。退出码 `20` 是驱动脚本的成本保护退出，`pipeline.evaluate` 未调用，评测进程退出码为 `null`。

| 项目 | 本次记录 |
|---|---|
| 回答模型 | 计划仅运行配置第一项 `deepseek-chat`；未获取服务端返回模型名 |
| 评判模型 | 默认自评，计划为 `deepseek-chat`；未实际调用 |
| 题数 | 默认计划 3 正向 + 3 负向，共 6 题；未生成测试集，实际评测 0 题 |
| 模型 API 调用 | 0 次；没有服务端 usage，逐次调用列表为空，各 usage 汇总字段为 `null` |
| Ragas | faithfulness、context_precision、answer_relevancy、answer_correctness、context_recall 均为 `null` |
| 质量门禁 | `NOT_RUN`，`gate_passed=null`；未产生 PASS/FAIL 结论，阈值仍为 0.8 / 0.8 |
| 当前 parse_check | `PASS`，6 页，切成 26 块；仅为文本层启发式检查 |
| 样本 SHA-256 | `fa8f8923bde06cfb3c682e004ebd12fd14ce53cff72a9365e1fff740e92f1a4f`，与要求一致 |
| 源文件大小 | 543,576 bytes |

预算按当前代码的正常成功路径核算，不把极端重试当作常规消耗。默认 `TEST_QA_NUM=3` 只控制正向题，另生成 3 道负样本；默认模型筛选会运行 3 个模型，本次只计划第一项。未指定独立裁判，沿用自评。

| 阶段 | 计划调用数 | 输入 token 代理估算 | 输出 token 规划余量 |
|---|---:|---:|---:|
| 正向出题、负样本生成及审核 | 5 | 9,207 | 4,280 |
| 检索后回答 | 6 | 5,547 | 1,080 |
| Ragas 五项指标 | 66 | 117,186 | 12,480 |
| 默认开启的 Trace | 6 | 7,749 | 2,400 |
| 合计 | 83 | 139,689 | 20,240 |

合计 **159,929 token**。仅 Ragas 的输入代理估算已超过 100,000。逐次计划见 `raw/out/token-estimate.json`。估算使用已安装的 `cl100k_base`，不是 DeepSeek 服务端计费 tokenizer，也不是实际 usage。采用 A 实际最长的 4 个块（合计 787 字符）作为保守 Top-K 预算，正向题/回答/参考答案假定为 40/120/60 字符，负题假定为 80 字符并正常拒答；占位文本只是当前文档切片，未作为测试集或结果使用。Ragas 提示词保留本机实际格式，包括中文字符串的 JSON 转义。未执行真实检索，实际召回与文本长度可能不同。

单题正常 Ragas 调用为忠实度 2、上下文精度 4、相关性 1、正确性 3、召回 1，共 11 次。部分正常分支会省略负样本审核或正确性分类。预估不包含失败重试、格式修复；输出规划余量不是硬上限，没有改动生成参数。

六路径均在仅导入 `backend.config` 后、导入依赖模块前改到隔离的 `work/.../out/`。已核对 `pipeline`、`retriever`、`html` 的同名常量，以及 history / HTML / trend 函数捕获的默认参数，全部指向隔离目录。但 `backend/core/pipeline.py:573` 另外以 `base_dir=BASE_DIR` 调用模型循环，`:446-447` 将逐题 `ragas_report_<model>.csv` 写在根目录。此 CSV 不受上述六常量控制；修改 `BASE_DIR` 或包装业务函数会超出本次硬约束，因此没有执行。

`CHROMA_DIR`、`SUMMARY_OUTPUT`、`HTML_OUTPUT`、`CHART_PATH`、`HISTORY_PATH`、`TREND_CHART_PATH` 的根目录原路径在预检前后全部一致；文件核对 SHA-256、大小和修改时间，目录核对文件清单、大小、SHA-256 和修改时间。额外逐题 CSV 也保持一致。已有证据、后端源码、冻结样本及原有前端工作共 239 个受保护文件的 SHA-256 和修改时间均一致。归档完成后另做一次复核，见 `raw/final-validation.json`。

`raw/out/` 保留本次预检原始输出，`raw/driver.py` 为原驱动副本，`raw/run.log` 为实际运行日志。本次未创建向量库，也没有可复制的真实评测 CSV / HTML / 指标。`result.json` 明确区分计划配置、当前解析检查和未产生的评测值。

Git 初始状态：`next` 分支，已有两份 landing 源文件修改、两份 landing 备份未跟踪，以及阶段 1 证据目录未跟踪。本次仅新增 `stage2/` 与被忽略的 `work/` 文件，并为新归档文件在 `.gitattributes` 追加逐文件 `-text` 或 LF 规则，以固定原始证据字节。未暂存、提交或 push，未改依赖。前后完整状态见 `raw/out/git-status-before.txt`、`raw/git-status-after.txt`。

凭据只由现有配置从 `.env` / 环境读取；未写入产物。归档完成后扫描完整凭据值、其前后 8 字符及常见密钥形式，只记录扫描通过与否，不记录凭据、片段或其哈希。校验本归档可使用 `SHA256SUMS.txt`，清单不包含自身；`.gitattributes` 对每个新文件明确指定字节保存规则。

未覆盖：真实嵌入与入库、实际检索、回答生成、Ragas、Trace、质量门禁、服务端模型身份与 usage、PDF 视觉一致性。没有重新处理 B，没有修改或 monkey patch pypdf，没有调阈值或用历史结果补值。
