"""Local JSON/CSV loading and mocked benchmark conversion."""

import json
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("wrapper", [None, "testset", "cases"])
def test_json_shapes_bom_whitespace_and_optional_fields(app, tmp_path, wrapper):
    rows = [
        {"question": "  中文问题  ", "ground_truth": "  答案  ", "contexts": ["内容"]},
        {"question": "负样本", "case_type": " negative "},
        {"question": "默认类型", "case_type": " "},
        {"question": "   "}, {"ground_truth": "无问题"},
    ]
    path = tmp_path / "cases.JSON"
    path.write_text(json.dumps(rows if wrapper is None else {wrapper: rows}), encoding="utf-8-sig")
    result = app.load_custom_testset(str(path))
    assert result == [
        {"question": "中文问题", "ground_truth": "答案", "contexts": ["内容"], "case_type": "positive"},
        {"question": "负样本", "ground_truth": "", "contexts": [], "case_type": "negative"},
        {"question": "默认类型", "ground_truth": "", "contexts": [], "case_type": "positive"},
    ]


@pytest.mark.parametrize("extension", [".csv", ".CSV", ".txt"])
def test_csv_bom_quoted_commas_and_case_types(app, tmp_path, extension):
    path = tmp_path / ("cases" + extension)
    path.write_text('question,ground_truth,case_type\n" 中文,问题 "," 答案,内容 ",negative\n普通问题,答案,positive\n', encoding="utf-8-sig")
    result = app.load_custom_testset(str(path))
    assert result[0] == {"question": "中文,问题", "ground_truth": "答案,内容", "contexts": [], "case_type": "negative"}
    assert result[1]["case_type"] == "positive"


def test_csv_missing_optional_columns_get_defaults(app, tmp_path):
    path = tmp_path / "minimal.csv"
    path.write_text("question\nonly question\n", encoding="utf-8")
    assert app.load_custom_testset(str(path)) == [
        {"question": "only question", "ground_truth": "", "contexts": [], "case_type": "positive"},
    ]


@pytest.mark.parametrize("data", [[], {}, [{"question": " "}], [{"ground_truth": "answer"}]])
def test_empty_json_testset_is_rejected(app, tmp_path, data):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="自定义测试集为空"):
        app.load_custom_testset(str(path))


@pytest.mark.parametrize("data", [42, "text", {"testset": "not a list"}])
def test_invalid_json_top_level_is_rejected(app, tmp_path, data):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 测试集必须是列表"):
        app.load_custom_testset(str(path))


def test_missing_unsupported_and_malformed_files(app, tmp_path):
    with pytest.raises(FileNotFoundError, match="测试集文件不存在"):
        app.load_custom_testset(str(tmp_path / "missing.json"))
    path = tmp_path / "cases.xlsx"
    path.write_text("not supported", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持的测试集格式"):
        app.load_custom_testset(str(path))
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        app.load_custom_testset(str(path))


def test_empty_csv_header_only_is_rejected(app, tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("question,ground_truth\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="自定义测试集为空"):
        app.load_custom_testset(str(path))


def test_csv_blank_cells_should_not_become_literal_nan(app, tmp_path):
    path = tmp_path / "blank.csv"
    path.write_text("question,ground_truth,case_type\n,orphan,positive\nvalid,,\n", encoding="utf-8")
    assert app.load_custom_testset(str(path)) == [
        {"question": "valid", "ground_truth": "", "contexts": [], "case_type": "positive"},
    ]


@pytest.mark.parametrize("extension", [".csv", ".txt"])
def test_backend_csv_blank_cells_match_main_loader(app, tmp_path, extension):
    from backend.services import testsets as backend_testsets

    path = tmp_path / ("blank" + extension)
    path.write_text("question,ground_truth,case_type\n,orphan,positive\nvalid,,\n", encoding="utf-8")
    expected = [
        {"question": "valid", "ground_truth": "", "contexts": [], "case_type": "positive"},
    ]
    result = backend_testsets.load_custom_testset(str(path))
    assert result == expected
    assert result == app.load_custom_testset(str(path))


class FakeDataset:
    features = {"question": object(), "best_answer": object()}

    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def select(self, indices):
        return FakeDataset([self.rows[i] for i in indices])


@pytest.mark.parametrize("limit, count", [(1, 1), (20, 2), (None, 2)])
def test_benchmark_mapping_and_limit_without_download(app, limit, count):
    app.load_dataset = Mock(return_value=FakeDataset([
        {"question": " q1 ", "best_answer": " a1 "},
        {"question": "q2", "best_answer": "a2"},
        {"question": "  ", "best_answer": "ignored"},
    ]))
    result = app.load_benchmark("truthful_qa", limit=limit)
    app.load_dataset.assert_called_once_with("truthfulqa/truthful_qa", "generation", split="validation")
    assert len(result) == count
    assert result[0] == {"question": "q1", "ground_truth": "a1", "contexts": [], "case_type": "positive"}


def test_benchmark_unknown_empty_and_unavailable(app):
    app.load_dataset = Mock()
    with pytest.raises(ValueError, match="不支持的基准数据集"):
        app.load_benchmark("unknown")
    app.load_dataset.assert_not_called()
    app.load_dataset.return_value = FakeDataset([])
    with pytest.raises(RuntimeError, match="抽样为空"):
        app.load_benchmark("truthful_qa")
    app.load_dataset.side_effect = OSError("offline fixture")
    with pytest.raises(RuntimeError, match="加载失败") as caught:
        app.load_benchmark("truthful_qa")
    assert isinstance(caught.value.__cause__, OSError)
