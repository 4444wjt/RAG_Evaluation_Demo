"""把 mutmut 的 JUnit 报告整理成 Markdown：共多少个变异、测试抓住多少、存活的是哪几处。

mutmut 的报告里每个变异是一个用例：failure = 存活（测试没发现代码被改坏），error = 超时，其余为被抓住。
用法（CI 中）：
    mutmut junitxml --untested-policy=failure > mutation.junit.xml
    python scripts/mutation_summary.py mutation.junit.xml >> "$GITHUB_STEP_SUMMARY"
"""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def summarize(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    survived = [case for case in cases if case.find("failure") is not None]
    timeouts = [case for case in cases if case.find("error") is not None]
    killed = len(cases) - len(survived) - len(timeouts)
    return cases, killed, survived, timeouts


def render(path):
    cases, killed, survived, timeouts = summarize(path)
    total = len(cases)
    rate = f"{killed / total:.1%}" if total else "—"
    lines = [
        "### 门禁代码变异测试（mutmut）",
        "",
        f"共 {total} 个变异，测试抓住 {killed} 个（{rate}），存活 {len(survived)} 个，超时 {len(timeouts)} 个。",
        "",
    ]
    if survived:
        lines += ["| 变异 | 行号 | 原代码 |", "|---|---|---|"]
        for case in survived:
            code = (case.findtext("system-out") or "").strip().replace("|", "\\|")
            lines.append(f"| {case.get('name')} | {case.get('line')} | `{code}` |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法：python scripts/mutation_summary.py mutation.junit.xml", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(render(Path(args[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
