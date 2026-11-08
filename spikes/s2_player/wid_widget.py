"""S2: libmpv embedded through `wid` (mpv draws into a native child window). Windows.

    python -m spikes.s2_player.wid_widget --work DIR [--segments 200] [--seed 2]

The simpler embedding from docs/architecture.md §8: a native QWidget, its HWND passed
to mpv as `wid`, `vo=gpu` (d3d11 on Windows). Qt never paints the video, so there is
no paint counter here. Not reliable on macOS (mpv cannot attach its Cocoa view to a
foreign NSView reliably), which is why the Mac prototype uses render_widget.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import mpv  # type: ignore[import-untyped]
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from spikes.s2_player.render_widget import BASE_OPTS, PlayerWindow, demo_edl


class MpvWidWidget(QWidget):
    """Native child window handed to mpv."""

    def __init__(self, parent: QWidget | None = None, **mpv_opts: Any) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen)  # Qt must not paint over mpv
        self.setMinimumSize(320, 180)
        opts = {**BASE_OPTS, **mpv_opts}
        opts.setdefault("vo", "gpu")
        self.mpv = mpv.MPV(wid=str(int(self.winId())), **opts)

    def paintEngine(self) -> None:  # type: ignore[override]
        return None

    def wait_paint_after(self, t: float, timeout: float = 2.0) -> float | None:
        return None  # mpv presents on its own; nothing to observe from Qt

    def first_paint_after(self, t: float, timeout: float = 2.0) -> float | None:
        return None

    def shutdown(self) -> None:
        self.mpv.terminate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path("spikes/data/s2/Той Студия"))
    ap.add_argument("--segments", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2)
    args = ap.parse_args(argv)
    if sys.platform == "darwin":
        print("warning: wid embedding is not reliable on macOS; use render_widget", file=sys.stderr)
    url, rebuild = demo_edl(args.work, args.segments, args.seed)
    app = QApplication(sys.argv[:1])
    video = MpvWidWidget()
    win = PlayerWindow(video, rebuild)
    win.resize(980, 640)
    win.show()
    video.mpv.loadfile(url)
    app.aboutToQuit.connect(video.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
