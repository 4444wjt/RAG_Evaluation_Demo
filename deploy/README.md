# 一键部署

用 Docker Compose 启动两个容器：`api`（FastAPI 后端）和 `web`（前端静态站点 + nginx 反向代理）。默认部署为**只读演示站**，与公网演示的访问方式一致：只能查看快照，不能发起在线评测。

## 快速开始

需要 Docker Engine 与 Compose v2.24 及以上版本。

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

尚未验证：`API_INSTALL=full` 的全量镜像构建与真实评测，以及 `WEB_BIND=0.0.0.0` 直接对外开放的情形。

后续新增的 `/api/inputs` 代理和示例文件复制规则不属于上述服务器验收记录。本轮仅完成本地 API / 前端离线验证，未重建或修改线上容器。

## 备用静态站：GitHub Pages

服务器或隧道中断时，用 GitHub Pages 上的纯静态站兜底：[`.github/workflows/pages.yml`](../.github/workflows/pages.yml) 在 `frontend/` 变动时用同一份只读快照构建前端（`VITE_READ_ONLY=1`、`VITE_STATIC_SITE=1`、相对 base），发布到 https://4444wjt.github.io/RAG_Evaluation_Demo/。

- 只有前端：首页、演示快照、各工作台视图和「门禁审计」都可查看；没有后端，不探测 API，顶栏显示「静态演示站 · 无后端」，不能发起评测。
- 首次使用前，在仓库 Settings → Pages 把 Source 设为 “GitHub Actions”；之后每次推送自动更新，也可在 Actions 页手动运行 “Pages demo”。
- 本地预览同一产物：在 `frontend/` 执行 `VITE_READ_ONLY=1 VITE_STATIC_SITE=1 npx vite build --base=./`，再用任意静态服务器打开 `dist/`。

公网演示站的实际服务器配置未入库；本目录是按仓库代码编写的标准部署方式，与公网站点采用相同的只读机制。
