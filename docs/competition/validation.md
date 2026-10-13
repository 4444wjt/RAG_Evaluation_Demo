# 本轮验收与运行状态

材料日期：2026-10-12（第 6 节）；2026-10-06（第 1–5 节）；正式运行日期：2026-10-05。区分历史执行记录、中文兼容层离线验证与真实模型运行；本文件不修改人工核验包状态。

## 1. 已有可回查材料

| 材料 | 实际来源 | 能说明什么 |
|---|---|---|
| 同机独立前端副本 | `work/competition/demo-clean-2026-10-05T09-45-29-146Z/replay-manifest.json`、`install.log`、`build.log` | 未复制node_modules、.env、后端、模型和旧产物；安装112个包，类型检查及构建成功；同机使用已有npm缓存，不是全新操作系统验收 |
| 桌面浏览器验收 | 既有执行记录及 `frontend/test-results/browser/.last-run.json`、`frontend/test-results/competition/` 截图 | 最近记录状态为passed；`.last-run.json`不含测试数量，不能从它推导通过项数；只展示冻结Demo，不证明真实模型运行 |
| 5题API草稿预跑 | `work/competition/runs/20261005T101847Z-pilot-c72cb679/` | HTTP 200，5条回答，64次逻辑调用，106,955 token；包含回答、指标、Trace、报告，旧run未用中文兼容层 |
| 准备目录 | `work/competition/runs/20261005T095709Z-pilot-736071b5/` | 状态为prepared；不能列成第二次已完成真实运行 |

本地 `work/` 和测试产物默认不入库；对外阅读入口为[5题真实预跑证据](evidence/20261005T101847Z-pilot-c72cb679/README.md)。旧目录原始内容保持不变，不以副本说明覆盖旧manifest。

## 2. 中文兼容层上一批离线验证

Ragas 0.1.21 的 `zh-sentence-v1` 仅修正中文及中英混排的句子输入，英文路径、公式、权重、Trace和Quality Gate保持。上一批实际执行的Python离线测试结果为 **291 passed**，这是当时适配批次的结果，不是本轮最终回归数量。

离线测试不调用提供方模型，不证明真实Judge判定正确或分数提升。旧5题低分、拒答和空Trace不重算；修复后5题真实验证另行归档，输入覆盖通过不等于裁判结论正确。

## 3. 本轮最终验收

验收日期：2026-10-05。归档工具批次先完成离线回归，随后按用户单独授权完成一次修复后5题草稿验证；本文件区分这两类结果，不改前端页面或人工核验包。

| 检查 | 本轮实际结果 |
|---|---|
| 完整Python离线回归 | `venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests`：**327 passed，28.06秒**；此项不调用提供方模型 |
| 修复后5题真实验证 | 新归档 `20261005T131353Z-pilot-35f623cb`：HTTP 200，5条回答，111,996 token、65次完整调用；15条中文句子抽取提示均非空；门禁FAIL |
| 受保护文件 | 归档工具批次前后178个既有文件SHA-256一致；修复后新run的40个源码／输入文件与manifest哈希一致，64项归档校验全部通过 |
| 旧run隔离 | 旧两个目录保持不变；修复后运行新建独立目录，没有覆盖旧结果 |
| 公开副本校验 | 修复前副本55个文件／54项外部校验；修复后分析副本70个文件／69项外部校验，全部一致，清单自身排除 |
| 文档链接 | 根README、docs全目录及相关说明共19份Markdown的本地链接检查通过，无缺失目标 |
| Git索引 | `git diff --cached --name-only`为空；没有暂存、commit或push |

离线测试覆盖：新schema／旧run拒绝、冻结工具源码、源文件与提示词版本、未知依赖、文件变更／删除／新增、指标缺失、零值、空／缺失／无效Trace、返回模型名与token缓存信息、截断日志和损坏配置的失败归档容错。回调观测、脱敏或文件哈希均不构成独立认证；修复后run记录了token，但没有账户账单，实际扣费仍未知。

### 本批修改范围

