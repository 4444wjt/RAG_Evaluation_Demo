#!/usr/bin/env sh
# 一键部署：构建并启动 api 与 web 两个容器，然后检查健康状态。
# 用法：在仓库的 deploy/ 目录执行 ./deploy.sh
set -eu

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "未找到 docker，请先安装 Docker Engine 与 Compose 插件。" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "未找到 docker compose 插件（需要 Compose v2）。" >&2
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "已从 .env.example 生成 .env（默认只读演示）。"
fi

port=$(grep -E '^WEB_PORT=' .env | cut -d= -f2)
port=${port:-8080}

docker compose up -d --build

echo "等待服务就绪..."
i=0
while [ $i -lt 30 ]; do
    if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
        echo "部署完成：http://127.0.0.1:${port}/  （演示入口：/#/lab/demo）"
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done

echo "服务在 60 秒内未就绪，请查看日志：docker compose logs" >&2
exit 1
