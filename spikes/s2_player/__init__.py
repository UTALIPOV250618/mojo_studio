"""S2: libmpv as the draft-editor player (see docs/spikes/S2-player.md)."""

import os
import sys

# Windows: python-mpv looks for libmpv-2.dll on PATH. Let it sit next to these
# scripts (or in %S2_MPV_DIR%) without touching the system PATH.
if sys.platform == "win32":  # pragma: no cover - Windows only
    _dirs = [os.environ.get("S2_MPV_DIR", ""), os.path.dirname(os.path.abspath(__file__))]
    os.environ["PATH"] = os.pathsep.join([d for d in _dirs if d] + [os.environ.get("PATH", "")])
