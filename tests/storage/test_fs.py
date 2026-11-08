import os

import pytest

from toystudio.storage.fs import atomic_write, free_bytes, is_removable


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


def test_free_bytes_of_missing_dir_uses_parent(tmp_path):
    assert free_bytes(tmp_path / "no" / "such" / "dir") == free_bytes(tmp_path) > 0


def test_is_removable_false_on_tmp(tmp_path):
    assert is_removable(tmp_path) is False
