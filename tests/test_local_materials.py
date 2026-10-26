"""Counterexamples for the optional local-material boundary."""

import hashlib

import pytest

from local_materials import require_local_materials


def test_absent_group_skips_with_reportable_reason(tmp_path):
    with pytest.raises(pytest.skip.Exception, match='^missing-local-material: work/local$'):
        require_local_materials(tmp_path, {'work/local': ('first.json', 'second.json')})


@pytest.mark.parametrize('present', [(), ('first.json',)])
def test_present_group_with_missing_files_fails(tmp_path, present):
    group = tmp_path / 'work/local'
    group.mkdir(parents=True)
    for name in present:
        (group / name).write_text('{}', encoding='utf-8')
    with pytest.raises(AssertionError, match='incomplete local material group'):
        require_local_materials(tmp_path, {'work/local': ('first.json', 'second.json')})


@pytest.mark.parametrize('missing_first', [False, True])
def test_absent_group_does_not_hide_corrupt_material(tmp_path, missing_first):
    group = tmp_path / 'work/local'
    group.mkdir(parents=True)
    (group / 'data.json').write_bytes(b'changed')
    groups = {'work/local': ('data.json',), 'work/absent': ('data.json',)}
    if missing_first:
        groups = dict(reversed(list(groups.items())))

    def check(path):
        assert hashlib.sha256((path / 'data.json').read_bytes()).hexdigest() == hashlib.sha256(b'original').hexdigest()

    with pytest.raises(AssertionError):
        require_local_materials(tmp_path, groups, checks={'work/local': check})


def test_complete_group_runs_its_validation(tmp_path):
    group = tmp_path / 'work/local'
    group.mkdir(parents=True)
    (group / 'data.json').write_bytes(b'original')
    checked = []
    require_local_materials(tmp_path, {'work/local': ('data.json',)},
                            checks={'work/local': lambda path: checked.append(path)})
    assert checked == [group]


def test_file_in_place_of_group_fails(tmp_path):
    (tmp_path / 'local').write_text('invalid', encoding='utf-8')
    with pytest.raises(AssertionError, match='not a directory'):
        require_local_materials(tmp_path, {'local': ('data.json',)})