- 工具：[tools/competition/run_evidence.py](../../tools/competition/run_evidence.py)。仅新schema 2运行记录生效，旧归档拒绝补写；新manifest由外部checksums.json校验。
- 新增测试：[test_competition_archive_metadata.py](../../tests/test_competition_archive_metadata.py)、[test_competition_archive_results.py](../../tests/test_competition_archive_results.py)。已有测试不改。
- 同步文档：README.md、docs/README.md、docs/architecture/README.md、docs/competition/reproducibility.md、project-introduction.md、reviewer-questions.md、demo-script.md、docs/demo/README.md、docs/demo/legacy-evaluation-guide.md、docs/evaluation-credibility.md、frontend/src/fixtures/README.md、scenarios/competition_25/README.md；新增本文件。
- 新增公开证据目录：[旧5题阅读索引](evidence/20261005T101847Z-pilot-c72cb679/README.md)。原manifest、输入、源码和结果保持原字节；只在公开副本处理2条API路径及4处日志路径；原runner按旧hash事后补存，非原归档当时已有文件。

### 历史下一次小批真实验证前（已由后续正式运行收口）

1. 确认范围为原5题D06、C02、C05、N01、F03，单模型，使用zh-sentence-v1新目录；不自动扩到25题。
2. 正常模式要求25题作者确认完成。若作者仍只想测量待确认5题草稿消耗，需要再次明确授权本次草稿预跑；旧一次授权不代表本批已经执行。
3. 确认请求模型ID、无嵌入凭据的HTTPS地址、对应环境密钥、本地Embedding缓存、Trace开启和现有五项指标；阈值保持0.8。Embedding权重来源和远端不可变模型版本仍有未观测信息，应接受并披露此边界。
4. 作者明确一次付费调用授权及可接受预算。旧106,955 token仅是参考，新适配可能改变实际调用量；工具不提供token／费用到额自动停机能力。执行后仍需人工核对账单与结果，不能承诺必定PASS。

## 4. 未完成事项与解释边界

- 25题参考材料已由作者Kk于2026-10-05逐题确认；正式25题已在2026-10-05完成一次单模型运行，后续不自动扩展三模型。
- 原13条冻结Demo作者核验保持一致3、分歧5、无法确认5，不继承到新题。
- 门禁复核、冻结Demo模型复跑及独立第三方复核未因离线测试或新5题运行升级。
- 当前CLI／Gradio和API共享后端编排，新运行均受中文兼容层影响；旧legacy实现不是当前正常入口。
- 修复后5题FAIL仅由正向Faithfulness均值0.6000低于0.8触发；正向Context Precision为0.9792且4/4有效。Trace不参与门禁；两条空Trace均有成功调用返回，但不因此证明拒答正确。
- 5题运行使用同源生成与裁判，存在自评偏差。token观察不等于扣费账单，文件哈希不是认证。
- 当前构建记录保留725.95kB App chunk告警，不因构建成功宣称生产部署已完成。

详见[复现说明](reproducibility.md)及[可信度限制](../evaluation-credibility.md)。


## 5. 2026-10-06 正式25题结果收口

- 正式 run：`work/competition/runs/20261005T144504Z-full-39511424/`；脱敏公开副本：`evidence/20261005T144504Z-full-39511424/`。原始 run 未覆盖。
- 25/25回答完成，五项指标各25/25有效；Trace非空18、空7、缺失0。325次完整调用，558,958 token；请求 `deepseek-chat`，实际返回 `deepseek-flash`。
- Gate 保留 FAIL：正向 Faithfulness 0.7375 < 0.8；正向 Context Precision 0.9833 >= 0.8。Trace、人工意见和离线分析不直接触发门禁。
- 25题完整分析表标记“AI辅助分析、待人工确认”，未写入人工核验身份、日期或结论；旧13条仅对应旧Demo。
- 后端离线回归：`venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests`，327 passed（29.33秒），无模型调用；合成夹具断言已修复，未弱化断言。
- 前端：`npm test` 164/164，类型检查与构建通过；桌面浏览器回归18项通过（1920×1080、1280×800）。隔离无密钥副本可离线安装、测试和构建，无 `/api/evaluate` 调用；不能宣称异机复现或真实模型复跑。
- 截图：`work/competition/demo-clean-20261006/screenshots/`；日志：`work/competition/desktop-browser-regression-20261006.log`、`desktop-ui-regression-20261006.log`。

