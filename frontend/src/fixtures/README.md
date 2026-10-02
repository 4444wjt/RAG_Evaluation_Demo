# Phase 5 离线演示快照

`demo.json` 是已有评测产物的冻结数据转换。没有调用模型 API、运行评测、重算指标、改变门禁或补写逐句证据。

## 来源与同次运行核验

- 项目根目录：`D:/RAG_Evaluation_Demo`。
- 原记录：`eval_history.json` 的第 99 条（0-based index `98`），version `20260930-1958`，timestamp `2026-09-30 19:58:34`。
- 原文档名：`demo.txt`；测试集来源：`自动生成`。
- `multi_model_summary.csv` 三行全部 20 个字段在数值/空值转换后与该历史记录 `models` 完全一致；历史中只有这一条匹配。
- `ragas_report.html` 的生成时间与历史 timestamp 完全一致；其摘要表、逐句追踪统计表与上述 summary 显示值一致。
- 三份用例 CSV 的 question、ground_truth、contexts 和 case_type 逐行一致。每模型 6 条，原构成为 3 条 positive 和 3 条 negative。
- HTML 内嵌 PNG 与根目录 `result_chart.png` 字节完全一致。
- 用例 CSV 未存独立 run ID，故该关联是产物一致性核验，不宣称存在独立 CSV run 标识。源文件修改时间记录于 provenance，仅作辅助说明。
- 旧 `scenarios/deposit_insurance/e2e_20260928_143401` 档案没有当前逐句 trace，未混入本快照。

## API 字段和缺失语义

`result` 固定含 8 个 API 字段：summary、summary_df、gate_passed、gate_message、chart_path、html_path、model_dfs、total_elapsed_seconds。`provenance` 另外标注来源、文件哈希、字段来源和限制。

- summary 直接取原历史 models；summary_df 取匹配的 summary CSV，内容相等。
- model_dfs 的键保持完整原模型名；记录保持 CSV 原列和行序。
- 仅将指标列的 CSV 数值转换为 JSON number，空白/NaN 转为 null；0 与 null 严格区分。
- question、answer、ground_truth、contexts、case_type、hallucination_trace 文本保持原值。contexts 仍是 CSV 原字符串；hallucination_trace 仍是 JSON 文本字符串，内含原 statements、verdict 和 evidence，不新增、不改写。
- gate_passed 原值为 true；total_elapsed_seconds 原值为 518.73。门禁不根据阈值重算。
- 原历史和 HTML **未保存 gate_message**。为满足 API 字符串形状，该字段是空字符串；provenance.missing_fields 记录此缺失。界面应显示“原始门禁说明未记录”，不生成或推导门禁原因。
- chart_path 与 html_path 指向根目录现有本地产物。路径是共享当前产物，后续评测可能覆盖，不是冻结快照链接；离线快照数据本身仍保留在 demo.json。

## SHA-256 来源记录

以下哈希对源文件完整字节计算。files 中的路径均相对项目根目录。源文件未修改。

| 源文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `multi_model_summary.csv` | 682 | `e3690316ce8997a30691fed11a86a8ba6e749485261a7045404840fcb7c01db0` |
| `ragas_report_deepseek-chat.csv` | 12546 | `499640e1e2aa363644b5e7c05df32b765f210793311f51d33298ebdd04c25623` |
| `ragas_report_Pro_Qwen_Qwen2.5-7B-Instruct.csv` | 12870 | `57b205ff48fc1245369666ba0cecfea8f27d0a3d78f8e3d1bd334d9b0abcceb7` |
| `ragas_report_glm-5.2-fast-preview.csv` | 13346 | `bf7c8bdf3bb49e19189ef3a83dd8280dafb2ead581bf7613c6d4017cf013b99a` |
| `ragas_report.html` | 112369 | `4dca7efd4af06b418f4ff1c227049dae10a66100cc8b455a6877ba16d1283aa3` |
| `eval_history.json` | 170374 | `4e8904fd656949be8d8ec2c941fc86d911984629a18eff551a0dff0b61333f1b` |
| `result_chart.png` | 80253 | `5ef7a3641eaa915a11126917b1e8c984f5a3724332913de55d9065896aee7946` |

生成的 `demo.json` SHA-256：`368e0e9bcda159c5ab3cfb2e9f59eaa03e431c5278bff0ee1a17c13e53a69946`。

## 转换与验证

使用项目现有 `venv/Scripts/python.exe`（Python 3.11.9）与标准库 csv/json/hashlib/html.parser/base64；不创建环境，不安装依赖。转换前核验 source 一致性，写出 UTF-8 JSON 且拒绝 NaN/Infinity。转换后再次核验 8 字段、summary/history 精确相等、18 条用例、零值/空值、trace 文本及源哈希。

本目录只保存演示 fixture 与说明，不改 backend、main.py、API 结构、现有测试或原产物；不提交、不推送。
