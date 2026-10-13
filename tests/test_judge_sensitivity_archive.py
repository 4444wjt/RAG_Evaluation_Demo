"""The archived 2026-10-12 judge sensitivity run re-analyzes offline to exactly the committed summary; no model is called."""

import json
from pathlib import Path

from tools.competition import judge_sensitivity as js

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN = PROJECT_ROOT / "docs" / "competition" / "judge-sensitivity" / "run-20261012"
ANALYSIS = PROJECT_ROOT / "docs" / "competition" / "judge-sensitivity" / "analysis-20261012"


def test_archived_run_reanalyzes_to_committed_summary(tmp_path):
    js.analyze(RUN, tmp_path / "analysis")
    assert json.loads((tmp_path / "analysis" / "summary.json").read_text(encoding="utf-8")) == \
        json.loads((ANALYSIS / "summary.json").read_text(encoding="utf-8"))


def test_archived_run_is_complete_and_authorized():
    manifest = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stopReason"] == "completed" and manifest["attempts"] == manifest["completedRecords"] == 60
    assert manifest["protectedSourcesUnchanged"] is True and manifest["missingUsage"] == 0
    authorization = json.loads((RUN / "authorization.json").read_text(encoding="utf-8"))
    assert authorization["status"] == "authorized" and authorization["maxRequests"] == 60 and authorization["maxRetries"] == 0
    js.verify_checksums(ANALYSIS)
