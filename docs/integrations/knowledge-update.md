# 来源变化后的知识条目复核

离线检查显式关联的知识条目与来源版本，仅接受严格 UTF-8 的 `.txt`、`.md`、`.rst`。示例在 [examples/knowledge_update](../../examples/knowledge_update/task.json)。来源路径相对 task.json 所在目录，必须留在该任务包内；命令参数路径相对当前工作目录。

```text
python scripts/knowledge_update.py scan --input examples/knowledge_update/task.json --output work/knowledge-scan-1
python scripts/knowledge_update.py recheck --input examples/knowledge_update/task.json --revisions examples/knowledge_update/revisions.json --output work/knowledge-recheck-1
```

task 声明来源的 old/new（新增 old=null、删除 new=null，不能同时为空）、条目的原稿、source_ids 和 old_citations。引文按完整连续逻辑行精确检查，支持成对的 line_start/line_end；重复命中全部保留。revisions 按 item_id 提交 proposed_draft 与 citations；省略 source_ids 继承关联，提供时整体替换（[] 清空），不按引文推断关联。未修订条目仍完整重查，open_questions 只记录未决。

来源状态包含 ADDED、DELETED、UNCHANGED、CHANGED、READ_ERROR；引用状态包含 MATCHED、AMBIGUOUS、LINE_MISMATCH、NOT_FOUND、MISSING_QUOTE、来源缺失/读错及映射错误。有变化、异常或未决时条目标 PENDING_REVIEW。所有条目始终 NOT_EVALUATED、publication_approved=false；MATCHED 和退出 0 均不证明稿件正确或批准发布。

退出码：0 完整写出检查结果（可以有待复核发现）；2 输入/用法错误，可附完整诊断；1 执行/写入失败。输出目录必须不存在，空目录也拒绝；重跑另选新目录。report.json 是主表，handover.md 从同表生成；run.json 最后写出，列输入/实现绑定、实际退出码和结果 SHA256，无此清单的目录不能消费。人工时间、节时和成本未知均为 null。

业务报告不含时间或机器路径。指纹规范：JSON 使用 UTF-8、sort_keys=true、ensure_ascii=false、separators=(",", ":")、allow_nan=false 后取 SHA256；task_fingerprint 绑定 task 原字节 SHA、按来源/old/new 顺序的实际输入绑定及核心 SHA/规则版本，run_fingerprint 再绑定 mode、revisions 原字节 SHA（scan 为 null）与 CLI SHA。哈希仅绑定字节，不认证身份或语义。

HTTP 用法：服务进程设置 `VERISCOPE_KNOWLEDGE_UPDATE=1` 后，使用 `/api/knowledge-update/sources` 查看审核来源，`sources/{source_id}/versions` 列采集时间，`versions/{数字时间戳}` 取固定文本；采集连接通过 `VERISCOPE_CHANGEDETECTION_URL`、`VERISCOPE_CHANGEDETECTION_API_KEY` 和 `VERISCOPE_KNOWLEDGE_SOURCES` 配置（清单默认 `deploy/knowledge-sources.json`）。清单中的来源已配置 watch_uuid（见 `deploy/knowledge-sources.json`）；修改正文选择规则须新建 watch。快照数量取决于来源是否真的发生过变更：首次接入通常只有一条，历史版本随来源实际更新而增加；要演示两份快照之间的差异，用 `examples/knowledge_update/` 的离线样例或手动粘贴文本。`sources` 返回的 `collector_ready` 只表示清单已为该来源配置采集（`watch_uuid` 非空且采集地址与密钥就绪），**不代表采集服务此刻在线**：服务不可用时 `/sources` 仍返回 200，失败会出现在 `sources/{source_id}/versions` 上（502 `COLLECTOR_ERROR`），不会被当成"没有版本"。`POST /api/knowledge-update/scan` 提交 JSON `{"task":"task.json 原文","files":{"sources/old.txt":"文本"}}`，文件键必须为 task 声明的规范 POSIX 相对路径，并与固定的 task.json/revisions.json 组成 Windows/Linux 均可解压的无冲突文件树，否则返回 `422 / NON_CANONICAL_PATH`；`recheck` 必须另加 `revisions` 原文，scan 不接受该字段。请求上限 8 MiB；输入错误返回 422 和诊断。`export` 使用相同输入并增加 `mode`，重新检查后返回 ZIP，可按包内说明用 CLI 写入新目录重核。HTTP report/run 为 v2，绑定 `entry=http` 与 HTTP 实现哈希；业务字段和 task_fingerprint 与同版本 CLI 相同，run_fingerprint 区分入口，不代表相互认证。此开关独立于旧接口的只读限制；服务端不长期保存稿件。

本机网络环境的注意事项：采集服务默认拒绝解析到保留地址的抓取，若本机出口把公网域名解析到 `198.18.0.0/15`（例如经本地代理或沙箱出口访问外网），changedetection 会以 `Fetch blocked` 拒抓，此时需以 `ALLOW_IANA_RESTRICTED_ADDRESSES=true` 启动采集进程才能取到快照；这是该环境下的临时放宽，默认仍须保持拦截。本机演示环境的采集进程不是产品组件，掉线后按原端口与数据目录自行拉起即可：

```
ALLOW_IANA_RESTRICTED_ADDRESSES=true \
  <changedetection 安装目录>/changedetection.io \
  -h 127.0.0.1 -p 5000 -C -d <快照数据目录> -l INFO
```

Windows 下可执行文件为 `<安装目录>/Scripts/changedetection.io.exe`，路径分隔符按本机习惯。拉起后 `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/` 应返回 200。已采集的快照保存在数据目录，重启不会丢失。
