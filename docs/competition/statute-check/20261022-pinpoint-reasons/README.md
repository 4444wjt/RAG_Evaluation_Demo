# 裁判漏检理由的离线文本分类

对 `../20261021-pinpoint-run/run/` 已归档裁判理由做离线文本分类，不发起模型请求。输入为该目录的 `package.json` 和 `records.json`，SHA-256 见 `analysis/reasons.json`；分析前先校验运行目录的完整校验清单。

## 分类口径

1. 只取 package.json 中 type 为 paragraph_existing 或 paragraph_absent、expected 为 not_supported 的材料；records.json 中 status == ok 且 result.verdict == supported 的记录计为漏检。original 与 pinpoint 分别报告，对外主张只引用 original。
2. 用正则 第[一二三四五六七八九十]+款 抽取款号集合；stated 来自 statement，said 来自 result.reason；不匹配单个字符「款」。
3. 分类优先级：said 为空为 no_paragraph_in_reason；否则 stated 与 said 有交集为 repeats_stated_paragraph；否则为 names_other_paragraph。
4. namesCorrectParagraph 仅在 names_other_paragraph 且 said 等于 {第<cn_label(sourceLocation.paragraph)>款} 时为 true；saidContainsCorrectParagraph 记录 said 是否含正确款号，包含 repeats_stated_paragraph 的情况，不改变分类。
5. hasArticleBoundParagraph 用正则 第[一二三四五六七八九十]+条第[一二三四五六七八九十]+款 抽取连续写法，检查其中是否含 sourceLocation 的正确条号与正确款号绑定的写法，不改变分类。

逐条漏检见 `analysis/reasons.json` 的 `misses`；用 `itemId + judge + prompt` 在输入 `records.json` 中定位 `result.reason`，不复制理由全文。

## 结果（次数）

数字均读取本归档 `analysis/reasons.json`。对外主张只引用 original；pinpoint 单列。

### original

漏检共 60 次。

| 类别 | 合计 | DeepSeek | Qwen2.5-72B |
|---|---:|---:|---:|
| 理由无「第X款」 | 32 | 22 | 10 |
| 复述陈述中的错误款号 | 20 | 6 | 14 |
| 写出正确款号仍判支持 | 8 | 2 | 6 |

其中 names_other_paragraph 共 8 次，namesCorrectParagraph 为 true 的有 8 次，hasArticleBoundParagraph 为 true 的有 8 次。

### pinpoint

漏检共 39 次。

| 类别 | 合计 | DeepSeek | Qwen2.5-72B |
|---|---:|---:|---:|
| 理由无「第X款」 | 10 | 0 | 10 |
| 复述陈述中的错误款号 | 21 | 9 | 12 |
| 写出正确款号仍判支持 | 8 | 0 | 8 |

其中 names_other_paragraph 共 8 次，namesCorrectParagraph 为 true 的有 8 次，hasArticleBoundParagraph 为 true 的有 8 次。

### original 的 names_other_paragraph 明细

| itemId | 裁判 | namesCorrectParagraph | hasArticleBoundParagraph |
|---|---|---|---|
| paragraph_existing-S01-P4 | DeepSeek | true | true |
| paragraph_existing-S50-P1 | DeepSeek | true | true |
| paragraph_absent-S07 | Qwen2.5-72B | true | true |
| paragraph_absent-S13 | Qwen2.5-72B | true | true |
| paragraph_absent-S49 | Qwen2.5-72B | true | true |
| paragraph_existing-S07-P3 | Qwen2.5-72B | true | true |
| paragraph_existing-S13-P2 | Qwen2.5-72B | true | true |
| paragraph_existing-S49-P2 | Qwen2.5-72B | true | true |

## 复算与校验

在仓库根目录运行（输出目录须为新的空目录）：

```sh
venv/Scripts/python.exe -B tools/competition/pinpoint_reasons.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/pinpoint-reasons-replay/a
venv/Scripts/python.exe -B tools/competition/pinpoint_reasons.py analyze --run docs/competition/statute-check/20261021-pinpoint-run/run --output work/pinpoint-reasons-replay/b
for file in reasons.json SHA256SUMS.txt; do
  cmp "work/pinpoint-reasons-replay/a/$file" "work/pinpoint-reasons-replay/b/$file"
  cmp "work/pinpoint-reasons-replay/a/$file" "docs/competition/statute-check/20261022-pinpoint-reasons/analysis/$file"
done
(cd docs/competition/statute-check/20261022-pinpoint-reasons && sha256sum -c SHA256SUMS.txt)
(cd docs/competition/statute-check/20261022-pinpoint-reasons/analysis && sha256sum -c SHA256SUMS.txt)
mkdir -p work/pinpoint-reasons-replay/pytest
venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests --basetemp work/pinpoint-reasons-replay/pytest/t
```

顶层校验清单覆盖 README 与 analysis 下所有文件（包括其 SHA256SUMS.txt），不含顶层清单自身；全部文件使用 UTF-8、LF。

## 边界

这是对理由文字的表层匹配，不代表裁判的内部推理；「写出正确款号」只说明理由文本里出现了正确款号。namesCorrectParagraph 只检查款号集合，不要求条号绑定；绑定字段另报，不改变分类。理由同时含陈述款号与正确款号时仍优先归入复述类。

材料是定向合成扰动、单一法规节选，多条变体共用底稿，不是独立样本；这里只分析单次运行。分类口径是在看过这批理由之后制定的（事后），没有预注册。
