# veriscope（法条引用离线诊断）

VeriScope 仓库中 `scripts/statute_diagnostic.py` 的安装版：零依赖、Python 3.11+、安装后不联网。入口命令只转交参数，诊断逻辑、输出格式和退出码与仓库脚本一致。

```bash
veriscope-statute --example --json
veriscope-statute --input 你的请求.json --json
```

`--example` 会换成随包示例请求（`constructed-request.json`，预期退出码 1）。退出码：0 = 现有规则未检出错误（不等于门禁通过或事实正确），1 = 检出错误，2 = 输入无效或需人工复核。

只包含法条诊断；Web 工作台、评测流水线和其它脚本仍需克隆仓库运行。
