# 款项定位：已确认材料与规则离线结果

本归档包含定向合成的款、项定位材料 66 条。作者 Ll 于 2026-10-21 确认全部预期标签；逐条记录与确认说明保存在 `package.json` 和 `RUN_CONFIRMATION.md`。这些是材料准备与规则离线结果，两个通用裁判对这批材料尚未运行。未授权模型请求，不参与 Quality Gate，不改变历史 FAIL。

## 文件

以下七个文件从已确认材料逐字节复制，均为 UTF-8、LF；没有复制授权草稿。

| 文件 | 内容 |
|---|---|
| `package.json` | 66 条陈述、问题、原检索片段、预期、来源哈希与作者确认 |
| `rules.json` | 逐条规则结果、error 代码、发现与规则源码哈希 |
| `RUN_CONFIRMATION.md` | 作者确认说明全文、逐条材料与固定分析口径；其中拟定请求和费用不是执行记录 |
| `prompt.original.txt` | 固定陈述核查提示词 |
| `prompt.pinpoint.txt` | 只在原提示末尾增加定位核对指令的提示词 |
| `prompts.diff` | 两份提示词的完整差异 |
| `cost-estimate.json` | 按历史用量折算的未来费用估算，非账单、非费用上限 |

另有本说明 `README.md` 与 `SHA256SUMS.txt`；清单覆盖本目录除清单自身外的全部文件。所有文件在根 `.gitattributes` 中有逐文件 LF 规则。`authorization.draft.json` 未收录：它未获授权，且绑定另一项工作维护的准备工具哈希，不能当作执行许可。

## 生成与逐字节复现

工具为 [`statute_pinpoint.py`](../../../../tools/competition/statute_pinpoint.py)，规则为 [`statute_check.py`](../../../../backend/core/statute_check.py)。在仓库根目录、使用带有所需依赖的 Python 执行；三个输出目录必须是新的空目录。以下命令仅离线准备材料，不调用模型。

```bash
python -B tools/competition/statute_pinpoint.py prepare --output work/pinpoint-reproduce/prepared
```

从本归档的 `package.json` 取出 `review.note`，按原样 UTF-8 写入确认说明文件，不重新改写作者原话：

```python
import json
from pathlib import Path

archive = Path("docs/competition/statute-check/20261021-pinpoint-prep")
review = json.loads((archive / "package.json").read_text(encoding="utf-8"))["review"]
assert review["reviewer"] == "Ll" and review["reviewedAt"] == "2026-10-21"
Path("work/pinpoint-reproduce/confirmation.txt").write_bytes(review["note"].encode("utf-8"))
```

```bash
python -B tools/competition/statute_pinpoint.py confirm --package work/pinpoint-reproduce/prepared/package.json --reviewer Ll --date 2026-10-21 --note-file work/pinpoint-reproduce/confirmation.txt --output work/pinpoint-reproduce/confirmed
```

将确认输出中的上述七个文件与本归档按字节比较。在材料所绑定的来源、规则与生成逻辑一致时，除不公开的授权草稿外，这七个文件可逐字节复现。工具输出的清单还包含授权草稿，本公开归档的清单按公开文件集合重新生成；本说明与公开清单不属于工具生成的七个文件。

## 结果与 S27-P1 漏检

仅 error 级发现算检出。按 `package.json` 的 `type` 分组，再读取 `rules.json` 中同 ID 的 `flagged`：

| 类型 | 条数 | error 检出条数 |
|---|---:|---:|
| `paragraph_existing` | 18 | 17 |
| `paragraph_absent` | 13 | 13 |
| `item_existing` | 3 | 0 |
| `item_absent` | 1 | 1 |
| `control` | 14 | 0 |
| `control_pinpoint` | 13 | 0 |
| `paragraph_omitted` | 4 | 0 |

