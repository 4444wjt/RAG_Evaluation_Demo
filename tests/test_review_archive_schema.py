"""The published review-archive schema must accept the frozen review package and reject contradictions."""

from copy import deepcopy
from pathlib import Path
import json

import jsonschema
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((PROJECT_ROOT / "schemas" / "review-archive.v1.schema.json").read_text(encoding="utf-8"))
ARCHIVE = json.loads((PROJECT_ROOT / "frontend" / "src" / "fixtures" / "credibility-review.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def _first(status):
    return next(index for index, record in enumerate(ARCHIVE["records"]) if record["status"] == status)


def test_schema_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_frozen_review_package_conforms():
    errors = sorted(VALIDATOR.iter_errors(ARCHIVE), key=lambda error: list(error.path))
    assert not errors, [f"{list(error.path)}: {error.message}" for error in errors]


def _broken(mutate):
    archive = deepcopy(ARCHIVE)
    mutate(archive)
    return archive


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda a: a.update(snapshotSha256="ABC"), id="bad-sha256"),
    pytest.param(lambda a: a["gateRuleReview"].update(note="已检查"), id="unverified-check-with-note"),
    pytest.param(lambda a: a["records"][_first("agree")]["answerReview"].update(extraction="missing"), id="agree-with-missing-extraction"),
    pytest.param(lambda a: a["records"][_first("agree")]["statementReviews"][0].update(humanVerdict="uncertain"), id="agree-with-uncertain-statement"),
    pytest.param(lambda a: a["records"][_first("disagree")].update(status="pending"), id="pending-with-prefilled-review"),
    pytest.param(lambda a: a["records"][_first("disagree")].update(reviewer=None), id="reviewed-without-reviewer"),
    pytest.param(lambda a: a["records"][0].update(machineScore=1.0), id="unknown-field"),
])
def test_schema_rejects_contradictions(mutate):
    assert not VALIDATOR.is_valid(_broken(mutate))
