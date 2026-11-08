"""S2: libmpv embedded through the render API (MpvRenderContext + QOpenGLWidget).

    python -m spikes.s2_player.render_widget --work DIR [--segments 200] [--seed 2]

Works on every OS (the only way that is reliable on macOS). mpv runs with
vo=libmpv and draws into the widget's FBO from paintGL; mpv's update callback comes
from an mpv thread, so it only emits a Qt signal and the repaint happens on the GUI
thread. Buttons: play/pause, seek (random frame), step, back step, rebuild EDL.
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

import mpv  # type: ignore[import-untyped]
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Options shared by both embeddings (docs/architecture.md §8).
BASE_OPTS: dict[str, Any] = {
    "hwdec": "auto-safe",
    "keep_open": "yes",
    "hr_seek": "yes",
    "pause": True,
    "input_default_bindings": False,
    "input_vo_keyboard": False,
    "osc": False,
}


def configure_gl() -> None:
    """Call before QApplication. macOS hands out a GL 2.1 legacy context by default:
    mpv then disables its good scalers ("GLSL version too old") and videotoolbox
    interop ("need >= OpenGL 3.0"), so decoding falls back to software + upload.
    A 4.1 core profile fixes both. Windows/Linux default contexts are already
    compatibility contexts of the highest version, so they are left alone."""
    if sys.platform == "darwin":
        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(1)
        QSurfaceFormat.setDefaultFormat(fmt)


_NAP_TOKEN: int | None = None


def disable_app_nap(reason: str = "video playback") -> bool:
    """macOS: hold an NSProcessInfo activity (user-initiated + latency-critical).

    Without it a process that is not frontmost (a benchmark started from a
    terminal) is App-Napped after ~20 s: paints come every ~100 ms and mpv drops a
    third of the frames. The product needs the same while playing. No-op elsewhere.
    """
    global _NAP_TOKEN
    if sys.platform != "darwin" or _NAP_TOKEN:
        return bool(_NAP_TOKEN)
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "libobjc.dylib")
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    send = objc.objc_msgSend

    def msg(obj: Any, sel: bytes, *args: Any, argtypes: tuple = ()) -> Any:
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
        return send(obj, objc.sel_registerName(sel), *args)

    text = msg(
        objc.objc_getClass(b"NSString"),
        b"stringWithUTF8String:",
        reason.encode(),
        argtypes=(ctypes.c_char_p,),
    )
    info = msg(objc.objc_getClass(b"NSProcessInfo"), b"processInfo")
    user_initiated, latency_critical = 0x00FFFFFF, 0xFF00000000
    token = msg(
        info,
        b"beginActivityWithOptions:reason:",
        ctypes.c_uint64(user_initiated | latency_critical),
        text,
        argtypes=(ctypes.c_uint64, ctypes.c_void_p),
    )
    if token:
        msg(token, b"retain")
    _NAP_TOKEN = token
    return bool(token)


def timer_resolution_ms() -> dict[str, float] | None:
    """Windows: (coarsest, finest, current) system timer resolution in ms."""
    if sys.platform != "win32":
        return None
    try:
        lo, hi, cur = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        ctypes.WinDLL("ntdll").NtQueryTimerResolution(
            ctypes.byref(lo), ctypes.byref(hi), ctypes.byref(cur)
        )
        return {"coarsest": lo.value / 1e4, "finest": hi.value / 1e4, "current": cur.value / 1e4}
    except Exception:  # noqa: BLE001 - diagnostics only
        return None


def disable_windows_throttling() -> dict[str, Any] | None:
    """Windows: opt this process out of power throttling (EcoQoS) and ask for 1 ms
    timers. Windows 11 throttles processes it considers background (e.g. started
    from another app): timers coalesce and a player can present only a few frames a
    second. Analogue of disable_app_nap on macOS. No-op elsewhere."""
    if sys.platform != "win32":
        return None

    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [
            ("Version", ctypes.c_ulong),
            ("ControlMask", ctypes.c_ulong),
            ("StateMask", ctypes.c_ulong),
        ]

    out: dict[str, Any] = {"timer_before": timer_resolution_ms()}
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    execution_speed, ignore_timer_resolution = 0x1, 0x4
    state = PowerThrottlingState(1, execution_speed | ignore_timer_resolution, 0)
    process_power_throttling = 4
    out["power_throttling_off"] = bool(
        k32.SetProcessInformation(
            ctypes.c_void_p(k32.GetCurrentProcess()),
            process_power_throttling,
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
    )
    out["time_begin_period_1ms"] = ctypes.WinDLL("winmm").timeBeginPeriod(1) == 0
    out["timer_after"] = timer_resolution_ms()
    return out


class MpvRenderWidget(QOpenGLWidget):
    """mpv drawn by the render API into this widget's FBO."""

    _mpv_update = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, block_for_target_time: bool = False, **mpv_opts: Any
    ) -> None:
        super().__init__(parent)
        opts = {**BASE_OPTS, **mpv_opts}
        self.block = block_for_target_time
        self.mpv = mpv.MPV(vo="libmpv", **opts)
        self.ctx: mpv.MpvRenderContext | None = None
        self._proc_fn = mpv.MpvGlGetProcAddressFn(self._get_proc_address)
        self._mpv_update.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self.frameSwapped.connect(self._swapped)
        # Paint bookkeeping for the bench (read from other threads).
        self.paint_cv = threading.Condition()
        self.paints = 0
        self.last_paint_t = 0.0
        self.render_ms: list[float] = []
        self.paint_times: list[float] = []
        self.swap_times: list[float] = []
        # Probe: read the drawn FBO back after each render (bench only, costs a few ms).
        self.probe = False
        self.probe_shots: list[tuple[float, Any]] = []
        self._gl_read: Any = None
        self._gl_bind: Any = None
        self._gl_lib: ctypes.CDLL | None = None

    # GL entry points for mpv. Qt's getProcAddress is the portable way; on macOS
    # core GL functions are plain symbols of the OpenGL framework, so fall back to
    # dlsym there.
    def _get_proc_address(self, _ctx: Any, name: bytes) -> int:
        glctx = QOpenGLContext.currentContext()
        addr = 0
        if glctx is not None:
            try:
                addr = int(glctx.getProcAddress(name) or 0)
            except Exception:  # noqa: BLE001 - fall through to dlsym
                addr = 0
        if not addr and sys.platform == "darwin":
            if self._gl_lib is None:
                self._gl_lib = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
            try:
                addr = ctypes.cast(getattr(self._gl_lib, name.decode()), ctypes.c_void_p).value or 0
            except AttributeError:
                addr = 0
        return addr

    def initializeGL(self) -> None:
        self.ctx = mpv.MpvRenderContext(
            self.mpv, "opengl", opengl_init_params={"get_proc_address": self._proc_fn}
        )
        self.ctx.update_cb = self._mpv_update.emit

    def paintGL(self) -> None:
        if self.ctx is None:
            return
        ratio = self.devicePixelRatioF()
        w, h = round(self.width() * ratio), round(self.height() * ratio)
        t0 = time.perf_counter()
        self.ctx.render(
            flip_y=True,
            opengl_fbo={"w": w, "h": h, "fbo": self.defaultFramebufferObject()},
            block_for_target_time=self.block,
        )
        t1 = time.perf_counter()
        if self.probe:
            self.probe_shots.append((t1, self._read_fbo(w, h)))
        with self.paint_cv:
            self.paints += 1
            self.last_paint_t = t1
            if len(self.render_ms) < 100_000:
                self.render_ms.append((t1 - t0) * 1000)
                self.paint_times.append(t1)
            self.paint_cv.notify_all()

    def _swapped(self) -> None:
        if self.ctx is not None:
            self.ctx.report_swap()
        with self.paint_cv:
            if len(self.swap_times) < 200_000:
                self.swap_times.append(time.perf_counter())

    def _read_fbo(self, w: int, h: int) -> Any:
        """The FBO mpv just drew into, as an HxWx4 RGBA array, top row first."""
        import numpy as np

        if self._gl_read is None:
            void_fn = ctypes.CFUNCTYPE(None, *([ctypes.c_int] * 6), ctypes.c_void_p)
            self._gl_read = void_fn(self._get_proc_address(None, b"glReadPixels"))
            bind_fn = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)
            self._gl_bind = bind_fn(self._get_proc_address(None, b"glBindFramebuffer"))
        gl_framebuffer, gl_rgba, gl_unsigned_byte = 0x8D40, 0x1908, 0x1401
        self._gl_bind(gl_framebuffer, self.defaultFramebufferObject())
        buf = np.empty((h, w, 4), dtype=np.uint8)
        self._gl_read(0, 0, w, h, gl_rgba, gl_unsigned_byte, buf.ctypes.data)
        return buf[::-1]

    def swap_after(self, t: float) -> float | None:
        """perf_counter time of the first buffer swap at or after t (None if none yet)."""
        with self.paint_cv:
            i = bisect.bisect_left(self.swap_times, t)
            return self.swap_times[i] if i < len(self.swap_times) else None

    def wait_paint_after(self, t: float, timeout: float = 2.0) -> float | None:
        """perf_counter time of the first paint that finished after t, or None."""
        deadline = time.perf_counter() + timeout
        with self.paint_cv:
            while self.last_paint_t <= t:
                left = deadline - time.perf_counter()
                if left <= 0:
                    return None
                self.paint_cv.wait(left)
            return self.last_paint_t

    def first_paint_after(self, t: float, timeout: float = 2.0) -> float | None:
        """perf_counter time of the earliest paint that finished after t, or None."""
        if self.wait_paint_after(t, timeout) is None:
            return None
        with self.paint_cv:
            i = bisect.bisect_right(self.paint_times, t)
            return self.paint_times[i] if i < len(self.paint_times) else self.last_paint_t

    def shutdown(self) -> None:
        if self.ctx is not None:
            self.makeCurrent()
            self.ctx.free()
            self.ctx = None
            self.doneCurrent()
        self.mpv.terminate()