`paragraph_existing-S27-P1` 所引为第十九条第一款，内容出自第二款。规则先删除句中条款引用和填充词，再计算字符二元组集合，共 32 个。第一款（含其各项）覆盖 12 个，覆盖度 `0.375`；第二款覆盖 31 个，覆盖度 `0.96875`；差值 `0.59375`。第二款满足最小覆盖度 `0.6`，与第一款的差值也满足 `0.25`，但原文「前款」使 `_mentions` 返回 `{(19, 1)}`，触发交叉引用豁免，不产生 `MISATTRIBUTED`。归档的 `flagged` 为假，error 代码为空。

款级核对内容归属，项级只核对编号存在性，没有按项内容归属比较，所以同款另一项存在但内容不符的 3 条全部漏检。未修改规则。

以下代码在仓库根目录执行，直接调用原规则复算全部 66 条，并打印 S27-P1 各款覆盖度和豁免集合；不需要模型或网络：

```python
import hashlib
import json
from collections import Counter
from pathlib import Path
from backend.core import statute_check as sc

archive = Path("docs/competition/statute-check/20261021-pinpoint-prep")
package = json.loads((archive / "package.json").read_text(encoding="utf-8"))
rules = json.loads((archive / "rules.json").read_text(encoding="utf-8"))
for name, digest in {**package["sourceFiles"], rules["checker"]: rules["checkerSha256"]}.items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
source = Path("scenarios/deposit_insurance.txt").read_text(encoding="utf-8")
counts, flagged = Counter(), Counter()
for item in package["items"]:
    result = sc.check_answer(item["statement"], item["contexts"], source, item["question"])
    result["codes"] = sorted({f["code"] for f in result["findings"] if f["severity"] == "error"})
    assert result == rules["items"][item["id"]], item["id"]
    counts[item["type"]] += 1
    flagged[item["type"]] += result["flagged"]
for kind in package["types"]:
    print(kind, counts[kind], flagged[kind])
item = next(i for i in package["items"] if i["id"] == "paragraph_existing-S27-P1")
sentence = next(sc.SENTENCE.finditer(sc.normalize(item["statement"]))).group()
claim = sc._bigrams(sc.REF.sub("", sentence))
provisions = sc.parse_provisions(source)
by_paragraph = {}
for n in sorted({p.paragraph for p in provisions if p.article == 19}):
    texts = [p.text for p in provisions if p.article == 19 and p.paragraph == n]
    pool = set().union(*(sc._bigrams(t) for t in texts))
    by_paragraph[n] = sc._coverage(claim, texts)
    print(n, len(claim & pool), len(claim), by_paragraph[n])
near = max(by_paragraph, key=by_paragraph.get)
mentioned = set().union(*(sc._mentions(p, provisions) for p in provisions
                          if p.article == 19 and p.paragraph == near))
print("near", near, "mentioned", mentioned)
print("ATTRIBUTION_MIN", sc.ATTRIBUTION_MIN, "ATTRIBUTION_GAP", sc.ATTRIBUTION_GAP)
print("gap", by_paragraph[near] - by_paragraph[1], "not_in_mentioned", (19, 1) not in mentioned)
```

## 边界

- 定向合成扰动、单一法规节选、多条变体共享底稿，只报条数，不能推广总体效果。
- 项级只有 S26 一份底稿。其历史三人标注 V/A 为「支持」、B 为「无法判断」，作者此次确认不替代或抹去历史分歧。固定分析口径还要求未来运行报告剔除 S26 全部变体后的敏感性条数。
- 规则读取完整法规节选，裁判的拟定输入仅为问题、陈述与原检索片段，信息条件不同。
- 两个通用裁判的对比尚未运行，不预测其结果。提示词、估算费用和拟定请求数均非运行证据；未来运行器与执行授权须另行审核。
- 作者确认的是预期标签；本次机制解释由 AI 分析，不冒充作者对解释的逐条确认。

## 完整性校验

```bash
cd docs/competition/statute-check/20261021-pinpoint-prep
sha256sum -c SHA256SUMS.txt
```