费用只有估算，没有账单，也没有费用自动封顶功能；本轮没有追加模型调用。

## 6. 2026-10-12 提交前收口

- 本机完整回归（Windows）：Python `venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests` **450 passed**；前端 `npm test` **174 passed**；浏览器回归 `npx playwright test` **48 passed**。均不调用模型。
- CI：2026-10-12 新增 `.gitattributes`，按记录逐文件固定检出换行符后，提交 `c66de7b` 的 CI（run 38249233917）Python 与前端两个任务均通过；此前 Linux 检出后换行符不同导致完整性哈希校验失败。本节提交推送后的 CI 结果以 GitHub Actions 记录为准。
- 门禁可信度四问：新增 [gate-credibility.md](gate-credibility.md)。其中 ④ 由 `tools/competition/gate_uncertainty.py` 离线计算，正向忠实度按题重抽 95% 区间为 55.0%–90.0%，跨过 0.8 门槛；门禁规则与已记录的 FAIL 不变。
- 裁判证据敏感性实验：作者 Kk 于 2026-10-12 确认 30 项材料并单独授权，运行一次，60 / 60 个请求有返回；59 个有效判定全部符合预期，1 条解析失败如实保留。按返回用量估算约 ¥0.13，不是账单。档案见 [judge-sensitivity/run-20261012](judge-sensitivity/run-20261012/manifest.json)。
- 5 题预跑的 `public-manifest.json` 记录时为混合换行，改为按原始字节存储，存储内容与记录的 SHA-256 一致。
- 冻结演示快照、25 题正式运行证据、人工核验包、题集、参考答案与门禁阈值均未修改。

## 7. 2026-10-13 发布收口验证

- 校准分析修正：未决标签保留每题全部陈述分母，只报告点估计上下界；端点区间不能保证包住混合赋值的区间，故有未决时 `interval`、`state` 置为 `null` 并写明原因。新增混合赋值反例与文本输出测试；无未决时保留既有计算，原 Quality Gate 不受影响。
- 本机后端离线回归：`venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests`，**620 passed**（80.55 秒）。前端 `npm test` **195 passed**；`npm run build` 包含类型检查并成功完成，App chunk 922.25 kB 的体积告警如实保留。`compileall` 与 `git diff --check` 通过。
- `build_audit_fixture.py --check`、`build_robustness_fixture.py --check`、`judge_calibration.py prepare --check` 均一致，不重新生成或覆盖冻结证据。
- 现有桌面浏览器回归 `competition-release.spec.ts` **2 passed**（1920×1080、1280×800）。另用 Playwright／Chrome 检查门禁审计与人工标注两页的四种页面／尺寸组合：无页面脚本异常、无主页面横向溢出、无 `/api/evaluate` 请求。四张截图保存在本机 `frontend/test-results/release-closeout/`，不纳入公开仓库。
- 修正前后 **208 个受保护文件 SHA-256 不变**，包括冻结 Demo、原作者核验包、正式题集、参考材料、门禁、Trace 与中文适配代码，以及既有实验归档。工作区九份清单的 188 项现有文件哈希一致。
- 工作区文件齐全不等于公开克隆齐全：三个历史证据目录有 17 项被忽略的产物不进入仓库，缺项和校验方式见 [公开检出校验边界](reproducibility.md#公开检出校验边界)。不改历史校验清单，不把 `--ignore-missing` 的通过称为完整归档通过。
- 原始 CRLF 归档在 `.gitattributes` 中保留 `-text`，并明确 `cr-at-eol`；保留行尾多余空白、空白末行和缩进空格的检查，避免暂存检查把合法回车逐行误报。归档本身的字节和哈希不变。
- 以上测试在本机 Windows 环境完成；本批提交的真实 Linux CI、CodeQL、变异测试与 Pages 构建，以推送后的 GitHub Actions 记录为准，未提前写成通过。
- 人工标注仍待完成：52 条材料没有填入模型生成的人工标签，也未将作者／AI 既有分析升级为非作者校准结果。本轮没有新增付费调用。
