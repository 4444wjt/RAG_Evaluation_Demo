# 父条资料准备

给定完整父条原文、全部段快照和一次检索命中，把命中段补全为父条，供你检查后使用。只做资料准备，不调用模型、不核验回答、不改变质量门禁。

本地工作台入口：`#/lab/parent-context`（资料准备 → 父条资料准备）。也可使用仅需 Python 标准库的命令行及本地 API。

## 先跑构造样例

在仓库根目录运行，输出目录必须尚不存在：

```powershell
python -B scripts/parent_context.py --source examples/parent_context/source.txt --segments examples/parent_context/segments.json --retrieval examples/parent_context/retrieval.json --binding examples/parent_context/binding.json --out work/parent-context-example
```

输出 `context.txt` 和 `report.json`。退出0和`PREPARED_FOR_REVIEW`只表示资料已准备、等待操作者复核，不是PASS。失败退出2、状态`REVIEW`，没有部分`context.txt`。同一输出目录拒绝覆盖。

## 用自己的资料

准备四份 UTF-8 文件；原始字节参与哈希，不要在登记后重新排版。

| 文件 | 内容 |
|---|---|
| source.txt | 每个非空物理行完整对应一个段；条首写“第X条＋空格＋正文”，完整条之间空一行。标题/来源单独成块；不自动修复PDF/OCR、折行、缺条或摘要 |
| segments.json | `page:1,total_pages:1,total:N,data:[...]`。每段有`id,position,document_id,content,enabled:true,status:"completed"`；position必须从1连续覆盖原文全部非空行；id稳定且唯一。QA/summary非空不接受 |
| retrieval.json | `CitationResourcesV1`，仅一个`records`记录，含真实`message_id,conversation_id,resources`；每个资源保留`segment_id,document_id,dataset_id,content`。资源position是召回排名，不是原文段号 |
| binding.json | `ParentContextBindingV1`，绑定原文/段表SHA、document/dataset身份、独立元数据行位置，以及操作者明确声明的完整父条边界 |

真实材料必须使用对应真实段表和命中，不能用构造ID冒充实际检索。构造样例可用于学习格式，不构成真实接入证据。

核对原文与全部段表之后，可创建绑定文件：

```powershell
python -B scripts/parent_context.py bind --source source.txt --segments segments.json --document-id YOUR_DOCUMENT_ID --dataset-id YOUR_DATASET_ID --metadata-positions 1 --assert-complete --out binding.json
```

`--metadata-positions`列出标题/来源等独立段的原文位置，用逗号分隔；没有这类行时省略。`--assert-complete`是你的声明，不是程序证明。将四份文件导入本地页面，或用第一条命令换成自己的路径即可准备资料。

本地 API 为 `POST /contexts/parent/prepare`，请求格式见下方 Schema 的 `request` 定义；经前端代理时路径为 `/api/contexts/parent/prepare`。四文件以原始字节的 base64 提交，总大小上限8 MiB，默认资料上限24000 UTF-8字节；超过上限整包待复核，不截断。服务在内存中计算，不保存上传资料，不请求评测接口、不读取密钥。公开只读模式拒绝该请求，请本地运行。

## 人工复核与下载

检查预览中的原文、段位置和诊断，并对照独立来源核对父条完整性。页面中填写复核人、选择“可用于后续处理”并勾选已核对来源、父条范围和预览后，才能下载资料。选择“仍需核查”须说明原因；待复核结果没有资料下载，只能下载报告和未完成复核记录。复核表仅保存在本机，不上传。

更换文件读取期间，准备与三种下载均暂停；新文件读取成功后，旧结果和复核清空。读取失败且未采纳新文件时，保留原输入及其结果，读取结束后恢复操作。CLI 仅在准备成功时写入资料；待复核时只写报告。补正输入后使用新的输出目录，不覆盖旧记录。

身份是操作者自述，记录不自动成为真人标签或独立使用证明。`gate_result`始终为空；不把`REVIEW`映射PASS，不自动触发下游模型或发布。

## 可证明的边界

同父条去重、条内按原文顺序，父条之间按首次命中顺序；明确保留独立标题/来源段。已映射段和未知段混合时整包待复核。不能证明操作者提供的原文在法律全文中完整、当前线上索引未变化、供应商实际收到同一请求，或下游不会裁剪。

尤其是“原文和段表同时漏掉同一款”仍可能机械匹配。哈希只绑定字节，不能发现这种共同缺失；独立官方原文核对仍由操作者完成。

格式规范见[`schemas/parent-context.v1.schema.json`](../../schemas/parent-context.v1.schema.json)；人为构造样例见[`examples/parent_context/`](../../examples/parent_context/README.md)。本地 API 与CLI共用同一Python准备实现。
