# 本机无外网试跑：Ollama Qwen3.5-2B（2026-10-14）

检验一件事：只用本机模型、本地 Embedding 和本地检索库时，后端完成一整轮评测，会不会向外网发起连接。

**结论：** 3 题完整评测跑通（HTTP 200，约 22 分钟）。后端进程**没有尝试任何外网连接**：拦截记录为 0 条。

这次只证明链路能在没有外网的条件下跑通。2B 小模型的分数和逐句核对**不能用来评价**，原因见下文。

## 做法

- **拦截外网**：后端由 [`tools/competition/offline_run.py`](../../../../tools/competition/offline_run.py) `serve` 启动。
  - 启动前在进程内给 socket 加守卫，只放行回环地址（`127.0.0.0/8`、`::1`、`localhost`）。
  - 连接外部地址、解析外部主机名、发 UDP 一律拒绝，并逐条记录到 `blocked-connections.jsonl`。
  - 守卫在 API 和评测引擎导入之前安装，所以 Embedding、Chroma、Ragas、LangChain 的任何外部连接都会被记录下来。
- **模型**：Ollama 0.35.1 上的 `qwen3.5:2b`，2.3B 参数，Q8_0 量化，digest 前 12 位 `324d162be6ca`，纯 CPU 运行。
  - 平台的模型名不允许冒号，先用 `ollama cp qwen3.5:2b qwen3.5-2b` 复制了一个不带冒号的名称。
- **关闭思考**：这个模型默认先输出一大段思考。实测一次简短调用约 2 分钟，接近平台 180 秒的超时。
  - `/no_think` 前缀无效；它的模板由 Ollama 内置的 renderer 生成，无法通过 Modelfile 修改。
  - 因此由同一工具的 `gateway` 在本机 `127.0.0.1:11435` 转发请求，只补上 `"reasoning_effort": "none"`，单次调用降到约 6 秒。
  - 这相当于「内部网关」式接入，**平台代码没有改动**。
- **接入方式**：全部走真实接口，与用户在工作台的操作相同：
  1. `PUT /models/local` 登记模型，密钥变量为 `VERISCOPE_OLLAMA_API_KEY`（Ollama 不校验密钥，值是占位符）；
  2. `POST /inputs` 导入测试集；
  3. `POST /evaluate` 只选这个模型，使用自定义测试集，不走自动出题（自动出题会调用内置 DeepSeek）。
- **题目与资料**：从 [25 题正式运行的题集](../../../../scenarios/competition_25/testset.json) 取第 1、6、16 题（2 道正向、1 道负向），资料为 `scenarios/deposit_insurance.txt`。

## 结果

| 项目 | 结果 |
|---|---|
| 接口返回 | 200，请求耗时 1339 秒 |
| 外网连接尝试 | **0**（`blocked-connections.jsonl` 为空） |
| Embedding | 从本机 Hugging Face 缓存加载，没有联网 |
| 文档切分 / 向量库 | 12 个块，本地 Chroma，Top-K=4 |
| Ragas 五项指标 | 都有输出；答案相关性两道正向题都是 0 |
| 逐句核对 | 模型输出了合法 JSON，但 6 条陈述的证据都没能在检索原文中逐字找到，全部被过滤（`evidence_not_in_context`），每题记为 `filtered_all` |
| 负向题 | 正确拒答（拒答准确率 1.0） |
| 门禁 | FAIL（正向忠实度 0.75 < 0.8）；只是这次试跑的输出，不作为对该模型的评价 |

逐题回答、指标、逐句核对的原始输出见 `api-response.json`，运行参数见 `run.json`。

## 说明了什么

1. **评测链路可以在没有外网的条件下跑通**：资料导入、切分、本地 Embedding、本地向量检索、调用本机模型、Ragas 打分、逐句核对、门禁、报告生成，整个过程后端进程没有向外部地址发起任何连接。
2. **模型登记功能在真实接口上可用**：登记后的模型可以被选中参评，密钥只从环境变量读取。
3. **逐句核对需要能逐字引用原文的模型**。这个 2B 模型改写了原文，平台的证据校验没有把这些改写当作证据，判定被全部过滤。这样处理是保守的：宁可没有判定，也不接受无法回查的证据。用小模型做裁判时，应改用更大的本机模型，或另外指定一个裁判。

## 不能说明什么

- **这是进程级拦截，不是物理断网。** 这次没有拔掉网线、没有在隔离网段里运行；Ollama 等其他进程不在守卫范围内（Ollama 本身只在本机回环地址上服务）。
- 安装依赖（pip / npm）、下载 Embedding 模型、构建 Docker 镜像都需要网络或镜像源，不在本次范围内。
- 只有 3 题、一个 2B 模型、一次运行，分数没有参考意义。
- 网关关闭了模型的思考，这个模型在默认（带思考）设置下能否在超时内完成评测，没有验证。

## 复现

```bash
ollama cp qwen3.5:2b qwen3.5-2b
# .env 中加入：VERISCOPE_OLLAMA_API_KEY=ollama
python tools/competition/offline_run.py gateway --upstream http://127.0.0.1:11434 --port 11435
python tools/competition/offline_run.py serve --log blocked.jsonl --port 8000
# 然后依次请求 PUT /models/local、POST /inputs、POST /evaluate（参数见 run.json）
```

## 文件

| 文件 | 内容 |
|---|---|
| `run.json` | 起止时间、请求参数、模型登记、模型版本、拦截条数 |
| `api-response.json` | 接口返回原文；仅把本机绝对路径换成文件名 |
| `testset.json` | 本次 3 题 |
| `blocked-connections.jsonl` | 外网连接拦截记录（本次为空） |
| `SHA256SUMS.txt` | 以上文件的 SHA-256 |
