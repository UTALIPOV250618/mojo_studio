import sys
from pathlib import Path

from tests.media_fixtures import make_clip, make_truncated, needs_ffmpeg
from toystudio.engine.media.ffmpeg import find_tool
from toystudio.engine.media.probe import check_integrity, probe_media


@needs_ffmpeg
def test_probe_basic(tmp_path):
    info = probe_media(make_clip(tmp_path / "a.mp4", duration_s=4, size="640x360", fps=25))
    assert info.width == 640 and info.height == 360
    assert abs(info.duration_us - 4_000_000) <= 50_000
    assert info.fps == 25.0 and info.has_audio and info.codec == "h264"


@needs_ffmpeg
def test_probe_rotated_reports_display_size(tmp_path):
    info = probe_media(make_clip(tmp_path / "r.mp4", size="640x360", rotate=90))
    assert (info.width, info.height) == (360, 640) and abs(info.rotation) == 90


@needs_ffmpeg
def test_probe_no_audio(tmp_path):
    assert probe_media(make_clip(tmp_path / "n.mp4", audio=False)).has_audio is False


@needs_ffmpeg
def test_integrity_ok(tmp_path):
    assert check_integrity(make_clip(tmp_path / "a.mp4")) is True


@needs_ffmpeg
def test_integrity_truncated_file(tmp_path):
    src = make_clip(tmp_path / "a.mp4", duration_s=6)
    assert check_integrity(make_truncated(src, tmp_path / "cut.mp4", keep=0.5)) is False


def test_find_tool_env_override(tmp_path, monkeypatch):
    exe = tmp_path / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe.write_text("")
    exe.chmod(0o755)
    monkeypatch.setenv("TOYSTUDIO_FFMPEG_DIR", str(tmp_path))
    assert Path(find_tool("ffprobe")) == exe


def test_popen_flags_hide_console_only_on_windows(monkeypatch):
    import subprocess

    from toystudio.engine.media import ffmpeg

    monkeypatch.setattr(ffmpeg.sys, "platform", "darwin")
    assert ffmpeg.popen_flags() == 0
    monkeypatch.setattr(ffmpeg.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert ffmpeg.popen_flags() == 0x08000000


def test_every_child_process_gets_the_no_window_flag(monkeypatch, tmp_path):
    import subprocess

    from toystudio.app import i18n
    from toystudio.engine.media import probe, thumbs

    seen: list[object] = []

    def fake_run(cmd, **kw):
        seen.append(kw.get("creationflags"))
        return subprocess.CompletedProcess(cmd, 1, b"{}", b"err")

    for mod in (probe, thumbs, i18n):
        monkeypatch.setattr(mod, "popen_flags", lambda: 0x08000000)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(probe, "find_tool", lambda name: name)
    monkeypatch.setattr(thumbs, "find_tool", lambda name: name)
    monkeypatch.setattr(thumbs, "probe_media", lambda p: type("I", (), {"duration_us": 2})())
    monkeypatch.setattr(i18n, "_lrelease", lambda: "lrelease")
    monkeypatch.setattr(i18n, "_DIR", tmp_path)
    (tmp_path / "ky.ts").write_text("<TS/>", "utf-8")
    try:
        probe.probe_media(tmp_path / "a.mp4")
    except ValueError:
        pass
    probe.check_integrity(tmp_path / "a.mp4")
    try:
        thumbs.card_thumbnail(tmp_path / "p.mp4", tmp_path / "card.jpg")
    except RuntimeError:
        pass
    i18n.build_translations()
    assert seen and all(f == 0x08000000 for f in seen), seen
