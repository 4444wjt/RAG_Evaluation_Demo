"""scripts/mutation_summary.py 把 mutmut 的 JUnit 报告正确汇总为得分与存活列表。"""
from pathlib import Path
import importlib.util

PROJECT_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mutation_summary", PROJECT_ROOT / "scripts" / "mutation_summary.py")
mutation_summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutation_summary)

REPORT = """<?xml version="1.0" ?>
<testsuites><testsuite name="mutmut" tests="4">
  <testcase name="Mutant #1" file="backend/core/gate.py" line="40"><system-out>    first_model = valid_summary[0]</system-out></testcase>
  <testcase name="Mutant #2" file="backend/core/gate.py" line="41"><system-out>    print("=" * 50)</system-out><failure message="bad_survived">diff</failure></testcase>
  <testcase name="Mutant #3" file="backend/core/gate.py" line="42"><system-out>    a | b</system-out><error message="bad_timeout" type="timeout">diff</error></testcase>
  <testcase name="Mutant #4" file="backend/core/gate.py" line="43"><system-out>    pass_flag = False</system-out></testcase>
</testsuite></testsuites>
"""


def test_counts_killed_survived_and_timeouts(tmp_path):
    report = tmp_path / "mutation.junit.xml"
    report.write_text(REPORT, encoding="utf-8")
    cases, killed, survived, timeouts = mutation_summary.summarize(report)
    assert (len(cases), killed, len(survived), len(timeouts)) == (4, 2, 1, 1)
    text = mutation_summary.render(report)
    assert "共 4 个变异，测试抓住 2 个（50.0%），存活 1 个，超时 1 个。" in text
    assert "| Mutant #2 | 41 | `print(\"=\" * 50)` |" in text
    assert "a \\| b" not in text  # 超时的变异不列入存活表


def test_requires_exactly_one_report_path():
    assert mutation_summary.main([]) == 2
