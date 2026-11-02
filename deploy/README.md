# 一键部署

用 Docker Compose 启动两个容器：`api`（FastAPI 后端）和 `web`（前端静态站点 + nginx 反向代理）。默认配置提供**只读演示站**；需要访客填写自己的模型和 API Key 在线评测时，使用下文独立的 **BYOK 配置**。

## 快速开始

需要 Docker Engine 与 Compose v2.24 及以上版本。`deploy.sh` 是 POSIX shell 脚本，还会用到 `cp`、`grep`、`cut`、`curl`，请在 Linux 或 WSL 中执行。首次构建要拉取镜像、安装依赖，这段时间不计入脚本之后的健康检查等待。

```bash
git clone https://github.com/4444wjt/RAG_Evaluation_Demo.git
cd RAG_Evaluation_Demo/deploy
./deploy.sh
```

脚本会：

1. 检查 Docker 与 Compose；
2. 没有 `deploy/.env` 时从 `.env.example` 复制一份（默认只读）；
3. 执行 `docker compose up -d --build`；
4. 轮询 `/api/health`，就绪后打印访问地址。

默认只监听本机：打开 `http://127.0.0.1:8080/`，演示入口为 `/#/lab/demo`。在服务器上对外提供访问时，建议前置 TLS 反向代理（见下文 HTTPS 一节）；确需直接开放端口时，在 `deploy/.env` 中设置 `WEB_BIND=0.0.0.0`，并确认防火墙或安全组规则。

不用脚本时也可以直接执行：

```bash
cd deploy
cp .env.example .env
docker compose up -d --build
```

## 结构

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 定义 `api` 与 `web` 两个服务；只有 `web` 对外暴露端口 |
| `Dockerfile.api` | 后端镜像；`INSTALL=demo` 只装 API 启动依赖，`INSTALL=full` 安装全部评测依赖 |
| `Dockerfile.web` | 多阶段构建：Node 24 构建前端，nginx 1.27 提供静态文件并反向代理 `/api` |
| `nginx/default.conf.template` | 反向代理规则；启动时由 nginx 官方镜像按环境变量渲染 |
| `.env.example` | 可配置项，只有变量名和默认值，没有密钥 |
| `deploy.sh` | 一键部署脚本 |

## 只读演示的两层拦截

`VERISCOPE_READ_ONLY=1`（默认）时：

- **nginx 层**：`POST /api/evaluate` 和 `POST /api/inputs` 直接返回 HTTP 423；`/api/` 下其他路径只允许 GET 与 HEAD；`/api/docs`、`/api/redoc`、`/api/openapi.json` 返回 404。
- **后端层**：`/evaluate`、`/inputs` 与模型配置接口（`/models`、`/models/local`）同样检查 `VERISCOPE_READ_ONLY` 并返回 423；资料导入在读取文件正文前返回 423（[backend/api/app.py](../backend/api/app.py)）。
- **前端**：以 `VITE_READ_ONLY=1` 构建，工作台只展示快照，不显示评测入口。

## 改为可评测部署

在 `deploy/.env` 中设置：

```dotenv
VERISCOPE_READ_ONLY=0
API_INSTALL=full
OPENAI_API_KEY=...
SILICONFLOW_API_KEY=...
```

然后重新执行 `docker compose up -d --build`。注意：

- 全量依赖包含 torch 等大型包，镜像构建明显更慢、更大。
- 评测需要本地可用的 Embedding 模型（见根目录 `.env.example` 的 `EMBEDDING_MODEL` 说明）；引擎评测时不联网下载模型。
- 评测是同步请求，可能持续数分钟；nginx 对 `/api/evaluate` 的超时设为 30 分钟。
- 可评测模式允许从电脑导入资料和测试集，单个文件最多 10 MiB；nginx 与后端均限制大小。内置示例随 API 镜像提供。导入文件保存到容器 `/app/work/uploads/`，默认没有持久卷，重建容器后需重新导入；导入成功只说明文件已保存，内容格式仍由既有评测流程检查。
- 这样部署会开放付费模型调用。请自行加上访问控制（例如反向代理鉴权或仅限内网访问），不要直接暴露在公网。

## 访客自带 API Key（BYOK）

需要访客自己填写模型和密钥时，使用独立配置 [docker-compose.byok.yml](docker-compose.byok.yml)，不要与默认配置叠加。默认 `docker-compose.yml` 继续提供只读演示。

先准备完整的本地 Embedding 模型目录，再在 `deploy/` 下操作：

```bash
cp byok.env.example .env.byok
# 编辑 .env.byok，把 BYOK_EMBEDDING_DIR 改为服务器上完整模型目录的绝对路径。
docker compose --env-file .env.byok -f docker-compose.byok.yml -p veriscope-byok config --quiet
docker compose --env-file .env.byok -f docker-compose.byok.yml -p veriscope-byok up -d --build
curl --fail http://127.0.0.1:8081/api/health
```

默认访问 `http://127.0.0.1:8081/`。健康检查仅确认 API 启动，不代表模型、Embedding 或完整评测已通过。首次使用请在页面填写自己的模型和 Key，用少量题目试跑；这会使用该 Key 对应的模型额度。对外访问须经 HTTPS。

