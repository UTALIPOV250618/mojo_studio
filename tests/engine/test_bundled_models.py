import csv
import hashlib

import numpy as np
import pytest

from tools.fetch_models import MODELS, main
from toystudio.engine import models
from toystudio.engine.models import model_path


def _present() -> bool:
    return all((models.MODELS_DIR / fn).exists() for _, _, fn in MODELS.values())


needs_models = pytest.mark.skipif(not _present(), reason="models not downloaded")


@needs_models
def test_model_files_match_pinned_sha256():
    for _url, sha, filename in MODELS.values():
        data = (models.MODELS_DIR / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha, filename


def _session(name):
    ort = pytest.importorskip("onnxruntime")
    return ort.InferenceSession(str(model_path(name)), providers=["CPUExecutionProvider"])


@needs_models
def test_silero_runs_on_zeros():
    out = _session("silero_vad").run(
        None,
        {
            "input": np.zeros((1, 512), np.float32),
            "state": np.zeros((2, 1, 128), np.float32),
            "sr": np.array(16000, np.int64),
        },
    )
    assert float(out[0][0][0]) < 0.1
    assert out[1].shape == (2, 1, 128)


@needs_models
def test_yamnet_runs_on_one_second():
    scores = _session("yamnet").run(None, {"waveform": np.zeros(16000, np.float32)})[0]
    assert scores.ndim == 2 and scores.shape[1] == 521
    with model_path("yamnet_class_map").open(encoding="utf-8", newline="") as f:
        names = [row[2] for row in list(csv.reader(f))[1:]]
    assert len(names) == 521
    assert names[int(scores.mean(axis=0).argmax())] == "Silence"


def test_model_path_hint_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODELS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="fetch_models.py"):
        model_path("silero_vad")


def _patched(monkeypatch, url, sha="0" * 64):
    monkeypatch.setattr("tools.fetch_models.MODELS", {"x": (url, sha, "silero_vad.onnx")})


def test_verify_on_empty_dir_fails(tmp_path):
    assert main(["--verify"], models_dir=tmp_path) == 1


def test_wrong_sha_download_keeps_existing_file(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    src.write_bytes(b"bad")
    d = tmp_path / "m"
    d.mkdir()
    (d / "silero_vad.onnx").write_bytes(b"good")
    _patched(monkeypatch, src.as_uri())
    assert main([], models_dir=d) == 1
    assert (d / "silero_vad.onnx").read_bytes() == b"good"
    assert not list(d.glob("*.part"))


def test_missing_source_fails_without_part(tmp_path, monkeypatch):
    d = tmp_path / "m"
    d.mkdir()
    _patched(monkeypatch, (tmp_path / "nope").as_uri())
    assert main([], models_dir=d) == 1
    assert not list(d.glob("*.part"))


@needs_models
def test_models_load_from_cyrillic_folder_as_bytes(tmp_path, monkeypatch):
    import shutil

    from tests.engine.test_vad import noise, write_wav
    from toystudio.engine.audio import events
    from toystudio.engine.audio.vad import detect_speech

    ort = pytest.importorskip("onnxruntime")
    folder = tmp_path / "Модели Той"
    folder.mkdir()
    for _url, _sha, filename in MODELS.values():
        shutil.copy(models.MODELS_DIR / filename, folder / filename)
    monkeypatch.setattr(models, "MODELS_DIR", folder)
    real = ort.InferenceSession
    loaded: list[type] = []

    def spy(model, *a, **k):  # type: ignore[no-untyped-def]
        loaded.append(type(model))
        return real(model, *a, **k)

    monkeypatch.setattr(ort, "InferenceSession", spy)
    events.label_indices.cache_clear()
    try:
        wav = write_wav(tmp_path / "Звук той.wav", noise(2))
        assert detect_speech(wav, on_progress=lambda _s: None, should_stop=lambda: False) == []
        scores = events.label_scores_from_wav(wav)
        assert scores.shape[1] == len(events.LABELS)
    finally:
        events.label_indices.cache_clear()
    assert loaded == [bytes, bytes]  # never a path: no filesystem-encoding issue on Windows
