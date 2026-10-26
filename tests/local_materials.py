"""Skip absent local material groups, but fail on incomplete or invalid groups."""

from pathlib import Path

import pytest


def require_local_materials(root, groups, *, checks=None):
    """Groups map repository-relative directories to required relative files.

    A supplied check validates a present group before any missing group can cause
    a skip. This keeps a second absent group from hiding damaged local evidence.
    """
    missing = []
    for directory, filenames in groups.items():
        path = Path(root) / directory
        if not path.exists():
            missing.append(directory)
            continue
        assert path.is_dir(), f'local material group is not a directory: {directory}'
        for filename in filenames:
            assert (path / filename).is_file(), f'incomplete local material group: {directory}/{filename}'
        if checks and directory in checks:
            checks[directory](path)
    if missing:
        pytest.skip('missing-local-material: ' + ', '.join(missing))
