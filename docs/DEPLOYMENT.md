# 落地与接入

本页说明本平台怎样接进实际工作：三种落地形态、接入自有模型的步骤、私有化 / 内网部署的注意事项。每一项都链接到仓库里已有的实现；没有实测过的部分单独列出。

## 三种落地形态

| 形态 | 适合 | 怎么接 | 已有实现 |
|---|---|---|---|
| 独立质检平台 | 测试或质检团队，上线前对 RAG 应用做一次完整评测 | 本机运行工作台，或用 Docker Compose 部署在内网 | [快速启动](../README.md#快速启动)、[deploy/](../deploy/README.md) |
| 嵌入现有系统 | 已有 RAG 应用或评测流程，想调用逐句核对和门禁 | HTTP 接口、Python 函数，或把被测应用的接口交给体检工具 | `backend/api/`、`backend.core.pipeline.evaluate`、`scripts/robustness_check.py` |
| CI 门禁 | 每次改提示词、换模型、调检索后自动回归 | GitHub Action 离线重放门禁，不调用模型、不需要密钥 | [action.yml](../action.yml)、[门禁回放](gate-replay.md) |

### 独立质检平台

- **本机**：按 [快速启动](../README.md#快速启动) 启动后端和前端，在工作台导入资料、选择模型、运行评测；每次评测生成逐题 CSV 和 HTML 报告。
- **服务器或内网**：`deploy/` 下的 Docker Compose 启动两个容器（后端 + nginx 前端）。默认是只读演示站，改为可评测部署见 [deploy/README.md](../deploy/README.md#改为可评测部署)。可评测部署会开放付费模型调用，必须自行加访问控制。

### 嵌入现有系统

**HTTP 接口**（直连后端时没有 `/api` 前缀；经 nginx 或前端开发代理时加 `/api`）：

| 接口 | 作用 |
|---|---|
| `GET /health` | 服务存活检查，不检查模型和密钥 |
| `POST /inputs?kind=document\|testset&filename=…` | 上传一份资料或测试集（请求体为文件原始字节，最大 10 MiB），返回 `relative_path` |
| `POST /evaluate` | 同步执行一次评测，返回汇总指标、门禁结论和逐题结果 |
| `GET /models` | 列出可用模型；只返回密钥是否已设置，不返回密钥值 |
| `PUT /models/local` | 登记自有模型（见下文），只接受运行后端的本机发起的请求 |

`POST /evaluate` 的请求字段（见 `backend/api/schemas.py`，未知字段会被拒绝）：

| 字段 | 说明 |
|---|---|
| `doc_path` | 资料路径：`demo.txt`、`scenarios/` 下的文件，或 `/inputs` 返回的 `relative_path`；为空时用 `demo.txt` |
| `testset_path` | 自定义测试集路径（同上，另可用 `testset_example.json` / `.csv`）；为空且未选基准时自动出题 |
| `benchmark` / `benchmark_limit` | 改用 TruthfulQA 基准（`"truthful_qa"`），题数 1–50 |
| `model_names` | 参评模型名列表；不传或为 `null` 时评测全部内置模型 |
| `faith_threshold` / `ctx_prec_threshold` | 门禁阈值，为空时用后端默认值 0.8 |
| `version_label` | 写入评测历史的版本标签 |

评测是同步请求，可能持续数分钟；同一时间只运行一个评测，其余请求返回 409。路径只允许上表范围内的相对路径，这是为了防止接口读取任意文件。

**Python 调用**：在项目根目录下，`backend.core.pipeline.evaluate(request)` 接受与上表同名字段的字典，返回包含汇总表、门禁结论和逐题结果的字典（接口返回的就是它的 JSON 序列化）。Python 调用不经过接口的路径限制，调用方自己负责输入文件。

**检验你自己的 RAG 应用**：[鲁棒性体检](competition/robustness/README.md) 的 `--generator-endpoint` 把 `{"question", "contexts"}` POST 给被测应用，读取返回的 `{"answer"}`，再用平台的逐句核对检查回答是否依据资料。被测应用须直接用传入的资料作答，不再自行检索。`tools/competition/prompt_case.py` 用同样方式比较两份提示词，见 [外部案例](competition/cases/langchain-chatchat/README.md)。

### CI 门禁

在其他仓库的工作流里加一步：

```yaml
- uses: 4444wjt/RAG_Evaluation_Demo@master
  with:
    result: path/to/api-response.json   # 一次评测的结果 JSON
    baseline: path/to/baseline.json     # 可选：上一次结果，逐题对比
    junit: quality-gate.junit.xml       # 可选：发布到检查结果
```

- 门禁不通过时这一步失败（退出码 1），作为合并或发布前的卡口。
- 只重放已有结果，不调用模型、不需要密钥。新的评测结果由前两种形态产生，再交给这一步。
- 完整示例见 [quality-gate-example.yml](../.github/workflows/quality-gate-example.yml)，输入与退出码见 [门禁回放](gate-replay.md)。

## 接入自有模型

所有模型都按 OpenAI 兼容协议调用（`backend/adapters/models/openai_compatible.py`）。vLLM、Ollama、LM Studio、各类内部模型网关只要提供 `/v1/chat/completions`，都可以接入。

**第 1 步：准备接口。** 确认接口地址和模型名。例如：

| 服务 | 接口地址 | 模型名 |
|---|---|---|
| vLLM | `http://<内网地址>:8000/v1` | 启动时 `--served-model-name` 的值 |
| Ollama | `http://127.0.0.1:11434/v1` | `ollama list` 中的名称 |
| 内部网关 | 网关提供的 `/v1` 地址 | 网关规定的模型名 |

模型名只能含字母、数字和 `. _ / -`。名称会写进报告文件名，冒号在 Windows 上会出问题。Ollama 带标签的名称（如 `qwen2.5:7b`）可以先用 `ollama cp qwen2.5:7b qwen2.5-7b` 复制出一个不带冒号的名称。

**第 2 步：在 `.env` 写入密钥。** 在项目根目录的 `.env` 中加一行，变量名必须形如 `VERISCOPE_<名称>_API_KEY`：

```dotenv
VERISCOPE_QWEN_LOCAL_API_KEY=你的密钥
```

- 接口不需要密钥时（如本机 Ollama），随便写一个非空值。
- 改完 `.env` 后重启后端。
- 自有模型不能借用内置模型的密钥变量（如 `OPENAI_API_KEY`），这样就不会把已有密钥发往新登记的地址。

**第 3 步：登记模型。** 两种方式，结果相同：

- 工作台侧栏「模型配置」→「登记模型」，填写模型名、显示名、接口地址和密钥变量名。页面只显示密钥「已设置 / 未设置」，不接收、不返回密钥本身。
- 或者直接在项目根目录新建 `models.local.json`：

```json
{
  "schemaVersion": 1,
  "models": [
    {
      "name": "qwen2.5-7b",
      "display_name": "内网 Qwen2.5-7B",
      "base_url": "http://127.0.0.1:11434/v1",
      "api_key_env": "VERISCOPE_QWEN_LOCAL_API_KEY"
    }
  ]
}
```

`models.local.json` 已被 git 忽略，不会入库。文件里出现密钥字段或其他未知字段时，后端拒绝读取并给出提示，不会静默忽略。

**第 4 步：试跑。** 在评测工作区「模型」一步选中新模型（新登记的模型默认不选中），先用自定义测试集的少量题目试跑，确认能返回结果后再跑完整题集。

注意：

- **逐句核对和 Ragas 指标都由被测模型自己执行。** 换成小模型后，核对的准确度可能下降。本机用 2B 模型试跑时，它给出的证据是改写过的原文，平台的证据校验把 6 条陈述全部过滤，没有留下可用的逐句判定（见 [本机无外网试跑](competition/offline-run/20261014-ollama-qwen3.5-2b/README.md)）。需要独立裁判时，参考 [裁判对照](judge-comparison.md)。
- **自动出题使用内置的 DeepSeek 槽位**（失败时换 Qwen）。只有自有模型时，请用自定义测试集；或者在 `.env` 里用 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`OPENAI_API_KEY` 把 DeepSeek 槽位指向自有模型。
- 登记只对工作台和 HTTP 接口生效。保留的 CLI / Gradio 入口（`main.py`）仍只使用内置模型。
- 登记只能在运行后端的本机修改（后端检查请求来源是 `127.0.0.1` / `::1`）；只读部署下 `GET /models` 和 `PUT /models/local` 都返回 423。只读返回 423 已在本机实测（2026-10-14）；来自其他地址的登记请求返回 403，已在真实网络栈上实测（2026-10-14，见下文「私有化 / 内网部署」的已实测项）。

## 私有化 / 内网部署

数据流向：资料、测试集、检索库和评测结果都保存在运行后端的机器上（项目目录与 `work/`）。评测时对外发出的请求只有调用模型接口这一项，地址就是登记的 `base_url`。模型部署在内网时，资料不离开内网。

**已核实（按代码）：**

- Embedding 以 `local_files_only=True` 加载（`backend/core/pipeline.py`），评测时不联网下载。需要预先把 `bge-small-zh-v1.5` 放到 `models/bge-small-zh-v1.5/`，或通过 `EMBEDDING_MODEL` 指定本地路径。
- 文本切分按字符计算，不调用联网分词服务；已安装的 ragas 0.1.21 的代码没有调用 tiktoken。
- 检索库是本地 Chroma，不依赖外部向量服务。
- 模型调用只发往登记的 `base_url`；内置模型的地址可用 `DEEPSEEK_BASE_URL`、`QWEN_BASE_URL` 覆盖。GLM 的地址目前写在 `backend/config.py` 中。
- 只读部署时，评测、导入和模型配置接口都返回 423，前端只展示快照（2026-10-14 本机实测：只读后端三个接口均返回 423；纯静态构建的模型配置页只显示说明，不发出任何 `/api/` 请求）。

**已实测（本机，进程级拦截）：**

- 2026-10-14 在本机用 Ollama 的 2B 模型跑了一次 3 题完整评测：后端进程启动前加上出站守卫，只放行回环地址，其余连接一律拒绝并记录。评测跑通（HTTP 200），**外网连接尝试 0 次**，包括 Embedding 加载、Chroma、Ragas 与 LangChain。详见 [本机无外网试跑](competition/offline-run/20261014-ollama-qwen3.5-2b/README.md)，工具为 `tools/competition/offline_run.py`。
- 这次是进程级拦截，不是物理断网，也不是在服务器的内网里运行；依赖安装与镜像构建不在范围内。

**已实测（本机，真实网络栈）：**

- 2026-10-14 在本机另起一个后端实例（端口 8002），用**局域网地址**而不是回环地址访问，检验「登记只接受本机请求」这道守卫是否真的按来源生效：

  | 请求 | 状态码 | 响应 |
  |---|---:|---|
  | `PUT http://<局域网地址>:8002/models/local` | **403** | `{"detail":"模型登记只能在运行服务的本机上修改。"}` |
  | `GET http://<局域网地址>:8002/models` | **200** | 返回 4 个模型；列模型不限制来源，按设计 |
  | `PUT http://127.0.0.1:8002/models/local` | **422** | `{"detail":"api_key_env 必须是文本。"}` |

  第三条是这次测试的关键对照：同一实例、同一接口，换成回环地址后**不再返回 403**，而是进入参数校验并因 `api_key_env` 类型不合法返回 422。这证明拦截依据的是请求来源，不是请求方法或路径。

- 三次请求都没有写入任何内容：`models.local.json` 在测试前后的 SHA-256 一致（`87c3a751f6d241291187108610875a9a0665e4143f3b25f86511f00cecf8f66c`）。
- 测试实例（8002）已停止，停止后 `http://127.0.0.1:8002/health` 无法连接；运行中的 8000 实例未操作，仓库文件未修改。
- **未独立核实**：本次没有执行系统级监听查询，「8002 上没有任何监听」只有「停止后连接不上」这一间接证据。

**尚未实测：**

- 物理断网的机器或隔离网段里的完整评测。实际部署前，请先在目标环境里用少量题目试跑一次。
- 上面的试跑中没有依赖尝试外网连接；如果你的版本不同，仍可以设置 `ANONYMIZED_TELEMETRY=False` 关闭 Chroma 的匿名遥测。
- Docker 部署中挂载 `models.local.json` 登记模型（上文第 4 条）没有实测。
- Docker 全量镜像（`API_INSTALL=full`）的构建与真实评测没有在服务器上验证过，见 [deploy/README.md](../deploy/README.md#验证状态)。

**内网部署的准备：**

1. **依赖**：构建镜像或安装依赖时需要 pip / npm 源。内网可以使用镜像源，或在有网的机器上先构建镜像再导入。
2. **Embedding 模型**：预先下载到 `models/`。Docker 部署时 `models/` 不在镜像里，需要挂载进容器。
3. **模型接口**：准备内网可访问的 OpenAI 兼容接口，按上一节接入。
4. **Docker 中登记模型**：容器里的 nginx 只放行 `/api/` 下的 GET 请求，后端也只接受本机发起的登记请求，所以容器部署时请在宿主机上先写好 `models.local.json`，再挂载到容器的 `/app/models.local.json`（文件不存在时 Docker 会创建同名目录，后端会报登记无法解析）。容器默认没有持久卷，不挂载的话，重建容器后登记会丢失。密钥变量写在 `deploy/.env`，由 compose 传给后端。示例（写在 `docker-compose.override.yml` 中，不改动仓库里的 compose 文件）：

   ```yaml
   services:
     api:
       volumes:
         - ../models.local.json:/app/models.local.json:ro
         - ../models:/app/models:ro
   ```

5. **访问控制**：可评测部署会调用模型、消耗费用或算力，请放在内网或反向代理鉴权之后，不要直接暴露到公网。
