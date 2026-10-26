# PDF-E2E-20261020T040755Z

本目录记录两份 PDF 走完「解析 → 入库检查 → 切块」的结果，不调用任何模型。

**结论：** 正常中文 PDF（A）检查结果为 PASS，切成 26 块；中文字体缺 ToUnicode 的构造样本（B）检查结果为 FAIL，被阻止入库。

| 文件 | 内容 |
|---|---|
| `summary.md` | 对比表与结论 |
| `run-A.json`、`run-B.json` | 每份样本的解析、检查、切块结果 |
| `raw/*-parse_check.json` | parse_check 的完整返回 |
| `raw/*-extracted.txt` | PyPDFLoader 提取的原始文本 |
| `raw/*-stderr.log`、`raw/logging.log` | 运行时的告警输出（本次均为空） |
| `raw/pdf_e2e_stage1.py` | 本次使用的脚本 |

复现命令（在仓库根目录执行）：

```bash
venv/Scripts/python.exe docs/competition/evidence/PDF-E2E-20261020T040755Z/raw/pdf_e2e_stage1.py . <输出目录> A=<A.pdf> B=<B.pdf>
```

阶段 2（对 A 的完整评测）另行记录。
