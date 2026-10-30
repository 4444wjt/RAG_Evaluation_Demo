# 外部使用记录 · O 本人电脑（2026-10-29）

同一操作者 O 继 [10-27 远程使用作者电脑](../external-repro-20261027/README.md)后，在本人 Windows 电脑用固定 r2 源码工具包完成 **1 项自选法规规则验收**：以《缔结条约管理办法》拟写 3 条构造陈述，发出 3 次请求，原退出码 **0/0/1**。下载、整理、拟写、运行与分析均由 O 本人完成，据 O 回传及用户补充确认。

## 输入与逐条结果

- 资料：[保存的 gov.cn HTML 原件](原件/缔结条约管理办法-gov.cn-原件.html)与[派生文本](原件/缔结条约管理办法-source.txt)，正文第一至三十六条、96 段；维护者逐段核对 96/96 一致。
- 工具：r2 源码包，固定源文件来自 `936f3a9`；三份 `hash-001.json` 均为 `ready: true`，记录的 7 个源文件 expected/actual 一致。回传环境为 Windows、Python 3.13.14。
- 请求：`engine=base`，`contexts=[source 全文]`，`question=""`。各目录的 `bindings-001.json` 绑定 source、statement、request 与 hash 检查文件。

| 陈述 | O 的构造意图与原文参照 | 原结果／退出码 | 原件入口 |
|---|---|---|---|
| 001 | 正确对照：第九条，启动谈判前不少于 20 个工作日 | `no_error_found`／0，未报错 | [陈述](okit-001/task/statement.txt) · [请求](okit-001/task/request-001.json) · [输出](okit-001/task/output-001.json) |
| 002 | 混写第十二条的条件、主体与 10 个工作日，以及第十三条征求司法部意见的 30 个工作日 | `no_error_found`／0，**预设引用偏离未发现** | [陈述](okit-002/task/statement.txt) · [请求](okit-002/task/request-001.json) · [输出](okit-002/task/output-001.json) |
| 003 | 第七条“可以”改成“应当” | `findings`／1，检出 `MODAL_SWAP` 情态差异 | [陈述](okit-003/task/statement.txt) · [请求](okit-003/task/request-001.json) · [输出](okit-003/task/output-001.json) |

按 O 的构造引用忠实性预期：正确对照报错 0/1；预设引用偏离发现 1/2、未发现 1/2。反馈关注数额是否按所引条款核对，维护者的说明回应见[确认与反馈摘要](confirmation-feedback.md)。原请求和结果保留。

这是本人异机使用记录，与复现旧 25 题结果、真人节时或跨平台全功能验收分别记录。

## 公开子集与校验

本目录收录回传 27 份原件中的 **23 份原字节子集**：`原件/` 两份，加三个 `okit-*/task/` 各 7 份（source、statement、request、output、bindings、hash、stderr）。原反馈表和三份 run 清单未随本目录公开；确认与反馈为维护者整理摘要。

[SHA256SUMS.txt](SHA256SUMS.txt) 列出这 23 份原件及两份说明，路径均相对此目录。哈希校验绑定文件字节，身份、电脑归属和许可来自 O 的申报与用户确认。

```bash
cd docs/competition/external-use-20261029
sha256sum -c SHA256SUMS.txt
```
