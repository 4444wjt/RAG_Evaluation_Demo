# 贡献指南

欢迎为鉴真全域 VeriScope AI 提交可复现的问题报告、文档改进、展示优化和测试。先查看根目录 README、docs/architecture/README.md 与现有 Issue，避免重复工作。

## 报告问题

请提供操作系统、Python／Node.js 版本、入口和复现步骤、期望与实际行为、脱敏日志及最小样例。UI 问题附截图和视口尺寸；评测问题说明测试集、模型、阈值、缺失值和结果出处。

不要粘贴 .env、API Key、Token、密码、私有文档或未获授权的真实评测数据。安全问题按 [SECURITY.md](SECURITY.md) 私下报告，不在公开 Issue 中发布漏洞细节或可利用凭据。

## 提交改动

1. Fork 确认的目标仓库，从 master 建立聚焦的 feat/、fix/、docs/ 分支。
2. 一个改动解决一个问题，说明修改范围和验证方法，保留现有入口与项目约定。
3. 不携带 node_modules、venv、模型权重、向量库、测试缓存和运行报告；新截图放到 docs/images/ 并注明来源。
4. 提交前检查文件清单和脱敏结果，再向维护者发起 Pull Request。

项目同时配置了 GitHub（remote github）和 Gitee（remote origin）。贡献者推送自己的 Fork；不要照搬维护者远端名称或对原仓库强制推送。

## 设计与实现约定

- 前端沿用 React、TypeScript、Vite、原生 CSS 与 Gold #F2C879，保留 PASS／FAIL／WARNING 语义色、MODEL_COLORS、完整模型名称和模型 IDs。
- 保留悬停、键盘焦点、禁用状态和减少动态效果支持。首页展示优先检查 1920×1080 与 1280×800，证据文字保持清晰。
- Python 使用 4 空格缩进。新增依赖应先说明必要性及许可证，不能顺便升级原有冻结依赖。
- 指标、score、正负样本和门禁口径的改动必须单独提出设计、兼容性与验证材料；不要把业务变更混入 UI 或文档精修。
- 前端、API 合同和后端分别明确改动范围，不绕过路径校验或把提供方凭据暴露到浏览器。
- 演示数据、快照出处和原始记录保持真实；缺失值不填成零，静态预览不标为实时结果。

## 验证

按改动范围执行，并在 PR 中如实记录命令、结果和未覆盖内容。

前端（frontend/）：

```powershell
npm ci
npm test
npm run typecheck
npm run build
npm run test:browser -- browser-tests/landing.spec.ts
```

首页桌面测试使用本地 Chrome / Edge；可用 PHASE5_BROWSER_PATH 指定浏览器位置。工作台改动需要再运行对应浏览器用例或完整浏览器套件。测试产物放在被忽略的 test-results/。

Python 离线测试（项目根目录）：

```powershell
.\venv\Scripts\python.exe -m pip install -r tests/requirements.txt
.\venv\Scripts\python.exe -m compileall -q main.py backend tests
.\venv\Scripts\python.exe -m pytest -q tests
```

使用离线样例和 mock 验证，不将真实模型调用作为默认测试步骤；真实调用需说明必要性、预算和使用授权。文档改动至少检查本地链接、图片、环境示例和忽略规则。

## 许可

本项目自有代码采用现有 MIT LICENSE，保留原版权声明。第三方代码和依赖继续遵循各自许可证；查看 frontend/THIRD_PARTY_NOTICES.md、frontend/licenses/ 与依赖包元数据。提交新增组件或依赖前，应确认来源、许可证兼容性并保留所需声明。

贡献内容应为本人有权提交的代码、文档或素材，并与对应文件的许可要求兼容。

## 审查与交流

PR 说明“改了什么、为什么改、如何验证”，附必要的前后截图或复现记录。保持专业、尊重他人，聚焦具体证据；不要用未经验证的性能或可靠性结论替代测试。