- 配置固定后端 `VERISCOPE_BYOK=1`、`VERISCOPE_READ_ONLY=0`、`VERISCOPE_KNOWLEDGE_UPDATE=1`，前端构建固定 `VITE_BYOK=1`、`VITE_READ_ONLY=0`。无需修改默认 `.env`，不会把作者的 `.env` 或模型密钥传入容器。
- API 安装完整评测依赖；本地模型以只读方式挂载到 `/opt/embedding`。模型须已完整缓存，运行评测时不下载。固定的 Node.js/curl 来源清单随 API 镜像复制。
- 访客资料与记录按随机会话保存到 API 私有的 `visitor-data` 卷，web 不挂载它；会话标识保留在当前标签页，Key 只保留在页面内存和本次后台任务。刷新后需重新填写 Key，关闭标签页可能失去原记录入口，请先下载备份。
- `POST /api/evaluate` 返回 202 和任务编号，页面每两秒查询 `GET /api/jobs/{id}`；不要因等待再次提交。只运行一个后台评测，繁忙时返回 409，不自动排队。容器重启不会自动恢复在途任务，已完成记录保存在卷中。
- 每会话最多 50 题、3 个模型（含裁判）、10 次评测、32 个导入文件、100 MiB 资料与结果；单文件最多 10 MiB。生成中的磁盘占用可能短暂超过限制，超额任务或上传不保留本次新增结果。24 小时未活动会话会在后续请求时清理。
- nginx 使用独立的 [byok.conf.template](nginx/byok.conf.template)，允许既有上传、导回、评测及手动检查请求；其他接口仅允许读取。不开放服务器模型登记、任意地址抓取或采集配置；知识更新仅有固定公开目录和手动文本检查。它不是账号体系，持有会话标识的人可以访问对应数据，服务器管理员也能读取磁盘。

停止使用时执行 `docker compose --env-file .env.byok -f docker-compose.byok.yml -p veriscope-byok down`，不加 `-v`，以保留资料卷。恢复默认只读演示时，在独立项目中使用原 `docker-compose.yml`，不要把两份配置合并。

2026-11-02 已完成 BYOK 云预览验收：使用访客 API 自动生成并评测 6 题（3 正向、3 负向），后端耗时 112.26 秒，保存完整 JSON、演示后返回及历史恢复通过；引用版本、知识更新、父条资料和历史记录共 4 条桌面流程、8 份下载及 23 项绑定与边界检查通过。正式入口尚待切换与发布后核验。

## HTTPS

compose 只提供 HTTP。公网部署建议在宿主机前置一层终止 TLS 的反向代理（如宿主机 nginx 或 Caddy），转发到 `127.0.0.1:${WEB_PORT}`。证书文件不要放进仓库。

## 验证状态

2026-10-13 在一台 Linux 云服务器（Docker 29.9、Compose v5.6，2 核 3.5G 内存）上从 GitHub 全新克隆，以独立的 Compose 项目名执行 `./deploy.sh`，默认只读配置下的结果：

| 检查 | 结果 |
|---|---|
| 两个镜像构建、容器启动 | 通过；api 健康检查为 healthy |
| `GET /` | 200 |
| `GET /api/health` | 200 |
| `GET /api/docs`、`/api/redoc`、`/api/openapi.json` | 404 |
| `POST /api/evaluate`（经 nginx） | 423，JSON 响应 |
| `POST /evaluate`（绕过 nginx 直接请求后端） | 423 |
| 对 `/api/` 其他路径发 POST | 403 |
| nginx 配置 `nginx -t` | 通过 |

这次服务器测试发现并修复了一处问题：`default_type` 原写在 `if` 块内，nginx 不允许，web 容器启动即退出；已移到 `location` 层。

上述 2026-10-13 只读验收未覆盖全量评测镜像与真实评测，也未验证 `WEB_BIND=0.0.0.0` 直接对外开放；本轮 BYOK 云预览结果见上文。

后来新增的 `/api/inputs` 代理和示例文件复制规则不属于上述历史只读记录。

## 备用静态站：GitHub Pages

服务器或隧道中断时，用 GitHub Pages 上的纯静态站兜底：[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) 在 `frontend/` 变动时用同一份只读快照构建前端（`VITE_READ_ONLY=1`、`VITE_STATIC_SITE=1`、相对 base），发布到 [4444wjt.github.io/RAG_Evaluation_Demo](https://4444wjt.github.io/RAG_Evaluation_Demo/)。

- 只有前端：首页、演示快照、各工作台视图和「门禁审计」都可查看；没有后端，不探测 API，顶栏显示「静态演示站 · 无后端」，不能发起评测。
- 首次使用前，在仓库 Settings → Pages 把 Source 设为 “GitHub Actions”；之后每次推送自动更新，也可在 Actions 页手动运行 “Pages demo”。
- 本地预览同一产物：在 `frontend/` 执行 `VITE_READ_ONLY=1 VITE_STATIC_SITE=1 npx vite build --base=./`，再用任意静态服务器打开 `dist/`。

本目录提供默认只读与可选 BYOK 两种部署方式；实际入口的能力以所选配置及发布后核验为准。
