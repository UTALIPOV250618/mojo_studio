"""Single-pass decode: proxy + wav + frame metrics from one ffmpeg run."""

import itertools
import threading
from pathlib import Path

import numpy as np
import pytest

from tests.media_fixtures import make_clip, needs_ffmpeg
from toystudio.engine.analysis.metrics import FrameReader
from toystudio.engine.analysis.single_pass import FfmpegFailed, Stopped, outputs_for, run
from toystudio.engine.media.probe import probe_media

pytestmark = needs_ffmpeg


def _run(src: Path, out, **kw):  # type: ignore[no-untyped-def]
    return run(
        src,
        probe_media(src),
        out,
        on_progress=kw.get("on_progress", lambda s: None),
        should_stop=kw.get("should_stop", lambda: False),
    )


def test_single_pass_outputs(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "a.mp4", duration_s=4, size="1280x720", fps=25)
    out = outputs_for(tmp_path / "cache", 1)
    m = run(src, probe_media(src), out, on_progress=lambda s: None, should_stop=lambda: False)
    p = probe_media(out.proxy)
    assert (p.width, p.height) == (960, 540) and p.fps == 25.0
    assert 15 <= len(m["sharpness"]) <= 17  # 4 fps x 4 s
    assert out.wav.exists() and out.envelope.exists() and out.frames.exists()
    assert p.has_audio
    assert not list((tmp_path / "cache").rglob("*.part"))
    with np.load(out.frames) as z:
        assert set(z.files) >= {"sharpness", "brightness", "motion", "shake", "histdiff", "t"}
    env = np.load(out.envelope)
    assert env.dtype == np.float32 and 390 <= len(env) <= 410  # 100 Hz x 4 s


def test_single_pass_rotated_portrait(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "r.mp4", duration_s=2, size="640x360", rotate=90)
    info = probe_media(src)
    assert (info.width, info.height) == (360, 640) and info.rotation != 0
    out = outputs_for(tmp_path / "cache", 2)
    m = _run(src, out)
    p = probe_media(out.proxy)
    assert (p.width, p.height) == (540, 960)
    assert p.rotation == 0
    assert len(m["sharpness"]) >= 7


def test_single_pass_no_audio(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "n.mp4", duration_s=2, audio=False)
    out = outputs_for(tmp_path / "cache", 3)
    _run(src, out)
    assert out.proxy.exists() and out.frames.exists()
    assert not probe_media(out.proxy).has_audio
    assert not out.wav.exists() and not out.envelope.exists()
    assert not list((tmp_path / "cache").rglob("*.part"))


def test_single_pass_cyrillic_paths(tmp_path: Path) -> None:
    base = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    src = make_clip(base / "видео 1.mp4", duration_s=2)
    out = outputs_for(base / "кэш", 4)
    m = _run(src, out)
    assert out.proxy.exists() and out.wav.exists() and len(m["t"]) >= 7
    assert not list(base.rglob("*.part"))


def test_single_pass_killed_leaves_no_partial(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "a.mp4", duration_s=20)
    out = outputs_for(tmp_path / "cache", 1)
    calls: list[float] = []
    with pytest.raises(Stopped):
        run(
            src,
            probe_media(src),
            out,
            on_progress=calls.append,
            should_stop=lambda: len(calls) > 0,
        )
    assert not out.proxy.exists() and not list((tmp_path / "cache").rglob("*.part"))


def test_progress_monotonic_and_reaches_duration(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "p.mp4", duration_s=6)
    out = outputs_for(tmp_path / "cache", 5)
    calls: list[float] = []
    _run(src, out, on_progress=calls.append)
    assert calls
    assert all(b >= a for a, b in itertools.pairwise(calls))
    assert calls[-1] == pytest.approx(probe_media(src).duration_us / 1e6, abs=0.1)


def test_pump_failure_raises_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self, *a, **k):  # type: ignore[no-untyped-def]
        raise OSError("pipe broke")
        yield

    monkeypatch.setattr(FrameReader, "chunks", boom)
    src = make_clip(tmp_path / "h.mp4", duration_s=20)
    out = outputs_for(tmp_path / "cache", 6)
    result: list[BaseException] = []

    def target() -> None:
        try:
            _run(src, out)
        except FfmpegFailed as e:
            result.append(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "run() hung"
    assert isinstance(result[0], FfmpegFailed)
    assert not list((tmp_path / "cache").rglob("*.part"))
