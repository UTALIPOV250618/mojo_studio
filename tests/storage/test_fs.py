import os

import pytest

from toystudio.storage.fs import atomic_write


def test_atomic_write_no_partial(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    atomic_write(p, b"old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write(p, b"new")
    assert p.read_bytes() == b"old"
    assert not (tmp_path / "x.json.tmp").exists()


def test_atomic_write_creates(tmp_path):
    p = tmp_path / "y.bin"
    atomic_write(p, b"data")
    assert p.read_bytes() == b"data"
