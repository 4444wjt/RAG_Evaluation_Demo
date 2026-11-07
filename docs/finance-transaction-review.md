# 交易风险线索与证据复核（本机桌面）

此模块用两条可调整的演示规则筛选交易，再核对结构化说明及其引用，记录人工决定并下载可重新计算的复核包。大额可以是正常业务，未触发也不代表安全；结果不是欺诈概率或欺诈认定。内置示例是合成交易和人为构造的错值、缺引用，不是真实欺诈标签。

## 启动

解压交付包，保留目录结构和 `frontend/dist`。运行环境需要预先具备 Python 3.11 及本项目现有 Python 依赖（包括 FastAPI、Starlette、Pydantic 和 Uvicorn；版本约束见根目录 `requirements.txt`）。源码包不附带 Python 环境、模型或凭据；此交易模块不需要 API Key、`.env` 或外部服务。构建文件已包含时，运行不需要 Node.js。源码开发者自行使用既有前端依赖执行 `npm run build`，启动器不会安装或构建。

在解压根目录打开 PowerShell，将下方变量替换为已有 Python 解释器的绝对路径：

```powershell
$python = Read-Host '已有 Python 解释器的完整路径'
& .\tools\finance\start-local.ps1 -PythonExecutable $python -Port 8096
```

打开 [本机交易风险复核](http://127.0.0.1:8096/#/lab/transaction-review)。启动器在前台运行，只绑定 `127.0.0.1`；按 Ctrl+C 停止。可用 `-Port` 指定另一个端口，并同步修改浏览器地址。解释器或构建缺失会停止；端口占用会报错，不会结束其他进程。启动器按自身位置找源码，可从其他工作目录用其完整路径运行。若进程设置了 `VERISCOPE_READ_ONLY=1`，交易分析和重开返回 HTTP 423；使用未设置该标志的本地运行环境。此次交付为本机桌面入口，公开部署需另行配置与验收。

## 导入、核对与重开

1. 在“交易风险复核”导入 UTF-8 CSV/JSON，或粘贴内容后读取。也可载入“模拟边界示例”。CSV 需要 `transaction_id,timestamp,customer_id,terminal_id,amount` 五列，支持带引号的逗号/换行；普通额外列（如 `label`、`fraud_label`、`notes`）剔除并显示，不参与筛选。CSV 表头含保留列 `currency`（去首尾空白、忽略大小写）一律拒绝，即使列值为空或全为同一种币种；请先按币种分批，确认单一币种后移除该列并在页面确认币种，或改用顶层 `currency` 的 JSON 请求。JSON 使用下方完整请求格式，不接受裸报告作为已验证结果。
2. 核对单一币种及“大额阈值 / 窗口分钟数 / 窗口最少笔数”，点击分析。查看交易总数、规则线索、字段一致/不一致/待复核的独立计数；选择案件查看窗口计算与来源交易。
3. 在“结构化说明”编辑交易 ID、字段、值与引用。可从检查结果的“编辑此说明”进入。缺引用或未知引用保留待复核；错值与有效但超出范围的引用标为不一致。普通自由文本没有被语义核验；`fraud_confirmed` 无论真假都待复核，因为交易记录不足以证明它。更改输入、规则或说明会撤下旧报告与人工决定，须重新分析。
4. 对每个案件选择待复核、升级调查、排除线索或补充证据；非待复核决定须填写原因。人工决定不会改变字段检查，也不会触发封号、扣款或转账。
5. 点击“下载完整复核包”，在浏览器下载列表确认 `transaction-review-bundle.json`。包包含请求、完整报告、规则/输入/代码绑定与决定。再次导入该包后点击“重开并验证”，等待本地服务重算整个报告；报告不匹配或代码版本已变时明确拒绝，应保留原件并用原请求重新分析。下载采用紧凑 JSON，请勿为大批包添加漂亮缩进。

## 数据、规则与限制

请求 `schemaVersion` 固定为整数 `1`，`currency` 是三个大写字母的单一币种代码；不换汇、不接受行内另一币种。原始请求上限 2 MiB UTF-8 / 5000 行，不截断；完整复核包重开上限单独为 24 MiB，包内请求仍受原上限约束。5000 行报告大小验收只覆盖固定工程负载，不保证任意超长文本输入也小于同一大小。

每笔交易的 ID 唯一，主体和终端按原文本保留。金额使用非负十进制、至多两位小数，建议在 JSON 中用字符串保留精度，输出统一为两位小数字符串。时间必须带时区，最多六位小数秒；超出微秒精度明确拒绝。缺失、重复 ID、负数、非有限金额与无时区时间均报错，不补零。

默认演示规则是金额 `>= 10000.00`，或同主体最近 10 分钟内笔数 `>= 3`。至少满足一条才产生案件。时间窗包含左右边界和全部同刻同主体记录，排除未来及其他主体，输入可无序。窗口合计用十进制计算。`review` 表示仍需复核，`no_signal` 表示无规则线索且没有未决字段检查。

最小合成请求如下；CSV 只提供交易行，币种与规则在页面确认：

```json
{"schemaVersion":1,"currency":"CNY","transactions":[{"transaction_id":"synthetic-001","timestamp":"2026-11-01T10:00:00+08:00","customer_id":"模拟客户","terminal_id":"模拟终端","amount":"12000.00"}],"rules":{"highAmount":"10000.00","windowMinutes":10,"minTransactions":3},"claims":[]}
```

`claims: []` 为线索案件生成固定字段事实；显式说明仅核对列出的 `amount/customer_id/terminal_id/timestamp/window_count/window_total/fraud_confirmed`。引用可用 `evidence_ids`，或当前报告 `evidenceIndex` 中的 `evidence_ref={group_id,start,end}` 零基左闭右开切片，二者不能同时使用。证据组仅对应当前输入。

服务只在内存计算交易，不将交易或决定写到服务器磁盘；需要留存时下载复核包并自行保管。请求中的文本不执行。复核包检查决定 ID、枚举和原因完整性；没有外部签名或权威副本，不能认证决定人，也不能发现合法决定被合法改写。

## CLI、API 与字节核对

在解压根目录复用同一 `$python`：

```powershell
& $python .\scripts\transaction_review.py analyze .\request.json --out .\report.json
& $python .\scripts\transaction_review.py replay .\transaction-review-bundle.json --out .\verified-bundle.json
```

省略 `--out` 时只向标准输出写 JSON。有效计算退出 0（有线索也为 0，查看 `status`）；输入、重开或 I/O 错误退出 2，原因写到标准错误。`report.json` 是单独报告；重开需要上述包含请求、报告、决定的完整包。

同源 API 为 `POST /api/transaction-review/analyze` 和 `POST /api/transaction-review/replay`，使用 UTF-8 `application/json`。成功为 HTTP 200，输入错误 422、超限 413、媒体类型错误 415、只读 423；`GET /api/health` 只报告进程存活，不验证模型或凭据。

报告的 `provenance.requestSha256` 绑定规范化请求（显式默认值、UTC 时间、金额字符串、按时间/ID 排序交易及紧凑排序键 UTF-8 JSON），不是下载文件的字节哈希。`codeSha256` 绑定列出的核心/API/CLI 文件字节。绑定不证明输入来源真实、语义正确或业务效果。在解压根目录运行以下命令，将每个文件的实际字节摘要与报告 `provenance.codeSha256` 中**同一路径**的值比较；它们不与 `requestSha256` 比较。交付包其它文件使用随包清单核对。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath .\backend\core\transaction_review.py, .\backend\api\transaction_review.py, .\scripts\transaction_review.py
```
