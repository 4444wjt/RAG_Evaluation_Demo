# 任务单：核查器同条件对照（T1 离线实现与干跑／T2 付费运行）

公共要求，T1、T2 都适用：
- 研究口径、两臂定义、指标和判定规则以同目录 `preregistration.md` 加 `preregistration-amendment-1.md`（已批准，冲突处以修订为准）为准，两者都**不得修改**。发现它无法执行时停下报告，不自行调整口径。
- 仓库：`D:/RAG_Evaluation_Demo`。工作区很脏（HEAD `936f3a9`，落后 master 23 个提交），**不要使用工作区里的源码**。所有仓库内容一律用 `git show 05099e7:<路径>` 或 `git archive 05099e7 <路径>` 取出到本目录。
- 读写范围：
  - 只写 `work/competition/ragchecker-checker-compare-20261030/`；
  - 可读整个仓库与 git 对象；
  - 禁止 `git add`、`git commit`、`git push`、`git stash`、`git checkout` 等任何会改动 git 状态的命令。
- 解释器：
  - A 臂用 `D:/RAG_Evaluation_Demo/venv/Scripts/python.exe`（已装 Ragas 0.1.21），只读使用，不得 pip install；
  - B 臂用本目录 `venv-ragchecker/Scripts/python.exe`（已装 ragchecker 0.1.9、refchecker 0.2.17），不得改它已有的包版本。
- 密钥：只从 `D:/RAG_Evaluation_Demo/.env` 的 `OPENAI_API_KEY` 读取（这个变量指向 DeepSeek），base URL 为 `https://api.deepseek.com`。不得打印、写入文件或记入日志；日志里只写「已读取」。
- 设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，避免 litellm 启动时联网。
- 停止条件见预登记第 8 节；另外，遇到依赖缺失、需要联网下载或权限不足时，也停下报告。

---

## T1：离线实现、测试与干跑（本次派发；不得联网，不得调用任何模型）

1. **固定输入**：把预登记第 2 节的 5 个文件取到 `inputs/`。用 `git hash-object` 核对 blob 与预登记一致，写入 `inputs/manifest.json`。
2. **实现 `run_compare.py`**：
   - A 臂：调用 `ChineseFaithfulness` 的 NLI 提示词构造（`_create_nli_prompt` 或 Ragas 0.1.21 中的等价入口）和原解析，跳过陈述抽取。
   - B 臂：调用 RefChecker `LLMChecker` 的原提示词与解析，只替换底层发送函数。
   - 两臂共用同一个 DeepSeek 发送客户端：非思考模式、temperature 0、max_tokens 2048、无重试。非思考模式的关闭方式按 DeepSeek 官方文档写，并在报告中注明出处；离线时无法确认的，标「待 T2 首个请求核验」。
   - **预算台账** `budget-ledger.jsonl`：每次请求前按高峰价预留费用。预留上限 = 提示字符数 × 2（作为 token 上界）× ¥2/百万 + 2048 × ¥8/百万。拿到 usage 后按实际 token 结算（缓存命中按 ¥0.04/百万）。下一次请求前，若累计「预留 + 实际」将达到 ¥8，立即停止。
   - 模式：`--dry-run` 不联网，列出全部计划请求、请求数、预留费用总额；`--run` 必须同时设置环境变量 `VS_ALLOW_PAID=1` 才可执行，否则直接退出。
   - 每个请求的完整提示文本和原始响应都落盘到 `raw/`（不含密钥）。
   - 写出 `results.jsonl`，每条一行，含臂、轮次、陈述 id、判定、原始标签、是否解析失败。
3. **分析脚本 `analyze.py`**：按预登记第 4–6 节计算全部指标并给出判定。必须先核对确定集为 46 条、未决集合恰为 S12、S14、S16、S18、S26、S35，不一致则报错退出。
4. **离线测试 `test_compare.py`**（用假 LLM），至少覆盖：
   - 两臂的解析与映射；
   - 确定集核对；
   - 预算到线时停止；
   - 未设 `VS_ALLOW_PAID` 时 `--run` 拒绝执行；
   - 提示中不出现人工标签、标注人信息、原题号或 10-05 判定（泄漏检查）；
   - 解析失败记为「无结果」；
   - 思考内容检测。
5. **执行**：用两个解释器分别运行测试（各测各自的臂），再执行 `--dry-run`。

**T1 验收标准**：
- 测试全部通过，测试日志落盘；
- 干跑列出的请求总数和预留费用总额均已写明，预留总额 ≤ ¥8；
- B 臂替换发送函数的位置写明（文件:行），并说明 RefChecker 的提示词与解析未改动；
- 产物 `T1-report.md` 写明：改了哪些文件、执行了哪些命令、结果、证据路径、未验证项、待决定事项。

## T2：付费运行与分析（另行派发，需联网；T1 审核通过前不得执行）

按交错顺序 A1、B1、A2、B2、A3、B3 运行，然后执行 `analyze.py`。

产物：
- `summary.json`、`report.md`；
- `SHA256SUMS.txt`（覆盖 `inputs/`、`raw/`、`results.jsonl`、台账、两个环境的 `pip freeze`）。

报告需写明：实际返回的模型名、usage、实付估算，以及按预登记规则得出的结论。
