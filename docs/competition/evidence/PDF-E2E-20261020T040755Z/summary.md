# PDF 端到端阶段 1：解析 → 入库检查 → 切块

运行时间：2026-10-20T04:07:55Z（UTC）。不调用任何模型。环境为项目 `venv`，pypdf 6.19.0，parse_check 1.0.0，切块参数 chunk_size=200、chunk_overlap=30。

| 样本 | 页数 | 字符数 | 汉字数 | 代理码点 | parse_check | 是否入库 | 块数 |
|---|---:|---:|---:|---:|---|---|---:|
| A（正常） | 6 | 4,139 | 2,468 | 0 | PASS | 是 | 26 |
| B（病态） | 1 | 631 | 37 | 0 | FAIL（第 1 页 GARBLED_TEXT_LAYER） | 否 | —（未切块） |

## 样本

| 代号 | 文件 | SHA-256 | 字节数 |
|---|---|---|---:|
| A | project-introduction.pdf | `fa8f8923bde06cfb3c682e004ebd12fd14ce53cff72a9365e1fff740e92f1a4f` | 543,576 |
| B | cjk_no_tounicode.pdf（构造样本，中文字体缺 ToUnicode） | `b312476886dfe7976b4761a44896f1a44e8a780477bc6f6d0ad06ccd1d1b92be` | 3,756 |

## B 为什么被拦

parse_check 第 1 页的实际返回（全文见 `raw/B-parse_check.json`）：

- 字符 631 个，其中汉字 37 个；
- C0 控制字符 109 个、C1 控制字符 68 个，控制字符合计 180 个；
- 控制字符占比 0.2853，超过阈值 0.05；
- 结论 `FAIL`，原因 `GARBLED_TEXT_LAYER`。

`backend/core/pipeline.py` 遇到 FAIL 会抛出 `RuntimeError` 并阻止入库；REVIEW 只告警，不阻止。本次的「是否入库」按这条规则判定。B 没有切块；A 正常切块。

## A 的切块

26 块，每块字符数最少 6、中位数 182.5、最多 199。前两块开头见 `run-A.json` 的 `split.first_two_head80`。

## 关于代理码点

B 在 pypdf 6.19.0 下没有代理码点，乱码表现为 C1 控制字符（68 个）。任务说明里写的「114 个汉字 / 31 个代理码点」是 pypdf 6.21.1 的结果；项目 `requirements.txt` 钉住的是 6.19.0，本次未改依赖、未打补丁。

## 没有验证到的

- DOCX、扫描件（REVIEW 分支）、layout 模式。
- 阶段 1 的「是否入库」按 pipeline.py 的判定规则复现，没有实际调用 `pipeline.evaluate`。真实入库与评测在阶段 2。
- 只用了两份样本，不能代表检查器在其它文档上的准确率。
