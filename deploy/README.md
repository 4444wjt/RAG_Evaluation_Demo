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

打开 `http://<服务器地址>:8080/`，演示入口为 `/#/lab/demo`。

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

- **nginx 层**：`POST /api/evaluate` 直接返回 HTTP 423；`/api/` 下其他路径只允许 GET 与 HEAD；`/api/docs`、`/api/redoc`、`/api/openapi.json` 返回 404。
- **后端层**：`/evaluate` 在读取请求参数前检查 `VERISCOPE_READ_ONLY`，同样返回 423（[backend/api/app.py](../backend/api/app.py)）。
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
- 这样部署会开放付费模型调用。请自行加上访问控制（例如反向代理鉴权或仅限内网访问），不要直接暴露在公网。

## HTTPS

compose 只提供 HTTP。公网部署建议在宿主机前置一层终止 TLS 的反向代理（如宿主机 nginx 或 Caddy），转发到 `127.0.0.1:${WEB_PORT}`。证书文件不要放进仓库。

## 验证状态

- **本机（Windows，无 Docker）已验证：**
  - 后端在只装 `fastapi`、`uvicorn`、`pydantic`、`numpy`、`pandas` 的前提下（屏蔽 Ragas、LangChain、torch 等重型包），API 能正常启动；`/health` 返回 200，`VERISCOPE_READ_ONLY=1` 时 `/evaluate` 返回 423；
  - 前端以 `VITE_READ_ONLY=1` 能正常构建；
  - `docker-compose.yml` 是合法的 YAML，`deploy.sh` 通过 `sh -n` 语法检查。
- **尚未验证：** 本机没有 Docker 和 nginx，镜像构建与容器运行还没有在本机实际执行；nginx 配置也没有用 `nginx -t` 检查过。
- 公网演示站的实际服务器配置未入库；本目录是按仓库代码编写的标准部署方式，与公网站点采用相同的只读机制。
