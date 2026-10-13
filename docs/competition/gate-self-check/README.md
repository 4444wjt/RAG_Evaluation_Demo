# 门禁代码变异测试归档（2026-10-13）

`mutation.junit.xml` 是 mutmut 2.5.1 对 [`backend/core/gate.py`](../../../backend/core/gate.py) 的一次本机运行结果，由 `mutmut junitxml --untested-policy=failure` 原样导出，未经编辑。`manifest.json` 记录运行条件、被测文件的 SHA-256，以及对存活变异的人工说明。

| 结果 | 数量 |
|---|---|
| 生成的变异 | 42 |
| 被测试抓住 | 39（92.9%） |
| 存活 | 3，均不影响裁决（见 `manifest.json`） |

- 运行时 `gate.py` 的 SHA-256 为 `89d98d3ae8eeb076da163427a68477e59de3a8783c69d0e5bcd614cc29e9d6ae`。门禁代码一旦改动，这份归档就不再对应当前代码：[`tools/competition/build_audit_fixture.py`](../../../tools/competition/build_audit_fixture.py) 会拒绝生成工作台「门禁审计」页的数据，需要重新运行变异测试并更新归档。
- 跳过只改日志文字的字符串类变异（`string`、`fstring`）；运行命令与说明见 [门禁可信度 · 附录](../gate-credibility.md#附门禁代码本身对不对)。
- CI 的 [`mutation.yml`](../../../.github/workflows/mutation.yml) 在门禁代码或门禁测试变动时于 Linux 上重新运行，结果写入作业摘要；本归档是 Windows 上的一次运行，两者的变异集合相同时结果应一致。
- 变异得分只说明测试能发现这些人工改动，不证明门禁代码没有其他缺陷。
