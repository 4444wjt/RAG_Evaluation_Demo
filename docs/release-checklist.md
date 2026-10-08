# 开源发布检查

整理日期：2026-10-04。当前工作区包含未提交的发布整理；未暂存、提交、推送或上传。

## 发布内容

- 根目录 `LICENSE` 保留项目原创代码的 MIT 条款与 `Copyright (c) 2026 KK`。
- 前端不再包含 React Bits Waves、GlareHover 源码或 Commons Clause 许可文件。
- 首页 `BeamConnector` 仍是 Magic UI Animated Beam 的 MIT 改编，保留署名和完整许可证。React、ECharts 与其 D3 子组件许可证也随生产构建副本提供。
- 前端运行依赖及其许可证单独列于 `frontend/THIRD_PARTY_NOTICES.md`；npm 直接／传递依赖各自保留上游许可。
- 公开文档保留架构、演示、截图和贡献说明。内部交接、提示词、阶段报告与旧诊断脚本已从公开文件清单移除。
- 本地 `.env`、依赖目录、模型、向量库、缓存与运行报告不应进入发布内容；提交前仍需人工检查实际文件清单和敏感信息。

## 本轮验证

- `frontend/npm test`：123 项通过。
- `frontend/npm run typecheck`：通过。
- `frontend/npm run build`：通过；Vite 输出既有的大 chunk 体积提示。
- `frontend/npm run test:browser -- browser-tests/landing.spec.ts`：11 项通过；覆盖 1920×1080、1280×800、焦点／悬停和流程生命周期。
- 桌面全页截图：`frontend/test-results/landing-1920x1080.png`、`frontend/test-results/landing-1280x800.png`。
- `git diff --check`：通过。发布前仍需对最终待提交清单做人工审阅。

## 维护者检查

- 确认 MIT 是项目原创代码的目标许可，并保留所有第三方声明。
- 检查目标分支、远端和待提交文件清单。
- 若发布构建产物，确认随分发包保留实际打包依赖要求的版权及许可声明。

本次只准备本地工作区，未执行 `git add`、`commit`、`push`、创建远端仓库或上传文件。


## 正式25题证据收口（2026-10-06）

- 正式 run 原始目录：`work/competition/runs/20261005T144504Z-full-39511424/`；脱敏公开包：`docs/competition/evidence/20261005T144504Z-full-39511424/`。
- 公开包包含分析表、完整分析JSON、请求/响应、用量、指标、Trace、报告、输入、运行工具源码、来源对应关系及重新计算的 `SHA256SUMS.txt`。扫描未发现凭据样式或机器绝对路径；未复制 `.env`、缓存、向量库或虚拟环境。
- 本次新分析全部标为“AI辅助分析、待人工确认”；旧Demo的13条作者核验、冻结快照、题集、算法、阈值和历史归档未被改写。
- 当前工作区仍未 `git add`、commit 或 push；最终候选清单需维护者继续人工检查。
