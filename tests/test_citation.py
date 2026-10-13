"""CITATION.cff 的必填字段齐全，版本与 main.py、CHANGELOG 的当前发布版本一致。"""
from pathlib import Path
import re

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_citation_matches_current_release():
    citation = yaml.safe_load((PROJECT_ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    for key in ("cff-version", "message", "title", "authors", "version", "date-released", "license", "repository-code"):
        assert citation.get(key), key
    version = re.search(r'^VERSION = "v([\d.]+)"', (PROJECT_ROOT / "main.py").read_text(encoding="utf-8"), re.M).group(1)
    assert citation["version"] == version
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}] - {citation['date-released']}" in changelog
