import csv
import hashlib

import numpy as np
import pytest

from tools.fetch_models import MODELS
from toystudio.engine import models
from toystudio.engine.models import model_path

ort = pytest.importorskip("onnxruntime")


def _present() -> bool:
    return all((models.MODELS_DIR / fn).exists() for _, _, fn in MODELS.values())


needs_models = pytest.mark.skipif(not _present(), reason="models not downloaded")


@needs_models
def test_model_files_match_pinned_sha256():
    for _url, sha, filename in MODELS.values():
        data = (models.MODELS_DIR / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha, filename


def _session(name):
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