class PlayerWindow(QWidget):
    """Video widget + the four controls from the brief."""

    def __init__(self, video: QWidget, rebuild: Any = None) -> None:
        super().__init__()
        self.video = video
        self.m: mpv.MPV = video.mpv  # type: ignore[attr-defined]
        self._rebuild = rebuild
        self._rng = random.Random(0)
        self.setWindowTitle("S2 player")
        self.status = QLabel("")
        buttons = QHBoxLayout()
        for text, fn in (
            ("Пуск/пауза", self.toggle),
            ("Перемотка", self.seek_random),
            ("Шаг", lambda: self.m.command("frame-step")),
            ("Шаг назад", lambda: self.m.command("frame-back-step")),
            ("Пересобрать EDL", self.rebuild),
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(video, 1)
        lay.addLayout(buttons)
        lay.addWidget(self.status)

    def toggle(self) -> None:
        self.m.pause = not self.m.pause

    def seek_random(self) -> None:
        dur = self.m.duration or 0
        self.m.command("seek", f"{self._rng.uniform(0, dur):.6f}", "absolute+exact")

    def rebuild(self) -> None:
        if self._rebuild is None:
            return
        pos = self.m.time_pos or 0
        url = self._rebuild()
        self.m.loadfile(url, "replace", start=f"{pos:.6f}")
        self.status.setText(f"EDL пересобран, позиция {pos:.3f} с")


def demo_edl(work: Path, n: int, seed: int) -> tuple[str, Any]:
    from spikes.s2_player.prepare import load_sources, prepare
    from spikes.s2_player.timeline import edit_segment, edl_url, make_segments

    paths = prepare(work)
    sources = load_sources(paths["proxy"])
    segments = make_segments(sources, n, seed=seed)
    rng = random.Random(seed)

    def rebuild() -> str:
        k = rng.randrange(len(segments))
        segments[:] = edit_segment(segments, sources, k, 25)
        return edl_url(segments, sources)

    return edl_url(segments, sources), rebuild


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path("spikes/data/s2/Той Студия"))
    ap.add_argument("--segments", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2)
    args = ap.parse_args(argv)
    url, rebuild = demo_edl(args.work, args.segments, args.seed)
    configure_gl()
    disable_app_nap()
    app = QApplication(sys.argv[:1])
    video = MpvRenderWidget()
    win = PlayerWindow(video, rebuild)
    win.resize(980, 640)
    win.show()
    video.mpv.loadfile(url)
    app.aboutToQuit.connect(video.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
