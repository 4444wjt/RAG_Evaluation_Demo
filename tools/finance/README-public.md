# 独立金融复核入口

公开体验复用交易核验核心，提供交易 CSV/JSON 导入、规则线索、字段与引用范围核对、人工决定、下载和重开。输入在服务端内存中计算；请使用脱敏资料或页面示例，复核包由浏览器下载到本机。它不调用模型，也不根据交易流水直接认定欺诈。

构建使用项目现有 Node 依赖，从 `frontend/` 执行：

```sh
VITE_FINANCE_PUBLIC=1 npm run build -- --base=/finance/ --outDir=dist-finance-public
```

PowerShell 可先设置 `$env:VITE_FINANCE_PUBLIC='1'`，再执行同一 `npm run build` 命令。不要覆盖已经验收的 `frontend/dist`。

在已有项目 Python 环境中，从仓库根目录启动：

```sh
python -m uvicorn tools.finance.public_app:create_app --factory --host 127.0.0.1 --port 8097 --no-access-log
```

工厂默认读取 `frontend/dist-finance-public`，也可通过 `VERISCOPE_FINANCE_DIST` 指定构建目录。反向代理将 `/finance/` 转发到服务根路径，并保留尾部斜杠，例如 `proxy_pass http://finance-service:8000/;`。未经过该前缀代理时，构建中的资源地址与 API 地址不会自动改成根路径。

此入口仅开放 `GET /api/health`、`POST /api/transaction-review/analyze` 和 `POST /api/transaction-review/replay`；模型配置、评测、历史和原件接口不对外提供。分析输入上限为 2 MiB，重开为 24 MiB，代理也需允许对应大小。保留原 `VERISCOPE_READ_ONLY=1` 的拒绝语义。

源码包应保留 `scripts/transaction_review.py`：报告对核心、API 和此 CLI 文件做 SHA-256 字节绑定。哈希一致只说明这些字节相同；不证明交易真实、欺诈成立或业务决定正确。下载包重开时完整重算报告，篡改或版本不一致会拒绝采信原报告与决定。

从已下载并解开的源码根目录，可以核对报告中的三个代码摘要：

```sh
sha256sum backend/core/transaction_review.py backend/api/transaction_review.py scripts/transaction_review.py
```

公开入口与原模型评测主站部署在不同版本目录和容器。更新或回退只切换金融路径，保留原站及其配置恢复点。
